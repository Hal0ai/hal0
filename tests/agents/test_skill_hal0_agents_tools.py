"""The shipped ``hal0-agents`` skill names only tools and arguments the agents MCP really has (#4060).

The skill teaches a bundled agent the ``hal0-agents`` MCP server. It once showed
``agent_id``/``prompt`` calls and a runnable Pi that the server does not offer, so an
agent following it sent arguments the server refused. These tests read the skill, take
every tool name and every argument it shows, and check them against the tool definitions
``hal0.mcp.agents`` registers, in process.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from hal0.mcp.agents import build_server

REPO = Path(__file__).resolve().parents[2]
SKILL = REPO / "installer" / "agent-skills" / "hal0-agents" / "SKILL.md"

_TOOL_NAME = re.compile(r"\bagents?_[a-z_]+\b")
_CALL = re.compile(r"^\s*→\s*(agents?_[a-z_]+)(.*)$")
_ARG_LINE = re.compile(r"^\s{2}([a-z_]+):\s")
_INLINE_ARG = re.compile(r"\b([a-z_]+):\s")
_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')


def _tools() -> dict[str, dict[str, Any]]:
    """Tool name -> its input schema, from the definitions the server registers."""
    server = build_server(state=SimpleNamespace())
    return {tool.name: tool.parameters for tool in server._tool_manager.list_tools()}


def _body() -> str:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    return text.split("---\n", 2)[2]


def _code_text(body: str) -> str:
    """Fenced blocks and backtick spans, where the skill writes tool names."""
    fenced = re.findall(r"```.*?\n(.*?)```", body, re.DOTALL)
    stripped = re.sub(r"```.*?```", "", body, flags=re.DOTALL)
    return "\n".join([*fenced, *re.findall(r"`([^`\n]+)`", stripped)])


def _examples(body: str) -> list[tuple[str, set[str]]]:
    """Each example call in a fenced block: (tool, the argument names it passes).

    A call starts at ``→ tool``. Its arguments are the ``name: value`` pairs on that
    line after the tool name (up to a second ``→``) and the two-space-indented
    ``name: value`` lines under it. ``←`` lines describe a result and are skipped.
    """
    calls: list[tuple[str, set[str]]] = []
    for block in re.findall(r"```[^\n]*\n(.*?)```", body, re.DOTALL):
        current: tuple[str, set[str]] | None = None
        for line in block.splitlines():
            head = _CALL.match(line)
            if head:
                current = (head.group(1), set())
                calls.append(current)
                inline = head.group(2).split("→", 1)[0]
                current[1].update(_INLINE_ARG.findall(_QUOTED.sub('""', inline)))
                continue
            if current is None or line.lstrip().startswith("←"):
                continue
            arg = _ARG_LINE.match(line)
            if arg:
                current[1].add(arg.group(1))
    return calls


def test_the_skill_shows_example_calls() -> None:
    assert _examples(_body()), "the parser found no example call; the test would pass vacuously"


def test_every_tool_the_skill_names_exists() -> None:
    tools = _tools()
    named = set(_TOOL_NAME.findall(_code_text(_body())))
    assert named, "no tool name found in the skill"
    assert not (named - set(tools)), f"tools that do not exist: {sorted(named - set(tools))}"


def test_every_registered_tool_is_listed() -> None:
    listed = set(re.findall(r"^\| `(agents?_[a-z_]+)` \|", _body(), re.MULTILINE))
    assert listed == set(_tools())


def test_every_argument_shown_is_a_parameter_of_its_tool() -> None:
    tools = _tools()
    for tool, args in _examples(_body()):
        unknown = args - set(tools[tool]["properties"])
        assert not unknown, f"{tool} does not take {sorted(unknown)}"


def test_every_required_parameter_appears_in_an_example() -> None:
    tools = _tools()
    shown: dict[str, list[set[str]]] = {}
    for tool, args in _examples(_body()):
        shown.setdefault(tool, []).append(args)
    for tool, examples in shown.items():
        required = set(tools[tool].get("required", []))
        covered = set().union(*examples)
        assert required <= covered, f"no example of {tool} passes {sorted(required - covered)}"


@pytest.mark.parametrize("stale", ["pi-agent", "agent_id"])
def test_stale_names_are_gone(stale: str) -> None:
    assert stale not in SKILL.read_text(encoding="utf-8")


def test_no_machine_specific_content() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert not re.search(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", text)
    assert "/home/" not in text
