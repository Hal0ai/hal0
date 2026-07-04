"""provider_from_config factory tests (ADR-0023: cognee branch removed)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from hal0.memory import provider_from_config


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


def test_factory_returns_pgvector_for_pgvector_engine():
    from hal0.memory.pgvector_provider import PgVectorProvider

    provider = provider_from_config(_cfg("pgvector"))
    assert isinstance(provider, PgVectorProvider)


def test_factory_returns_hindsight_when_engine_hindsight():
    with (
        patch("hal0.memory.HindsightProvider", autospec=True) as mock_cls,
        patch("hal0.memory._build_hindsight_client", return_value=object()),
    ):
        provider_from_config(_cfg("hindsight"))
        assert mock_cls.called


def test_factory_unknown_engine_falls_back_to_hindsight():
    # ADR-0023: the cognee branch is gone — an unknown/legacy engine name
    # (e.g. a lingering "cognee" in an old TOML) resolves to Hindsight.
    with (
        patch("hal0.memory.HindsightProvider", autospec=True) as mock_cls,
        patch("hal0.memory._build_hindsight_client", return_value=object()),
    ):
        provider_from_config(_cfg("cognee"))
        assert mock_cls.called


def test_factory_degrades_to_pgvector_when_hindsight_unavailable():
    from hal0.memory.pgvector_provider import PgVectorProvider

    with patch("hal0.memory._build_hindsight_client", side_effect=RuntimeError("no daemon")):
        provider = provider_from_config(_cfg("hindsight"))
        assert isinstance(provider, PgVectorProvider)


def test_factory_default_engine_is_hindsight_after_cutover():
    cfg = SimpleNamespace(
        memory=SimpleNamespace(
            embedding=SimpleNamespace(
                rerank_gateway_url="http://127.0.0.1:8080",
                rerank_model="builtin.jina-reranker-v1-tiny-en-q8",
                rerank_connect_timeout_s=1.0,
                rerank_read_timeout_s=8.0,
            ),
            graph=SimpleNamespace(enabled=False, extraction_slot="utility"),
        )
    )
    with (
        patch("hal0.memory.HindsightProvider", autospec=True) as mock_cls,
        patch("hal0.memory._build_hindsight_client", return_value=object()),
    ):
        provider_from_config(cfg)
        assert mock_cls.called


def test_factory_seeds_hindsight_graph_gate_from_config():
    """hal0.toml [memory.graph] must reach the provider — the dashboard's
    graph panel reads graph_status() and previously always saw enabled=False
    regardless of config (config/runtime drift on CT105)."""
    from hal0.memory.hindsight_provider import HindsightProvider

    cfg = _cfg("hindsight")
    cfg.memory.graph = SimpleNamespace(enabled=True, extraction_slot="utility")
    with patch("hal0.memory._build_hindsight_client", return_value=object()):
        provider = provider_from_config(cfg)

    assert isinstance(provider, HindsightProvider)
    status = provider.graph_status()
    assert status["enabled"] is True
    # ADR-0023: the configured extraction_slot reaches the provider (and is
    # mirrored on the deprecated ``route`` key for the old dashboard).
    assert status["extraction_slot"] == "utility"
    assert status["route"] == "utility"
