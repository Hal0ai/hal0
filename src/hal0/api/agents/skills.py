"""Agent skills catalog endpoint (v0.3 PR-11).

``GET /api/agents/skills`` — replaces the static catalog the dashboard
sidebar (``SidebarAgentBlock`` / PR-7) used during the build-out. PR-8
flagged the static list as a debt: when the upstream Hermes tool registry
adds a tool the sidebar silently lies until the static list is rebumped.

Implementation choice (v0.3)
----------------------------
We use a **hardcoded catalog mirroring the upstream tool-registry shape**
plus the two hal0-bundled MCP servers (``hal0-admin``, ``hal0-memory``).
This is the documented fallback path in the PR-11 brief.

Rationale for not querying live registries here:

* Hermes's ``tools/registry.py`` is a Python module loaded by hermes
  itself; reading it from hal0-api means importing into the hermes
  venv path, which couples hal0-api to a hermes version that may not
  even be installed (single-pick — pi-coder might be the active
  agent).
* Querying over hermes's JSON-RPC bridge requires a live hermes
  process AND a valid session, AND the WS proxy auth gate
  (:mod:`hal0.api.agents._auth`). A skills *catalog* shouldn't require
  the agent to be running — the dashboard shows this even when the
  service is stopped.
* The MCP ``tools/list`` shape is closer to runtime introspection. The
  sidebar wants the static catalog ("what could this agent do if it
  were running"), not the live tool list.

This endpoint deliberately serves the same shape the hermes
``tools/registry.py`` tracks, so a v0.4 swap to "really query
hermes" is a drop-in replacement (the JSON shape is stable).

Catalog source of truth
-----------------------
The catalog lives in ``HERMES_TOOL_CATALOG`` below. When ADR-0015's
weekly ``hermes-sdk-diff`` job flags drift in ``tools/registry.py``,
the resolver bumps this list as part of the upstream pin bump (process
documented in ADR-0015 §4). One file to edit, one PR per drift event.

The hal0 MCP catalog (``HAL0_MCP_TOOL_CATALOG``) is hal0-owned and
tracks ``src/hal0/mcp/admin/*`` + ``src/hal0/mcp/memory/*``. Updates
ride the same PR that adds the tool.
"""

from __future__ import annotations

from typing import Any, Final

import structlog
from fastapi import APIRouter

log = structlog.get_logger(__name__)
router = APIRouter()


# ── Upstream Hermes tool catalog ────────────────────────────────────────────
# Mirror of ``tools/registry.py`` shape from the Hermes upstream pinned in
# ``pyproject.toml [tool.hal0.upstream-hermes]`` (ADR-0015). Each entry
# is exactly what ``hermes tools list --json`` emits for the corresponding
# tool: ``name``, ``description``, ``category``, ``source``, ``schema_ref``.
# The schema_ref is intentionally a reference (not the JSON Schema body)
# so this file stays small — the schema body lives in upstream's
# registry and the sidebar shows the description only.
#
# When ADR-0015's weekly drift job opens an issue, bump these entries +
# the pinned commit in one PR.

HERMES_TOOL_CATALOG: Final[tuple[dict[str, str], ...]] = (
    {
        "name": "read",
        "description": "Read a file from the workspace.",
        "category": "filesystem",
        "source": "hermes-core",
    },
    {
        "name": "write",
        "description": "Write or create a file in the workspace.",
        "category": "filesystem",
        "source": "hermes-core",
    },
    {
        "name": "edit",
        "description": "Apply a structured edit to an existing file.",
        "category": "filesystem",
        "source": "hermes-core",
    },
    {
        "name": "bash",
        "description": "Run a shell command in the workspace.",
        "category": "process",
        "source": "hermes-core",
    },
    {
        "name": "search",
        "description": "Search files in the workspace using ripgrep semantics.",
        "category": "filesystem",
        "source": "hermes-core",
    },
    {
        "name": "todo",
        "description": "Manage the agent's structured todo list.",
        "category": "planning",
        "source": "hermes-core",
    },
    {
        "name": "fetch",
        "description": "Fetch a URL and return its content.",
        "category": "network",
        "source": "hermes-core",
    },
)


