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

import asyncio
import contextlib
import logging
import re
from typing import Any

from fastapi import APIRouter, Request
from pydantic import ValidationError

from hal0.api._audit import record_action
from hal0.api.middleware.error_codes import BadRequest, Hal0Error
from hal0.config.loader import HAL0_TOML_LOCK, load_hal0_config, save_hal0_config
from hal0.config.schema import MemoryGraphConfig
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
#
# AUTH POSTURE (#1302, ratified — perimeter-only for *identity*). The
# agent header is self-asserted and hal0 does not authenticate it: KB-1's
# optional key auth gates whether a caller may reach these routes at all
# (tier), but no per-agent credential exists to bind the id to (ADR-0012
# dropped per-agent auth and TLS platform wide, not just here). We
# validate the header's *shape* and refuse a body-supplied
# ``source`` so audit can never disagree with the namespace a write landed
# in — but a caller who can already reach these routes can claim any agent
# id. Therefore ``private:<agent>`` is an ISOLATION boundary between
# cooperating agents on one host, NOT a security boundary against a hostile
# LAN caller; the reverse proxy is the auth boundary, and a multi-tenant
# deployment must inject ``X-hal0-Agent`` there from an authenticated
# identity (overwriting any client value). Documented for operators in
# ``docs/concepts/security.mdx`` §"What X-hal0-Agent is and is not".
#
# Independent of the header, destructive calls carry their own layer:
# bulk delete (>1 id) gates through the approval queue on BOTH MCP mounts
# (:func:`hal0.mcp.admin.is_gated` owns the classification), and bank
# delete requires ``?confirm=<bank_id>``.


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


