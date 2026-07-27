"""[memory] engine selector field (brain-redesign P1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.config.schema import MemoryConfig


def test_engine_defaults_to_hindsight():
    assert MemoryConfig().engine == "hindsight"


def test_engine_accepts_known_engines():
    for e in ("hindsight", "mem0", "pgvector"):
        assert MemoryConfig(engine=e).engine == e


def test_engine_rejects_unknown():
    with pytest.raises(ValidationError):
        MemoryConfig(engine="weaviate")


def test_engine_rejects_retired_cognee_literal():
    # HAL0-SUNSET: v1.0.0 — 'cognee' was a back-compat alias that resolved to
    # hindsight at runtime; it is now retired and fails validation like any
    # other unknown engine. The Cognee store has been dark since v0.4.
    with pytest.raises(ValidationError):
        MemoryConfig(engine="cognee")
