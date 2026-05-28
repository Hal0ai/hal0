"""hal0-memory Hermes plugin — ``sync_turn`` must NOT send ``dataset``.

The Hermes-side plugin runs inside the agent venv and uses an MCP
transport import that isn't available at hal0 test time. We load the
module from disk via :mod:`importlib`, stub the upstream
``agent.memory_provider.MemoryProvider`` ABC, and inspect the body
sent to ``_call_mcp``.

Why this matters: the server's ``_resolve_dataset`` rejects ``private:``
prefixes via ``_AGENT_ID_PATTERN`` and now resolves the dataset itself
from the ``X-hal0-Agent`` + ``X-hal0-Private: 1`` headers (PR #366).
Sending the bogus dataset turned every ``sync_turn`` into a silent 4xx
that lost durable memory.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_PATH = (
    REPO_ROOT / "installer" / "agents" / "hermes" / "plugins" / "hal0-memory" / "__init__.py"
)


@pytest.fixture
def hal0_memory_module():
    """Load the plugin module with a stubbed upstream ``MemoryProvider`` ABC.

    The plugin lives under ``installer/`` so it isn't normally importable.
    """
    # Stub the upstream ``agent.memory_provider`` import — that package
    # only resolves inside the hermes-agent venv. A minimal abstract base
    # is enough for the plugin import to succeed.
    if "agent" not in sys.modules:
        pkg = types.ModuleType("agent")
        pkg.__path__ = []  # mark as namespace package
        sys.modules["agent"] = pkg
    if "agent.memory_provider" not in sys.modules:
        memprov_mod = types.ModuleType("agent.memory_provider")

        class _MemoryProvider:
            """Bare-bones ABC stub matching the upstream surface."""

            name = ""

            def initialize(self, *args, **kwargs): ...

            def system_prompt_block(self) -> str:
                return ""

            def prefetch(self, query: str, **kwargs):
                return ""

            def sync_turn(self, user: str, assistant: str, **kwargs): ...

            def get_tool_schemas(self):
                return []

            def handle_tool_call(self, name: str, args, **kwargs):
                return ""

            def shutdown(self): ...

        memprov_mod.MemoryProvider = _MemoryProvider
        sys.modules["agent.memory_provider"] = memprov_mod

    spec = importlib.util.spec_from_file_location("hal0_memory_plugin", PLUGIN_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_provider(module, captured: list[tuple[str, dict]]):
    provider = module.Hal0MemoryProvider()
    provider.initialize(session_id="s1", user_id="u1")

    def _capture(tool: str, args: dict) -> dict:
        captured.append((tool, args))
        return {"status": "ok"}

    provider._call_mcp = _capture  # type: ignore[assignment]
    return provider


def test_sync_turn_omits_dataset(hal0_memory_module) -> None:
    """The fix: ``sync_turn`` no longer includes a ``dataset`` field —
    the server resolves it from ``X-hal0-Agent`` + ``X-hal0-Private``."""
    captured: list[tuple[str, dict]] = []
    provider = _make_provider(hal0_memory_module, captured)

    provider.sync_turn("hello", "world")

    assert len(captured) == 1
    tool, args = captured[0]
    assert tool == "memory_add"
    # The whole point of the fix: NO dataset field.
    assert "dataset" not in args, (
        f"sync_turn must omit dataset (server resolves from X-hal0-Agent); got args={args}"
    )
    # The text + tags shape stays intact so existing assertions further
    # up the stack don't move.
    assert "User: hello" in args["text"]
    assert "Assistant: world" in args["text"]
    assert "chat" in args["tags"]
    assert "hermes" in args["tags"]


def test_sync_turn_still_forwards_graph_config_when_enabled(hal0_memory_module) -> None:
    """ADR-0014 graph extraction toggle is unaffected by the dataset
    drop — the route/upstream config still rides on the same payload."""
    captured: list[tuple[str, dict]] = []
    provider = _make_provider(hal0_memory_module, captured)
    provider._graph_cfg = {
        "enabled": True,
        "route": "upstream",
        "upstream": {"provider": "openai", "model": "gpt-4o"},
    }

    provider.sync_turn("user msg", "asst msg")

    assert len(captured) == 1
    _, args = captured[0]
    assert "dataset" not in args
    assert args["graph"] == {
        "enabled": True,
        "route": "upstream",
        "upstream": {"provider": "openai", "model": "gpt-4o"},
    }
