"""Unit tests for the ADR-0023 [memory.graph] schema.

ADR-0023 replaced the inert cognee-era ``route``/``upstream`` enum with a single
``extraction_slot`` knob. These pin the contract every memory-graph surface
depends on:

  - default OFF + ``extraction_slot == "utility"``.
  - the slot-name grammar (lowercase alnum/-/_, ≤32, leading alphanumeric).
  - legacy ``route``/``upstream`` keys in an old hal0.toml are silently dropped
    on load (``extra="ignore"``) rather than failing validation.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import (
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
        assert c.memory.graph.extraction_slot == "utility"

    def test_disabled_round_trips(self) -> None:
        """An off-by-default block must round-trip cleanly."""
        mg = MemoryGraphConfig()
        dumped = mg.model_dump()
        rebuilt = MemoryGraphConfig.model_validate(dumped)
        assert rebuilt.extraction_slot == "utility"

    def test_legacy_route_upstream_keys_are_no_longer_emitted(self) -> None:
        """The dumped block carries only the ADR-0023 fields."""
        dumped = MemoryGraphConfig().model_dump()
        assert set(dumped) == {"enabled", "extraction_slot", "llm_timeout_s"}


class TestExtractionSlotGrammar:
    @pytest.mark.parametrize("slot", ["utility", "agent", "coder-mini", "slot_1", "a", "npu"])
    def test_valid_slot_names(self, slot: str) -> None:
        mg = MemoryGraphConfig(extraction_slot=slot)
        assert mg.extraction_slot == slot

    @pytest.mark.parametrize(
        "slot",
        [
            "",  # empty
            "BadCase",  # uppercase
            "-leading-dash",  # leading non-alnum
            "_leading_underscore",  # leading underscore
            "has space",  # space
            "x" * 33,  # too long (>32)
            "slot!",  # punctuation
        ],
    )
    def test_invalid_slot_names_rejected(self, slot: str) -> None:
        with pytest.raises(ValidationError) as ei:
            MemoryGraphConfig(extraction_slot=slot)
        assert "extraction_slot" in str(ei.value)


class TestLegacyKeyDrop:
    def test_legacy_route_and_upstream_keys_silently_dropped(self) -> None:
        """An old hal0.toml block with ``route``/``upstream`` loads cleanly and
        those keys are dropped (extra="ignore") — no hard-fail on upgrade."""
        legacy = {
            "enabled": True,
            "route": "primary",
            "upstream": {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},
        }
        mg = MemoryGraphConfig.model_validate(legacy)
        assert mg.enabled is True
        # Legacy route value did NOT become the extraction_slot; default holds.
        assert mg.extraction_slot == "utility"
        dumped = mg.model_dump()
        assert "route" not in dumped
        assert "upstream" not in dumped

    def test_legacy_keys_dropped_via_hal0_config(self) -> None:
        """Same legacy block nested under a full Hal0Config load drops cleanly."""
        c = Hal0Config.model_validate(
            {"memory": {"graph": {"enabled": False, "route": "agent", "upstream": None}}}
        )
        assert c.memory.graph.enabled is False
        assert c.memory.graph.extraction_slot == "utility"
