"""Per-agent memory stats endpoint (v0.3 PR-11).

``GET /api/agents/{agent_id}/memory/stats`` — counts the dashboard's
``SidebarAgentBlock`` (PR-7) renders next to the memory chip:
``writes`` / ``reads`` / ``last_write``.

Flagged as missing during PR-6 + PR-8 integration: the sidebar block
needed *some* representation of "how active is this agent's memory"
without re-fetching the full memory list. The block degrades to
``"—"`` / ``"never"`` chips when this endpoint returns the
``unavailable`` shape, so a hal0 install without Cognee still renders
sensibly.

Where the data comes from
-------------------------
Reads through the same in-process :class:`hal0.memory.CogneeWrapper`
hal0-memory MCP + ``/api/memory/*`` REST shims use. We do NOT hit the
``/mcp/memory`` HTTP endpoint — it would re-enter the app over the
loopback socket and require a real MCP session (the same
session-establishment overhead PR-3's bootstrap fixed by switching to
REST). Direct wrapper access keeps the stats endpoint cheap.

Namespace resolution
--------------------
Per ADR-0005 §3, a bundled agent's writes land under
``private:<agent_id>`` and reads can union ``shared`` + the agent's
own private namespace. Stats are scoped to the **per-agent private
namespace** so the sidebar reflects what THIS agent has done — the
``shared`` dataset is global and would muddy the chip.

Fallback shape
--------------
When the memory wrapper is absent (Cognee init failed, hal0 install
without the optional memory extra), the route returns:

.. code-block:: json

   {
     "agent_id": "hermes",
     "namespace": "private:hermes",
     "writes": 0,
     "reads": 0,
     "last_write": null,
     "available": false
   }

The sidebar block keys off ``available`` to render the "memory not
configured" hint. ``0`` / ``null`` rather than ``null`` everywhere so
the rendering code doesn't have to special-case both shapes.
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from fastapi import APIRouter, Request

from hal0.errors import NotFound

log = structlog.get_logger(__name__)
router = APIRouter()


# Mirror :data:`hal0.api.agents.personas._AGENT_PERSONAS_ROOTS` — same
# v0.3 single-pick rationale (hermes only; pi-coder lights up in v0.4
# by adding a row here, route handler stays unchanged).
_KNOWN_AGENT_IDS: Final[frozenset[str]] = frozenset({"hermes"})


# When list_items() is called against a namespace that contains many
# items, we don't want to enumerate every page just to count — the
# sidebar only needs an approximation. Counts ≥ this floor are reported
# verbatim ("99+"-style cap is the client's choice).
_LIST_PAGE_LIMIT: Final[int] = 500


def _namespace_for(agent_id: str) -> str:
    """Compose the per-agent private dataset name.

    Matches the resolver pattern :mod:`hal0.api.mcp_mount` plumbs onto
    the memory MCP — single source of truth would couple this module
    to the resolver's internal API. Keeping the format literal here is
    cheap because the convention (``private:<agent_id>``) is documented
    in ADR-0005 §3 and won't change.
    """
    return f"private:{agent_id}"


def _empty_stats(agent_id: str, *, available: bool, reason: str | None = None) -> dict[str, Any]:
    """Fallback shape when memory is unavailable or the namespace is empty.

    The ``available`` flag lets the sidebar block distinguish "memory not
    configured" from "memory configured but empty" without a separate
    health probe.
    """
    body: dict[str, Any] = {
        "agent_id": agent_id,
        "namespace": _namespace_for(agent_id),
        "writes": 0,
        "reads": 0,
        "last_write": None,
        "available": available,
    }
    if reason is not None:
        body["reason"] = reason
    return body


@router.get("/{agent_id}/memory/stats")
async def get_agent_memory_stats(agent_id: str, request: Request) -> dict[str, Any]:
    """Return memory counters for the agent's private namespace.

    Shape (documented + tested):

    * ``agent_id``    — the requested id, echoed for caching layers.
    * ``namespace``   — ``private:<agent_id>``, the dataset queried.
    * ``writes``      — number of items in the agent's private namespace.
      A snapshot at request time, NOT a monotonically increasing
      counter (the wrapper doesn't track per-write history; what we
      have is "how many items are currently in this dataset").
    * ``reads``       — count of read events attributed to this agent
      in the wrapper's audit log. v0.3 wrapper doesn't expose a
      per-namespace read counter, so this stays ``0`` until the
      wrapper grows one. Documented null-not-implemented rather than
      a fabricated number.
    * ``last_write``  — ISO-8601 timestamp of the most recent item, or
      ``null`` if the namespace is empty.
    * ``available``   — ``true`` when the wrapper is reachable.

    Unknown ``agent_id`` → 404 (matches the personas + restart routes).
    """
    if agent_id not in _KNOWN_AGENT_IDS:
        raise NotFound(
            f"unknown agent {agent_id!r}",
            code="agent.unknown",
            details={"agent_id": agent_id},
        )

    namespace = _namespace_for(agent_id)
    wrapper = getattr(request.app.state, "memory_wrapper", None)
    if wrapper is None:
        log.info(
            "agent.memory.stats_unavailable",
            agent_id=agent_id,
            reason="no_wrapper",
        )
        return _empty_stats(
            agent_id,
            available=False,
            reason="memory wrapper not initialised",
        )

    # Pull the most recent page and report:
    #   - len(items) as the floor for ``writes`` (clamped at the page
    #     size; the sidebar's purpose is to show "is this active",
    #     not produce exact analytics)
    #   - items[0].timestamp as ``last_write``
    #
    # We deliberately don't paginate to enumerate the full namespace —
    # a v0.4 wrapper method (``count(dataset=...)``) is the right way
    # to do this. Until then a single page is the right cost/value
    # tradeoff for a sidebar chip.
    try:
        page = await wrapper.list_items(
            dataset=namespace,
            cursor=None,
            limit=_LIST_PAGE_LIMIT,
        )
    except Exception as exc:
        # Wrapper failures shouldn't 500 the sidebar chip — surface as
        # ``available=false`` with the reason in logs, not in the body
        # (the reason can leak operator-private detail).
        log.warning(
            "agent.memory.stats_list_failed",
            agent_id=agent_id,
            namespace=namespace,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _empty_stats(
            agent_id,
            available=False,
            reason="memory wrapper list_items raised",
        )

    items = page.get("items") if isinstance(page, dict) else None
    if not isinstance(items, list):
        return _empty_stats(agent_id, available=True)

    last_write: str | None = None
    if items:
        # list_items orders by timestamp DESC, so item 0 is the most
        # recent. Tolerate both the raw int timestamp shape and the
        # iso-string shape — the wrapper's :func:`_row_to_record`
        # currently emits a string in :class:`MemoryRecord.to_dict`.
        first = items[0]
        if isinstance(first, dict):
            ts = first.get("timestamp")
            if isinstance(ts, (int, float)):
                # Cognee's timestamps are seconds-since-epoch; convert
                # to ISO so the dashboard renders a stable string.
                from datetime import UTC, datetime

                last_write = datetime.fromtimestamp(ts, tz=UTC).isoformat()
            elif isinstance(ts, str) and ts:
                last_write = ts

    return {
        "agent_id": agent_id,
        "namespace": namespace,
        "writes": len(items),
        # v0.3 wrapper doesn't expose a read counter; reported as 0
        # consistently so the sidebar's chip renders without a
        # "null vs 0" branch. ``available=true`` distinguishes this
        # from the "no wrapper" case.
        "reads": 0,
        "last_write": last_write,
        "available": True,
    }


# Re-exported for tests so they can monkeypatch the registry without
# poking at the underscore-prefixed name.
KNOWN_AGENT_IDS = _KNOWN_AGENT_IDS

__all__ = ["KNOWN_AGENT_IDS", "router"]
