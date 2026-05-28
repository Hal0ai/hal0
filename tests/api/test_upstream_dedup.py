"""R4 H2 regression — single composite ``hal0`` upstream + TTL cache.

The bug: ``_autoregister_slot_upstreams`` previously registered one
Upstream per slot. Lemonade serialises chat loading on a single port
(typically 8001), so ``primary`` and ``agent-hermes`` both produced
``Upstream(url="http://127.0.0.1:8001/v1")``. ``/v1/models`` deduped on
id and credited whichever entry iterated first, leaving the dashboard
showing a duplicate provider that looked empty.

PR-1-bundle fix: replace per-slot registration with one composite
``hal0`` upstream pointed at hal0-api's own /v1, and aggregate the
chat-capable slot models behind a 5s TTL cache.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hal0.api import (
    _autoregister_slot_upstreams,
    _fetch_hal0_composite_models,
    _hal0_model_cache_clear,
)
from hal0.upstreams.registry import Upstream, UpstreamRegistry


class _FakeSlotManager:
    """Minimal stub returning a hand-rolled slot catalogue.

    Mirrors the parts of :class:`SlotManager` that
    ``_autoregister_slot_upstreams`` and ``_fetch_hal0_composite_models``
    actually touch — :meth:`iter_configs`.
    """

    def __init__(self, configs: list[dict[str, Any]]):
        self._configs = configs

    async def iter_configs(self) -> list[dict[str, Any]]:
        return list(self._configs)


def _two_chat_slots() -> list[dict[str, Any]]:
    """Two chat-capable slots on the same Lemonade port (mirrors the live
    bug at ``port=8001`` for both ``primary`` and ``agent-hermes``)."""
    return [
        {
            "name": "primary",
            "type": "llm",
            "port": 8001,
            "provider": "lemonade",
            "model_default": "qwen3-coder-next-reap-40b-a3b-q4kxl",
        },
        {
            "name": "agent-hermes",
            "type": "llm",
            "port": 8001,
            "provider": "lemonade",
            "model_default": "qwen3-coder-reap-25b-a3b-q5km",
        },
        {
            "name": "embed",
            "type": "embedding",
            "port": 0,
            "provider": "lemonade",
            "model_default": "Qwen3-Embedding-0.6B-GGUF",
        },
    ]


@pytest.fixture(autouse=True)
def _reset_module_cache() -> None:
    """Punch the module-level TTL cache between tests."""
    _hal0_model_cache_clear()


@pytest.mark.asyncio
async def test_autoregister_creates_single_hal0_upstream() -> None:
    """Exactly one upstream named ``hal0`` lands in the registry — no
    duplicate ``primary`` / ``agent-hermes`` entries pointing at the
    same Lemonade port."""
    registry = UpstreamRegistry()
    slot_mgr = _FakeSlotManager(_two_chat_slots())

    await _autoregister_slot_upstreams(registry, slot_mgr)

    names = sorted(u.name for u in registry.list())
    assert names == ["hal0"]
    assert "primary" not in names
    assert "agent-hermes" not in names

    hal0 = registry.get("hal0")
    assert hal0 is not None
    # Points at hal0-api's own /v1, not directly at the slot-local
    # llama-server — keeps the dispatcher's prompt-cache + dispatch
    # path in the loop.
    assert hal0.url == "http://127.0.0.1:8080/v1"
    assert hal0.kind == "slot"
    assert hal0.slot_name is None  # composite — not a single slot


@pytest.mark.asyncio
async def test_autoregister_is_idempotent_and_respects_overrides() -> None:
    """If ``hal0`` is already registered (operator override via
    upstreams.toml) the autoregister is a no-op so the override wins."""
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

    await _autoregister_slot_upstreams(registry, slot_mgr)

    hal0 = registry.get("hal0")
    assert hal0 is not None
    assert hal0.kind == "remote"
    assert hal0.url == "https://hal0.thinmint.dev/v1"


@pytest.mark.asyncio
async def test_composite_fetch_aggregates_chat_slot_models() -> None:
    """``_fetch_hal0_composite_models`` returns the deduped union of
    every chat-capable slot's model id — and excludes non-chat
    capabilities."""
    registry = UpstreamRegistry()
    slot_mgr = _FakeSlotManager(_two_chat_slots())
    await _autoregister_slot_upstreams(registry, slot_mgr)

    hal0 = registry.get("hal0")
    assert hal0 is not None
    models = await _fetch_hal0_composite_models(hal0, slot_mgr)

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
    registry = UpstreamRegistry()
    catalog: list[dict[str, Any]] = [
        {
            "name": "primary",
            "type": "llm",
            "port": 8001,
            "model_default": "model-a",
        }
    ]
    slot_mgr = _FakeSlotManager(catalog)
    await _autoregister_slot_upstreams(registry, slot_mgr)
    hal0 = registry.get("hal0")
    assert hal0 is not None

    # Synthetic monotonic clock — caller-injected so we don't sleep.
    clock = {"t": 1000.0}

    def fake_now() -> float:
        return clock["t"]

    _hal0_model_cache_clear()

    first = await _fetch_hal0_composite_models(hal0, slot_mgr, now=fake_now, ttl_seconds=5.0)
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
    cached = await _fetch_hal0_composite_models(hal0, slot_mgr, now=fake_now, ttl_seconds=5.0)
    assert cached == ["model-a"], "Cache should still hide the new slot inside the TTL window"

    # Past the TTL, the new model surfaces.
    clock["t"] += 10.0
    refreshed = await _fetch_hal0_composite_models(hal0, slot_mgr, now=fake_now, ttl_seconds=5.0)
    assert sorted(refreshed) == ["model-a", "model-b"]


@pytest.mark.asyncio
async def test_composite_fetch_handles_empty_catalog() -> None:
    """No catastrophic failure when ``iter_configs`` returns nothing
    (cold start before any slot TOML has been written)."""
    registry = UpstreamRegistry()
    slot_mgr = _FakeSlotManager([])
    await _autoregister_slot_upstreams(registry, slot_mgr)
    hal0 = registry.get("hal0")
    assert hal0 is not None

    models = await _fetch_hal0_composite_models(hal0, slot_mgr)
    assert models == []


def test_module_cache_clear_is_callable() -> None:
    """The cache-punch helper is exposed so slot swap/restart paths can
    invalidate eagerly when they know the catalogue is changing."""
    _hal0_model_cache_clear()  # must not raise


# ── Smoke: composite upstream behind /v1/models handler ────────────────────
# The v1.py list_models handler short-circuits the composite case so it
# doesn't recurse into itself over HTTP. Verified end-to-end via the
# live LXC smoke; here we lock the contract at the helper level.


@pytest.mark.asyncio
async def test_composite_fetch_excludes_slots_without_model_id() -> None:
    """Slots that haven't picked a model yet (empty ``model_default``)
    are silently skipped instead of advertising an empty id."""
    registry = UpstreamRegistry()
    slot_mgr = _FakeSlotManager(
        [
            {"name": "primary", "type": "llm", "model_default": "qwen3"},
            {"name": "agent-hermes", "type": "llm", "model_default": ""},
            {"name": "stt", "type": "transcription", "model_default": "whisper-tiny"},
        ]
    )
    await _autoregister_slot_upstreams(registry, slot_mgr)
    hal0 = registry.get("hal0")
    assert hal0 is not None
    models = await _fetch_hal0_composite_models(hal0, slot_mgr)
    assert models == ["qwen3"]


@pytest.mark.asyncio
async def test_composite_fetch_reads_nested_model_default_from_toml() -> None:
    """Real on-disk slot TOMLs put the model id under ``[model] default``
    (not the flat ``model_default``). SlotManager.iter_configs surfaces
    that nested shape verbatim; the composite fetcher must read it."""
    registry = UpstreamRegistry()
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
    _hal0_model_cache_clear()
    await _autoregister_slot_upstreams(registry, slot_mgr)
    hal0 = registry.get("hal0")
    assert hal0 is not None
    models = await _fetch_hal0_composite_models(hal0, slot_mgr)
    assert sorted(models) == sorted(
        [
            "qwen3-coder-next-reap-40b-a3b-q4kxl",
            "qwen3-coder-reap-25b-a3b-q5km",
        ]
    )


# Ensure both async helpers are importable from the public module surface
# so downstream tooling (PR-3 ``hermes_provision`` rework) can reach them.
def test_public_symbol_exports() -> None:
    assert callable(_autoregister_slot_upstreams)
    assert callable(_fetch_hal0_composite_models)
    assert callable(_hal0_model_cache_clear)
    assert asyncio.iscoroutinefunction(_autoregister_slot_upstreams)
    assert asyncio.iscoroutinefunction(_fetch_hal0_composite_models)