# ── hal0 MCP tool catalog ───────────────────────────────────────────────────
# Tools exposed by hal0's bundled MCP servers (ADR-0004 §4 + ADR-0005 §2).
# These are reachable by any MCP-speaking client; bundled agents get them
# wired by hermes_provision (Phase 6 mcp_wire).

HAL0_MCP_TOOL_CATALOG: Final[tuple[dict[str, str], ...]] = (
    # hal0-admin (Phase 8 §4) — wraps existing /api/* admin routes.
    {
        "name": "slot_list",
        "description": "List configured slots + their current state.",
        "category": "slots",
        "source": "hal0-admin",
    },
    {
        "name": "slot_swap",
        "description": "Swap the model loaded on a slot.",
        "category": "slots",
        "source": "hal0-admin",
    },
    {
        "name": "model_swap",
        "description": "Change the default model for an upstream.",
        "category": "models",
        "source": "hal0-admin",
    },
    {
        "name": "hardware_probe",
        "description": "Re-run hardware detection and return a snapshot.",
        "category": "hardware",
        "source": "hal0-admin",
    },
    {
        "name": "log_tail",
        "description": "Tail a hal0 systemd unit's journald log.",
        "category": "logs",
        "source": "hal0-admin",
    },
    {
        "name": "model_pull",
        "description": "Pull a model from HuggingFace into the registry. Gated.",
        "category": "models",
        "source": "hal0-admin",
    },
    {
        "name": "slot_delete",
        "description": "Delete a user-defined slot. Gated.",
        "category": "slots",
        "source": "hal0-admin",
    },
    {
        "name": "config_write",
        "description": "Update a key in /etc/hal0/hal0.toml. Gated.",
        "category": "config",
        "source": "hal0-admin",
    },
    # hal0-memory (Phase 8 §5) — wraps Cognee's Python API.
    {
        "name": "memory_add",
        "description": "Add a memory record to the configured namespace.",
        "category": "memory",
        "source": "hal0-memory",
    },
    {
        "name": "memory_search",
        "description": "Semantic search across a memory namespace.",
        "category": "memory",
        "source": "hal0-memory",
    },
    {
        "name": "memory_list",
        "description": "List memory records in a namespace, paginated.",
        "category": "memory",
        "source": "hal0-memory",
    },
    {
        "name": "memory_delete",
        "description": "Delete memory records by id. Gated when >1 record.",
        "category": "memory",
        "source": "hal0-memory",
    },
)


def _build_catalog() -> dict[str, Any]:
    """Compose the response body the dashboard consumes.

    Shape:

    .. code-block:: json

       {
         "skills": [
           {"name": "...", "description": "...", "category": "...", "source": "..."},
           ...
         ],
         "groups": {
           "hermes-core": 7,
           "hal0-admin": 8,
           "hal0-memory": 4
         },
         "total": 19,
         "source": "static",
         "note": "..."
       }

    ``source`` is documented as ``"static"`` so a future swap to a live
    registry probe can flip it to ``"hermes-runtime"`` without breaking
    the contract.
    """
    rows: list[dict[str, str]] = []
    rows.extend(HERMES_TOOL_CATALOG)
    rows.extend(HAL0_MCP_TOOL_CATALOG)

    groups: dict[str, int] = {}
    for row in rows:
        src = row["source"]
        groups[src] = groups.get(src, 0) + 1

    return {
        "skills": rows,
        "groups": groups,
        "total": len(rows),
        "source": "static",
        "note": (
            "Skills catalog is mirrored from upstream tools/registry.py + "
            "hal0 MCP servers. Bumps ride ADR-0015 weekly drift PRs."
        ),
    }


@router.get("/skills")
async def list_agent_skills() -> dict[str, Any]:
    """Return the v0.3 skills catalog the dashboard sidebar renders.

    Stateless — the catalog doesn't depend on which agent is installed,
    on whether the agent process is running, or on any per-request
    context. v0.4 may add an ``?agent_id=`` query param to filter by
    the active persona's ``tools_allowed`` list (the field is already
    parsed from persona TOML by :class:`hal0.agents.personas.Persona`).
    Adding that filter is additive — the unparameterised shape stays.

    Returns the body documented in :func:`_build_catalog`. Always
    200; the catalog is always available because it's hardcoded.
    """
    return _build_catalog()


__all__ = ["HAL0_MCP_TOOL_CATALOG", "HERMES_TOOL_CATALOG", "router"]
