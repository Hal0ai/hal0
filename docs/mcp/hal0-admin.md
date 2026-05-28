---
title: hal0-admin MCP server
description: The tool surface bundled agents (and external MCP clients) use to drive a hal0 host. Slots, models, capabilities, config, hardware, memory delegates. Mounted at /mcp/admin.
sidebar:
  order: 2
---

`hal0-admin` is the **drive-the-host** MCP server. It exposes 25 tools
that cover every `/api/*` route the dashboard already calls — slot
lifecycle, model registry, capability overlay, config, hardware
introspection, plus delegates into the memory MCP. Bundled agents use
it as their primary tool surface; external MCP clients can talk to it
too.

- **Mount:** `/mcp/admin`
- **Transport:** Streamable HTTP (FastMCP)
- **Source:** [`src/hal0/mcp/admin.py`](https://github.com/Hal0ai/hal0/blob/main/src/hal0/mcp/admin.py)
- **Wired by:** `src/hal0/api/mcp_mount.py:127` (`build_admin_server`)

## Design contract

Per [ADR-0004 §4](../../internal/adr/0004-agents.md):

> A tool ships iff it maps to an existing `/api/*` route the dashboard
> already calls. No new privileged surface gets invented for the agent.

Translation: every admin MCP tool corresponds to something an operator
could already do via the REST API. The MCP server adds three things
on top: a stable tool *name* the agent can call by string, the
**autonomous vs gated** classification, and an audit row per call.

## Tool taxonomy

Three buckets. The full list is in `src/hal0/mcp/admin.py:642`
(`build_server`); read there for the canonical names + descriptions.

### Autonomous read (12 tools)

Safe to call without approval. Pure introspection.

- `slot_list`, `slot_status` — slot lifecycle state.
- `model_list` — registry + upstream merge.
- `hardware_probe` — backends, memory, accelerators.
- `logs_tail` — *gated* in v0.3 (secret-redactor coverage gap;
  see `src/hal0/mcp/admin.py:225`). Stays in the read bucket name-wise.
- `capability_list`, `provider_list`, `version_info`.
- Host-introspection probes (issue #237): `gpu_target_version`,
  `npu_status`, `env_report`, `model_store_probe`.

### Autonomous write (5 tools)

Mutating but reversible + low blast radius:

- `model_swap` — hot-swap the primary slot's model.
- `memory_add`, `memory_search`, `memory_list` — delegated to
  the memory MCP via in-process dispatcher.
- `memory_delete` — *autonomous* when `len(ids) == 1`,
  *gated* when `len(ids) > 1`. Classification is in
  `src/hal0/mcp/admin.py:400` (`is_gated`).

### Gated (8 tools)

Always require approval. Routed through the lifespan-scoped
`ApprovalQueue` (`src/hal0/mcp/approval_queue.py`):

- `model_pull`, `model_delete`
- `slot_create`, `slot_delete`, `slot_restart`
- `capability_set`
- `config_write`
- `provider_credential_write` *(stub: no live REST route yet — see
  the drift note in `src/hal0/mcp/admin.py:255`)*
- `logs_tail` *(redactor coverage gap; see above)*

## Gating mechanics

```py
# src/hal0/mcp/admin.py:415
async def dispatch(*, tool, args, client_id, bearer, base_url,
                   approval_queue, memory_dispatcher=None):
    if gated:
        approval_id = await approval_queue.enqueue(
            tool=tool, args=args, client_id=client_id, executor=_executor,
        )
        return {"status": "pending_approval", "approval_id": approval_id}
    # autonomous: run immediately
    return await _execute_tool(...)
```

Approvals surface in the dashboard bell and via
`hal0 agent approvals {list,approve,deny}` —
`src/hal0/cli/agent_commands.py:373`.

## REST passthrough vs in-process

Most tools forward to a live `/api/*` route through `httpx`. The
REST layer's auth + validation stays the single source of truth.
See `src/hal0/mcp/admin.py:258` (`_REST_MAP`) for the table.

Two paths short-circuit the REST hop:

- `memory_*` tools — when a `memory_dispatcher` callable is wired
  (production), they call the Cognee wrapper directly in-process.
- Host-introspection probes — implemented in
  `src/hal0/mcp/probes.py` and dispatched without an HTTP hop.

## Audit

Every tool call emits a structured row through the `hal0.mcp.audit`
structlog logger:

```py
audit_log.info(
    "mcp.tool.invoked",
    client_id=client_id, tool=tool, args=args,
    gated=gated, outcome=outcome, timestamp=time.time(),
)
```

Source: `src/hal0/mcp/admin.py:379` (`_audit`). Structlog routes
through to journald, so audit history persists for free.

## Secret redaction

`logs_tail` proxies journald lines back to the agent. Journald carries
secrets in three high-frequency shapes:

- `Authorization: Bearer <token>` (HTTP traces).
- `HAL0_BEARER_TOKEN=<token>` (slot startup env dumps — relic of
  pre-ADR-0012 days, still in some toolbox env files).
- Bare `Bearer <token>` (debug breadcrumbs).

A single compiled regex (`src/hal0/mcp/admin.py:109`,
`_LOG_SECRET_RE`) rewrites the token to `***REDACTED***` before the
line ships back. The redactor is intentionally narrow — coverage gaps
(provider API keys, X-API-Key) are why `logs_tail` is **gated** in
v0.3. See `docs/internal/phase-8-pending/mcp-backend.md` §2.

## Tool annotations

FastMCP exposes per-tool **annotations** via `ToolAnnotations`
(`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`).
Source: `src/hal0/mcp/admin.py:_ANNOTATIONS`. Smart MCP clients can
read these to render the UI accordingly (red border on
`destructiveHint=True`, etc.). The list is hand-maintained alongside
the tool catalog.

## See also

- [`overview.md`](./overview.md) — transport, mount, identity, host model.
- [`hal0-memory.md`](./hal0-memory.md) — the other bundled MCP server.
- [ADR-0004 — Agents](../../internal/adr/0004-agents.md) — the tool catalog's origin + the autonomous/gated split.
- [ADR-0012 — Remove auth and Caddy entirely](../../internal/adr/0012-remove-auth-and-caddy.md) — context for the absent bearer.
