"""R4 H2 regression + P2-composite rebuild — direct-read model catalogue.

The bug: ``_autoregister_slot_upstreams`` (haloai heritage) used to
register one Upstream per slot. The pre-container gateway serialised chat
loading on a single shared port (typically 8001), so ``primary`` and
``agent-hermes`` both produced ``Upstream(url="http://127.0.0.1:8001/v1")``.
``/v1/models`` deduped on id and credited whichever entry iterated first,
leaving the dashboard showing a duplicate provider that looked empty.

PR-1-bundle fix: replace per-slot registration with one composite ``hal0``
upstream pointed at hal0-api's own /v1, aggregating the chat-capable slot
models behind a 5s TTL cache.

P2-composite rebuild: that composite was itself a fake registry entry
(url pointing back at hal0-api's own :8080) that forced several
detect-and-skip guards through the dispatcher. It has been deleted —
``/v1/models`` (and the dashboard's synthetic ``hal0`` tile) now read the
aggregated catalogue *directly* via :func:`hal0.api._fetch_hal0_composite_models`
and :func:`hal0.api._prime_hal0_composite_cache`, with no pseudo-upstream
ever registered in the routing table.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.api import (
    _fetch_hal0_composite_models,
    _hal0_model_cache_clear,
    _prime_hal0_composite_cache,
)
from hal0.upstreams.registry import Upstream, UpstreamRegistry


class _FakeSlotManager:
    """Minimal stub returning a hand-rolled slot catalogue.

    Mirrors the parts of :class:`SlotManager` that
    :func:`_fetch_hal0_composite_models` actually touches —
    :meth:`iter_configs`.
    """

    def __init__(self, configs: list[dict[str, Any]]):
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


def _two_chat_slots() -> list[dict[str, Any]]:
    """Two chat-capable slots sharing one port (mirrors the historical
    bug at ``port=8001`` for both ``primary`` and ``agent-hermes``)."""
    return [
        {
            "name": "primary",
            "type": "llm",
            "port": 8001,
            "provider": "llama-server",
            "model_default": "qwen3-coder-next-reap-40b-a3b-q4kxl",
        },
        {
            "name": "agent-hermes",
            "type": "llm",
            "port": 8001,
            "provider": "llama-server",
            "model_default": "qwen3-coder-reap-25b-a3b-q5km",
        },
        {
            "name": "embed",
            "type": "embedding",
            "port": 0,
            "provider": "llama-server",
            "model_default": "Qwen3-Embedding-0.6B-GGUF",
        },
    ]


@pytest.fixture(autouse=True)
def _reset_module_cache() -> None:
    """Punch the module-level TTL cache between tests."""
    _hal0_model_cache_clear()


@pytest.mark.asyncio
async def test_no_pseudo_upstream_is_registered_in_the_routing_table() -> None:
    """Priming the composite catalogue never adds an ``Upstream`` to the
    registry — no per-chat-slot upstreams pointed at the (shared / dead)
    TOML ports, and no synthetic ``hal0`` entry either.

    hermes-role-slots: chat slots are NOT independently addressable on
    their TOML ports (``primary`` + ``agent-hermes`` both pin
    ``port=8001``; ``utility`` pins a dead ``:8081``), so per-slot routing
    upstreams were never auto-registered here (container slots register
    their own ``kind="remote"`` upstreams at load time) — and now neither
    is the composite: the catalogue is read directly from slot config.
    """
    registry = UpstreamRegistry()
    slot_mgr = _FakeSlotManager(_two_chat_slots())
    model_cache: dict[str, list[str]] = {}

    await _prime_hal0_composite_cache(registry, slot_mgr, model_cache)

    assert registry.list() == []
    assert sorted(model_cache["hal0"]) == sorted(
        [
            "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "qwen3-coder-reap-25b-a3b-q5km",
        ]
    )


@pytest.mark.asyncio
async def test_priming_is_a_noop_when_operator_defines_a_real_hal0_upstream() -> None:
    """If ``hal0`` is already registered (operator override via
    upstreams.toml) priming the direct-read cache is skipped so the
    override's own model cache (populated like any other remote) isn't
    clobbered by the composite catalogue."""
    registry = UpstreamRegistry()
    # Pretend the operator pre-registered a custom hal0 endpoint.
    registry.upsert(
        Upstream(
            name="hal0",
            kind="remote",
            url="https://hal0.thinmint.dev/v1",
            auth_style="none",
        )
    )
    slot_mgr = _FakeSlotManager(_two_chat_slots())
    model_cache: dict[str, list[str]] = {"hal0": ["already-fetched-remote-model"]}

    await _prime_hal0_composite_cache(registry, slot_mgr, model_cache)

    hal0 = registry.get("hal0")
    assert hal0 is not None
    assert hal0.kind == "remote"
    assert hal0.url == "https://hal0.thinmint.dev/v1"
    # Untouched — priming didn't overwrite the operator upstream's own cache.
    assert model_cache["hal0"] == ["already-fetched-remote-model"]


@pytest.mark.asyncio
async def test_composite_fetch_aggregates_chat_slot_models() -> None:
    """``_fetch_hal0_composite_models`` returns the deduped union of
    every chat-capable slot's model id — and excludes non-chat
    capabilities."""
    slot_mgr = _FakeSlotManager(_two_chat_slots())

    models = await _fetch_hal0_composite_models(slot_mgr)

    assert sorted(models) == sorted(
        [
            "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "qwen3-coder-reap-25b-a3b-q5km",
        ]
    )
    # No embed model bleed-through.
    assert "Qwen3-Embedding-0.6B-GGUF" not in models


@pytest.mark.asyncio
async def test_composite_fetch_caches_for_ttl() -> None:
    """Within the TTL window, ``_fetch_hal0_composite_models`` returns
    the cached list without re-querying the slot catalogue. Beyond it,
    the catalogue is re-evaluated."""
    catalog: list[dict[str, Any]] = [
        {
            "name": "primary",
            "type": "llm",
            "port": 8001,
            "model_default": "model-a",
        }
    ]
    slot_mgr = _FakeSlotManager(catalog)

    # Synthetic monotonic clock — caller-injected so we don't sleep.
    clock = {"t": 1000.0}

    def fake_now() -> float:
        return clock["t"]

    first = await _fetch_hal0_composite_models(slot_mgr, now=fake_now, ttl_seconds=5.0)
    assert first == ["model-a"]

    # Mutate the catalogue but advance the clock by less than the TTL —
    # the cached entry should still be returned.
    catalog.append(
        {
            "name": "agent-hermes",
            "type": "llm",
            "port": 8001,
            "model_default": "model-b",
        }
    )
    clock["t"] += 1.0
    cached = await _fetch_hal0_composite_models(slot_mgr, now=fake_now, ttl_seconds=5.0)
    assert cached == ["model-a"], "Cache should still hide the new slot inside the TTL window"

    # Past the TTL, the new model surfaces.
    clock["t"] += 10.0
    refreshed = await _fetch_hal0_composite_models(slot_mgr, now=fake_now, ttl_seconds=5.0)
    assert sorted(refreshed) == ["model-a", "model-b"]


@pytest.mark.asyncio
async def test_composite_fetch_handles_empty_catalog() -> None:
    """No catastrophic failure when ``iter_configs`` returns nothing
    (cold start before any slot TOML has been written)."""
    slot_mgr = _FakeSlotManager([])

    models = await _fetch_hal0_composite_models(slot_mgr)
    assert models == []


def test_module_cache_clear_is_callable() -> None:
    """The cache-punch helper is exposed so slot swap/restart paths can
    invalidate eagerly when they know the catalogue is changing."""
    _hal0_model_cache_clear()  # must not raise


@pytest.mark.asyncio
async def test_composite_fetch_excludes_slots_without_model_id() -> None:
    """Slots that haven't picked a model yet (empty ``model_default``)
    are silently skipped instead of advertising an empty id."""
    slot_mgr = _FakeSlotManager(
        [
            {"name": "primary", "type": "llm", "model_default": "qwen3"},
            {"name": "agent-hermes", "type": "llm", "model_default": ""},
            {"name": "stt", "type": "transcription", "model_default": "whisper-tiny"},
        ]
    )
    models = await _fetch_hal0_composite_models(slot_mgr)
    assert models == ["qwen3"]


@pytest.mark.asyncio
async def test_composite_fetch_reads_nested_model_default_from_toml() -> None:
    """Real on-disk slot TOMLs put the model id under ``[model] default``
    (not the flat ``model_default``). SlotManager.iter_configs surfaces
    that nested shape verbatim; the composite fetcher must read it."""
    slot_mgr = _FakeSlotManager(
        [
            {
                "name": "primary",
                "type": "llm",
                "port": 8001,
                "model": {"default": "qwen3-coder-next-reap-40b-a3b-q4kxl"},
            },
            {
                "name": "agent-hermes",
                "type": "llm",
                "port": 8001,
                "model": {"default": "qwen3-coder-reap-25b-a3b-q5km"},
            },
        ]
    )
    models = await _fetch_hal0_composite_models(slot_mgr)
    assert sorted(models) == sorted(
        [
            "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "qwen3-coder-reap-25b-a3b-q5km",
        ]
    )


# Ensure the async helpers are importable from the public module surface so
# downstream tooling (PR-3 ``hermes_provision`` rework) can reach them.
def test_public_symbol_exports() -> None:
    assert callable(_fetch_hal0_composite_models)
    assert callable(_hal0_model_cache_clear)
    assert callable(_prime_hal0_composite_cache)
    assert asyncio.iscoroutinefunction(_fetch_hal0_composite_models)
    assert asyncio.iscoroutinefunction(_prime_hal0_composite_cache)
