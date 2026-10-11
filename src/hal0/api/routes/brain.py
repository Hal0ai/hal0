"""hal0-brain chat surface — ``POST /api/brain/chat`` (SSE).

The PRIMARY route for the first-class hal0-brain steward (SPEC §G / R4). The
brain engine lives in :mod:`hal0.brain.chat` and consumes the shared tool-loop
engine (:mod:`hal0.toolloop.engine`) with ZERO Hermes/board import dependency.

The dashboard's slide-out chat and the CLI both drive this route.

Auth: ``/api/brain`` classifies ADMIN (deny-by-default) in
:mod:`hal0.security.exposure` — the enforcement middleware gates every call.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.post("/chat")
async def brain_chat(request: Request):
    """hal0-brain platform steward. SSE stream. SPEC §G / R4.

    Delegates to :func:`hal0.brain.chat.run_brain_chat`.
    """
    from hal0.brain.chat import run_brain_chat

    return await run_brain_chat(request)


__all__ = ["router"]
