"""``[brain_chat] tool_model = ""`` is a trap, and must not stay one (Stream A).

Found on a live box: an explicit empty string in ``hal0.toml``. On disk that is
indistinguishable from a key nobody set; to pydantic it is not — an explicit
``""`` OVERRIDES the ``"hal0/agent"`` default, removing the steward's
tool-routing target with no error, no warning, and no symptom except that tool
turns stop going anywhere useful.

The fix keeps "no tool routing" reachable, but only if you say so out loud.
"""

from __future__ import annotations

import logging

import pytest

from hal0.config.schema import (
    BRAIN_TOOL_MODEL_DEFAULT,
    BRAIN_TOOL_MODEL_DISABLED,
    BrainChatConfig,
    Hal0Config,
)


def test_the_default_is_still_the_anchor() -> None:
    assert BrainChatConfig().tool_model == BRAIN_TOOL_MODEL_DEFAULT == "hal0/agent"


@pytest.mark.parametrize("empty", ["", "   ", "\t", "\n", None])
def test_an_empty_tool_model_resolves_to_the_default(empty) -> None:
    """The regression itself."""
    assert BrainChatConfig(tool_model=empty).tool_model == BRAIN_TOOL_MODEL_DEFAULT


def test_the_coercion_is_loud(caplog: pytest.LogCaptureFixture) -> None:
    """"Or at minimum warn loudly" — we do both. A silent coercion would swap
    one invisible behaviour for another."""
    with caplog.at_level(logging.WARNING, logger="hal0.config.schema"):
        BrainChatConfig(tool_model="")
    assert caplog.records, "coercing an empty tool_model logged nothing"
    text = caplog.text
    assert "tool_model" in text
    assert "hal0/agent" in text
    # The warning has to name the deliberate opt-out, or an operator who
    # actually wanted no routing has no way to discover the new spelling.
    assert any(word in text for word in BRAIN_TOOL_MODEL_DISABLED)


@pytest.mark.parametrize("word", sorted(BRAIN_TOOL_MODEL_DISABLED))
def test_disabling_is_spelled_explicitly(word: str) -> None:
    """The escape hatch survives — it just cannot be reached by accident."""
    assert BrainChatConfig(tool_model=word).tool_model == ""
    assert BrainChatConfig(tool_model=word.upper()).tool_model == ""
    assert BrainChatConfig(tool_model=f"  {word} ").tool_model == ""


def test_disabling_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="hal0.config.schema"):
        BrainChatConfig(tool_model="off")
    assert not caplog.records, "a deliberate opt-out must not be nagged about"


def test_a_real_value_is_kept_and_stripped() -> None:
    assert BrainChatConfig(tool_model="hal0/code").tool_model == "hal0/code"
    assert BrainChatConfig(tool_model="  hal0/code  ").tool_model == "hal0/code"


def test_hal0_code_is_a_valid_override() -> None:
    """``installer/etc-hal0/slots/brain.toml`` documents ``hal0/code`` as the
    other confirmed-clean native tool-caller. It must survive normalisation."""
    assert BrainChatConfig(tool_model="hal0/code").tool_model == "hal0/code"


def test_a_full_config_with_an_empty_tool_model_still_loads() -> None:
    """The coercion runs on the nested model, not just direct construction —
    which is the path a real ``hal0.toml`` takes."""
    cfg = Hal0Config(brain_chat={"tool_model": ""})
    assert cfg.brain_chat.tool_model == BRAIN_TOOL_MODEL_DEFAULT


def test_the_normalised_value_round_trips_through_a_dump() -> None:
    """What gets written back must be the resolved value, not the trap."""
    cfg = BrainChatConfig(tool_model="")
    assert cfg.model_dump()["tool_model"] == BRAIN_TOOL_MODEL_DEFAULT
    assert BrainChatConfig(**cfg.model_dump()).tool_model == BRAIN_TOOL_MODEL_DEFAULT


def test_tool_model_is_a_known_settings_key() -> None:
    """It was absent from the apply registry, so a settings PUT touching it
    came back badged "unknown key" despite being a declared field."""
    from hal0.api._settings_apply import REGISTRY

    assert "brain_chat.tool_model" in REGISTRY
    assert REGISTRY["brain_chat.tool_model"]["apply_class"] == "immediate"
