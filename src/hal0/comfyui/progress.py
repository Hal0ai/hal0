"""Step progress of the renders hal0 submitted to ComfyUI, for ``GET /api/comfyui/status``.

ComfyUI reports per-step progress only over its websocket, and only to the ``client_id``
that submitted the prompt. hal0's own renders (``/v1/images/generations`` and the
dashboard's Generate tab, which goes through it) each use a fresh ``client_id``, so
:mod:`hal0.providers.comfyui` listens on that socket while it polls ``/history`` and feeds
what it hears here. A render from the ComfyUI editor has no ``client_id`` hal0 knows, so
it never gets an entry; the dashboard shows queue-level state for it instead.

State is process-local and unlocked: the listener, ``infer`` and the ``/status`` handler
all run on the one event loop, and none of the functions here awaits.

An entry exists from the moment ComfyUI accepts the prompt until ``infer`` leaves, whether
by returning or raising (``infer`` calls :func:`finish` in a ``finally``). ``step`` and
``total`` are ``None`` until the first progress frame arrives (the checkpoint load and the
queue wait come before it).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


def _now() -> float:
    """The clock ``elapsed_s`` is measured on. Monotonic; a seam for tests."""
    return time.monotonic()


@dataclass
class _Render:
    prompt_id: str
    model: str
    started: float
    step: int | None = None
    total: int | None = None
    node: str | None = None


# Insertion order is start order (``start`` re-inserts), so the last value is the newest.
_renders: dict[str, _Render] = {}


def start(prompt_id: str, model: str) -> None:
    """Begin tracking a render ComfyUI has accepted. ``model`` is the id the render used."""
    _renders.pop(prompt_id, None)
    _renders[prompt_id] = _Render(prompt_id=prompt_id, model=model, started=_now())


def update(prompt_id: str, *, step: int, total: int, node: str | None) -> None:
    """Record the latest step of a tracked render.

    ``node`` is the graph node ComfyUI is executing; ``None`` keeps the one already known.
    A render that is not tracked (never started, or already finished) is ignored, so a late
    frame cannot bring a finished render back.
    """
    render = _renders.get(prompt_id)
    if render is None:
        return
    render.step = step
    render.total = total
    if node is not None:
        render.node = node


def finish(prompt_id: str) -> None:
    """Stop tracking a render. Unknown ids are ignored."""
    _renders.pop(prompt_id, None)


def current() -> dict[str, Any] | None:
    """The newest live render, by start time, or ``None`` when no hal0 render is live.

    ``elapsed_s`` runs from :func:`start` on a monotonic clock, rounded to one decimal.
    """
    if not _renders:
        return None
    render = next(reversed(_renders.values()))
    return {
        "model": render.model,
        "step": render.step,
        "total": render.total,
        "node": render.node,
        "elapsed_s": round(_now() - render.started, 1),
        "prompt_id": render.prompt_id,
    }


def _reset() -> None:
    """Forget every render. For test isolation only."""
    _renders.clear()
