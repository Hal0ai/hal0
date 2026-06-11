"""Tests for the read-only ComfyUI status aggregator + gated switchover.

The dashboard's Image-Gen tab renders a single "generation engine" pane backed
by ``GET /api/comfyui/status``. That endpoint aggregates three sources, all of
which degrade gracefully (the pane polls it every few seconds and must never
crash on a dead container):

  - docker container state (``comfyui`` running / exited / absent)
  - systemd state of the LLM stack (lemonade + hermes) — to show which mode
    currently owns the single iGPU
  - ComfyUI's own HTTP API (``/system_stats`` + ``/queue``) for live telemetry

The switchover *write* path (POST /api/comfyui/switchover) is feature-gated
behind ``HAL0_COMFYUI_SWITCHOVER_ENABLED`` and returns 501 until a privileged
root path is wired in a separate, explicitly-confirmed step.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

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


def _patch(container: str, lemonade: bool, hermes: bool, fetch):
    """Patch the three status seams on the comfyui route module."""
    base = "hal0.api.routes.comfyui"
    return (
        patch(f"{base}._container_state", new_callable=AsyncMock, return_value=container),
        patch(
            f"{base}._systemd_active",
            new_callable=AsyncMock,
            side_effect=lambda unit: hermes if "hermes" in unit else lemonade,
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
    c, s, f = _patch(container="running", lemonade=False, hermes=False, fetch=_fetch_busy)
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
    c, s, f = _patch(container="running", lemonade=False, hermes=False, fetch=_fetch_idle)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    body = r.json()
    assert body["engine"] == "running"
    assert body["queue"] == {"running": 0, "pending": 0}


def test_status_starting_when_container_running_but_http_unreachable(client: TestClient) -> None:
    # Container is up but ComfyUI hasn't bound :8188 yet — fail soft, not a 500.
    c, s, f = _patch(container="running", lemonade=False, hermes=False, fetch=_fetch_down)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    assert r.status_code == 200
    body = r.json()
    assert body["reachable"] is False
    assert body["engine"] == "starting"
    assert body["memory"] is None


def test_status_stopped_and_inference_mode_when_container_absent(client: TestClient) -> None:
    # ComfyUI down, LLM stack up → inference owns the GPU.
    c, s, f = _patch(container="absent", lemonade=True, hermes=True, fetch=_fetch_down)
    with c, s, f:
        r = client.get("/api/comfyui/status")
    body = r.json()
    assert body["mode"] == "inference"
    assert body["engine"] == "stopped"
    assert body["container"]["state"] == "absent"
    assert body["inference"] == {"lemonade": True, "hermes": True}


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
    c, s, f = _patch(container="running", lemonade=False, hermes=False, fetch=_fetch_idle)
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
        c, s, f = _patch(container="absent", lemonade=True, hermes=True, fetch=_fetch_down)
        with c, s, f:
            r = client.get("/api/comfyui/status")
    assert r.json()["inventory"] is None


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
