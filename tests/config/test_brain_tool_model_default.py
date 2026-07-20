"""Per spec-p3-brain.final.md §5a + spec §5.3:
BrainChatConfig.tool_model default is 'hal0/agent' (was '').
"""

from __future__ import annotations

from hal0.config.schema import BrainChatConfig


def test_brain_chat_tool_model_default_is_hal0_agent() -> None:
    """Default tool_model is 'hal0/agent' per spec-p3-brain §5a."""
    cfg = BrainChatConfig()
    assert cfg.tool_model == "hal0/agent", (
        f"BrainChatConfig.tool_model default = {cfg.tool_model!r}, expected 'hal0/agent'"
    )
