"""``hal0 chat`` — terminal REPL over the local ``/v1/chat/completions`` (§21.14).

Useful for SSH/headless boxes where the dashboard's slide-out chat isn't
reachable: a plain read-eval-print loop that talks to a co-resident chat
slot (default alias ``agent`` — ADR-0023 canonical) the same way any other
OpenAI-shaped client would, via ``hal0.cli._shared``-style thin HTTP calls.

Two pieces are explicitly REUSED rather than re-implemented, per the design
note in plan §21.14:

* ``/think on|off|default`` toggles :func:`hal0.normalize.thinking.apply_thinking_policy`
  — the SAME reasoning-suppression lever ``POST /v1/chat/completions`` applies
  server-side (:func:`hal0.api.routes.v1._normalize_chat_body`). ``on``/``off``
  force ``chat_template_kwargs.enable_thinking`` explicit for every turn from
  that point on; ``default`` stops forcing it and lets the server apply the
  slot's own configured default.
* Reasoning-token separation reuses :func:`hal0.toolloop.engine.split_thinking`
  and :func:`hal0.toolloop.engine.assistant_thinking` — the shared tool-loop
  core both the hal0-brain dashboard chat (``hal0.api.routes.board_chat``)
  and OmniRouter's re-entrant loop use to fold ``<think>...</think>`` /
  DeepSeek-style ``reasoning_content`` out of a completion. Only the
  STRIPPED visible reply is folded back into this REPL's in-memory
  ``messages`` history before the next turn — a long session never re-feeds
  its own prior chain-of-thought back to the model as context bloat.

The SSE consumption mirrors the ``data: ...`` framing
``hal0.cli.slot_commands.slot_logs --follow`` already parses: one JSON
object per ``data:`` line, terminated by the literal ``[DONE]`` sentinel.
``--no-stream`` is a thin toggle that sets ``stream: false`` on the request
body and reads the whole completion in one shot instead.
"""

from __future__ import annotations

import json as jsonlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
import typer
from rich.console import Console

from hal0.normalize.thinking import apply_thinking_policy
from hal0.toolloop.engine import assistant_text, assistant_thinking, split_thinking

console = Console()

# ADR-0023 canonical chat-slot alias — the tool-calling anchor every
# fallback chain ends in (see hal0.api.routes.board_chat's BRAIN_SLOT_MODEL
# docstring). Bare alias, not ``hal0/agent`` — matches what
# ``_rewrite_chat_slot_alias`` (hal0.api.routes.v1) resolves directly.
DEFAULT_MODEL = "agent"
DEFAULT_BASE_URL = "http://127.0.0.1:8080/v1"

ThinkMode = Literal["default", "on", "off"]


@dataclass
class ChatSession:
    """In-memory REPL state: the running ``messages`` history + knobs.

    Kept as a plain dataclass (no I/O) so the turn-building/history-strip
    logic is unit-testable without a live server — the HTTP transport lives
    in the module-level ``_post_completions`` / ``_iter_stream_events``
    functions, which tests monkeypatch.
    """

    model: str = DEFAULT_MODEL
    stream: bool = True
    think_mode: ThinkMode = "default"
    history: list[dict[str, Any]] = field(default_factory=list)

    def clear(self) -> None:
        """``/clear`` — drop the whole conversation history."""
        self.history.clear()

    def set_think(self, mode: str) -> bool:
        """Apply ``/think <mode>``; returns False on an unrecognised mode."""
        mode = mode.strip().lower()
        if mode not in ("on", "off", "default"):
            return False
        self.think_mode = mode  # type: ignore[assignment]
        return True

    def build_body(self, user_text: str) -> dict[str, Any]:
        """Append the user's turn to history and build the request body.

        ``think_mode == "default"`` sends the body untouched — the server's
        own ``_normalize_chat_body`` already applies the slot's configured
        default via the exact same :func:`apply_thinking_policy`. ``on``/
        ``off`` force an explicit ``chat_template_kwargs.enable_thinking``
        client-side so the REPL's toggle takes effect immediately regardless
        of the slot's default.
        """
        self.history.append({"role": "user", "content": user_text})
        body: dict[str, Any] = {
            "model": self.model,
            "messages": list(self.history),
            "stream": self.stream,
        }
        if self.think_mode != "default":
            body = apply_thinking_policy(body, default_thinking=self.think_mode == "on")
        return body

    def finish_assistant_turn(self, content: str, reasoning: str) -> tuple[str, str]:
        """Split reasoning out of a completed reply; fold only the stripped
        visible text back into ``history``.

        Returns ``(thinking_text, visible_text)`` for the caller to display.
        ``content`` may itself carry inline ``<think>...</think>`` tags
        (``split_thinking`` handles that); ``reasoning`` is whatever the
        backend reported via an explicit ``reasoning_content``/``reasoning``
        field (``assistant_thinking`` on the non-streaming path; accumulated
        streaming deltas on the streaming path).
        """
        inline_thinking, visible = split_thinking(content)
        thinking = "\n".join(t for t in (reasoning, inline_thinking) if t)
        self.history.append({"role": "assistant", "content": visible})
        return thinking, visible


