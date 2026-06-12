"""Tests for the read-only ComfyUI status aggregator + gated switchover.

The dashboard's Image-Gen tab renders a single "generation engine" pane backed
by ``GET /api/comfyui/status``. That endpoint aggregates three sources, all of
which degrade gracefully (the pane polls it every few seconds and must never
crash on a dead container):

  - docker container state (``comfyui`` running / exited / absent)
  - systemd state of the agent stack (hermes) — to show which mode
    currently owns the single iGPU
  - ComfyUI's own HTTP API (``/system_stats`` + ``/queue``) for live telemetry

The switchover *write* path (POST /api/comfyui/switchover) is feature-gated
behind ``HAL0_COMFYUI_SWITCHOVER_ENABLED`` (501 when off). When on it validates
the target mode, no-ops if already there, refuses to drop a busy render queue
without ``force``, and drives the GpuArbiter in the background behind a 202 —
the ``switchover`` block on /status is what tracks the transition to terminal.
No subprocess is ever spawned for the switch (the shell-script path is retired):
tests install a stub arbiter on ``app.state.slot_manager`` and assert the
arbiter calls, mirroring the patched status seams.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from fastapi.testclient import TestClient

# system_stats payload shaped like ComfyUI's real response on the Strix Halo box
# (verified live 2026-06-10: vram_total reports the 80 GB GTT envelope, ram_total
# the full ~128 GB pool — NOT the 96 GB the brief assumed). vram in bytes.
_SYSTEM_STATS = {
    "system": {"ram_total": 128 * 1024**3, "ram_free": 46 * 1024**3},  # → 82 used / 128 ceil
    "devices": [
        {
            "name": "Radeon 8060S Graphics : native",
            "type": "cuda",
            "vram_total": 80 * 1024**3,
            "vram_free": 26 * 1024**3,  # → 54 GB used
        }
    ],
}

# /queue: one running job, two pending. ComfyUI shape is
# {"queue_running": [[num, id, prompt, extra, outputs]], "queue_pending": [...]}.
_QUEUE_BUSY = {
    "queue_running": [[0, "abc", {}, {}, {}]],
    "queue_pending": [[1, "def", {}, {}, {}], [2, "ghi", {}, {}, {}]],
}
_QUEUE_IDLE = {"queue_running": [], "queue_pending": []}


def _patch(container: str, hermes: bool, fetch):
    """Patch the three status seams on the comfyui route module."""
    base = "hal0.api.routes.comfyui"
    return (
        patch(f"{base}._container_state", new_callable=AsyncMock, return_value=container),
        patch(
            f"{base}._systemd_active",
            new_callable=AsyncMock,
            side_effect=lambda unit: hermes,
        ),
        patch(f"{base}._fetch_json", new_callable=AsyncMock, side_effect=fetch),
    )


async def _fetch_busy(path: str):
    return _SYSTEM_STATS if "system_stats" in path else _QUEUE_BUSY


async def _fetch_idle(path: str):
    return _SYSTEM_STATS if "system_stats" in path else _QUEUE_IDLE


async def _fetch_down(path: str):
    return None  # ComfyUI HTTP unreachable


def test_status_generating_when_container_running_and_queue_busy(client: TestClient) -> None:
    _install_arbiter(client, mode="img")  # arbiter is the mode source of truth
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_busy)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mode"] == "generation"
    assert body["reachable"] is True
    assert body["container"]["state"] == "running"
    assert body["engine"] == "generating"
    assert body["queue"] == {"running": 1, "pending": 2}
    # 80 total - 26 free = 54 GB used on the iGPU; pressure trips >= 50.
    assert body["memory"]["gtt_used_gb"] == pytest.approx(54.0, abs=0.5)
    assert body["memory"]["gtt_ceil_gb"] == 80
    assert body["memory"]["pressure"] is True
    # RAM ceiling is DERIVED from ram_total (128 GB here), not hardcoded to 96.
    assert body["memory"]["ram_used_gb"] == pytest.approx(82.0, abs=0.5)
    assert body["memory"]["ram_ceil_gb"] == 128


def test_status_running_idle_when_container_up_but_queue_empty(client: TestClient) -> None:
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_idle)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    body = r.json()
    assert body["engine"] == "running"
    assert body["queue"] == {"running": 0, "pending": 0}


def test_status_starting_when_container_running_but_http_unreachable(client: TestClient) -> None:
    # Container is up but ComfyUI hasn't bound :8188 yet — fail soft, not a 500.
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_down)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["engine"] == "starting"
    assert body["memory"] is None


def test_status_engine_running_when_reachable_but_container_probe_blind(
    client: TestClient,
) -> None:
    # Post-D9 the docker probe can't see the podman hal0-slot-img container
    # ("absent" forever) — a reachable ComfyUI IS the engine truth. Without
    # this, the resident container renders engine "stopped" 24/7 in the pane.
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_idle)
    with c, s, f:
        body = client.get("/api/comfyui/status").json()
    assert body["reachable"] is True
    assert body["engine"] == "running"


def test_status_stopped_and_inference_mode_when_container_absent(client: TestClient) -> None:
    # ComfyUI down, LLM stack up → inference owns the GPU.
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    body = r.json()
    assert body["mode"] == "inference"
    assert body["engine"] == "stopped"
    assert body["container"]["state"] == "absent"
    assert body["inference"] == {"hermes": True}


def test_status_reports_model_inventory_from_the_share(
    client: TestClient, tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The "models on share" card shows VERIFIED file counts per category — never
    # invented. Point the scanner at a temp models dir laid out like the share.
    models = tmp_path / "models"
    for cat, n in {"checkpoints": 6, "loras": 11, "vae": 3, "diffusion_models": 4}.items():
        d = models / cat
        d.mkdir(parents=True)
        for i in range(n):
            (d / f"m{i}.safetensors").touch()
    monkeypatch.setenv("COMFYUI_MODELS_DIR", str(models))
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_idle)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    inv = r.json()["inventory"]
    assert inv["checkpoints"] == 6
    assert inv["loras"] == 11
    assert inv["vae"] == 3
    assert inv["diffusion"] == 4  # diffusion_models + unet folded together


def test_status_inventory_is_none_when_share_absent(client: TestClient, tmp_path) -> None:
    # No share mounted (e.g. dev box / fresh install) → inventory is null, the
    # pane simply hides the counts rather than showing zeros it can't verify.
    import os as _os

    _os.environ.pop("COMFYUI_MODELS_DIR", None)
    with patch.dict(_os.environ, {"COMFYUI_MODELS_DIR": str(tmp_path / "nope")}):
        c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
        with c, s, f:
            r = client.get("/api/comfyui/status")
    assert r.json()["inventory"] is None


@pytest.fixture(autouse=True)
def _reset_comfyui_module_state():
    # The switchover tracker is module-global (the app object is per-test but the
    # route module is not) — reset around every test.
    from hal0.api.routes import comfyui as comfyui_mod

    comfyui_mod._reset_state()
    yield comfyui_mod
    comfyui_mod._reset_state()


class _StubArbiter:
    """Stands in for ``manager.arbiter`` — the D4-D6 GpuArbiter surface."""

    def __init__(self, mode: str = "llm") -> None:
        self.ensure_img = AsyncMock()
        self.restore_llm = AsyncMock()
        self.set_pin = Mock()
        self.status = Mock(
            return_value={
                "mode": mode,
                "pinned": False,
                "saved_llm_slots": [],
                "idle_restore_at": None,
            }
        )


def _install_arbiter(client: TestClient, mode: str = "llm") -> _StubArbiter:
    """Hang a stub manager+arbiter on app.state, the way the route finds it.

    ``mode`` is the arbiter's reported GPU mode ("llm" | "img"). The arbiter is
    the source of truth for the current mode — the docker/systemd probes are
    only a legacy fallback (post-D9 the docker container is gone while
    the daemon era is over, so they lie).
    """
    arb = _StubArbiter(mode=mode)
    client.app.state.slot_manager = SimpleNamespace(arbiter=arb)
    return arb


def test_switchover_generation_calls_arbiter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # ON: the background task drives arbiter.ensure_img — NO subprocess, the
    # shell-script seam is gone from the module entirely.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client)
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "generation"})
    assert r.status_code == 202
    assert r.json() == {"status": "switching", "mode": "generation"}
    arb.ensure_img.assert_awaited_once_with(pin=False)
    arb.restore_llm.assert_not_awaited()
    from hal0.api.routes import comfyui as comfyui_mod

    assert not hasattr(comfyui_mod, "_run_script")  # scripts retired for real


def test_switchover_inference_calls_restore(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client, mode="img")
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_idle)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "inference"})
    assert r.status_code == 202
    arb.restore_llm.assert_awaited_once_with(force=False)
    arb.ensure_img.assert_not_awaited()


def test_switchover_pin_param(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # {"pin": true} rides along to ensure_img so generation can hold the GPU.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client)
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "generation", "pin": True})
    assert r.status_code == 202
    arb.ensure_img.assert_awaited_once_with(pin=True)


def test_switchover_force_passes_to_restore(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The UI confirm dialog already warned that queued jobs drop — force wins
    # over the busy queue AND propagates to restore_llm (pin override).
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client, mode="img")
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_busy)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "inference", "force": True})
    assert r.status_code == 202
    arb.restore_llm.assert_awaited_once_with(force=True)


def test_switchover_refused_while_another_switch_in_flight(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    _reset_comfyui_module_state,
) -> None:
    # One switch at a time — racing pairs of systemctl/docker is never right.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    comfyui_mod = _reset_comfyui_module_state
    comfyui_mod._switch["active"] = True
    comfyui_mod._switch["target"] = "generation"
    arb = _install_arbiter(client)
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "inference"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "comfyui.switch_in_progress"
    arb.ensure_img.assert_not_awaited()
    arb.restore_llm.assert_not_awaited()


def test_switchover_noop_when_already_in_generation_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Arbiter already reports img mode → target reached; never re-drive it.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client, mode="img")
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_idle)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "generation"})
    assert r.status_code == 200
    assert r.json() == {"status": "noop", "mode": "generation"}
    arb.ensure_img.assert_not_awaited()


def test_switchover_noop_when_already_in_inference_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Container down → inference already owns the GPU.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client)
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "inference"})
    assert r.status_code == 200
    assert r.json() == {"status": "noop", "mode": "inference"}
    arb.restore_llm.assert_not_awaited()


def test_switchover_to_inference_refused_while_queue_busy(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Tearing ComfyUI down mid-render kills the running + queued jobs — refuse
    # unless the caller explicitly forces it (the UI confirm dialog is the force).
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client, mode="img")
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_busy)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "inference"})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "comfyui.busy"
    arb.restore_llm.assert_not_awaited()


def test_switchover_post_migration_inference_uses_arbiter_truth(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Post-D9 reality: docker container gone ("absent") while inference stays
    # active through Phase D — the legacy probe would call this "already in
    # inference" forever, locking restore_llm out of the API (pinned img mode =
    # permanent lockout). Arbiter says img → the switch MUST run.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client, mode="img")
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "inference"})
    assert r.status_code == 202
    arb.restore_llm.assert_awaited_once_with(force=False)


def test_status_mode_is_arbiter_truth_post_migration(client: TestClient) -> None:
    # Same post-migration shape on the read path: docker absent + inference up
    # used to render mode "inference"/endpoint null while img owned the GPU.
    _install_arbiter(client, mode="img")
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_idle)
    with c, s, f:
        body = client.get("/api/comfyui/status").json()
    assert body["mode"] == "generation"
    assert body["endpoint"] == ":8188"


def test_status_endpoint_exposed_in_inference_mode_when_reachable(client: TestClient) -> None:
    # Resident container: the ComfyUI web UI stays up in inference mode so
    # users can build workflows/prompts before flipping the switch — /status
    # must advertise the endpoint whenever the UI is actually reachable.
    _install_arbiter(client, mode="llm")
    c, s, f = _patch(container="running", hermes=True, fetch=_fetch_idle)
    with c, s, f:
        body = client.get("/api/comfyui/status").json()
    assert body["mode"] == "inference"
    assert body["endpoint"] == ":8188"


def test_status_mode_legacy_fallback_when_arbiter_missing(client: TestClient) -> None:
    # Arbiter unwired → fall back to the docker/systemd-derived mode.
    client.app.state.slot_manager = None
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_idle)
    with c, s, f:
        body = client.get("/api/comfyui/status").json()
    assert body["mode"] == "generation"
    assert body["endpoint"] == ":8188"
    assert body["arbiter"] is None


def test_switchover_503_when_arbiter_unwired(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Gate open + valid non-noop request but no slot manager on app.state →
    # explicit 503, never a dangling 202 that can't do anything.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    client.app.state.slot_manager = None
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "generation"})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "comfyui.arbiter_unavailable"


def test_pin_503_when_manager_has_no_arbiter(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A manager object WITHOUT an .arbiter attribute must degrade to the same
    # 503, not a 500 (getattr guard).
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    client.app.state.slot_manager = SimpleNamespace()  # no .arbiter
    r = client.post("/api/comfyui/pin", json={"pinned": True})
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "comfyui.arbiter_unavailable"


def test_status_exposes_switchover_state(client: TestClient, _reset_comfyui_module_state) -> None:
    # The pane's 4s poll drives the "switching…" UI from this block.
    comfyui_mod = _reset_comfyui_module_state
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        idle = client.get("/api/comfyui/status").json()["switchover"]
        comfyui_mod._switch.update(active=True, target="generation", error=None)
        active = client.get("/api/comfyui/status").json()["switchover"]
    assert idle == {"active": False, "target": None, "error": None}
    assert active == {"active": True, "target": "generation", "error": None}


def test_switchover_arbiter_error_recorded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A failed arbiter switch (incl. ArbiterPinned) must not strand the tracker
    # as active, and the error must be visible on the next poll — same contract
    # the script failures had. Never silently swallowed, never a raised 500.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client)
    arb.ensure_img.side_effect = RuntimeError("img slot failed to load")
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.post("/api/comfyui/switchover", json={"mode": "generation"})
        assert r.status_code == 202
        sw = client.get("/api/comfyui/status").json()["switchover"]
    assert sw["active"] is False
    assert "img slot failed to load" in sw["error"]


def test_pin_endpoint_toggles(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    # Same 501 feature gate as the switchover, then a plain set_pin passthrough
    # that reflects the new value back.
    arb = _install_arbiter(client)
    r = client.post("/api/comfyui/pin", json={"pinned": True})
    assert r.status_code == 501
    arb.set_pin.assert_not_called()

    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    r = client.post("/api/comfyui/pin", json={"pinned": True})
    assert r.status_code == 200
    assert r.json() == {"pinned": True}
    arb.set_pin.assert_called_once_with(True)

    r = client.post("/api/comfyui/pin", json={"pinned": False})
    assert r.status_code == 200
    assert r.json() == {"pinned": False}
    arb.set_pin.assert_called_with(False)


def test_pin_endpoint_rejects_non_bool_body(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    arb = _install_arbiter(client)
    r = client.post("/api/comfyui/pin", json={"pinned": "yes"})
    assert r.status_code == 422
    arb.set_pin.assert_not_called()


def test_status_carries_arbiter_block(client: TestClient) -> None:
    # /status folds arbiter.status() in under "arbiter" so the pane can render
    # mode/pin/idle-restore without a second endpoint.
    arb = _install_arbiter(client)
    arb.status.return_value = {
        "mode": "img",
        "pinned": True,
        "saved_llm_slots": ["primary", "utility"],
        "idle_restore_at": None,
    }
    c, s, f = _patch(container="running", hermes=False, fetch=_fetch_idle)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    assert r.status_code == 200
    assert r.json()["arbiter"] == {
        "mode": "img",
        "pinned": True,
        "saved_llm_slots": ["primary", "utility"],
        "idle_restore_at": None,
    }


def test_status_arbiter_fail_soft(client: TestClient) -> None:
    # The status route is fail-soft by design — an arbiter blow-up degrades to
    # "arbiter": null, the rest of the pane keeps rendering.
    arb = _install_arbiter(client)
    arb.status.side_effect = RuntimeError("state file corrupt")
    c, s, f = _patch(container="absent", hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    assert r.status_code == 200
    assert r.json()["arbiter"] is None


def test_switchover_rejects_unknown_mode(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With the gate open, the body must name a real target mode.
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    r = client.post("/api/comfyui/switchover", json={"mode": "turbo"})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "comfyui.invalid_mode"


def test_switchover_gated_off_returns_501(client: TestClient) -> None:
    # Default: the privileged root path is NOT wired. Must refuse, not pretend.
    r = client.post("/api/comfyui/switchover", json={"mode": "generation"})
    assert r.status_code == 501
    assert "switchover" in r.json()["error"]["code"]


def test_switchover_enabled_flag_is_read_per_request(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # When the flag is on, the gate opens (even though the stub then reports the
    # root path is still unimplemented — proves the flag is consulted live).
    monkeypatch.setenv("HAL0_COMFYUI_SWITCHOVER_ENABLED", "1")
    r = client.post("/api/comfyui/switchover", json={"mode": "generation"})
    assert r.status_code != 501
