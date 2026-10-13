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
