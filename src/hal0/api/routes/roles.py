"""Runtime role-slot resolution endpoint (mounted under ``/api/agents``).

Finding #2 of the Hermes integration suite design. Auxiliary-role→slot
resolution used to run only at provision time, freezing role assignments
into static Hermes config. This route promotes that policy to a live hal0
contract the provider queries at runtime::

    GET /api/agents/{agent_id}/role-slots
        → {
            "agent_id": str,
            "generation": str,          # content-addressed stamp; changes iff the map changes
            "base_url": str,            # hal0 /v1 gateway the provider dials
            "epoch": str,               # per-process event epoch (footer/stream reset detect)
            "event_cursor": int,        # newest event id at resolve time — open the stream here
            "roles": [RoleSlot, ...],   # role → {slot, slot_id, model, alias, ready, capabilities, …}
            "slots_source": "live" | "unavailable",
          }

The resolution policy itself lives in :mod:`hal0.agents.role_resolution`,
shared verbatim with the provisioner so both resolve roles identically —
this route never reimplements role policy.

Invalidation
------------
The provider follows the design's invalidate-and-refetch protocol: it
subscribes to ``GET /api/events/stream?since=<cursor>`` and refetches this
map on slot create/delete, model swap, capability change, or readiness
transition (the ``slot.*`` events the slot state machine already emits),
comparing ``generation`` to decide whether anything actually moved. As a
targeted signal, this route additionally emits one ``agent.role_slots.changed``
event whenever a resolve observes a new generation for an agent, so a
consumer watching the ``agent.*`` glob learns about role-map churn without
inferring it from lower-level slot events.

Auth: classified CLIENT (read-only, provider-facing) in
:mod:`hal0.security.exposure` — the provider authenticates with the same
inference-client token it uses for ``/v1`` and ``/api/models``, not an
admin credential. All mutating ``/api/agents`` routes stay ADMIN.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

from fastapi import APIRouter, Request

from hal0.agents import role_resolution
from hal0.agents.manager import BUNDLED_AGENTS, AgentManager
from hal0.api.middleware.error_codes import Hal0Error
from hal0.errors import NotFound
from hal0.slot_view import config_enrichment, serialize_slot

router = APIRouter()


def _gateway_base_url() -> str:
    """The hal0 OpenAI-compat gateway ``/v1`` base the provider should dial.

    Mirrors ``hermes_provision``'s live-resolve target: the gateway (not a
    concrete per-slot backend port), so a model swap behind an alias never
    rewrites the URL the provider holds.
    """
    root = os.environ.get("HAL0_API_URL", "http://127.0.0.1:8080").rstrip("/")
    return f"{root}/v1"


def _known_agent(agent_id: str) -> bool:
    """True when ``agent_id`` is a recognised bundled/installed agent.

    Mirrors the ``/api/agents/{name}/activity`` guard: an unknown id 404s
    rather than silently returning an all-degraded map for a name that was
    never a real agent.
    """
    try:
        mgr = AgentManager()
        if agent_id in mgr.installed_names():
            return True
    except Exception:
        # Manager unavailable (odd entrypoint / test) — fall back to the
        # static bundled catalogue so the endpoint still resolves.
        pass
    return agent_id in BUNDLED_AGENTS


async def _live_slot_payloads(request: Request) -> tuple[list[dict[str, Any]], bool]:
    """Build the ``/api/slots``-shaped slot dicts the resolver consumes.

    Reuses :func:`serialize_slot` + :func:`config_enrichment` (the same
    enrichment the ``/api/slots`` aggregator applies) so role resolution
    sees exactly the fields the provision path sees — ``type`` / ``device``
    / ``state`` / ``model_id`` / ``id`` / ``labels`` — WITHOUT the heavy
    container/metrics/memory probes the full aggregator runs.

    Returns ``(payloads, live)`` where ``live`` is ``False`` when the slot
    manager could not be read, so the caller can mark the map degraded
    instead of masquerading "couldn't ask" as "no slots".
    """
    sm = getattr(request.app.state, "slot_manager", None)
    if sm is None:
        return [], False

    try:
        real = list(await sm.list())
    except Exception:
        return [], False

    model_cache = getattr(request.app.state, "model_cache", {}) or {}
    payloads = [serialize_slot(s, model_cache=model_cache) for s in real]

    try:
        configs = list(await sm.iter_configs())
    except Exception:
        configs = []

    enrichment = config_enrichment(configs)
    cfg_by_name = {str(c.get("name", "")): c for c in configs if isinstance(c, dict)}
    for payload in payloads:
        name = str(payload.get("name", ""))
        extra = enrichment.get(name)
        if extra:
            for key, value in extra.items():
                payload.setdefault(key, value)
        # ``config_enrichment`` surfaces ``type`` but not the raw ``device``
        # the NPU-utility fallback keys off — lift it straight from config.
        cfg = cfg_by_name.get(name) or {}
        device = cfg.get("device")
        if device and "device" not in payload:
            payload["device"] = device
    return payloads, True


def _event_cursor(request: Request) -> int:
    """Newest event id currently in the bus ring, or 0.

    The provider opens ``/api/events/stream?since=<cursor>`` at this value so
    it starts tailing exactly where the fetched map's inventory stood.
    """
    bus = getattr(request.app.state, "events", None)
    ring = getattr(bus, "ring", None)
    if ring:
        try:
            return int(ring[-1]["id"])
        except (KeyError, TypeError, ValueError, IndexError):
            return 0
    return 0


async def _emit_on_change(request: Request, agent_id: str, generation: str) -> None:
    """Emit ``agent.role_slots.changed`` when this resolve sees a new generation.

    Per-process, per-agent last-seen generation is tracked on ``app.state``.
    Emission is best-effort: a missing bus (first-run before lifespan wiring)
    or an emit failure never breaks the read.
    """
    state = request.app.state
    seen: dict[str, str] = getattr(state, "role_slot_generations", None)
    if seen is None:
        seen = {}
        state.role_slot_generations = seen

    previous = seen.get(agent_id)
    seen[agent_id] = generation
    if previous == generation:
        return

    bus = getattr(state, "events", None)
    if bus is None:
        return
    # An event-bus hiccup must not fail the authoritative read.
    with contextlib.suppress(Exception):
        await bus.emit(
            "agent.role_slots.changed",
            "info",
            "roles",
            f"role-slot map for {agent_id} changed",
            data={"agent_id": agent_id, "generation": generation, "previous": previous},
        )


@router.get("/{agent_id}/role-slots")
async def get_role_slots(agent_id: str, request: Request) -> dict[str, Any]:
    """Resolve the current role→slot map for ``agent_id`` at runtime.

    Generation-stamped: the ``generation`` field is a content hash of the
    resolution, so the provider can compare it after an invalidating event
    to decide whether a refetch actually moved anything.
    """
    if not _known_agent(agent_id):
        raise NotFound(f"unknown agent {agent_id!r}", code="agent.unknown")

    base_url = _gateway_base_url()
    try:
        payloads, live = await _live_slot_payloads(request)
    except Hal0Error:
        raise
    except Exception:  # defensive: never 500 a read the provider polls
        payloads, live = [], False

    entries = role_resolution.resolve_role_slots(payloads, base_url=base_url)
    generation = role_resolution.generation_of(entries)
    await _emit_on_change(request, agent_id, generation)

    return {
        "agent_id": agent_id,
        "generation": generation,
        "base_url": base_url,
        "epoch": getattr(request.app.state, "audit_epoch", ""),
        "event_cursor": _event_cursor(request),
        "roles": [e.as_dict() for e in entries],
        "slots_source": "live" if live else "unavailable",
    }