async def _propagate_shielded(slot: str, timeout_s: int) -> dict[str, Any]:
    """Propagate an extraction-slot/timeout change, safe against cancellation.

    ``asyncio.to_thread`` submits :func:`~hal0.memory.extraction_env.apply_extraction_slot`
    to a real OS thread; cancelling the awaiting coroutine (client disconnect,
    shutdown, request timeout) does NOT stop that thread — it just makes the
    ``await`` raise early while the write/restart keeps running in the
    background. This is called from inside :data:`HAL0_TOML_LOCK`'s critical
    section (#1682 review): an early return there would release the lock
    while the orphaned worker could still clobber the drop-in, letting a
    second PUT's propagation interleave with it — one write wins the drop-in,
    the other wins ``hal0.toml``, and they can disagree.

    Shield the wait so the underlying task is never itself cancelled, and if
    the *caller's* await is cancelled anyway, wait out the (already-running,
    un-cancellable) worker before re-raising — so the lock is held for the
    worker's true lifetime, not just until the first cancellation.

    That cleanup wait is ALSO shielded, in a loop (#1717 review): a second
    cancellation arriving while we're waiting out the first one (e.g. a
    request timeout followed by server shutdown) must not re-propagate
    early either — an unshielded ``await task`` there would exit and
    release the lock exactly like the bug this function exists to close.
    """
    from hal0.memory.extraction_env import apply_extraction_slot

    task = asyncio.ensure_future(
        asyncio.to_thread(apply_extraction_slot, slot, timeout_s=timeout_s)
    )
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(task)
        raise


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

    async with HAL0_TOML_LOCK:
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

        slot_changed = new_cfg.extraction_slot != cfg.memory.graph.extraction_slot
        timeout_changed = new_cfg.llm_timeout_s != cfg.memory.graph.llm_timeout_s

        # Validate an EXPLICIT slot change against the live slot set —
        # reject an unknown / non-llm slot with the valid options so the
        # gate never flips onto a target that can't serve extraction.
        if slot_changed:
            available = await _enabled_llm_slots(request)
            if available and new_cfg.extraction_slot not in available:
                raise MemoryGraphSlotInvalid(
                    f"extraction_slot {new_cfg.extraction_slot!r} is not an enabled llm slot",
                    details={"available_slots": ", ".join(available)},
                )

        # Whether this PUT needs to propagate to hindsight-api: an explicit
        # slot/timeout change, or (#1682 review) the on-disk drop-in has
        # drifted from what hal0.toml already claims to be running — a host
        # stuck in the pre-seam-bug broken state (toml already says slot A,
        # drop-in never written) must still reconcile on the next PUT of its
        # unchanged slot, not no-op on config equality.
        #
        # Reconciliation only runs while the gate is meant to be enabled
        # (#1717 review): a bare {"enabled": false} must always be able to
        # disable, even with a stale/deleted configured slot — disabling
        # doesn't need the (unchanged) slot to be valid.
        needs_propagation = slot_changed or timeout_changed
        if not needs_propagation and new_cfg.enabled:
            from hal0.memory.extraction_env import drop_in_matches

            if not drop_in_matches(new_cfg.extraction_slot, new_cfg.llm_timeout_s):
                # Reconciliation is inferred work we chose to do ourselves,
                # not an explicit operator action — only do it when we can
                # POSITIVELY confirm the unchanged slot is still valid
                # (#1717 review). An empty/unknown available set (no slots
                # enabled, or enumeration failed) means we can't tell, and
                # forcing a restart onto an unverifiable slot could replace
                # a possibly-still-working drop-in with a broken one; skip
                # reconciliation instead of erroring this — often
                # unrelated — request.
                available = await _enabled_llm_slots(request)
                needs_propagation = new_cfg.extraction_slot in available

        # Flip the live wrapper's reported state BEFORE persisting.
        try:
            wrapper.set_graph_enabled(new_cfg.enabled, extraction_slot=new_cfg.extraction_slot)
        except ValueError as exc:
            raise MemoryGraphConfigInvalid(str(exc)) from exc

        # Persist BEFORE propagating (#1641). The propagation awaits a ~60s
        # restart, and cancelling that await (client disconnect, shutdown,
        # request timeout) does NOT stop the worker thread — with the save
        # afterwards the daemon could end up on the new slot while hal0.toml
        # still held the old one. Config is the source of truth and the
        # propagation is documented best-effort in the other direction (a
        # restart failure is surfaced, never rolled back), so saving first is
        # the ordering that cannot produce a silent divergence.
        cfg.memory.graph = new_cfg
        try:
            save_hal0_config(cfg)
        except OSError as exc:
            raise Hal0Error(
                f"could not persist hal0 config: {exc}",
                details={"error": str(exc), "errno": getattr(exc, "errno", None)},
            ) from exc

        # Propagate the extraction slot + LLM timeout to hindsight-api (drop-in
        # + restart) so the engine's native extraction LLM follows the choice.
        # ``needs_propagation`` was already computed (and validated) above.
        propagation: dict[str, Any] | None = None
        if needs_propagation:
            # Thread hop (#1641): the propagation shells out to the privileged
            # seam and then waits on a hindsight-api restart — a bounded but
            # multi-second blocking call that would otherwise stall the whole
            # event loop for the engine's cold start. Shielded against
            # cancellation (see _propagate_shielded) so this lock's critical
            # section can't be exited early while the worker is still live.
            propagation = await _propagate_shielded(new_cfg.extraction_slot, new_cfg.llm_timeout_s)

        out = new_cfg.model_dump(mode="json")
        # Echo the live status so the dashboard's optimistic-update path
        # gets the counters in the same round trip without a second fetch.
        out["status"] = wrapper.graph_status()
        if propagation is not None:
            out["propagation"] = propagation
        return out


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

    ``ids`` are **document ids** — the ``id`` field every read surface
    returns (``/api/memory/list``, ``/api/memory/search``, recall). In
    unified-bank mode a per-fact id (``metadata.fact_id``) is accepted as
    an alias and resolved to its owning document; the engine only ever
    deletes documents. See #1456.

    ``dataset`` optionally directs the engine's bank sweep (e.g.
    ``project:<id>`` items live outside the default shared + own-private
    sweep). It goes through the same ``resolve_read_datasets`` closed-set
    resolver the MCP surface uses — a list that names no addressable
    namespace is a 400, never a silent widening to ``shared`` (#1451).

    Identity headers **are** consulted: in unified-bank mode the single
    shared bank holds every agent's ``visibility:private`` docs and every
    project's docs, so the delete is gated on the same ACL the read paths
    enforce (``_deletable_ids``) — a doc the caller could not have read in
    this call is withheld. The wrapper's audit log stamps the call with the
    agent identity either way.
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
    else:
        # #1451: this branch used to forward a list verbatim
        # (``[str(d) for d in requested]``), skipping the closed-set
        # resolver the MCP surface goes through — the exact two-surface
        # drift ``hal0.memory.namespace`` exists to prevent.
        try:
            dataset = resolve_read_datasets(
                requested if isinstance(requested, list) else str(requested),
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
    "MemoryUnavailable",
    "router",
]
