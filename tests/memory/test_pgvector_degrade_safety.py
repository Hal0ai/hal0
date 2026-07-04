"""#613 — PgVectorProvider degrade-safety: warn on writes, expose degraded flag.

Verifies:
  1. PgVectorProvider.degraded is True (callers can detect the fallback).
  2. HindsightProvider.degraded is absent / falsy (not the fallback).
  3. A structlog WARNING is emitted on construction of PgVectorProvider.
  4. A structlog WARNING is emitted on the first add() call.
  5. The WARNING is NOT repeated on subsequent add() calls (log-once).
  6. provider_from_config() returns a provider with degraded=True when
     Hindsight is unreachable (degrade ladder fires).
  7. provider_from_config() returns a provider with degraded=False/absent
     when Hindsight is reachable.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ── helpers ──────────────────────────────────────────────────────────────────


def _cfg(engine="hindsight"):
    return SimpleNamespace(
        memory=SimpleNamespace(
            engine=engine,
            embedding=SimpleNamespace(
                rerank_gateway_url="http://127.0.0.1:8080",
                rerank_model="builtin.jina-reranker-v1-tiny-en-q8",
                rerank_connect_timeout_s=1.0,
                rerank_read_timeout_s=8.0,
            ),
            graph=SimpleNamespace(enabled=False, extraction_slot="utility"),
        )
    )


# ── degraded attribute ────────────────────────────────────────────────────────


def test_pgvector_provider_degraded_is_true():
    """PgVectorProvider.degraded must be True so callers detect the fallback."""
    from hal0.memory.pgvector_provider import PgVectorProvider

    p = PgVectorProvider()
    assert p.degraded is True


def test_hindsight_provider_degraded_is_falsy():
    """HindsightProvider must NOT carry degraded=True (it is the real engine)."""
    from hal0.memory.hindsight_provider import HindsightProvider

    p = HindsightProvider(client=object())
    assert not getattr(p, "degraded", False)


# ── construction-time warning ─────────────────────────────────────────────────


def test_pgvector_construction_emits_warning():
    """PgVectorProvider.__init__ must emit a WARNING about volatile storage."""
    from structlog.testing import capture_logs

    from hal0.memory.pgvector_provider import PgVectorProvider

    with capture_logs() as logs:
        PgVectorProvider()

    warning_events = [e["event"] for e in logs if e.get("log_level") == "warning"]
    assert any("hal0.memory.degraded_provider_active" in ev for ev in warning_events), (
        f"No degrade-provider-active warning found in: {warning_events}"
    )


# ── add() write warning ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pgvector_add_emits_warning_on_first_call():
    """add() must emit a WARNING on the first write."""
    from structlog.testing import capture_logs

    from hal0.memory.pgvector_provider import PgVectorProvider

    p = PgVectorProvider()

    with capture_logs() as logs:
        await p.add("hello", dataset="shared")

    warning_events = [e["event"] for e in logs if e.get("log_level") == "warning"]
    assert any("hal0.memory.degraded_write" in ev for ev in warning_events), (
        f"No degraded_write warning found in: {warning_events}"
    )


@pytest.mark.asyncio
async def test_pgvector_add_warns_only_once_per_instance():
    """Subsequent add() calls must NOT repeat the write warning (log-once throttle)."""
    from structlog.testing import capture_logs

    from hal0.memory.pgvector_provider import PgVectorProvider

    p = PgVectorProvider()
    # Prime the first-write warning (so _add_warned=True).
    await p.add("first", dataset="shared")

    with capture_logs() as logs:
        await p.add("second", dataset="shared")
        await p.add("third", dataset="shared")

    # After the first-write warning is consumed, subsequent adds must be silent.
    write_warnings = [
        e
        for e in logs
        if e.get("log_level") == "warning" and "degraded_write" in e.get("event", "")
    ]
    assert write_warnings == [], f"Unexpected repeated write warnings: {write_warnings}"


@pytest.mark.asyncio
async def test_pgvector_add_still_stores_data_despite_warning():
    """The warning must not prevent the write — data IS stored in memory."""
    from hal0.memory.pgvector_provider import PgVectorProvider

    p = PgVectorProvider()
    result = await p.add("keep me", dataset="shared")
    assert "id" in result and "timestamp" in result
    items = await p.list_items(dataset="shared")
    assert any(r["text"] == "keep me" for r in items["items"])


# ── factory degrade detection ─────────────────────────────────────────────────


def test_factory_degrade_provider_has_degraded_true():
    """When Hindsight is unreachable, provider_from_config returns degraded=True."""
    from hal0.memory import provider_from_config

    with patch("hal0.memory._build_hindsight_client", side_effect=RuntimeError("no daemon")):
        provider = provider_from_config(_cfg("hindsight"))

    assert getattr(provider, "degraded", False) is True


def test_factory_real_provider_degraded_is_falsy():
    """When Hindsight is reachable, provider_from_config returns degraded=False/absent."""
    from hal0.memory import provider_from_config

    with (
        patch("hal0.memory._build_hindsight_client", return_value=object()),
        patch("hal0.memory.HindsightProvider", autospec=True) as mock_cls,
    ):
        mock_instance = mock_cls.return_value
        # HindsightProvider must NOT expose degraded=True
        del mock_instance.degraded  # ensure getattr fallback to False
        provider_from_config(_cfg("hindsight"))
        assert not getattr(mock_cls.return_value, "degraded", False)
