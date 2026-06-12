"""provider_from_config factory tests (P0: Cognee-only branch)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from hal0.memory import provider_from_config


def _cfg(engine="cognee"):
    return SimpleNamespace(
        memory=SimpleNamespace(
            engine=engine,
            embedding=SimpleNamespace(
                model="BAAI/bge-small-en-v1.5",
                rerank_enabled=False,
                rerank_url="http://127.0.0.1:8086",
                rerank_over_fetch_factor=5,
                rerank_max_candidates=500,
                rerank_connect_timeout_s=1.0,
                rerank_read_timeout_s=8.0,
            ),
            graph=SimpleNamespace(enabled=False, route="upstream"),
        )
    )


def test_factory_returns_cognee_for_default_engine():
    # Patch the constructor so we don't stand up real Cognee.
    with patch("hal0.memory.CogneeWrapper", autospec=True) as mock_cls:
        provider_from_config(_cfg("cognee"))
        assert mock_cls.called


def test_factory_returns_hindsight_when_engine_hindsight():
    with (
        patch("hal0.memory.HindsightProvider", autospec=True) as mock_cls,
        patch("hal0.memory._build_hindsight_client", return_value=object()),
    ):
        provider_from_config(_cfg("hindsight"))
        assert mock_cls.called


def test_factory_degrades_to_pgvector_when_hindsight_unavailable():
    from hal0.memory.pgvector_provider import PgVectorProvider

    with patch("hal0.memory._build_hindsight_client", side_effect=RuntimeError("no daemon")):
        provider = provider_from_config(_cfg("hindsight"))
        assert isinstance(provider, PgVectorProvider)


def test_factory_default_engine_is_hindsight_after_cutover():
    from types import SimpleNamespace

    cfg = SimpleNamespace(
        memory=SimpleNamespace(
            embedding=SimpleNamespace(
                model="m",
                rerank_enabled=False,
                rerank_url="http://127.0.0.1:8086",
                rerank_over_fetch_factor=5,
                rerank_max_candidates=500,
                rerank_connect_timeout_s=1.0,
                rerank_read_timeout_s=8.0,
            ),
            graph=SimpleNamespace(enabled=False, route="upstream"),
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
    cfg.memory.graph = SimpleNamespace(enabled=True, route="upstream")
    with patch("hal0.memory._build_hindsight_client", return_value=object()):
        provider = provider_from_config(cfg)

    assert isinstance(provider, HindsightProvider)
    status = provider.graph_status()
    assert status["enabled"] is True
    assert status["route"] == "upstream"
