"""Provider-agnostic OpenAI tool-calling loop core.

See :mod:`hal0.toolloop.engine` for :func:`run_tool_loop` and its
supporting pure helpers — the single machine both ``board_chat`` and
``omni_router.OmniRouter.run_loop`` drive.
"""

from __future__ import annotations

from hal0.toolloop.engine import (
    DispatchFn,
    LlmFn,
    OnEvent,
    assistant_message,
    assistant_text,
    assistant_thinking,
    build_tool_message,
    extract_tool_calls,
    openai_tool_schema,
    parse_text_tool_calls,
    run_tool_loop,
    split_thinking,
)

__all__ = [
    "DispatchFn",
    "LlmFn",
    "OnEvent",
    "assistant_message",
    "assistant_text",
    "assistant_thinking",
    "build_tool_message",
    "extract_tool_calls",
    "openai_tool_schema",
    "parse_text_tool_calls",
    "run_tool_loop",
    "split_thinking",
]
