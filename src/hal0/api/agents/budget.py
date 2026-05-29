"""Per-persona budget endpoints (OpenRouter Phase 0 prereq).

REST surface around :mod:`hal0.agents.budget`. Reads / writes the
``[persona.budget]`` block inside the persona TOML, surfaces the
ledger-derived spend stats, and exposes ``check`` (dry-run) +
``charge`` (post-response record) so the V1 OpenRouter provider has a
budget gate from day 1 without re-inventing this layer.

Route shape mirrors :mod:`hal0.api.agents.personas` — every endpoint is
parameterized by ``agent_id`` so v0.4's pi-coder unlock + the per-agent
containing scope (deferred per PLANNING.md §5 Q2) light up by adding
registry rows, not rewriting the route table.

Mounted from :mod:`hal0.api` at prefix ``/api/agents`` so the realized
routes are:

    GET    /api/agents/{agent_id}/personas/{persona_id}/budget
    PUT    /api/agents/{agent_id}/personas/{persona_id}/budget
    POST   /api/agents/{agent_id}/personas/{persona_id}/budget/check
    POST   /api/agents/{agent_id}/personas/{persona_id}/budget/charge
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Request

from hal0.agents import budget as budget_mod
from hal0.agents import personas as personas_mod
from hal0.agents.budget import AGENTS_ROOT, Budget
from hal0.api.agents.personas import _AGENT_PERSONAS_ROOTS, _safe_error_message
from hal0.errors import BadRequest, Hal0Error, NotFound

log = structlog.get_logger(__name__)

router = APIRouter()


# ── agent ledger root registry ─────────────────────────────────────────────
#
# Mirrors :data:`hal0.api.agents.personas._AGENT_PERSONAS_ROOTS` but
# resolves the AGENT-LEVEL state directory (the personas/ subdir lives
# beneath it). Per ADR-0004 §2 + the personas module's seed-default
# convention, the canonical layout is::
#
#   /var/lib/hal0/agents/{agent_id}/personas/{persona_id}.toml
#   /var/lib/hal0/agents/{agent_id}/personas/{persona_id}/spend.jsonl
#
# Tests rewrite this dict + ``_AGENT_PERSONAS_ROOTS`` to point at a
# tmp_path; in production the dict carries the canonical AGENTS_ROOT
# entry.

_AGENT_LEDGER_ROOTS: dict[str, Path] = {
    "hermes": AGENTS_ROOT,
}


def _resolve_agent_personas_root(agent_id: str) -> Path:
    """Resolve the personas store root, raising NotFound for unknown agents.

    Reuses the same registry the personas module's CRUD routes use so a
    test that monkey-patches one rewires the other automatically as long
    as both registries are kept in sync (the test fixture below does so).
    """
    root = _AGENT_PERSONAS_ROOTS.get(agent_id)
    if root is None:
        raise NotFound(
            f"unknown agent {agent_id!r}",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )
    return root


def _resolve_ledger_root(agent_id: str) -> Path:
    """Resolve the AGENT-LEVEL state root (parent of personas/)."""
    root = _AGENT_LEDGER_ROOTS.get(agent_id)
    if root is None:
        raise NotFound(
            f"unknown agent {agent_id!r}",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )
    return root


# ── helpers ────────────────────────────────────────────────────────────────


def _load_persona_or_404(
    personas_root: Path,
    agent_id: str,
    persona_id: str,
) -> personas_mod.Persona:
    """Load + parse one persona; map errors onto the API contract.

    404 for missing files; 400 for malformed TOML / invalid budget;
    Hal0Error subclasses pass straight to the middleware.
    """
    try:
        return personas_mod.load_persona(persona_id, root=personas_root)
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


def _budget_response(
    persona: personas_mod.Persona,
    ledger: budget_mod.BudgetLedger,
) -> dict[str, Any]:
    """Compose the response shape both GET + PUT + charge return.

    Carries the configured caps, the ledger-derived running totals, and
    per-window remaining headroom. ``remaining_usd`` omits windows with
    no configured cap so the UI can iterate without branching.
    """
    stats = budget_mod.spend_stats(ledger)
    remaining: dict[str, float] = {}
    if persona.budget.daily_usd is not None:
        remaining["daily_usd"] = max(0.0, persona.budget.daily_usd - stats.today_usd)
    if persona.budget.monthly_usd is not None:
        remaining["monthly_usd"] = max(0.0, persona.budget.monthly_usd - stats.mtd_usd)
    if persona.budget.lifetime_usd is not None:
        remaining["lifetime_usd"] = max(0.0, persona.budget.lifetime_usd - stats.lifetime_usd)
    return {
        "budget": persona.budget.to_dict(),
        "spend": {
            "today_usd": stats.today_usd,
            "mtd_usd": stats.mtd_usd,
            "lifetime_usd": stats.lifetime_usd,
        },
        "remaining": remaining,
    }


def _parse_budget_body(body: dict[str, Any] | None) -> Budget:
    """Parse a PUT body into a :class:`Budget`.

    Accepts the same shape :meth:`Budget.to_dict` emits — missing keys
    mean "no cap on that window". Wraps the value-error path in a 400
    so the dashboard's editor surface gets a structured message.
    """
    if body is None:
        body = {}
    if not isinstance(body, dict):
        raise BadRequest(
            "request body must be a JSON object",
            code="budget.invalid_body",
        )
    try:
        return budget_mod.parse_budget(body)
    except ValueError as exc:
        raise BadRequest(str(exc), code="budget.invalid") from exc


# ── GET ────────────────────────────────────────────────────────────────────


@router.get("/{agent_id}/personas/{persona_id}/budget")
async def get_persona_budget(agent_id: str, persona_id: str) -> dict[str, Any]:
    """Return the configured budget + running spend stats for one persona."""
    personas_root = _resolve_agent_personas_root(agent_id)
    ledger_root = _resolve_ledger_root(agent_id)
    persona = _load_persona_or_404(personas_root, agent_id, persona_id)
    ledger = budget_mod.ledger_for(agent_id, persona_id, root=ledger_root)
    return _budget_response(persona, ledger)


# ── PUT ────────────────────────────────────────────────────────────────────


async def _read_json_body(request: Request) -> Any:
    """Read + JSON-decode the request body, accepting empty/missing bodies.

    We accept-Any-validate-ourselves because FastAPI's typed body
    declaration would reject an array body with a pydantic 422 envelope
    that doesn't match our ``budget.invalid_body`` 400 contract. Empty
    bodies (no Content-Length, or a literal ``null``) decode to ``None``
    so the handler's downstream validation can produce a structured 400.
    """
    raw = await request.body()
    if not raw:
        return None
    try:
        import json as _json

        return _json.loads(raw)
    except ValueError as exc:
        raise BadRequest(
            f"request body must be valid JSON: {exc}",
            code="budget.invalid_body",
        ) from exc


@router.put("/{agent_id}/personas/{persona_id}/budget")
async def put_persona_budget(
    agent_id: str,
    persona_id: str,
    request: Request,
) -> dict[str, Any]:
    """Replace the persona's ``[persona.budget]`` block; preserve everything else.

    The PUT mutates the persona's budget section in place — other fields
    (system prompt, tool gating, approval policy, model preference) are
    NOT touched, so a dashboard slider for the daily cap can't accidentally
    re-render the prompt block.

    Re-running ``hal0 agent reprovision hermes`` after this PUT preserves
    the operator-set budget: ``_phase_persona_seed`` calls
    :func:`seed_default_personas` with ``overwrite=False`` (the default),
    which skips existing files. Only ``--repair`` re-writes the seeds.
    """
    personas_root = _resolve_agent_personas_root(agent_id)
    ledger_root = _resolve_ledger_root(agent_id)
    body = await _read_json_body(request)
    new_budget = _parse_budget_body(body)
    persona = _load_persona_or_404(personas_root, agent_id, persona_id)
    persona.budget = new_budget
    try:
        personas_mod.save_persona(persona, root=personas_root)
    except OSError as exc:  # pragma: no cover — defensive
        raise Hal0Error(
            "failed to write persona file",
            code="persona.write_failed",
            details={"agent_id": agent_id, "persona_id": persona_id, "error": str(exc)},
        ) from exc
    ledger = budget_mod.ledger_for(agent_id, persona_id, root=ledger_root)
    log.info(
        "budget.updated",
        agent_id=agent_id,
        persona_id=persona_id,
        budget=new_budget.to_dict(),
    )
    return _budget_response(persona, ledger)


# ── POST /check ────────────────────────────────────────────────────────────


@router.post("/{agent_id}/personas/{persona_id}/budget/check")
async def check_persona_budget(
    agent_id: str,
    persona_id: str,
    request: Request,
) -> dict[str, Any]:
    """Dry-run pre-call gate: does ``estimated_cost_usd`` fit in budget?

    Body shape::

        {"estimated_cost_usd": float}

    Returns the :class:`BudgetCheck` shape — ``allowed`` (bool),
    ``reason`` (string or null), ``remaining_usd`` (per-window
    headroom after subtracting the estimate). The V1 OpenRouter
    provider calls this BEFORE issuing the upstream request; a
    ``False`` ``allowed`` short-circuits with a structured envelope.
    """
    body = await _read_json_body(request)
    if not isinstance(body, dict):
        raise BadRequest(
            "request body must be a JSON object",
            code="budget.invalid_body",
        )
    raw = body.get("estimated_cost_usd")
    if not isinstance(raw, int | float) or isinstance(raw, bool):
        raise BadRequest(
            "estimated_cost_usd must be a number",
            code="budget.invalid_estimate",
        )
    estimate = float(raw)
    if estimate < 0:
        raise BadRequest(
            "estimated_cost_usd must be >= 0",
            code="budget.invalid_estimate",
        )

    personas_root = _resolve_agent_personas_root(agent_id)
    ledger_root = _resolve_ledger_root(agent_id)
    persona = _load_persona_or_404(personas_root, agent_id, persona_id)
    ledger = budget_mod.ledger_for(agent_id, persona_id, root=ledger_root)
    result = budget_mod.check_budget(persona.budget, ledger, estimate)
    return {
        "allowed": result.allowed,
        "reason": result.reason,
        "remaining_usd": result.remaining_usd,
        "hard_cap": persona.budget.hard_cap,
    }


# ── POST /charge ───────────────────────────────────────────────────────────


@router.post("/{agent_id}/personas/{persona_id}/budget/charge")
async def charge_persona_budget(
    agent_id: str,
    persona_id: str,
    request: Request,
) -> dict[str, Any]:
    """Post-response charge recorder.

    Body shape::

        {
          "surface": "openrouter",
          "model": "anthropic/claude-3.7-sonnet",
          "cost_usd": 0.0421,
          "request_id": "req_abc123"
        }

    Caller invokes this AFTER the upstream returns; we append a row to
    the spend ledger + return the updated remaining-headroom map so the
    caller can decide whether to short-circuit subsequent calls in the
    same session without re-reading the budget.
    """
    body = await _read_json_body(request)
    if not isinstance(body, dict):
        raise BadRequest(
            "request body must be a JSON object",
            code="budget.invalid_body",
        )
    surface = body.get("surface")
    model = body.get("model")
    cost_raw = body.get("cost_usd")
    request_id = body.get("request_id")

    if not isinstance(surface, str) or not surface.strip():
        raise BadRequest("'surface' must be a non-empty string", code="budget.invalid_charge")
    if not isinstance(model, str) or not model.strip():
        raise BadRequest("'model' must be a non-empty string", code="budget.invalid_charge")
    if not isinstance(cost_raw, int | float) or isinstance(cost_raw, bool):
        raise BadRequest("'cost_usd' must be a number", code="budget.invalid_charge")
    cost = float(cost_raw)
    if cost < 0:
        raise BadRequest("'cost_usd' must be >= 0", code="budget.invalid_charge")
    if not isinstance(request_id, str) or not request_id.strip():
        raise BadRequest("'request_id' must be a non-empty string", code="budget.invalid_charge")

    personas_root = _resolve_agent_personas_root(agent_id)
    ledger_root = _resolve_ledger_root(agent_id)
    persona = _load_persona_or_404(personas_root, agent_id, persona_id)
    ledger = budget_mod.ledger_for(agent_id, persona_id, root=ledger_root)
    row = budget_mod.record_charge(
        ledger,
        persona_id=persona.id,
        surface=surface,
        model=model,
        cost_usd=cost,
        request_id=request_id,
    )
    response = _budget_response(persona, ledger)
    response["recorded"] = True
    response["row"] = {
        "ts": row.ts.isoformat(),
        "surface": row.surface,
        "model": row.model,
        "cost_usd": row.cost_usd,
        "request_id": row.request_id,
    }
    return response
