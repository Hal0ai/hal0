"""The composite ``hal0`` upstream must not absorb chat passthrough.

hermes-role-slots fix: Lemonade serves chat models by name on lemond, and
the composite ``hal0`` upstream (URL = hal0-api itself) only exists to
aggregate chat model ids for ``GET /v1/models``. If the dispatcher matched
a chat model id against the composite's cache in the passthrough step, the
request would forward back into hal0-api (loop / wrong co-resident model)
instead of falling through to the lemonade proxy. These tests lock the
skip so chat model ids reach the legacy fallback (→ NoRouteFound → lemonade
proxy) rather than the composite.
"""

from __future__ import annotations

import pytest

from hal0.dispatcher.router import Dispatcher, NoRouteFound
from hal0.upstreams.registry import Upstream, UpstreamRegistry


def _make_request(path: str = "/v1/chat/completions", method: str = "POST"):
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": method,
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


def _registry_with_composite() -> UpstreamRegistry:
    reg = UpstreamRegistry()
    reg.upsert(
        Upstream(
            name="hal0",
            kind="slot",
            url="http://127.0.0.1:8080/v1",
            slot_name=None,
            auth_style="none",
            advertise_models=True,
        )
    )
    return reg


@pytest.mark.asyncio
async def test_chat_model_id_skips_composite_and_falls_through() -> None:
    """A chat model id present in the composite's cache must NOT route to
    the composite; with no other upstream it raises NoRouteFound (which the
    v1 layer turns into the lemonade fall-through)."""
    reg = _registry_with_composite()
    # The composite cache lists the chat model id (as it would after
    # _fetch_hal0_composite_models primes it).
    cache = {"hal0": ["hermes-4-14b-q5km"]}
    dispatcher = Dispatcher(
        upstream_registry=reg,
        model_registry=None,
        cached_models=lambda name: cache.get(name, []),
    )
    with pytest.raises(NoRouteFound):
        await dispatcher.dispatch(
            _make_request(),
            body={"model": "hermes-4-14b-q5km", "messages": []},
        )


@pytest.mark.asyncio
async def test_non_composite_passthrough_still_works() -> None:
    """A real (non-composite) upstream still wins passthrough — the skip is
    scoped to the self-referential composite only."""
    reg = _registry_with_composite()
    reg.upsert(
        Upstream(
            name="openrouter",
            kind="remote",
            url="https://openrouter.ai/api/v1",
            auth_style="none",
        )
    )
    cache = {
        "hal0": ["hermes-4-14b-q5km"],
        "openrouter": ["meta/llama-3.1-405b"],
    }
    dispatcher = Dispatcher(
        upstream_registry=reg,
        model_registry=None,
        cached_models=lambda name: cache.get(name, []),
    )
    call = await dispatcher.dispatch(
        _make_request(),
        body={"model": "meta/llama-3.1-405b"},
    )
    assert call.upstream_name == "openrouter"
    assert call.resolution_path == "passthrough:openrouter"
