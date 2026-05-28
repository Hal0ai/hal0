"""Persona endpoints for the bundled agents surface (v0.3 PR-4).

Thin FastAPI wrapper around :mod:`hal0.agents.personas`. The persona TOML
store + hot-reload nudge live in that module; this file is the HTTP
shape the dashboard's persona picker (PR-8) + the ``hal0 agent persona``
CLI (future) consume.

Route shape (master-plan §2 generalization): every endpoint is
parameterized by ``agent_id`` so v0.4 can unlock pi-coder by adding a
registry entry without rewriting the route table. v0.3 only resolves
``"hermes"`` — any other id returns 404 at :func:`_resolve_agent`.

Mounted from :mod:`hal0.api` at prefix ``/api/agents`` so the realized
routes are:

    GET  /api/agents/{agent_id}/personas
    GET  /api/agents/{agent_id}/personas/{persona_id}
    POST /api/agents/{agent_id}/personas/{persona_id}/activate
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter

from hal0.agents import personas as personas_mod
from hal0.errors import BadRequest, Hal0Error, NotFound

log = structlog.get_logger(__name__)

router = APIRouter()


# ── agent registry ──────────────────────────────────────────────────────────
#
# v0.3 ships hermes only. Future agents (pi-coder in v0.4) get a row here
# pointing at their personas root and the existing /personas routes light
# up unchanged. Keeping this as a module-level dict (vs hard-coding the
# "hermes" string in every handler) is the v0.3-only place that knows
# about agent identity — every route below uses :func:`_resolve_agent`.

_AGENT_PERSONAS_ROOTS: dict[str, Path] = {
    "hermes": personas_mod.PERSONAS_ROOT,
}


def _resolve_agent(agent_id: str) -> Path:
    """Map an agent id onto its personas store root.

    Raises :class:`NotFound` with a stable code so the dashboard can
    distinguish "unknown agent" from "unknown persona" (which share the
    same HTTP status but should render different UX).
    """
    root = _AGENT_PERSONAS_ROOTS.get(agent_id)
    if root is None:
        raise NotFound(
            f"unknown agent {agent_id!r}",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )
    return root


# ── serialisers ─────────────────────────────────────────────────────────────


def _persona_summary(persona: personas_mod.Persona, active_id: str | None) -> dict[str, Any]:
    """Compact row shape used by the list endpoint.

    Matches the dashboard persona picker (PR-8) contract — ``id`` plus
    enough metadata to render a card without a follow-up detail fetch.
    """
    return {
        "id": persona.id,
        "display_name": persona.display_name,
        "summary": persona.summary,
        "active": persona.id == active_id,
    }


def _persona_detail(
    persona: personas_mod.Persona,
    *,
    raw_toml: str,
    active_id: str | None,
) -> dict[str, Any]:
    """Full persona payload — parsed dataclass + raw TOML body.

    The raw body is included so the PR-8 persona editor can let the
    operator hand-edit the TOML without losing comments or formatting
    on a round-trip through the dataclass. The parsed fields are there
    so the picker can render labels + policy chips without re-parsing
    TOML in the browser.
    """
    approval = persona.approval
    return {
        "id": persona.id,
        "display_name": persona.display_name,
        "summary": persona.summary,
        "active": persona.id == active_id,
        "system_prompt": persona.system_prompt,
        "tools_allowed": list(persona.tools_allowed),
        "memory_namespace": persona.memory_namespace,
        "preferred_upstream": persona.preferred_upstream,
        "preferred_model": persona.preferred_model,
        "approval": {
            "default_policy": approval.default_policy,
            "auto_approve": list(approval.auto_approve),
            "require_approval": list(approval.require_approval),
        },
        "raw_toml": raw_toml,
    }


def _safe_error_message(exc: Exception) -> str:
    """Strip absolute filesystem paths out of a persona-error message.

    :class:`hal0.agents.personas.PersonaError` includes the offending
    path so the CLI can point at it; the API surface mustn't leak that
    to the dashboard (path layout is operator-private). We replace any
    absolute-looking prefix with ``<persona>``.
    """
    msg = str(exc)
    # PersonaError messages always start with ``<path>: …`` — chop
    # everything before the first ``: `` if it looks like a filesystem
    # path. Conservative; only rewrites when the prefix actually starts
    # with ``/``.
    if msg.startswith("/"):
        try:
            _, rest = msg.split(": ", 1)
        except ValueError:
            return msg
        return f"<persona>: {rest}"
    return msg


# ── GET /{agent_id}/personas ────────────────────────────────────────────────


@router.get("/{agent_id}/personas")
async def list_agent_personas(agent_id: str) -> dict[str, Any]:
    """List every persona registered for ``agent_id``.

    Returns ``{"agent_id", "active", "personas": [...]}`` so the
    dashboard can render the picker with one fetch. The ``active`` field
    at the envelope level mirrors the ``active: bool`` flag on each row
    — clients can rely on either.
    """
    root = _resolve_agent(agent_id)
    items = personas_mod.list_personas(root=root)
    active = personas_mod.get_active(root=root)
    return {
        "agent_id": agent_id,
        "active": active,
        "personas": [_persona_summary(p, active) for p in items],
    }


# ── GET /{agent_id}/personas/{persona_id} ───────────────────────────────────


@router.get("/{agent_id}/personas/{persona_id}")
async def get_agent_persona(agent_id: str, persona_id: str) -> dict[str, Any]:
    """Return parsed persona + raw TOML body for one persona.

    404 if the agent id is unknown OR the persona file is missing.
    400 if the file exists but fails to parse — the dashboard surfaces
    the message verbatim so the operator can fix the offending TOML.
    """
    root = _resolve_agent(agent_id)
    target = root / f"{persona_id}.toml"
    try:
        persona = personas_mod.load_persona(persona_id, root=root)
    except FileNotFoundError as exc:
        raise NotFound(
            f"persona {persona_id!r} not found",
            code="persona.not_found",
            details={"agent_id": agent_id, "persona_id": persona_id},
        ) from exc
    except personas_mod.PersonaError as exc:
        raise BadRequest(
            _safe_error_message(exc),
            code="persona.malformed",
            details={"agent_id": agent_id, "persona_id": persona_id},
        ) from exc

    try:
        raw_toml = target.read_text(encoding="utf-8")
    except OSError:
        # Loaded successfully but reading the raw bytes failed — return
        # an empty body rather than 500ing the whole request. Parsed
        # content is the authoritative shape; raw_toml is a convenience
        # for the editor.
        raw_toml = ""

    active = personas_mod.get_active(root=root)
    return _persona_detail(persona, raw_toml=raw_toml, active_id=active)


# ── POST /{agent_id}/personas/{persona_id}/activate ─────────────────────────


@router.post("/{agent_id}/personas/{persona_id}/activate")
async def activate_agent_persona(
    agent_id: str,
    persona_id: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Swap the active persona for ``agent_id`` to ``persona_id``.

    Body shape ``{"reload": bool}``; ``reload`` defaults to ``true`` so
    the dashboard's persona picker just POSTs an empty body and gets the
    hot-reload nudge for free. ``reload=false`` writes ``active.txt``
    but skips the JSON-RPC call — useful for batch tooling that wants
    to flip the pointer without bothering a running Hermes process.

    Response carries the *previous* active persona id so the dashboard
    can render an undo affordance. ``reloaded`` reflects the hot-reload
    helper's success — ``false`` doesn't mean activation failed; it
    just means the next message will use the new persona instead of
    the in-flight one.
    """
    root = _resolve_agent(agent_id)
    do_reload = True
    if isinstance(body, dict) and "reload" in body:
        do_reload = bool(body["reload"])

    previous = personas_mod.get_active(root=root)

    if do_reload:
        try:
            result = personas_mod.activate(persona_id, root=root)
        except FileNotFoundError as exc:
            raise NotFound(
                f"persona {persona_id!r} not found",
                code="persona.not_found",
                details={"agent_id": agent_id, "persona_id": persona_id},
            ) from exc
        except personas_mod.PersonaError as exc:
            raise BadRequest(
                _safe_error_message(exc),
                code="persona.malformed",
                details={"agent_id": agent_id, "persona_id": persona_id},
            ) from exc
        except Hal0Error:
            raise
        except Exception as exc:  # pragma: no cover — defensive
            log.warning(
                "persona.activate_failed",
                agent_id=agent_id,
                persona_id=persona_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise Hal0Error(
                "persona activation failed",
                code="persona.activate_failed",
            ) from exc

        hot_reload = result.get("hot_reload") or {}
        return {
            "agent_id": agent_id,
            "active": result.get("persona_id", persona_id),
            "previous": previous,
            "reloaded": bool(hot_reload.get("ok")),
            "reload_error": hot_reload.get("error"),
        }

    # reload=false branch: write active.txt but DON'T nudge hermes.
    try:
        personas_mod.set_active(persona_id, root=root)
    except FileNotFoundError as exc:
        raise NotFound(
            f"persona {persona_id!r} not found",
            code="persona.not_found",
            details={"agent_id": agent_id, "persona_id": persona_id},
        ) from exc

    return {
        "agent_id": agent_id,
        "active": persona_id,
        "previous": previous,
        "reloaded": False,
        "reload_error": None,
    }
