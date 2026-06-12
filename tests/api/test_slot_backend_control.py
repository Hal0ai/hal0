"""Tests for the runtime-backend control endpoint (ADR-0022 B3, Phase E).

POST /api/slots/{name}/backend switches a slot's runtime backend by
writing the slot's ``device`` field to TOML (rocm→gpu-rocm,
vulkan→gpu-vulkan, cpu→cpu, auto→clear) and restarting the slot when it
is currently loaded so the container re-renders under the new backend.

Phase E (#687) semantics under test:
  - "loaded" is the SlotManager's own truth (``is_ready_for_dispatch``) —
    no external daemon is consulted.
  - ``actual_backend`` is always ``None``: per-process backend
    introspection retired with the legacy runtime (#663 — the running
    image is the backend-of-record, surfaced as ``actual_image``).
  - Backend builds ship inside the container images, so
    ``_BACKEND_BUILD_BIN`` is empty and ``_backend_build_present`` always
    returns True; it is kept as a monkeypatchable seam, and the 409
    ``backend.build_missing`` path is exercised through that seam.

Validation:
  - cpu / auto → always valid.
  - flm/npu → 400 ``backend.not_selectable``.

Idempotent: same backend already declared → no-op, ``reloaded: false``.

Response 200 carries the standard ``_slot_to_dict`` payload PLUS
``requested_backend`` / ``declared_backend`` / ``actual_backend`` /
``reloaded``.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import hal0.api.routes.slots as slots_mod


@pytest.fixture
def slot_toml(tmp_hal0_home: str) -> Path:
    """Write a chat.toml declaring device=gpu-vulkan (container runtime)."""
    root = Path(tmp_hal0_home) / "etc" / "hal0" / "slots"
    root.mkdir(parents=True, exist_ok=True)
    path = root / "chat.toml"
    path.write_text(
        "\n".join(
            [
                'name = "chat"',
                'type = "llm"',
                'device = "gpu-vulkan"',
                'runtime = "container"',
                "enabled = true",
                "port = 8081",
                "[model]",
                'default = "qwen3-4b"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _read_device(slot_toml: Path) -> str | None:
    with slot_toml.open("rb") as fh:
        return tomllib.load(fh).get("device")


def _patch_loaded(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, *, loaded: bool
) -> list[str]:
    """Force is_ready_for_dispatch + capture restart calls on the live SM."""
    sm = client.app.state.slot_manager
    restarts: list[str] = []
    monkeypatch.setattr(sm, "is_ready_for_dispatch", lambda _name: loaded)

    real_status = sm.status

    async def _restart(name: str) -> object:
        restarts.append(name)
        return await real_status(name)

    monkeypatch.setattr(sm, "restart", _restart)
    return restarts


# ── happy path: switch vulkan → rocm (loaded → restart) ─────────────────────


def test_switch_to_rocm_writes_device_and_restarts_when_loaded(
    slot_toml: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    restarts = _patch_loaded(client, monkeypatch, loaded=True)
    r = client.post("/api/slots/chat/backend", json={"backend": "rocm"})
    assert r.status_code == 200, r.text
    body = r.json()
    # device persisted to TOML.
    assert _read_device(slot_toml) == "gpu-rocm"
    # response contract.
    assert body["requested_backend"] == "rocm"
    assert body["declared_backend"] == "rocm"
    # Phase E: per-process backend introspection retired — always None.
    assert body["actual_backend"] is None
    assert body["reloaded"] is True
    # standard slot payload still present.
    assert body["name"] == "chat"
    # a loaded slot is restarted so the container re-renders from TOML.
    assert restarts == ["chat"]


def test_switch_backend_while_unloaded_skips_restart(
    slot_toml: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Offline slot → TOML write only, no restart, reloaded=false."""
    restarts = _patch_loaded(client, monkeypatch, loaded=False)
    r = client.post("/api/slots/chat/backend", json={"backend": "rocm"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert _read_device(slot_toml) == "gpu-rocm"
    assert body["reloaded"] is False
    assert not restarts, "unloaded slot must not be restarted"


# ── device alias + gpu- normalization ───────────────────────────────────────


def test_accepts_device_alias_and_normalizes_gpu_form(
    slot_toml: Path,
    client: TestClient,
) -> None:
    r = client.post("/api/slots/chat/backend", json={"device": "gpu-rocm"})
    assert r.status_code == 200, r.text
    assert r.json()["requested_backend"] == "rocm"
    assert _read_device(slot_toml) == "gpu-rocm"


# ── build presence: container images always carry the build ─────────────────


def test_build_presence_defaults_to_true_no_409(
    slot_toml: Path,
    client: TestClient,
) -> None:
    """Backend builds ship inside container images — no host-side check.

    ``_BACKEND_BUILD_BIN`` is empty and ``_backend_build_present`` always
    returns True, so an unpatched rocm switch succeeds.
    """
    assert slots_mod._BACKEND_BUILD_BIN == {}
    assert slots_mod._backend_build_present("rocm") is True
    assert slots_mod._backend_build_present("vulkan") is True
    r = client.post("/api/slots/chat/backend", json={"backend": "rocm"})
    assert r.status_code == 200, r.text
    assert _read_device(slot_toml) == "gpu-rocm"


def test_rocm_build_missing_returns_409_via_seam(
    slot_toml: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The monkeypatchable seam still gates the switch (future host builds)."""
    monkeypatch.setattr(slots_mod, "_backend_build_present", lambda b: b != "rocm")
    r = client.post("/api/slots/chat/backend", json={"backend": "rocm"})
    assert r.status_code == 409, r.text
    assert r.json()["error"]["code"] == "backend.build_missing"
    # device must NOT have changed.
    assert _read_device(slot_toml) == "gpu-vulkan"


# ── 400 not_selectable for flm/npu ───────────────────────────────────────────


@pytest.mark.parametrize("bad", ["flm", "npu"])
def test_flm_npu_rejected_400(
    bad: str,
    slot_toml: Path,
    client: TestClient,
) -> None:
    r = client.post("/api/slots/chat/backend", json={"backend": bad})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "backend.not_selectable"


def test_unknown_backend_rejected_400(
    slot_toml: Path,
    client: TestClient,
) -> None:
    r = client.post("/api/slots/chat/backend", json={"backend": "cuda"})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "backend.not_selectable"


# ── auto clears device ───────────────────────────────────────────────────────


def test_auto_clears_device(
    slot_toml: Path,
    client: TestClient,
) -> None:
    r = client.post("/api/slots/chat/backend", json={"backend": "auto"})
    assert r.status_code == 200, r.text
    # device cleared (empty string written).
    dev = _read_device(slot_toml)
    assert not dev, f"expected device cleared, got {dev!r}"
    assert r.json()["requested_backend"] == "auto"


# ── idempotent no-op ─────────────────────────────────────────────────────────


def test_idempotent_same_backend_no_reload(
    slot_toml: Path,
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Requesting the already-declared backend is a no-op, even when loaded
    (actual_backend is always None post-#663, so the declared device is
    the only idempotency input)."""
    restarts = _patch_loaded(client, monkeypatch, loaded=True)
    r = client.post("/api/slots/chat/backend", json={"backend": "vulkan"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reloaded"] is False
    assert body["declared_backend"] == "vulkan"
    assert body["actual_backend"] is None
    # No restart cycle.
    assert not restarts, "idempotent no-op must not restart"
    # device unchanged.
    assert _read_device(slot_toml) == "gpu-vulkan"


# ── missing body ─────────────────────────────────────────────────────────────


def test_missing_backend_field_400(
    slot_toml: Path,
    client: TestClient,
) -> None:
    r = client.post("/api/slots/chat/backend", json={})
    assert r.status_code == 400, r.text
    assert r.json()["error"]["code"] == "backend.missing"


# ── back-compat: old slot name "primary" resolves to chat (#654) ─────────────


def test_legacy_primary_name_resolves_to_chat_slot(
    slot_toml: Path,
    client: TestClient,
) -> None:
    """POST /api/slots/primary/backend resolves via the hidden alias to the
    chat slot's chat.toml — proves old names still work post-rename."""
    r = client.post("/api/slots/primary/backend", json={"backend": "rocm"})
    assert r.status_code == 200, r.text
    # The alias wrote to the canonical chat.toml fixture.
    assert _read_device(slot_toml) == "gpu-rocm"
