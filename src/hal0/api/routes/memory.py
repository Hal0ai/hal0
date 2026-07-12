"""Memory endpoints — graph-extraction gate + status.

Mounted under ``/api/memory/*``. The dashboard's Memory tab + the
``hal0 memory graph {enable,disable,status}`` CLI both read + write
through this surface; there is no other writer for ``[memory.graph]``
so a swap-flip from either client lands atomically through the same
``save_hal0_config`` pipeline.

The actual graph-extraction dispatch lives in the active memory provider
(:class:`hal0.memory.MemoryProvider`); this module is the thin HTTP
veneer that:

  - Returns ``graph_status()`` (enabled / route / counters / last-built).
  - Validates the toggle payload against :class:`MemoryGraphConfig`.
  - Persists to ``hal0.toml`` via the existing atomic writer.
  - Flips the live wrapper so callers don't need a restart.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api._audit import record_action
from hal0.api.middleware.error_codes import BadRequest, Conflict, Hal0Error
from hal0.config.loader import load_hal0_config, save_hal0_config
from hal0.config.schema import MemoryGraphConfig
from hal0.memory.hindsight_client import DEFAULT_BASE_URL as _HINDSIGHT_BASE_URL
from hal0.memory.hindsight_provider import bank_to_namespace
from hal0.memory.namespace import (
    DEFAULT_DATASET,
    MemoryNamespaceError,
    resolve_read_datasets,
    resolve_write_dataset,
)

router = APIRouter()

log = logging.getLogger(__name__)


# ── identity + namespace helpers ────────────────────────────────────────
#
# Auth was removed, so hal0-api is open on 0.0.0.0:8080; agent identity
# flows on the ``X-hal0-Agent`` header (NOT Bearer — auth surface was
# removed).
# Private-mode opt-in flows on ``X-hal0-Private`` to match the MCP mount
# (:mod:`hal0.api.mcp_mount`); the same toggle gates the same namespace
# promotion rule across both surfaces (issue #317).


_AGENT_HEADER = "x-hal0-agent"
_PRIVATE_HEADER = "x-hal0-private"
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# Security hardening: agent identity feeds the
# ``private:<agent>`` dataset name AND the audit log's ``source``
# field. We allow alnum + ``-`` + ``_`` only, up to 64 chars — keeps
# the resolved namespace path-traversal-free, sql-quotable, and
# bounded. Matches the convention used by other hal0 identity headers
# (slot names, capability ids).
_AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class MemoryNamespaceInvalid(Hal0Error):
    """The caller's headers + body produced an unresolvable namespace.

    Distinct from a body-shape error so the dashboard can paint a
    different toast ("you asked for private without an agent identity")
    vs a generic 400.
    """

    code = "memory.namespace_invalid"
    status = 400


class MemoryAgentIdInvalid(Hal0Error):
    """The ``X-hal0-Agent`` header value failed the
    identity-shape check.

    Distinct from :class:`MemoryNamespaceInvalid` so the dashboard
    can render a focused message ("agent id must be alnum/-/_, ≤64
    chars, no ``private:`` prefix") rather than a generic namespace
    error.
    """

    code = "memory.agent_id_invalid"
    status = 400


def _agent_id(request: Request) -> str:
    """Return the validated ``X-hal0-Agent`` value or ``"anonymous"``.

    Mirrors :func:`hal0.api.mcp_mount.client_id_resolver` for the REST
    surface — both translate the absence of an identity header into the
    same sentinel so audit + dataset resolution stay consistent.

    Validation (hardening surfaced by PR #366 review):

      - Empty / whitespace → ``"anonymous"`` (back-compat with
        unauthenticated callers).
      - Values starting with ``private:`` are REJECTED so a caller
        cannot manufacture ``private:private:bob`` by smuggling the
        prefix through the header. The ``private`` toggle is the
        only path to the namespace.
      - Values must match ``^[a-zA-Z0-9_\\-]{1,64}$`` — agent ids
        flow into the memory dataset name + the audit log's
        ``source`` field. Path-traversal candidates (``../etc``),
        control chars, and over-long values are all rejected here.
    """
    raw = request.headers.get(_AGENT_HEADER)
    if raw is None:
        return "anonymous"
    candidate = raw.strip()
    if not candidate:
        return "anonymous"
    if candidate.startswith("private:"):
        raise MemoryAgentIdInvalid(
            "X-hal0-Agent must not be prefixed with 'private:' — the "
            "private namespace is reached via X-hal0-Private: 1, not by "
            "embedding the prefix in the identity header",
            details={"header": "X-hal0-Agent"},
        )
    if not _AGENT_ID_PATTERN.match(candidate):
        raise MemoryAgentIdInvalid(
            "X-hal0-Agent must match [a-zA-Z0-9_-]{1,64}",
            details={"header": "X-hal0-Agent"},
        )
    return candidate


def _is_private(request: Request) -> bool:
    """Return whether the caller opted into ``--private`` mode."""
    raw = request.headers.get(_PRIVATE_HEADER, "")
    return raw.strip().lower() in _TRUTHY


class MemoryGraphConfigInvalid(Hal0Error):
    """Schema validation failure for ``[memory.graph]``."""

    code = "config.memory_graph_invalid"
    status = 400


class MemoryGraphSlotInvalid(Hal0Error):
    """Enable rejected: ``extraction_slot`` is not an enabled llm slot.

    ADR-0023 — graph extraction is dispatched to a local llm slot. A slot that
    doesn't exist (or isn't type=llm/enabled) is rejected with the list of valid
    slots so the dashboard + CLI can fail fast without flipping the gate on.
    """

    code = "config.memory_graph_slot_invalid"
    status = 422


class MemoryUnavailable(Hal0Error):
    """The memory engine failed to initialise at boot.

    Returned when the API got far enough to mount the router but the
    underlying memory engine isn't usable — e.g. the Hindsight daemon is
    unreachable on a stripped-down install. Letting this surface as a 503
    instead of a generic 500 means the dashboard can paint a clear
    "Memory engine unavailable" state rather than a red toast.
    """

    code = "memory.unavailable"
    status = 503


async def _enabled_llm_slots(request: Request) -> list[str]:
    """Return the names of enabled ``type=llm`` slots (valid extraction targets)."""
    slot_manager = getattr(request.app.state, "slot_manager", None)
    if slot_manager is None:
        return []
    from hal0.api import hal0_chat_slot_alias_map

    try:
        alias_map = await hal0_chat_slot_alias_map(slot_manager)
    except Exception:
        return []
    return sorted(alias_map.keys())


def _wrapper(request: Request) -> Any:
    """Return the live memory provider or raise 503."""
    wrapper = getattr(request.app.state, "memory_provider", None)
    if wrapper is None:
        raise MemoryUnavailable("memory engine is not available on this hal0 instance")
    return wrapper


def _validation_error_details(exc: ValidationError) -> dict[str, str]:
    out: dict[str, str] = {}
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ()))
        out[loc or "<root>"] = err.get("msg", "invalid")
    return out


# ── GET /api/memory/graph/status ───────────────────────────────────────────


@router.get("/graph/status")
async def graph_status(request: Request) -> dict[str, Any]:
    """Return live graph-extraction state (ADR-0023).

    Response shape (stable contract — the dashboard depends on every
    key being present)::

        {
          "enabled":         bool,
          "extraction_slot": str,            # the local llm slot used for extraction
          "route":           str,            # deprecated mirror of extraction_slot
          "slot_resolves":   bool,           # does extraction_slot match an enabled llm slot?
          "available_slots": [str, ...],     # enabled llm slots the operator can pick
          "in_flight":       int,
          "builds_ok":       int,
          "errors":          int,
          "last_built_at":   iso8601 | None,
          "last_error":      str | None,
        }
    """
    wrapper = _wrapper(request)
    status = wrapper.graph_status()
    available = await _enabled_llm_slots(request)
    status["available_slots"] = available
    status["slot_resolves"] = status.get("extraction_slot") in available
    # llm_timeout_s lives in hal0.toml (not on the provider) — echo it so the
    # dashboard's graph panel can edit it without a second config fetch.
    status["llm_timeout_s"] = load_hal0_config().memory.graph.llm_timeout_s
    await _augment_build_counters(request, status)
    return status


async def _augment_build_counters(request: Request, status: dict[str, Any]) -> None:
    """Replace the provider's placeholder ``0``/``None`` build counters with
    real extraction/consolidation activity aggregated from Hindsight's
    per-bank ``/stats`` (``operations_by_status``, ``pending_operations``,
    ``failed_operations``, ``last_consolidated_at``).

    Extraction runs inside the Hindsight daemon, so hal0 keeps no in-process
    counter — we read it back. Best-effort: on any error the provider's
    placeholder values are left intact, so the endpoint never fails.
    """
    provider = getattr(request.app.state, "memory_provider", None)
    client = getattr(provider, "hindsight_client", None) if provider is not None else None
    if client is None:
        return

    async def _get(path: str) -> Any | None:
        try:
            return await client.request_json("GET", path)
        except Exception:
            return None

    banks_resp = await _get("/v1/default/banks")
    banks = banks_resp.get("banks") if isinstance(banks_resp, dict) else banks_resp
    if not isinstance(banks, list):
        return

    in_flight = builds_ok = errors = 0
    last_built: str | None = None
    saw_stats = False
    for entry in banks:
        bank_id = entry.get("bank_id") if isinstance(entry, dict) else entry
        if not bank_id:
            continue
        st = await _get(f"/v1/default/banks/{bank_id}/stats")
        if not isinstance(st, dict):
            continue
        saw_stats = True
        by_status = st.get("operations_by_status") or {}
        in_flight += int(st.get("pending_operations") or 0)
        in_flight += int(by_status.get("processing") or 0) + int(by_status.get("claimed") or 0)
        builds_ok += int(by_status.get("completed") or 0)
        errors += int(st.get("failed_operations") or by_status.get("failed") or 0)
        built = st.get("last_consolidated_at")
        if built and (last_built is None or built > last_built):
            last_built = built

    if saw_stats:
        status["in_flight"] = in_flight
        status["builds_ok"] = builds_ok
        status["errors"] = errors
        status["last_built_at"] = last_built


# ── POST /api/memory/graph/retry ────────────────────────────────────────────


async def _bank_failed_op_ids(client: Any, bank_id: str, *, page: int, cap: int) -> list[str]:
    """Page the Hindsight ledger for ``bank_id`` and collect failed op ids.

    The list endpoint caps ``limit`` (empties out above ~100), so we walk it
    in ``page``-sized windows up to ``cap`` (a backstop against a runaway
    ledger). Best-effort: a page that fails to fetch ends the walk.
    """
    ids: list[str] = []
    offset = 0
    while offset < cap:
        try:
            resp = await client.request_json(
                "GET", f"/v1/default/banks/{bank_id}/operations?limit={page}&offset={offset}"
            )
        except Exception:
            break
        ops = resp.get("operations") if isinstance(resp, dict) else None
        if not ops:
            break
        ids += [o["id"] for o in ops if str(o.get("status")).lower() == "failed" and o.get("id")]
        if len(ops) < page:
            break
        offset += page
    return ids


@router.post("/graph/retry")
async def retry_failed_extractions(request: Request) -> dict[str, Any]:
    """Requeue every failed extraction/consolidation operation across banks.

    Graph extraction runs inside the Hindsight daemon; when the extraction
    slot is mis-pointed (ADR-0023) the ops pile up as ``failed``. Once the
    slot resolves again, this re-runs them (failed→completed) — rebuilding the
    graph for those memories and clearing the health panel's error count.

    Best-effort and idempotent: each failed op is re-POSTed to Hindsight's
    ``/operations/{id}/retry``; ops it declines (already running / no payload)
    count as ``skipped``. Returns a per-bank tally so the dashboard can toast
    ``N requeued``.
    """
    import asyncio

    provider = getattr(request.app.state, "memory_provider", None)
    client = getattr(provider, "hindsight_client", None) if provider is not None else None
    if client is None:
        raise MemoryUnavailable("memory engine is not available on this hal0 instance")

    _PAGE = 100
    _CAP = 2000  # backstop; far above any real failed-op count

    banks_resp = None
    try:
        banks_resp = await client.request_json("GET", "/v1/default/banks")
    except Exception as exc:
        raise MemoryUnavailable("could not enumerate memory banks") from exc
    banks = banks_resp.get("banks") if isinstance(banks_resp, dict) else banks_resp
    if not isinstance(banks, list):
        raise MemoryUnavailable("could not enumerate memory banks")

    async def _retry_one(bank_id: str, op_id: str) -> bool:
        try:
            res = await client.request_json(
                "POST", f"/v1/default/banks/{bank_id}/operations/{op_id}/retry"
            )
        except Exception:
            return False
        # Hindsight replies {success: true, ...}; treat a 2xx with no explicit
        # success flag as queued too.
        return not isinstance(res, dict) or bool(res.get("success", True))

    per_bank: dict[str, dict[str, int]] = {}
    total_queued = total_skipped = 0

    async with record_action(
        request, category="memory", action="memory.graph.retry_failed", target="*"
    ) as rec:
        for entry in banks:
            bank_id = entry.get("bank_id") if isinstance(entry, dict) else entry
            if not bank_id:
                continue
            failed_ids = await _bank_failed_op_ids(client, bank_id, page=_PAGE, cap=_CAP)
            queued = skipped = 0
            # Bounded concurrency: the retry POST only requeues (cheap); the
            # heavy extraction runs later in the Hindsight worker pool.
            for i in range(0, len(failed_ids), 10):
                chunk = failed_ids[i : i + 10]
                for ok in await asyncio.gather(*[_retry_one(bank_id, x) for x in chunk]):
                    if ok:
                        queued += 1
                    else:
                        skipped += 1
            per_bank[bank_id] = {"queued": queued, "skipped": skipped, "failed": len(failed_ids)}
            total_queued += queued
            total_skipped += skipped
        rec.after = {"queued": total_queued, "skipped": total_skipped}

    return {"queued": total_queued, "skipped": total_skipped, "banks": per_bank}


# ── PUT /api/memory/graph ──────────────────────────────────────────────────


@router.put("/graph")
async def update_graph_config(request: Request) -> dict[str, Any]:
    """Replace the ``[memory.graph]`` section (ADR-0023).

    Body shape: any subset of :class:`MemoryGraphConfig` fields
    (``enabled``, ``extraction_slot``). The merge preserves un-set fields
    (PATCH-style "flip enabled but keep the slot") because dashboards
    typically send the delta, not the whole block.

    When ``extraction_slot`` changes, it is validated against the live
    enabled-llm-slot set and propagated to the hindsight-api service (via a
    systemd drop-in + restart) so the engine's native extraction LLM follows
    the operator's choice. On success persists ``hal0.toml`` atomically and
    flips the live wrapper's reported state.
    """
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error("request body must be valid JSON", details={"error": str(exc)}) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")

    wrapper = _wrapper(request)
    cfg = load_hal0_config()
    current_raw = cfg.memory.graph.model_dump(mode="python")
    merged_raw = {**current_raw, **body}

    try:
        new_cfg = MemoryGraphConfig.model_validate(merged_raw)
    except ValidationError as exc:
        raise MemoryGraphConfigInvalid(
            "memory.graph config failed schema validation",
            details=_validation_error_details(exc),
        ) from exc

    # Validate extraction_slot against the live slot set when it is being
    # changed — reject an unknown / non-llm slot with the valid options so the
    # gate never flips onto a target that can't serve extraction.
    slot_changed = new_cfg.extraction_slot != cfg.memory.graph.extraction_slot
    if slot_changed:
        available = await _enabled_llm_slots(request)
        if available and new_cfg.extraction_slot not in available:
            raise MemoryGraphSlotInvalid(
                f"extraction_slot {new_cfg.extraction_slot!r} is not an enabled llm slot",
                details={"available_slots": ", ".join(available)},
            )

    # Flip the live wrapper's reported state BEFORE persisting.
    try:
        wrapper.set_graph_enabled(new_cfg.enabled, extraction_slot=new_cfg.extraction_slot)
    except ValueError as exc:
        raise MemoryGraphConfigInvalid(str(exc)) from exc

    # Propagate the extraction slot + LLM timeout to hindsight-api (drop-in +
    # restart) so the engine's native extraction LLM follows the choice.
    # Best-effort: a restart failure is surfaced in the response but does not
    # roll back the config.
    timeout_changed = new_cfg.llm_timeout_s != cfg.memory.graph.llm_timeout_s
    propagation: dict[str, Any] | None = None
    if slot_changed or timeout_changed:
        from hal0.memory.extraction_env import apply_extraction_slot

        propagation = apply_extraction_slot(
            new_cfg.extraction_slot, timeout_s=new_cfg.llm_timeout_s
        )

    cfg.memory.graph = new_cfg
    try:
        save_hal0_config(cfg)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist hal0 config: {exc}",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    out = new_cfg.model_dump(mode="json")
    # Echo the live status so the dashboard's optimistic-update path
    # gets the counters in the same round trip without a second fetch.
    out["status"] = wrapper.graph_status()
    if propagation is not None:
        out["propagation"] = propagation
    return out


# ── GET /api/memory/provider, PUT /api/memory/provider ─────────────────────
#
# Per-agent memory-provider routing (hindsight | honcho). Distinct from
# ``[memory.graph]`` above: that gate controls Hindsight's own extraction
# LLM, this controls *which engine* an agent's Hermes memory plugin talks
# to at all. See ``hal0.memory.honcho_migrate`` for the backfill/sync jobs
# that move data between the two once an agent is switched.

_HEALTH_PROBE_TIMEOUT = 1.0

#: systemd unit laid down only when the operator opts into Honcho at
#: install time (``HAL0_INSTALL_HONCHO=1`` — see installer/install.sh). Its
#: absence means the compose stack was never provisioned on this box, not
#: merely stopped, so the remediation for those two cases must differ.
_HONCHO_UNIT_PATH = Path("/etc/systemd/system/hal0-honcho.service")


class MemoryProviderInvalid(Hal0Error):
    """Unknown ``provider`` value in a ``PUT /api/memory/provider`` body."""

    code = "memory.provider_invalid"
    status = 400


class MemoryProviderUnavailable(Conflict):
    """The requested engine (honcho) failed its health probe.

    Rejecting the switch here — rather than persisting a provider the
    agent can't actually reach — keeps ``memory.agent_providers`` honest:
    an entry in that map always names a *live* engine, not an aspirational
    one. The 409 body carries the remediation the CLI/dashboard print.
    """

    code = "memory.provider_unavailable"


async def _probe_health(url: str) -> bool:
    """Best-effort GET ``url``; True on any 2xx, False on anything else."""
    try:
        async with httpx.AsyncClient(timeout=_HEALTH_PROBE_TIMEOUT) as client:
            resp = await client.get(url)
        return resp.status_code < 300
    except Exception:
        return False


@router.get("/provider")
async def get_memory_provider() -> dict[str, Any]:
    """Report engine health + the live per-agent provider map.

    Response shape::

        {
          "engines": {
            "hindsight": {"healthy": bool, "url": str},
            "honcho":    {"healthy": bool, "url": str},
          },
          "agents": {"<agent_id>": {"provider": str, "private": bool}, ...},
        }
    """
    cfg = load_hal0_config()
    honcho_url = f"http://127.0.0.1:{cfg.honcho.port}"
    hindsight_healthy, honcho_healthy = True, True
    try:
        hindsight_healthy = await _probe_health(f"{_HINDSIGHT_BASE_URL}/health")
    except Exception:
        hindsight_healthy = False
    try:
        honcho_healthy = await _probe_health(f"{honcho_url}/health")
    except Exception:
        honcho_healthy = False

    agents: dict[str, Any] = {}
    for agent_id, provider in cfg.memory.agent_providers.items():
        agents[agent_id] = {
            "provider": provider,
            "private": bool(cfg.memory.agent_private.get(agent_id, False)),
        }

    return {
        "engines": {
            "hindsight": {"healthy": hindsight_healthy, "url": _HINDSIGHT_BASE_URL},
            "honcho": {"healthy": honcho_healthy, "url": honcho_url},
        },
        "agents": agents,
    }


@router.put("/provider")
async def set_memory_provider(request: Request) -> dict[str, Any]:
    """Route ``agent`` to ``provider``, persist, and best-effort restart its gateway.

    Body: ``{"agent": str, "provider": "hindsight"|"honcho", "private": bool|None,
    "restart": bool}`` (``restart`` defaults True). ``private`` is only
    stored when explicitly supplied — omitting it leaves any existing
    ``agent_private`` entry untouched.

    When ``provider == "honcho"`` the Honcho engine is health-probed first;
    an unreachable engine raises 409 with a remediation message rather than
    persisting a provider the agent can't use.

    On success the agent's gateway unit (``hal0-agent@<agent>.service``) is
    restarted so the new provider takes effect immediately, unless
    ``restart: false`` was requested. Restart is best-effort: failure is
    reported in the response, not raised, since the config write already
    succeeded and a stale-until-restart agent is recoverable by hand
    (``systemctl restart`` or ``hal0 agent bootstrap <agent> --repair``).
    """
    body = await _read_json_body(request)
    agent = body.get("agent")
    provider = body.get("provider")
    if not isinstance(agent, str) or not agent:
        raise BadRequest("'agent' is required", details={"path": "/api/memory/provider"})
    if not _AGENT_ID_PATTERN.match(agent):
        raise BadRequest(
            "'agent' must match [a-zA-Z0-9_-]{1,64}", details={"path": "/api/memory/provider"}
        )
    if provider not in ("hindsight", "honcho"):
        raise MemoryProviderInvalid(
            f"'provider' must be 'hindsight' or 'honcho', got {provider!r}",
            details={"path": "/api/memory/provider"},
        )

    cfg = load_hal0_config()

    if provider == "honcho":
        honcho_url = f"http://127.0.0.1:{cfg.honcho.port}"
        if not await _probe_health(f"{honcho_url}/health"):
            provisioned = _HONCHO_UNIT_PATH.exists()
            if provisioned:
                remediation = (
                    "start it (hal0 services start honcho) or enable it first "
                    "(hal0.toml [honcho] enabled = true)"
                )
            else:
                # Honcho is opt-in; a default install never lays down the
                # unit, so "services start" would just fail with "Unit not
                # found". Point at the real provisioning path instead.
                remediation = (
                    "it has never been provisioned on this box (Honcho is "
                    "opt-in) — re-run the installer with "
                    "HAL0_INSTALL_HONCHO=1 to provision the hal0-honcho "
                    "service, then retry"
                )
            raise MemoryProviderUnavailable(
                f"honcho engine is not reachable at {honcho_url} — {remediation} "
                "before routing an agent to it",
                details={"url": honcho_url, "provisioned": provisioned},
            )

    cfg.memory.agent_providers[agent] = provider
    private = body.get("private")
    if private is not None:
        cfg.memory.agent_private[agent] = bool(private)

    try:
        save_hal0_config(cfg)
    except OSError as exc:
        raise Hal0Error(
            f"could not persist hal0 config: {exc}",
            details={"error": str(exc), "errno": getattr(exc, "errno", None)},
        ) from exc

    restarted = False
    note = None
    if body.get("restart", True):
        from hal0.services.systemd import unit_action

        unit = f"hal0-agent@{agent}.service"
        try:
            result = await unit_action(unit, "restart")
            restarted = bool(result.get("ok"))
            if not restarted:
                note = str(result.get("message"))
        except ValueError as exc:
            note = str(exc)
    else:
        note = f"restart skipped — run: hal0 agent bootstrap {agent} --repair"

    return {
        "agent": agent,
        "provider": provider,
        "private": bool(cfg.memory.agent_private.get(agent, False)),
        "restarted": restarted,
        "provisioned": restarted,
        "note": note,
    }


# ── GET /api/memory/honcho/stats, GET/PUT /api/memory/honcho/sync ─────────
#
# Observability + control surface for the self-hosted Honcho v3 stack,
# powering the dashboard's "Honcho" provider card. Distinct from
# ``/provider`` above: that endpoint reports per-agent routing + a plain
# health probe of both engines; this one drills into Honcho itself (peer/
# conclusion counts, deriver queue depth) and the recurring graph-sync job
# defined in :mod:`hal0.memory.honcho_migrate` / ``hal0 memory sync-graph``.

_HONCHO_SYNC_TIMER = "hal0-honcho-sync.timer"
_HONCHO_SYNC_SERVICE = "hal0-honcho-sync.service"
_HONCHO_STATS_TIMEOUT = 5.0


async def _honcho_probe_json(
    client: httpx.AsyncClient, method: str, path: str, **kwargs: Any
) -> Any | None:
    """Best-effort JSON call against the Honcho API; ``None`` on any failure."""
    try:
        resp = await client.request(method, path, **kwargs)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


@router.get("/honcho/stats")
async def honcho_stats() -> dict[str, Any]:
    """Fail-soft Honcho engine aggregate — powers the dashboard's Honcho card.

    Response shape (stable contract, every key always present)::

        {
          "enabled":            bool,       # [honcho].enabled in hal0.toml
          "reachable":          bool,       # /health answered 2xx
          "version":            "3.0.11" | None,
          "url":                "http://127.0.0.1:8000",
          "workspace":          "hal0",
          "peers":              int | None,
          "observations":       int | None,  # conclusions with level="explicit"
                                              # (directly extracted from messages)
          "conclusions":        int | None,  # conclusions with level in
                                              # {deductive, inductive, contradiction}
                                              # (produced by dreaming/reasoning)
          "deriver_pending":    int | None,
          "deriver_processing": int | None,
        }

    Unreachable → every count stays ``None`` and the endpoint still returns
    HTTP 200 (mirrors ``GET /api/memory/engine`` in memory_admin.py) so the
    dashboard always has a card to paint.
    """
    cfg = load_hal0_config()
    honcho = cfg.honcho
    url = f"http://127.0.0.1:{honcho.port}"

    out: dict[str, Any] = {
        "enabled": honcho.enabled,
        "reachable": False,
        "version": None,
        "url": url,
        "workspace": honcho.workspace,
        "peers": None,
        "observations": None,
        "conclusions": None,
        "deriver_pending": None,
        "deriver_processing": None,
    }

    out["reachable"] = await _probe_health(f"{url}/health")
    if not out["reachable"]:
        return out

    import asyncio

    ws = honcho.workspace
    async with httpx.AsyncClient(base_url=url, timeout=_HONCHO_STATS_TIMEOUT) as client:
        openapi, peers, all_conclusions, explicit_conclusions, queue = await asyncio.gather(
            _honcho_probe_json(client, "GET", "/openapi.json"),
            _honcho_probe_json(
                client, "POST", f"/v3/workspaces/{ws}/peers/list", json={"filters": None}
            ),
            _honcho_probe_json(
                client,
                "POST",
                f"/v3/workspaces/{ws}/conclusions/list",
                params={"page": 1, "size": 1},
                json={"filters": None},
            ),
            _honcho_probe_json(
                client,
                "POST",
                f"/v3/workspaces/{ws}/conclusions/list",
                params={"page": 1, "size": 1},
                json={"filters": {"level": "explicit"}},
            ),
            _honcho_probe_json(client, "GET", f"/v3/workspaces/{ws}/queue/status"),
        )

    if isinstance(openapi, dict):
        out["version"] = (openapi.get("info") or {}).get("version")
    if isinstance(peers, dict):
        out["peers"] = peers.get("total")

    explicit_total = (
        explicit_conclusions.get("total") if isinstance(explicit_conclusions, dict) else None
    )
    all_total = all_conclusions.get("total") if isinstance(all_conclusions, dict) else None
    if explicit_total is not None:
        out["observations"] = explicit_total
    if all_total is not None and explicit_total is not None:
        out["conclusions"] = max(all_total - explicit_total, 0)

    if isinstance(queue, dict):
        out["deriver_pending"] = queue.get("pending_work_units")
        out["deriver_processing"] = queue.get("in_progress_work_units")

    return out


@router.get("/honcho/sync")
async def honcho_sync_status() -> dict[str, Any]:
    """Report the recurring ``hal0-honcho-sync.timer`` graph-sync job's health.

    Response shape (stable contract)::

        {
          "timer_enabled":     bool,        # unit-file enabled (survives reboot)
          "interval":          "hourly" | "*-*-* *:00:00" | None,  # OnCalendar=
          "last_run_at":       iso8601 | None,
          "last_run_ok":       bool | None,
          "last_run_error":    str | None,
          "last_synced_count": int | None,   # conclusions migrated by that one run
          "next_run_at":       str | None,   # raw systemd timestamp (not ISO)
        }

    All fields are fail-soft: a host without systemd, or a fresh state file
    that has never run, yields honest ``None``/``False`` rather than an
    error — matches ``GET /api/memory/graph/status``'s posture.
    """
    import asyncio

    from hal0.memory.honcho_migrate import MigrateState
    from hal0.services.systemd import timer_schedule, unit_state

    state_info, timer_info = await asyncio.gather(
        unit_state(_HONCHO_SYNC_TIMER), timer_schedule(_HONCHO_SYNC_TIMER)
    )
    timer_enabled = state_info.get("unit_file_state") == "enabled"

    run_info = MigrateState().data.get("honcho_to_hindsight", {})

    return {
        "timer_enabled": timer_enabled,
        "interval": timer_info.get("calendar"),
        "last_run_at": run_info.get("last_run_at"),
        "last_run_ok": run_info.get("last_run_ok"),
        "last_run_error": run_info.get("last_run_error"),
        "last_synced_count": run_info.get("last_synced_count"),
        "next_run_at": timer_info.get("next_elapse"),
    }


@router.put("/honcho/sync")
async def set_honcho_sync_timer(request: Request) -> dict[str, Any]:
    """Enable/disable the recurring graph-sync timer. Body: ``{"enabled": bool}``.

    ``enabled: true`` runs the systemd equivalent of ``enable --now``
    (enable the unit file, then start it immediately); ``enabled: false``
    runs the equivalent of ``disable --now`` (stop, then disable). Fail-soft
    like the services management surface's ``unit_action``: a systemctl
    failure is reported via ``ok: false`` + ``note``, not raised, since the
    caller can retry or fall back to a manual ``systemctl`` call.

    Returns the updated status (same shape as ``GET /honcho/sync``) plus
    ``ok``/``note`` describing whether the systemctl calls succeeded.
    """
    body = await _read_json_body(request)
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise BadRequest(
            "'enabled' is required and must be a bool",
            details={"path": "/api/memory/honcho/sync"},
        )

    from hal0.services.systemd import unit_action

    if enabled:
        results = [
            await unit_action(_HONCHO_SYNC_TIMER, "enable"),
            await unit_action(_HONCHO_SYNC_TIMER, "start"),
        ]
    else:
        results = [
            await unit_action(_HONCHO_SYNC_TIMER, "stop"),
            await unit_action(_HONCHO_SYNC_TIMER, "disable"),
        ]
    ok = all(bool(r.get("ok")) for r in results)
    note = None if ok else "; ".join(str(r.get("message")) for r in results if not r.get("ok"))

    status = await honcho_sync_status()
    status["ok"] = ok
    status["note"] = note
    return status


@router.post("/honcho/sync/run")
async def run_honcho_sync_now() -> dict[str, bool | str | None]:
    """Trigger one graph-sync run now, non-blocking.

    Starts the oneshot ``hal0-honcho-sync.service`` unit (the same unit the
    timer fires) and returns immediately — the run itself may take a while
    against a large Honcho conclusion backlog, so this does not wait for
    completion. Poll ``GET /honcho/sync`` for ``last_run_at``/``last_run_ok``
    to see the outcome.
    """
    from hal0.services.systemd import unit_action

    result = await unit_action(_HONCHO_SYNC_SERVICE, "start")
    started = bool(result.get("ok"))
    return {"started": started, "note": None if started else str(result.get("message"))}


# ── REST shims for /api/memory/{add,search,list,delete} (#302) ─────────────
#
# Plain-HTTP veneer over the memory provider for callers that don't speak the
# MCP protocol (Hermes bootstrap CLI, dashboard Agents > Peers tab,
# in-process scripts). The MCP transport at /mcp/memory/mcp stays
# available for proper MCP clients; these routes are a parallel path
# for the much-larger HTTP-only audience.
#
# Why: #302 surfaced that the bootstrap + CLI + dashboard were all
# POSTing to /mcp/memory as if it were one-shot JSON-RPC. Real FastMCP
# transport needs initialize + session-tagged subsequent calls — that's
# work for a future MCP-SDK-client refactor. Until then, REST shims are
# the cheapest unblock so identity cards actually get written.


@router.post("/add")
async def memory_add(request: Request) -> dict[str, Any]:
    """Add a memory item. Body: ``{text, dataset?, tags?, metadata?, document_id?}``.

    Returns ``{id, timestamp}`` plus ``operation_id`` when the engine
    ingests asynchronously (Hindsight retain). Reuse ``document_id``
    across calls to upsert one logical document.

    Identity headers (issue #317):

      - ``X-hal0-Agent``: agent identity. Stamped onto
        the wrapper's ``source`` field — server-injected so callers
        cannot lie. Absent header → ``"anonymous"``.
      - ``X-hal0-Private: 1``: opt into the private namespace.
        Promotes ``dataset`` to ``private:<agent>`` regardless of the
        body value.

    The body's ``source`` field is REJECTED — clients supplying it is
    treated as an attempt to impersonate, matching the MCP rule. Use
    the ``X-hal0-Agent`` header to claim identity.

    Returns ``{id, timestamp}`` from :meth:`MemoryProvider.add`.
    """
    body = await _read_json_body(request)
    text = body.get("text")
    if not isinstance(text, str) or not text:
        raise Hal0Error(
            "memory_add requires 'text' (non-empty string)",
            details={"path": "/api/memory/add"},
        )
    if "source" in body:
        # Source is server-injected from the X-hal0-Agent
        # header so callers cannot impersonate another agent in the
        # audit log.
        raise Hal0Error(
            "memory_add 'source' is server-injected from X-hal0-Agent and cannot be supplied",
            details={"path": "/api/memory/add"},
        )

    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        dataset = resolve_write_dataset(
            body.get("dataset"),
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    document_id = body.get("document_id")
    if document_id is not None and (
        not isinstance(document_id, str) or not _AGENT_ID_PATTERN.match(document_id)
    ):
        raise BadRequest(
            "memory_add 'document_id' must match the identity grammar (alnum/-/_ ≤64 chars)",
            details={"path": "/api/memory/add"},
        )

    wrapper = _wrapper(request)
    return await wrapper.add(
        text=text,
        dataset=dataset,
        tags=body.get("tags") or [],
        source=agent_id,
        metadata=body.get("metadata") or {},
        client_id=agent_id if agent_id != "anonymous" else None,
        document_id=document_id,
    )


@router.post("/search")
async def memory_search(request: Request) -> dict[str, Any]:
    """Search memory. Body: ``{query, limit?, dataset?, tags?, before?, after?}``.

    Identity headers behave like ``/add`` — ``X-hal0-Private: 1``
    expands a default-empty ``dataset`` to ``[shared, private:<agent>]``
    so a private-mode caller sees both their own scoped
    items + the shared bucket without per-call opt-in.

    Returns ``{items: [MemoryRecord, ...]}`` — wrapped in an envelope so
    we can add ``next_cursor`` / counters later without breaking clients.
    """
    body = await _read_json_body(request)
    query = body.get("query")
    if not isinstance(query, str) or not query:
        raise Hal0Error(
            "memory_search requires 'query' (non-empty string)",
            details={"path": "/api/memory/search"},
        )

    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        dataset = resolve_read_datasets(
            body.get("dataset"),
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    wrapper = _wrapper(request)
    items = await wrapper.search(
        query=query,
        limit=int(body.get("limit", 10)),
        dataset=dataset,
        tags=body.get("tags") or [],
        before=body.get("before"),
        after=body.get("after"),
        client_id=agent_id if agent_id != "anonymous" else None,
    )
    return {"items": items}


@router.post("/recall")
async def memory_recall(request: Request) -> dict[str, Any]:
    """Token-budgeted recall (Hindsight's preferred path).

    Body: ``{query, max_tokens?, types?, dataset?, tags?}``. Identity +
    namespace resolution behave like ``/search`` (X-hal0-Agent +
    X-hal0-Private). Returns ``{items: [MemoryItem, ...]}`` ordered by
    relevance (no numeric score — Hindsight recall returns none).

    Falls back to ``search`` semantics on engines without a richer recall
    (the ABC default), so this route is safe regardless of active engine.

    Contract note (#1026): this is the NAMESPACE recall — ACL-scoped,
    cross-bank fan-out, envelope ``{items}``. It is distinct from the bank
    console recall ``POST /api/memory/banks/{bank}/recall`` (single-bank
    Hindsight passthrough, envelope ``{results}``). Same verb, different scope.
    """
    body = await _read_json_body(request)
    query = body.get("query")
    if not isinstance(query, str) or not query:
        raise BadRequest(
            "memory_recall requires 'query' (non-empty string)",
            details={"path": "/api/memory/recall"},
        )
    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        dataset = resolve_read_datasets(
            body.get("dataset"),
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    wrapper = _wrapper(request)
    items = await wrapper.recall(
        query=query,
        types=body.get("types"),
        max_tokens=int(body.get("max_tokens", 4096)),
        dataset=dataset,
        tags=body.get("tags") or [],
        tags_match=body.get("tags_match"),
        client_id=agent_id if agent_id != "anonymous" else None,
    )
    return {"items": items}


@router.get("/list")
async def memory_list(
    request: Request,
    dataset: str | None = None,
    bank: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Paginated list. Returns ``{items: [...], next_cursor: str | null}``.

    Identity rules mirror ``/search``: ``X-hal0-Private: 1`` with no
    explicit ``?dataset=`` resolves to the caller's own private bucket
    so the ``hal0 agent memory list`` CLI subcommand can enumerate
    per-agent items without the operator passing the namespace by hand.

    ``?bank=`` is a convenience alias for the dashboard's Hindsight bank
    browser: a bank id (``private__hermes``) is translated to the matching
    dataset namespace (``private:hermes``) when no explicit ``?dataset=`` is
    given. If both are supplied and conflict, the explicit ``dataset`` wins.
    """
    if bank is not None:
        bank_dataset = bank_to_namespace(bank)
        if dataset is None:
            dataset = bank_dataset
        elif dataset != bank_dataset:
            log.info(
                "memory_list: ?dataset=%r overrides conflicting ?bank=%r (->%r)",
                dataset,
                bank,
                bank_dataset,
            )

    agent_id = _agent_id(request)
    private = _is_private(request)
    try:
        resolved = resolve_write_dataset(
            dataset,
            private=private,
            client_id=agent_id if agent_id != "anonymous" else None,
        )
    except MemoryNamespaceError as exc:
        raise MemoryNamespaceInvalid(str(exc)) from exc

    wrapper = _wrapper(request)
    return await wrapper.list_items(
        dataset=resolved,
        cursor=cursor,
        limit=limit,
        client_id=agent_id if agent_id != "anonymous" else None,
    )


@router.post("/delete")
async def memory_delete(request: Request) -> dict[str, int]:
    """Delete by id. Body: ``{ids: [...], dataset?}``. Returns ``{deleted: int}``.

    ``dataset`` optionally directs the engine's bank sweep (e.g.
    ``project:<id>`` items live outside the default shared + own-private
    sweep). Identity headers otherwise are not consulted: id-scoped
    delete bypasses the namespace surface entirely (the wrapper's audit
    log still stamps the call with the agent identity for forensics —
    see the provider's audit hook).
    """
    body = await _read_json_body(request)
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise Hal0Error(
            "memory_delete requires 'ids' (non-empty list)",
            details={"path": "/api/memory/delete"},
        )
    agent_id = _agent_id(request)
    private = _is_private(request)
    requested = body.get("dataset")
    dataset: str | list[str] | None
    if requested is None or (isinstance(requested, str) and not requested.strip()):
        dataset = None
    elif isinstance(requested, list):
        dataset = [str(d) for d in requested]
    else:
        try:
            dataset = resolve_write_dataset(
                str(requested),
                private=private,
                client_id=agent_id if agent_id != "anonymous" else None,
            )
        except MemoryNamespaceError as exc:
            raise MemoryNamespaceInvalid(str(exc)) from exc
    wrapper = _wrapper(request)
    # #1024 hardening: id-scoped delete is destructive — record it (actor +
    # ids + outcome) so bulk removals are attributable after the fact.
    async with record_action(
        request,
        category="memory",
        action="memory.items.delete",
        target=",".join(str(i) for i in ids)[:200],
    ):
        return await wrapper.delete(
            ids=ids,
            client_id=agent_id if agent_id != "anonymous" else None,
            dataset=dataset,
        )


async def _read_json_body(request: Request) -> dict[str, Any]:
    """Tolerant JSON body parser (mirrors v1.py:_read_json_body)."""
    try:
        body = await request.json()
    except Exception as exc:
        raise Hal0Error(
            "request body must be valid JSON",
            details={"error": str(exc)},
        ) from exc
    if not isinstance(body, dict):
        raise Hal0Error("request body must be a JSON object")
    return body


# ── Helper exports for tests ────────────────────────────────────────────────


__all__ = [
    "DEFAULT_DATASET",
    "MemoryAgentIdInvalid",
    "MemoryGraphConfig",
    "MemoryGraphConfigInvalid",
    "MemoryGraphSlotInvalid",
    "MemoryNamespaceInvalid",
    "MemoryProviderInvalid",
    "MemoryProviderUnavailable",
    "MemoryUnavailable",
    "router",
]
