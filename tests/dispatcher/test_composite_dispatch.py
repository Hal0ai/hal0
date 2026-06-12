"""Tests for the composite ``hal0`` upstream's dispatch exemptions.

The composite ``hal0`` upstream (built by ``_autoregister_slot_upstreams``)
exists ONLY so the ``/v1/models`` aggregator has a single synthetic entry
that advertises every registered model id. It has no backing server:

  1. **Never forwarded to.** Its registered ``url`` is hal0-api's own
     ``:8080/v1`` surface — forwarding a chat request there would re-enter
     ``/v1/chat/completions`` and recurse forever. Dispatch therefore SKIPS
     the composite at Steps 1/2/3; a registry id bound to the composite
     with no live serving slot falls through to the Step 4 legacy
     heuristics or surfaces a clean :class:`NoRouteFound` envelope.
  2. **Readiness-gate exemption.** ``hal0`` is a synthetic upstream with no
     backing slot. ``_slot_name_of`` used to fall back to ``upstream.name``
     and hand ``forward()`` ``slot_name="hal0"``, so the swap-window gate
     called ``_current_state("hal0")`` → OFFLINE → a spurious 503. The
     composite must yield an EMPTY slot_name (no gate, no SERVING wrap).
"""

from __future__ import annotations

import httpx
import pytest
from starlette.requests import Request

from hal0.dispatcher.router import (
    Dispatcher,
    NoRouteFound,
    _is_hal0_composite,
    _resolve_target_url,
    _slot_name_of,
)
from hal0.upstreams.registry import Upstream, UpstreamRegistry

# ── test doubles ──────────────────────────────────────────────────────────────


class _FakeUpstreamRegistry(UpstreamRegistry):
    def __init__(self, upstreams: list[Upstream]) -> None:
        super().__init__()
        self._store: dict[str, Upstream] = {u.name: u for u in upstreams}

    def list(self) -> list[Upstream]:  # type: ignore[override]
        return list(self._store.values())

    def get(self, name: str) -> Upstream | None:  # type: ignore[override]
        return self._store.get(name)


class _FakeModelRegistry:
    def __init__(self, routes: dict[str, str] | None = None) -> None:
        self._routes = routes or {}

    def route_for(self, model_id: str) -> str | None:
        return self._routes.get(model_id)


def _composite() -> Upstream:
    """The synthetic composite as ``_autoregister_slot_upstreams`` builds it."""
    return Upstream(
        name="hal0",
        kind="slot",
        url="http://127.0.0.1:8080/v1",
        slot_name=None,
        auth_style="none",
        warmup_strategy="none",
        advertise_models=True,
    )


def _make_request(path: str = "/v1/chat/completions") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 12345),
        "http_version": "1.1",
        "root_path": "",
    }
    return Request(scope)


# ── helper-level contracts ────────────────────────────────────────────────────


def test_is_hal0_composite_only_matches_the_synthetic_entry() -> None:
    assert _is_hal0_composite(_composite()) is True
    # A real per-slot upstream carries a slot_name.
    assert (
        _is_hal0_composite(
            Upstream(name="primary", kind="slot", url="http://x/v1", slot_name="primary")
        )
        is False
    )
    # A remote provider named "hal0" (operator override) is not the composite.
    assert _is_hal0_composite(Upstream(name="hal0", kind="remote", url="https://x/v1")) is False


def test_slot_name_of_exempts_composite_from_readiness_gate() -> None:
    """The composite yields an EMPTY slot_name so ``forward()`` skips the
    readiness gate + SERVING wrap (regression: it used to fall back to the
    name ``"hal0"`` and 503 against a non-existent slot)."""
    assert _slot_name_of(_composite()) == ""
    # Real slot still carries its name through.
    real = Upstream(name="primary", kind="slot", url="http://x/v1", slot_name="primary")
    assert _slot_name_of(real) == "primary"


def test_resolve_target_url_joins_upstream_url_plainly() -> None:
    """``_resolve_target_url`` is a plain join — no special-casing.

    The composite is never forwarded to (dispatch skips it at every
    resolution step), so it needs no redirect; real upstreams forward to
    their own url unchanged.
    """
    real = Upstream(
        name="primary", kind="slot", url="http://127.0.0.1:8081/v1", slot_name="primary"
    )
    assert _resolve_target_url(real, "/v1/chat/completions") == (
        "http://127.0.0.1:8081/v1/chat/completions"
    )


# ── dispatch path: composite is skipped everywhere ────────────────────────────


@pytest.mark.asyncio
async def test_composite_advertised_model_with_no_live_slot_is_no_route() -> None:
    """A model id only the composite advertises must NOT resolve to it.

    Steps 2/3 skip the composite; with no other upstream serving the id,
    dispatch falls to the Step 4 legacy heuristics and surfaces a clean
    NoRouteFound — never a forward into hal0-api's own ``:8080``.
    """
    upstreams = _FakeUpstreamRegistry([_composite()])
    models = _FakeModelRegistry(routes={})
    cache = {"hal0": ["hermes-4-14b-q5km", "qwen3-coder-next-reap-40b-a3b-q4kxl"]}

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        cached_models=lambda name: cache.get(name, []),
    )

    with pytest.raises(NoRouteFound):
        await dispatcher.dispatch(
            _make_request(),
            body={"model": "hermes-4-14b-q5km", "messages": []},
        )


@pytest.mark.asyncio
async def test_registry_binding_to_composite_falls_through_to_no_route() -> None:
    """A registry id bound to the composite is treated as 'not served'.

    Step 1 drops the composite binding (no backing server) instead of
    raising UnknownUpstream; with no live slot advertising the id, the
    request surfaces NoRouteFound.
    """
    upstreams = _FakeUpstreamRegistry([_composite()])
    models = _FakeModelRegistry(routes={"gemma3-4b-FLM": "hal0"})
    cache = {"hal0": ["gemma3-4b-FLM"]}

    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=models,
        cached_models=lambda name: cache.get(name, []),
    )

    with pytest.raises(NoRouteFound):
        await dispatcher.dispatch(
            _make_request(),
            body={"model": "gemma3-4b-FLM", "messages": []},
        )


@pytest.mark.asyncio
async def test_composite_never_receives_forwards() -> None:
    """End-to-end: even with a warm composite cache, no HTTP request is ever
    sent to the composite's url (hal0-api's own ``:8080``)."""
    upstreams = _FakeUpstreamRegistry([_composite()])
    cache = {"hal0": ["hermes-4-14b-q5km"]}

    def handler(req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"composite must never be forwarded to (url={req.url})")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    dispatcher = Dispatcher(
        upstream_registry=upstreams,
        model_registry=_FakeModelRegistry(),
        cached_models=lambda name: cache.get(name, []),
        http_client=client,
    )
    try:
        with pytest.raises(NoRouteFound):
            await dispatcher.dispatch(
                _make_request(),
                body={"model": "hermes-4-14b-q5km", "messages": []},
            )
    finally:
        await dispatcher.aclose()
