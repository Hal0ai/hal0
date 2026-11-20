"""The STATE.md live-state hook speaks the pinned Hermes ``pre_llm_call`` contract (#3853).

Hermes v0.22.4 (``4d043038``) reads a shell hook's stdout with ``json.loads``
and keeps only a JSON object carrying a non-empty string ``context``
(``agent/shell_hooks.py:_parse_response`` / ``_parse_context``). Raw markdown is
logged as "shell hook stdout was not valid JSON" and dropped. Only
``pre_llm_call``'s return value is injected (into the user message,
``agent/turn_context.py:_collect_pre_llm_call_context``); ``on_session_start``
is an observer whose return is ignored (``agent/conversation_loop.py``).

``_hermes_parse_context`` below mirrors that parser so the test needs no
Hermes install (CI has none).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[2]
HOOK = REPO / "installer" / "agents" / "hermes" / "hooks" / "inject-system-state.sh"

STATE_BODY = (
    "# Live system state\n"
    "_as_of: 2026-11-20T00:00:00Z_\n"
    '- Chat model: `qwen "quoted"` via `model_aliases.agent`\n'
    "- Path: C:\\not\\a\\path\twith a tab\n"
    "- Unicode: café ✓\n"
)


def _hermes_parse_context(stdout: str) -> dict[str, Any] | None:
    """Mirror of the pinned Hermes ``_parse_response`` for a context event."""
    stdout = (stdout or "").strip()
    if not stdout:
        return None
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    context = data.get("context")
    return {"context": context} if isinstance(context, str) and context.strip() else None


def _payload(*, is_first_turn: bool) -> str:
    """The stdin Hermes writes for pre_llm_call (``_serialize_payload``)."""
    return json.dumps(
        {
            "hook_event_name": "pre_llm_call",
            "tool_name": None,
            "tool_input": None,
            "session_id": "s1",
            "cwd": "/",
            "profile": "default",
            "extra": {
                "task_id": "t",
                "turn_id": "u",
                # A user message that quotes the key must not fool the gate.
                "user_message": 'say "is_first_turn": false please',
                "conversation_history": [],
                "is_first_turn": is_first_turn,
                "model": "hal0/agent",
                "platform": "cli",
            },
        },
        ensure_ascii=False,
    )


def _run(tmp_path: Path, stdin: str | None, *, body: str | None = STATE_BODY) -> str:
    state = tmp_path / "STATE.md"
    if body is not None:
        state.write_text(body, encoding="utf-8")
    env = {**os.environ, "HAL0_HERMES_STATE_FILE": str(state)}
    proc = subprocess.run(
        ["sh", str(HOOK)],
        input=stdin if stdin is not None else "",
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=10,
        check=True,
    )
    return proc.stdout


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_first_turn_emits_the_state_as_hermes_context(tmp_path: Path) -> None:
    out = _run(tmp_path, _payload(is_first_turn=True))
    parsed = _hermes_parse_context(out)
    assert parsed is not None, f"Hermes would drop this stdout: {out[:200]!r}"
    assert parsed["context"].rstrip("\n") == STATE_BODY.rstrip("\n")


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_later_turns_inject_nothing(tmp_path: Path) -> None:
    out = _run(tmp_path, _payload(is_first_turn=False))
    assert out == ""


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_a_manual_run_with_no_payload_still_emits_the_state(tmp_path: Path) -> None:
    # The release-validation lanes run the hook by hand with no stdin payload.
    parsed = _hermes_parse_context(_run(tmp_path, None))
    assert parsed is not None
    assert "Chat model" in parsed["context"]


@pytest.mark.skipif(shutil.which("sh") is None, reason="needs a POSIX sh")
def test_missing_or_empty_state_emits_nothing(tmp_path: Path) -> None:
    assert _run(tmp_path, _payload(is_first_turn=True), body=None) == ""
    assert _run(tmp_path, _payload(is_first_turn=True), body="") == ""
