"""The profile-centric conversion blocks a slot whose env sets a llama-server
option: the converted launch refuses it, so the operator moves it to the
slot's profile instead."""

from __future__ import annotations

from hal0.config.migrations import profile_centric as pc
from tests.config.profile_centric_fixtures import old_model, write_db, write_slot


def test_a_slot_env_with_a_llama_option_blocks_that_slot(tmp_hal0_home: str) -> None:
    write_slot(
        "agent",
        {
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "port": 8082,
            "profile": "chat",
            "workers": 1,
            "model": {"default": "big-chat"},
            "server": {"env": {"LLAMA_ARG_MODEL": "/etc/shadow", "HSA_OVERRIDE_GFX_VERSION": "1"}},
        },
    )
    write_db([old_model("big-chat", metadata={"context_length": 32768})])
    plan = pc.plan_conversion()
    agent = next(s for s in plan.slots if s.name == "agent")
    assert agent.status == "blocked"
    assert "LLAMA_ARG_MODEL" in str(agent.error)
    assert "agent.toml" in plan.blockers


def test_a_current_shape_slot_with_a_llama_option_is_blocked_too(tmp_hal0_home: str) -> None:
    """A slot already in the new shape takes the converter's current-shape
    shortcut; its env is still checked first, so the slot the launch refuses
    is reported as blocked instead of "unchanged"."""
    write_slot(
        "agent",
        {
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "port": 8082,
            "profile": "chat",
            "model": {"default": "big-chat"},
            "server": {"env": {"LLAMA_ARG_AGENT": "1"}},
        },
    )
    write_db([old_model("big-chat", metadata={"context_length": 32768})])
    plan = pc.plan_conversion()
    agent = next(s for s in plan.slots if s.name == "agent")
    assert agent.status == "blocked"
    assert "LLAMA_ARG_AGENT" in str(agent.error)


def test_a_current_shape_slot_with_ordinary_env_is_unchanged(tmp_hal0_home: str) -> None:
    write_slot(
        "agent",
        {
            "name": "agent",
            "type": "llm",
            "device": "gpu-rocm",
            "port": 8082,
            "profile": "chat",
            "model": {"default": "big-chat"},
            "server": {"env": {"HSA_OVERRIDE_GFX_VERSION": "11.5.1"}},
        },
    )
    write_db([old_model("big-chat", metadata={"context_length": 32768})])
    plan = pc.plan_conversion()
    agent = next(s for s in plan.slots if s.name == "agent")
    assert agent.status == "unchanged"
