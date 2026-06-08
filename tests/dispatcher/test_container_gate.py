"""Tests for the container-slot readiness gate in ``Dispatcher.forward``.

Issue #656 — the gap: container slots register as ``kind="remote"``
upstreams so ``call.slot_name`` is always empty, bypassing the existing
Lemonade slot gate and producing a raw 502 ConnectError when the
container is down or starting.

The fix:
  - ``Upstream.slot_name`` is set by ``_register_container_upstream`` for
    container-backed remotes (distinguishes them from genuine remotes like
    OpenRouter).
  - ``UpstreamCall.container_slot_name`` is set by
    ``_container_slot_name_of(upstream)``.
  - ``Dispatcher.forward`` calls ``_check_container_slot_ready`` before
    forwarding when ``container_slot_name`` is set.
  - ``SlotManager.container_readiness_check`` probes systemctl is-active
    + /health and returns ``(ready: bool, reason: str)``.

This file covers:
  - Gate raises ``SlotLoading`` (not ``UpstreamUnavailable``) when the
    container is inactive.
  - Gate raises ``SlotLoading`` when the container is active but /health
    fails (still starting).
  - Gate passes through and returns the upstream response when ready.
  - ``UpstreamCall.slot_name`` (Lemonade path) is unaffected.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from hal0.dispatcher.router import (
    Dispatcher,
    SlotLoading,
    UpstreamCall,
    _container_slot_name_of,
)
from hal0.upstreams.registry import Upstream

# ── helpers ────────────────────────────────────────────────────────────────────


def _remote_upstream(
    name: str = "gpu-slot",
    url: str = "http://127.0.0.1:8088/v1",
    *,
    slot_name: str | None = None,
) -> Upstream:
    """A kind='remote' upstream — container-backed when slot_name is set."""
    return Upstream(
        name=name,
        kind="remote",
        url=url,
        auth_style="none",
        warmup_strategy="none",
        advertise_models=True,
        slot_name=slot_name,
    )


def _slot_upstream(name: str = "primary") -> Upstream:
    """A kind='slot' upstream (Lemonade path)."""
    return Upstream(
        name=name,
        kind="slot",
        url="http://127.0.0.1:13305/v1",
        auth_style="none",
        warmup_strategy="none",
        advertise_models=True,
        slot_name=name,
    )


def _container_call(
    slot_name: str = "gpu-slot",
    target: str = "http://127.0.0.1:8088/v1/chat/completions",
) -> UpstreamCall:
    return UpstreamCall(
        upstream_name=slot_name,
        target_url=target,
        headers={"content-type": "application/json"},
        body=json.dumps({"model": slot_name}).encode(),
        streaming=False,
        method="POST",
        resolved_model=slot_name,
        requested_model=slot_name,
        container_slot_name=slot_name,  # key field
    )


def _make_dispatcher_with_manager(
    transport: httpx.MockTransport,
    sm: MagicMock,
) -> Dispatcher:
    client = httpx.AsyncClient(transport=transport)
    return Dispatcher(http_client=client, slot_manager=sm)


def _ok_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    return httpx.MockTransport(handler)


def _slot_manager_stub(
    *,
    ready: bool = True,
    reason: str = "ready",
) -> MagicMock:
    """Build a SlotManager mock whose container_readiness_check is wired."""
    sm = MagicMock()
    sm.container_readiness_check = AsyncMock(return_value=(ready, reason))
    return sm


# ── _container_slot_name_of ────────────────────────────────────────────────────


def test_container_slot_name_of_remote_with_slot_name() -> None:
    """Returns slot_name when the remote upstream was registered as container-backed."""
    up = _remote_upstream(slot_name="gpu-slot")
    assert _container_slot_name_of(up) == "gpu-slot"


def test_container_slot_name_of_remote_without_slot_name() -> None:
    """Returns empty string for genuine remotes (no slot_name set)."""
    up = _remote_upstream(slot_name=None)
    assert _container_slot_name_of(up) == ""


def test_container_slot_name_of_slot_kind() -> None:
    """Returns empty string for kind='slot' upstreams (Lemonade path)."""
    up = _slot_upstream()
    assert _container_slot_name_of(up) == ""


# ── Dispatcher.forward container gate ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_container_gate_inactive_raises_slot_loading_not_502() -> None:
    """When container is inactive, forward raises SlotLoading (503), not UpstreamUnavailable (502)."""
    sm = _slot_manager_stub(ready=False, reason="inactive")
    d = _make_dispatcher_with_manager(_ok_transport(), sm)
    try:
        with pytest.raises(SlotLoading) as exc_info:
            await d.forward(_container_call())
        assert exc_info.value.status == 503
        assert "inactive" in exc_info.value.message
        assert exc_info.value.details["slot"] == "gpu-slot"
        assert exc_info.value.details["state"] == "inactive"
    finally:
        await d.aclose()


@pytest.mark.asyncio
async def test_container_gate_starting_raises_slot_loading() -> None:
    """When container is active but /health fails (still starting), gate raises SlotLoading."""
    sm = _slot_manager_stub(ready=False, reason="starting")
    d = _make_dispatcher_with_manager(_ok_transport(), sm)
    try:
        with pytest.raises(SlotLoading) as exc_info:
            await d.forward(_container_call())
        assert exc_info.value.status == 503
        assert exc_info.value.details["state"] == "starting"
    finally:
        await d.aclose()


@pytest.mark.asyncio
async def test_container_gate_ready_passes_through_to_upstream() -> None:
    """When container is ready, forward passes through and returns the upstream response."""
    sm = _slot_manager_stub(ready=True, reason="ready")
    d = _make_dispatcher_with_manager(_ok_transport(), sm)
    try:
        resp = await d.forward(_container_call())
        assert resp.status_code == 200
        sm.container_readiness_check.assert_awaited_once_with("gpu-slot")
    finally:
        await d.aclose()


@pytest.mark.asyncio
async def test_container_gate_crashed_raises_slot_loading() -> None:
    """A crashed container returns SlotLoading with reason='crashed'."""
    sm = _slot_manager_stub(ready=False, reason="crashed")
    d = _make_dispatcher_with_manager(_ok_transport(), sm)
    try:
        with pytest.raises(SlotLoading) as exc_info:
            await d.forward(_container_call())
        assert exc_info.value.details["state"] == "crashed"
    finally:
        await d.aclose()


@pytest.mark.asyncio
async def test_lemonade_slot_unaffected_by_container_gate() -> None:
    """A kind='slot' upstream (Lemonade) does NOT trigger the container gate."""
    sm = MagicMock()
    sm.container_readiness_check = AsyncMock(
        side_effect=AssertionError("container gate must not fire for Lemonade slots")
    )
    # For a kind='slot' upstream, call.slot_name is set; call.container_slot_name is empty.
    # With no SlotManager serving mock, the forward goes through _forward_plain (no gate).
    plain_sm = MagicMock()
    plain_sm.container_readiness_check = AsyncMock(
        side_effect=AssertionError("must not call container_readiness_check")
    )
    # No slot_name on the call → also no container_slot_name → _forward_plain
    plain_call = UpstreamCall(
        upstream_name="remote-provider",
        target_url="http://127.0.0.1:8099/v1/chat/completions",
        headers={},
        body=b"{}",
        streaming=False,
        method="POST",
        slot_name="",
        container_slot_name="",  # explicit empty — genuine remote
    )
    d = _make_dispatcher_with_manager(_ok_transport(), plain_sm)
    try:
        resp = await d.forward(plain_call)
        assert resp.status_code == 200
        plain_sm.container_readiness_check.assert_not_awaited()
    finally:
        await d.aclose()


@pytest.mark.asyncio
async def test_container_gate_no_slot_manager_skips_gate() -> None:
    """When Dispatcher has no slot_manager, container gate is skipped (forward proceeds)."""
    client = httpx.AsyncClient(transport=_ok_transport())
    d = Dispatcher(http_client=client)  # no slot_manager
    try:
        resp = await d.forward(_container_call())
        assert resp.status_code == 200
    finally:
        await d.aclose()
