"""First-class hal0-brain module: primary route + the no-Hermes invariant.

Covers SPEC §G / R4:

* ``POST /api/brain/chat`` is the PRIMARY route and drives the shared tool
  loop, emitting the documented SSE contract.
* ``hal0.api.routes.board_chat`` is a thin alias resolving to the SAME module
  object as :mod:`hal0.brain.chat` (so ``bc._foo`` / ``monkeypatch.setattr``
  stay coherent) with ``run_board_chat is run_brain_chat``.
* HARD INVARIANT: the brain source has ZERO Hermes/board import dependency.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

import hal0.brain
import hal0.brain.chat as brain_chat
from hal0.api.middleware import error_codes
from hal0.api.routes import board_chat as bc
from hal0.api.routes import brain as brain_routes

_BRAIN_SRC = Path(brain_chat.__file__).parent


class _StubLLM:
    """Return canned OpenAI chat-completion responses, one per turn."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)

    async def __call__(self, body: dict[str, Any]) -> dict[str, Any]:
        return self._responses.pop(0)


def _final(text: str) -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


def _make_app(stub: Any, tmp_path) -> TestClient:
    app = FastAPI()
    error_codes.install(app)
    app.include_router(brain_routes.router, prefix="/api/brain")
    # Board client present so the chat runs; the brain module never imports it.
    app.state.hermes_kanban = object()
    app.state.board_chat_llm = stub
    app.state.brain_persona_root = tmp_path / "personas"
    return TestClient(app)


def _sse_events(text: str) -> list[dict[str, Any]]:
    return [
        json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")
    ]


# ── primary route ───────────────────────────────────────────────────────────


def test_brain_chat_primary_route_streams_sse(tmp_path) -> None:
    client = _make_app(_StubLLM([_final("hello from the brain")]), tmp_path)
    resp = client.post("/api/brain/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert {"type": "token", "text": "hello from the brain"} in events
    assert events[-1] == {"type": "done"}


# ── thin alias identity ──────────────────────────────────────────────────────


def test_board_chat_is_thin_alias_of_brain_chat() -> None:
    # Same module object — one namespace, monkeypatch-coherent.
    assert bc is brain_chat
    assert bc.run_board_chat is brain_chat.run_brain_chat
    assert hal0.brain.run_brain_chat is brain_chat.run_brain_chat


# ── HARD INVARIANT: no Hermes/board import dependency in the brain source ─────


def test_brain_source_has_no_hermes_or_board_imports() -> None:
    offenders: list[str] = []
    for path in _BRAIN_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            mods: list[str] = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                mods = [node.module]
            for mod in mods:
                low = mod.lower()
                if low == "hermes" or low.startswith("hermes.") or mod == "hal0.board":
                    offenders.append(f"{path.name}: {mod}")
    assert offenders == [], f"brain must not import Hermes/board: {offenders}"
