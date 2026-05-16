"""Tests for slot adoption (issue #30) and warming→idle (issue #31).

Both issues touch the same code path — the state machine in
``hal0.slots.manager`` — so they share fixtures and tests live in one
file.

Issue #30: a slot whose systemd unit is up but whose ``state.json``
reports OFFLINE used to stay OFFLINE in ``/api/slots`` indefinitely.
``SlotManager.status()`` now runs a bidirectional reconciler: when
state.json says OFFLINE/ERROR but ``systemctl is-active`` says active,
it runs ``_probe_once`` against the provider and (on success) adopts
the running unit into READY or IDLE.  This was reproducible on
hal0-test where kokoro and moonshine slots were getting started
out-of-band but never moved past OFFLINE in the dashboard.

Issue #31: a slot started without a model (``llama-server --model ""``,
FLM with no advertised tags, moonshine with ``model_loaded=false``)
used to either (a) time out in WARMING and land in ERROR or (b) for
``/health``-only providers, get marked READY despite serving zero
models.  Both are UX traps — the dashboard now distinguishes a
``ready`` slot (model loaded) from an ``idle`` slot (process up, no
model) and the router skips IDLE slots.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from hal0.slots import manager as manager_mod
from hal0.slots.manager import SlotManager
from hal0.slots.state import SlotState


def _patch_httpx(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Replace ``hal0.slots.manager.httpx.AsyncClient`` with a MockTransport-backed one.

    The probe code constructs a fresh ``httpx.AsyncClient`` per attempt,
    so we patch the class binding inside the manager module to return a
    client wired to ``handler``.  This keeps the test surface narrow —
    we don't need to stub out the entire probe.
    """
    transport = httpx.MockTransport(handler)
    original = manager_mod.httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs.pop("transport", None)
        return original(*args, transport=transport, **kwargs)

    monkeypatch.setattr(manager_mod.httpx, "AsyncClient", _factory)


# ── #31 — _await_ready resolves to IDLE when there's no model ─────────────────


async def test_await_ready_returns_idle_when_v1_models_empty_chat_sentinel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FLM/vLLM started with no model resolves to IDLE, not ERROR."""
    monkeypatch.setattr(manager_mod, "_IDLE_STABILISE_S", 0.0)
    monkeypatch.setattr(manager_mod, "_HEALTH_BACKOFF_S", (0.001,))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    state = await sm._await_ready("test", 9999, "flm")
    assert state == SlotState.IDLE


async def test_await_ready_returns_idle_when_model_not_loaded_moonshine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moonshine with ``model_loaded=false`` body resolves to IDLE."""
    monkeypatch.setattr(manager_mod, "_IDLE_STABILISE_S", 0.0)
    monkeypatch.setattr(manager_mod, "_HEALTH_BACKOFF_S", (0.001,))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"model_loaded": False})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    state = await sm._await_ready("test", 9999, "moonshine")
    assert state == SlotState.IDLE


async def test_await_ready_returns_idle_when_llama_server_has_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """llama-server / kokoro with empty /v1/models resolves to IDLE."""
    monkeypatch.setattr(manager_mod, "_IDLE_STABILISE_S", 0.0)
    monkeypatch.setattr(manager_mod, "_HEALTH_BACKOFF_S", (0.001,))

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    state = await sm._await_ready("test", 9999, "llama-server")
    assert state == SlotState.IDLE


async def test_await_ready_returns_ready_for_kokoro_with_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """kokoro reports READY when /health=200 and /v1/models is non-empty."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "kokoro"}]})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    state = await sm._await_ready("test", 9999, "kokoro")
    assert state == SlotState.READY


async def test_await_ready_returns_ready_for_moonshine_when_model_loaded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """moonshine reports READY when /health body has ``model_loaded=true``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"model_loaded": True, "model_id": "moonshine-base-en"})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    state = await sm._await_ready("test", 9999, "moonshine")
    assert state == SlotState.READY


# ── #31 — load() lands in IDLE when the probe stabilises empty ────────────────


