"""HTTP routes for the chat-template catalog.

Mounted under ``/api/chat-templates`` (see :mod:`hal0.api.__init__`):

  - ``GET  /api/chat-templates``  — list available templates (auto + store dir).
  - ``POST /api/chat-templates``  — write a custom template to the store dir.

Templates live at ``<model_store_root>/chat-templates/<id>.jinja``.
``auto`` is a synthetic sentinel that means "use the model's embedded template".
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hal0.config.paths import model_store_root

router = APIRouter()

# Valid template id: lowercase alphanumeric, underscore, hyphen; 1-41 chars.
_VALID_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")


def _templates_dir() -> Path:
    return Path(model_store_root()) / "chat-templates"


def _catalog() -> list[dict[str, Any]]:
    """Build the full catalog: ``auto`` first, then store entries sorted by id."""
    entries: list[dict[str, Any]] = [{"id": "auto", "label": "Auto (GGUF embedded)"}]
    store = _templates_dir()
    if store.is_dir():
        for p in sorted(store.glob("*.jinja")):
            entries.append({"id": p.stem, "label": p.stem})
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

    return {"id": body.id, "label": body.id}
