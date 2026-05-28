---
title: MCP in hal0
description: hal0 is an MCP host. v0.3 bundles two FastMCP-built servers (hal0-admin, hal0-memory) mounted on the FastAPI app. Both speak Streamable-HTTP. No bearer auth (post-ADR-0012).
sidebar:
  order: 1
---

hal0 is an **MCP host**. It hosts two MCP servers in v0.3 and is
designed to host more — both bundled (`hal0-admin`, `hal0-memory`) and
extension-installed (a planned framework, not in v0.3).

The two bundled servers run as sub-ASGI apps inside the main
`hal0-api` FastAPI process. They share its lifecycle, its port
(`:8080`), and its observability surface. There are no separate
processes, no separate ports.

## What you can hit today

| Endpoint        | Server          | Source                          |
|-----------------|-----------------|----------------------------------|
| `/mcp/admin`    | hal0-admin      | `src/hal0/mcp/admin.py`          |
| `/mcp/memory`   | hal0-memory     | `src/hal0/mcp/memory.py`         |

Both are mounted by `mount_mcp_servers()` in
`src/hal0/api/mcp_mount.py:105`. Mount happens once at app startup;
the lifespan enters each server's session manager.

## Transport

Both servers use **FastMCP** (the upstream Python SDK,
`mcp.server.fastmcp.FastMCP`) and expose their tools over
**Streamable HTTP** — `POST` with a JSON-RPC body and a
`Mcp-Session-Id` header for session continuity.

ADR-0004 §1 D1 records why hal0 picked the Python SDK over the
TypeScript SDK the `mcp-builder` skill recommends — orchestrator and
every other hal0 surface is Python, no MCPB distribution target.

The mount uses `app.mount()` rather than `include_router()` because
FastMCP delivers a complete Starlette app (its own session manager,
HTTP transports, `/messages` writer) that we want to expose unmodified.
See `src/hal0/mcp/admin.py:29` for the rationale.

## A minimal Streamable-HTTP call

```sh
# tools/list against the admin server.
curl -s -X POST http://127.0.0.1:8080/mcp/admin \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

A real client maintains the `Mcp-Session-Id` header across the
session — see the MCP spec for details. Hermes-Agent's
`mcp_wire` bootstrap phase is the canonical round-trip example
(`src/hal0/agents/hermes_provision.py:874`).

## Identity (no auth)

Per [ADR-0012](../../internal/adr/0012-remove-auth-and-caddy.md), hal0
removed its entire FastAPI auth surface. There is **no bearer token**
the dashboard or an agent has to mint, no password, no OTP.

The `MCPAuthMiddleware` retained in `mcp_mount.py:83` is now an
**identity-stash** middleware, not an auth gate. It parses any
`Authorization: Bearer` header the caller happens to pass and stashes
`client_id` in a `ContextVar` so the tool dispatchers (`memory_*`
namespace promotion, audit log) can see who's calling. Missing or
absent → `client_id = "anonymous"`.

The intended identity source going forward is the `X-hal0-Agent`
header (set from each agent's `HAL0_AGENT_ID` env). The middleware
rename to `MCPIdentityMiddleware` + server-side header read are
deferred follow-ups — see [`agents/identity.md`](../agents/identity.md)
for the current state and the gap.

## What MCP runs in-process vs over REST

Most hal0-admin tools are **REST passthroughs** — they forward
through `httpx` to an existing `/api/*` route. The REST layer owns
authorization + validation; the MCP server adds the tool name +
gating + audit. See `src/hal0/mcp/admin.py:258` for the
`_REST_MAP` table.

A few tools run **in-process** to avoid an HTTP hop:

- All `memory_*` tools, when a `memory_dispatcher` callable is wired
  (it is, in production). Direct Cognee wrapper call. Source:
  `src/hal0/mcp/admin.py:489`.
- Host-introspection probes (`gpu_target_version`, `npu_status`,
  `env_report`, `model_store_probe`). Source: `src/hal0/mcp/probes.py`.

## Hosting other MCP servers (planned)

The "MCP host" platform — letting operators install + supervise
arbitrary aftermarket MCP servers via a catalog UI on `/agents` — is
a v0.3 deliverable in progress, not yet shipped. The framework will
likely follow the same shape as the bundled servers: ASGI mount
under `/mcp/<name>`, lifespan-managed, dashboard surface for
install + enable + observe.

Track via the v0.3 milestone issues and any future ADR-0015. **There
is no ADR-0015 in `main` yet** — don't link one until it lands.

## See also

- [`hal0-admin.md`](./hal0-admin.md) — admin tools, mount path, source.
- [`hal0-memory.md`](./hal0-memory.md) — memory tools, mount path, source.
- [ADR-0004 — Agents](../../internal/adr/0004-agents.md) — origin of the hal0-admin tool catalog and the autonomous/gated split.
- [ADR-0005 — Memory engine = Cognee](../../internal/adr/0005-memory-engine-cognee.md) — origin of the hal0-memory contract.
- [ADR-0012 — Remove auth and Caddy entirely](../../internal/adr/0012-remove-auth-and-caddy.md) — why there's no bearer.
- [`agents/mcp-client.md`](../agents/mcp-client.md) — the *outbound* side: per-agent MCP-client allow-list.
