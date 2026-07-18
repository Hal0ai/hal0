"""Tests for ``ModelCapabilities`` + the ``Model`` §7.1d field additions.

Covers: tri-state nullable bool validation, ``extra="forbid"``, JSON
round-trip through ``Model.model_dump()``, and the ``Model.modalities``
alias-folding read accessor.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hal0.model_meta.modality import Modality
from hal0.registry.model import Model, ModelCapabilities


def test_model_capabilities_defaults_all_none() -> None:
    caps = ModelCapabilities()
    assert caps.tool_calling is None


def test_model_capabilities_tri_state() -> None:
    assert ModelCapabilities(tool_calling=True).tool_calling is True
    assert ModelCapabilities(tool_calling=False).tool_calling is False
    assert ModelCapabilities(tool_calling=None).tool_calling is None


def test_model_capabilities_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ModelCapabilities(mtp=True)  # not landed on this class yet (ML-5 owns it)


def test_model_default_capability_flags_is_inert() -> None:
    m = Model(id="x", path="/tmp/x.gguf")
    assert m.capability_flags.tool_calling is None
    assert m.architecture is None
    assert m.modalities_override is None


def test_model_capability_flags_round_trips_through_model_dump() -> None:
    m = Model(
        id="x",
        path="/tmp/x.gguf",
        capability_flags=ModelCapabilities(tool_calling=True),
        architecture="qwen3next",
    )
    dumped = m.model_dump()
    assert dumped["capability_flags"]["tool_calling"] is True
    assert dumped["architecture"] == "qwen3next"

    restored = Model.model_validate(dumped)
    assert restored.capability_flags.tool_calling is True
    assert restored.architecture == "qwen3next"


def test_model_modalities_folds_aliases_from_capabilities_list() -> None:
    m = Model(id="x", path="/tmp/x.gguf", capabilities=["chat", "stt", "embedding"])
    assert m.modalities == [Modality.CHAT, Modality.ASR, Modality.EMBED]


def test_model_modalities_drops_unknown_without_raising() -> None:
    m = Model(id="x", path="/tmp/x.gguf", capabilities=["chat", "not-a-modality"])
    assert m.modalities == [Modality.CHAT]


def test_model_modalities_override_accepts_modality_list() -> None:
    m = Model(id="x", path="/tmp/x.gguf", modalities_override=["image", "video"])
    assert m.modalities_override == [Modality.IMAGE, Modality.VIDEO]