async def test_load_lands_in_idle_when_no_model(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When _await_ready resolves to IDLE, load() persists IDLE not READY."""

    async def _idle(self: SlotManager, slot_name: str, port: int, provider: str) -> SlotState:
        return SlotState.IDLE

    monkeypatch.setattr(SlotManager, "_await_ready", _idle)

    sm = SlotManager()
    snap = await sm.load("primary")
    assert snap.state == SlotState.IDLE, (
        "load() must surface the WARMING→IDLE edge when _await_ready returns IDLE "
        "(issue #31: --model='' must land in idle, not ready)"
    )


# ── #30 — status() adopts running-but-OFFLINE slots ──────────────────────────


async def test_status_adopts_running_slot_with_offline_state(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running unit + OFFLINE state.json + healthy probe → READY.

    Reproduces the issue #30 scenario: kokoro/moonshine started outside
    the hal0 load() flow, state.json never written.  status() must run
    an adoption probe and transition to READY.
    """
    systemctl_stub["is_active_state"] = "active"

    async def _probe(
        self: SlotManager, port: int, provider: str
    ) -> tuple[bool, SlotState | None, str]:
        return True, SlotState.READY, "test-stub /v1/models non-empty"

    monkeypatch.setattr(SlotManager, "_probe_once", _probe)

    sm = SlotManager()
    snap = await sm.status("primary")
    assert snap.state == SlotState.READY, (
        f"status() must adopt a running slot whose state.json was OFFLINE (got {snap.state})"
    )
    assert snap.metadata.get("adopted") is True


async def test_status_adopts_running_slot_into_idle_when_no_model(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Running unit + probe returns IDLE → adopted into IDLE, not READY."""
    systemctl_stub["is_active_state"] = "active"

    async def _probe(
        self: SlotManager, port: int, provider: str
    ) -> tuple[bool, SlotState | None, str]:
        return True, SlotState.IDLE, "test-stub /v1/models empty"

    monkeypatch.setattr(SlotManager, "_probe_once", _probe)

    sm = SlotManager()
    snap = await sm.status("primary")
    assert snap.state == SlotState.IDLE


async def test_status_does_not_adopt_when_probe_fails(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running unit with a failing probe stays OFFLINE; next call retries."""
    systemctl_stub["is_active_state"] = "active"

    probe_calls = {"n": 0}

    async def _probe(
        self: SlotManager, port: int, provider: str
    ) -> tuple[bool, SlotState | None, str]:
        probe_calls["n"] += 1
        return False, None, "test-stub upstream timeout"

    monkeypatch.setattr(SlotManager, "_probe_once", _probe)

    sm = SlotManager()
    snap = await sm.status("primary")
    # No state.json yet, probe fails → return synthetic OFFLINE.
    assert snap.state == SlotState.OFFLINE
    # Next call still probes (no negative caching).
    await sm.status("primary")
    assert probe_calls["n"] == 2


async def test_status_adopts_error_state_when_unit_recovers(
    slot_root: Path,
    systemctl_stub: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A slot stuck in ERROR re-adopts to READY once the unit is healthy."""
    # Park the slot at ERROR.
    sm = SlotManager()
    await sm._transition("primary", SlotState.ERROR, force=True)
    # systemd reports the unit as active again.
    systemctl_stub["is_active_state"] = "active"

    async def _probe(
        self: SlotManager, port: int, provider: str
    ) -> tuple[bool, SlotState | None, str]:
        return True, SlotState.READY, "recovered"

    monkeypatch.setattr(SlotManager, "_probe_once", _probe)

    snap = await sm.status("primary")
    assert snap.state == SlotState.READY
    assert snap.metadata.get("adopted") is True


# ── #30 — _probe_once classifies providers correctly ─────────────────────────


async def test_probe_once_chat_sentinel_empty_models_returns_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    ok, resolved, _ = await sm._probe_once(9999, "flm")
    assert ok is True
    assert resolved == SlotState.IDLE


async def test_probe_once_health_404_returns_not_adoptable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    ok, resolved, _ = await sm._probe_once(9999, "llama-server")
    assert ok is False
    assert resolved is None


async def test_probe_once_kokoro_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "kokoro"}]})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    ok, resolved, _ = await sm._probe_once(9999, "kokoro")
    assert ok is True
    assert resolved == SlotState.READY


async def test_probe_once_moonshine_model_not_loaded_returns_idle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"model_loaded": False})
        return httpx.Response(404)

    _patch_httpx(monkeypatch, handler)
    sm = SlotManager()
    ok, resolved, _ = await sm._probe_once(9999, "moonshine")
    assert ok is True
    assert resolved == SlotState.IDLE


# ── state-machine — WARMING → IDLE is a legal edge ───────────────────────────


def test_warming_to_idle_is_a_legal_transition() -> None:
    """Issue #31 broadened WARMING's outbound set to include IDLE."""
    from hal0.slots.state import LEGAL_TRANSITIONS

    assert SlotState.IDLE in LEGAL_TRANSITIONS[SlotState.WARMING]
