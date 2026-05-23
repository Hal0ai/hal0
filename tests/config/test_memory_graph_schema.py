"""Unit tests for the ADR-0014 [memory.graph] schema additions.

Pins the contract every other ADR-0014 surface depends on:

  - default OFF + route="upstream" matches the v0.3 ship matrix.
  - upstream + model required when enabled=true + route="upstream".
  - "primary" / "agent" routes accept no upstream block.
  - route enum rejects typos at load time with the field path.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import (
    GraphUpstreamConfig,
    Hal0Config,
    MemoryConfig,
    MemoryGraphConfig,
)


class TestMemoryGraphDefaults:
    def test_top_level_default(self) -> None:
        """Hal0Config carries an off-by-default memory.graph section."""
        c = Hal0Config()
        assert isinstance(c.memory, MemoryConfig)
        assert isinstance(c.memory.graph, MemoryGraphConfig)
        assert c.memory.graph.enabled is False
        assert c.memory.graph.route == "upstream"
        assert c.memory.graph.upstream is None

    def test_disabled_round_trips_without_upstream(self) -> None:
        """An off-by-default block must round-trip without an upstream entry."""
        mg = MemoryGraphConfig()
        dumped = mg.model_dump()
        # rebuild from the dump — should validate cleanly.
        MemoryGraphConfig.model_validate(dumped)


class TestMemoryGraphRouteEnum:
    @pytest.mark.parametrize("route", ["upstream", "primary", "agent"])
    def test_valid_routes(self, route: str) -> None:
        # primary + agent don't need an upstream block (the §2 contract:
        # only route="upstream" demands one).
        mg = MemoryGraphConfig(enabled=False, route=route)
        assert mg.route == route

    def test_invalid_route_rejected_with_field_path(self) -> None:
        with pytest.raises(ValidationError) as ei:
            MemoryGraphConfig(route="bogus")
        msg = str(ei.value)
        assert "route" in msg
        assert "bogus" in msg


class TestMemoryGraphUpstreamRule:
    def test_enabled_upstream_requires_model(self) -> None:
        """enabled=true + route=upstream + missing upstream → ValidationError."""
        with pytest.raises(ValidationError) as ei:
            MemoryGraphConfig(enabled=True, route="upstream")
        assert "upstream" in str(ei.value)

    def test_enabled_upstream_with_empty_model_rejected(self) -> None:
        with pytest.raises(ValidationError) as ei:
            MemoryGraphConfig(
                enabled=True,
                route="upstream",
                upstream=GraphUpstreamConfig(provider="openrouter", model=""),
            )
        assert "upstream" in str(ei.value)

    def test_enabled_upstream_with_provider_and_model_ok(self) -> None:
        mg = MemoryGraphConfig(
            enabled=True,
            route="upstream",
            upstream=GraphUpstreamConfig(
                provider="openrouter",
                model="anthropic/claude-3.5-sonnet",
            ),
        )
        assert mg.enabled is True
        assert mg.upstream is not None
        assert mg.upstream.model == "anthropic/claude-3.5-sonnet"

    @pytest.mark.parametrize("route", ["primary", "agent"])
    def test_enabled_local_routes_dont_need_upstream(self, route: str) -> None:
        """primary + agent routes are valid with no upstream block."""
        mg = MemoryGraphConfig(enabled=True, route=route)
        assert mg.enabled is True
        assert mg.upstream is None

    def test_disabled_with_upstream_still_validates(self) -> None:
        """A user-prep-only upstream block (enabled=false) round-trips."""
        mg = MemoryGraphConfig(
            enabled=False,
            route="upstream",
            upstream=GraphUpstreamConfig(
                provider="openrouter", model="anthropic/claude-3.5-sonnet"
            ),
        )
        assert mg.enabled is False


class TestGraphUpstreamConfig:
    def test_empty_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GraphUpstreamConfig(provider="", model="x")

    def test_whitespace_provider_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GraphUpstreamConfig(provider="   ", model="x")

    def test_unknown_provider_accepted_for_forward_compat(self) -> None:
        # extra="allow" + named set is suggestion-only: unknown
        # providers round-trip so the upstreams catalog can grow ahead
        # of this enum.
        u = GraphUpstreamConfig(provider="future-vendor", model="foo")
        assert u.provider == "future-vendor"