def _post_completions(client: httpx.Client, url: str, body: dict[str, Any]) -> dict[str, Any]:
    """One non-streaming ``POST /v1/chat/completions`` call → parsed JSON."""
    resp = client.post(url, json=body)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def _iter_stream_events(
    client: httpx.Client, url: str, body: dict[str, Any]
) -> Iterator[dict[str, Any]]:
    """Consume one OpenAI-style SSE chat-completion stream.

    Mirrors the ``data: ...`` framing :func:`hal0.cli.slot_commands.slot_logs`
    already parses for ``--follow``: one JSON object per ``data:`` line,
    terminated by the literal ``[DONE]`` sentinel. Malformed lines are
    skipped rather than raised — a stray keep-alive comment shouldn't crash
    the REPL turn.
    """
    with client.stream("POST", url, json=body) as resp:
        resp.raise_for_status()
        for raw in resp.iter_lines():
            if not raw or not raw.startswith("data:"):
                continue
            payload = raw[len("data:") :].strip()
            if payload == "[DONE]":
                return
            try:
                parsed = jsonlib.loads(payload)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                yield parsed


def _handle_think_command(session: ChatSession, line: str) -> None:
    parts = line.split(maxsplit=1)
    mode = parts[1].strip() if len(parts) > 1 else ""
    if session.set_think(mode):
        console.print(f"[dim](thinking: {session.think_mode})[/dim]")
    else:
        console.print("[yellow]usage: /think on|off|default[/yellow]")


def _run_streaming_turn(
    session: ChatSession, client: httpx.Client, url: str, user_text: str
) -> None:
    """Stream one turn's tokens live, then fold the stripped reply into history."""
    body = session.build_body(user_text)
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    reasoning_started = False
    content_started = False
    console.print("hal0> ", end="", markup=False, highlight=False)
    for chunk in _iter_stream_events(client, url, body):
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        reasoning_piece = delta.get("reasoning_content") or delta.get("reasoning")
        if isinstance(reasoning_piece, str) and reasoning_piece:
            if not reasoning_started:
                console.print(
                    "(thinking) ", end="", style="dim italic", markup=False, highlight=False
                )
                reasoning_started = True
            reasoning_parts.append(reasoning_piece)
            console.print(
                reasoning_piece, end="", style="dim italic", markup=False, highlight=False
            )
        content_piece = delta.get("content")
        if isinstance(content_piece, str) and content_piece:
            if reasoning_started and not content_started:
                console.print()  # blank line between thinking and the visible reply
            content_started = True
            content_parts.append(content_piece)
            console.print(content_piece, end="", markup=False, highlight=False)
    console.print()
    session.finish_assistant_turn("".join(content_parts), "".join(reasoning_parts))


def _run_nonstreaming_turn(
    session: ChatSession, client: httpx.Client, url: str, user_text: str
) -> None:
    """``--no-stream``: wait for the whole completion, then print it at once."""
    body = session.build_body(user_text)
    response = _post_completions(client, url, body)
    thinking, visible = session.finish_assistant_turn(
        assistant_text(response), assistant_thinking(response)
    )
    if thinking:
        console.print(f"(thinking) {thinking}", style="dim italic", markup=False, highlight=False)
    console.print(f"hal0> {visible}", markup=False, highlight=False)


def chat_command(
    model: str = typer.Option(
        DEFAULT_MODEL,
        "--model",
        help="Slot alias or model id to chat with (default: the 'agent' chat slot).",
    ),
    base_url: str = typer.Option(
        DEFAULT_BASE_URL,
        "--base-url",
        envvar="HAL0_CHAT_BASE_URL",
        help="hal0 OpenAI-compatible API base (…/v1).",
    ),
    no_stream: bool = typer.Option(
        False,
        "--no-stream",
        help="Disable SSE streaming — wait for each full reply instead of printing tokens live.",
    ),
) -> None:
    """Terminal chat REPL over ``/v1/chat/completions``.

    In-REPL commands:

    \b
      /think on|off|default   force reasoning on/off, or fall back to the
                               slot's own configured default
      /clear                  reset the conversation history
      /quit                   exit (Ctrl-D also works)

    Handy for SSH/headless boxes where the dashboard's slide-out chat isn't
    reachable. Reasoning tokens are always split out of the model's reply
    before it's folded back into history, regardless of ``/think`` state —
    long sessions never balloon the context with the model's own prior
    chain-of-thought.
    """
    session = ChatSession(model=model, stream=not no_stream)
    url = base_url.rstrip("/") + "/chat/completions"
    console.print(
        f"[dim]hal0 chat — model={model} think={session.think_mode} "
        f"stream={session.stream} · {url}[/dim]"
    )
    console.print("[dim]/think on|off|default  ·  /clear  ·  /quit (or Ctrl-D)[/dim]")
    with httpx.Client(timeout=120.0) as client:
        while True:
            try:
                line = input("you> ")
            except EOFError:
                console.print()
                break
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line == "/clear":
                session.clear()
                console.print("[dim](history cleared)[/dim]")
                continue
            if line.startswith("/think"):
                _handle_think_command(session, line)
                continue
            try:
                if session.stream:
                    _run_streaming_turn(session, client, url, line)
                else:
                    _run_nonstreaming_turn(session, client, url, line)
            except httpx.HTTPError as exc:
                console.print(f"[red]transport error:[/red] {exc}")


__all__ = ["ChatSession", "chat_command"]
