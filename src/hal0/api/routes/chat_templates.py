"""HTTP routes for the chat-template catalog.

Mounted under ``/api/chat-templates`` (see :mod:`hal0.api.__init__`):

  - ``GET  /api/chat-templates``  — list available templates (auto + store dir).
  - ``POST /api/chat-templates``  — write a custom template to the store dir.

Templates live at ``<model_store_root>/chat-templates/<id>.jinja``.
``auto`` is a synthetic sentinel that means "use the model's embedded template".

Each catalog entry carries a best-effort ``valid``/``error`` lint (see
:func:`_render_check`) so the slot-edit dropdown can flag a malformed template
that an operator dropped into the store dir, instead of letting it surface only
as a slot cold-start crash.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from jinja2.sandbox import ImmutableSandboxedEnvironment
from pydantic import BaseModel

from hal0.config.paths import model_store_root

router = APIRouter()

# Valid template id: lowercase alphanumeric, underscore, hyphen; 1-41 chars.
_VALID_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

# A benign 4-turn conversation used to render-check a template. Covers the
# variables chat templates almost always reference. Kept deliberately simple:
# the goal is to catch gross syntax/render breakage, not to exercise tools or
# multimodal content.
_SAMPLE_CONTEXT: dict[str, Any] = {
    "messages": [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
        {"role": "user", "content": "How are you?"},
    ],
    "add_generation_prompt": True,
    "enable_thinking": False,
    "bos_token": "<s>",
    "eos_token": "</s>",
    "tools": None,
}


def _render_check(text: str) -> str | None:
    """Best-effort render lint for a chat template.

    Returns ``None`` when the template parses and renders against
    :data:`_SAMPLE_CONTEXT`, else a short ``"ErrorType: message"`` string.

    NOTE: this uses Jinja2, while llama.cpp renders chat templates with its
    own ``minja`` engine. The two are close but not identical, so a ``valid``
    result is a strong signal — not a guarantee — of minja compatibility. It
    reliably catches the common failure (a syntactically broken template) that
    would otherwise only show up when the slot fails to start.
    """

    def _raise_exception(message: str = "") -> str:
        # minja exposes raise_exception(); templates call it in guard branches.
        # On our benign sample it should not fire — if it does, the template is
        # rejecting an ordinary conversation, which is a real problem to flag.
        raise ValueError(message or "template called raise_exception")

    def _strftime_now(_fmt: str = "") -> str:
        # Gemma / Llama-3.1 templates call strftime_now(); a fixed stub keeps
        # the render deterministic.
        return "2026-01-01"

    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True)
    env.globals["raise_exception"] = _raise_exception
    env.globals["strftime_now"] = _strftime_now
    try:
        env.from_string(text).render(**_SAMPLE_CONTEXT)
    except Exception as exc:  # a lint must never crash the catalog
        return f"{type(exc).__name__}: {exc}".strip()
    return None


def _templates_dir() -> Path:
    return Path(model_store_root()) / "chat-templates"


def _entry(template_id: str, label: str, error: str | None) -> dict[str, Any]:
    return {"id": template_id, "label": label, "valid": error is None, "error": error}


def _catalog() -> list[dict[str, Any]]:
    """Build the full catalog: ``auto`` first, then store entries sorted by id."""
    # ``auto`` defers to the GGUF's embedded template — nothing for us to lint.
    entries: list[dict[str, Any]] = [_entry("auto", "Auto (GGUF embedded)", None)]
    store = _templates_dir()
    if store.is_dir():
        for p in sorted(store.glob("*.jinja")):
            try:
                error = _render_check(p.read_text())
            except OSError as exc:
                error = f"unreadable: {exc}"
            entries.append(_entry(p.stem, p.stem, error))
    return entries


@router.get("")
async def list_chat_templates() -> list[dict[str, Any]]:
    """Return all available chat templates."""
    return _catalog()


class _TemplateBody(BaseModel):
    id: str
    content: str


@router.post("")
async def create_chat_template(body: _TemplateBody) -> dict[str, Any]:
    """Write a custom chat template to the model store.

    The ``id`` must match ``[a-z0-9][a-z0-9_-]{0,40}``; any path-traversal
    attempt or uppercase character is rejected with HTTP 400.
    """
    if not _VALID_ID_RE.fullmatch(body.id):
        raise HTTPException(status_code=400, detail=f"Invalid template id: {body.id!r}")

    store = _templates_dir()
    try:
        store.mkdir(parents=True, exist_ok=True)
        (store / f"{body.id}.jinja").write_text(body.content)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write template: {exc}") from exc

    # Write succeeds regardless of lint result (mirrors the filesystem-drop
    # path, which never runs through here) — but report the lint so a caller
    # writing a broken template gets immediate feedback.
    return _entry(body.id, body.id, _render_check(body.content))
