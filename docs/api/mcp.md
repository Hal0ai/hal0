---
title: MCP servers
description: hal0's two Model Context Protocol servers — /mcp/admin (slot, model, capability, config, hardware, log admin) and /mcp/memory (Cognee-backed long-term memory).
sidebar:
  order: 6
---

hal0 exposes **two Model Context Protocol servers** that any MCP-speaking
client can drive — bundled agents, Claude Code, external RAG services, your
own scripts.

| Endpoint       | Purpose                                              |
|----------------|------------------------------------------------------|
| `/mcp/admin`   | Slot, model, capability, config, hardware, log admin |
| `/mcp/memory`  | Long-term memory (add / search / list / delete)      |

Both are mounted on the main hal0 API process. Same host, same port, same
Bearer token. They speak **Streamable HTTP** (the MCP SDK default transport)
via the upstream `mcp` Python SDK's `FastMCP` server class.

See [ADR-0004](../internal/adr/0004-agents.md) for the admin server contract
and [ADR-0005](../internal/adr/0005-memory-engine-cognee.md) for the memory
server contract.

## Transport

Both servers are mounted as ASGI sub-applications under hal0's main FastAPI
app (`app.mount("/mcp/admin", ...)` and `app.mount("/mcp/memory", ...)`).
That means the standard MCP SDK Streamable-HTTP transport works against
them out of the box — point any client at the full URL and go.

```
http://hal0.local:8080/mcp/admin   (direct)
https://hal0.example.com/mcp/admin (TLS via your upstream reverse proxy)
```

The memory server is a **focused alternative mount** — it exposes only the
four `memory_*` tools, no admin surface. Use it when a narrow integration
should not see the full admin tool catalog (smaller attack surface). The
admin server *also* exposes the `memory_*` tools as delegates, so an agent
that needs both can speak to `/mcp/admin` exclusively.

## Authentication

Auth reuses the existing **Bearer token** from
[ADR-0001](../internal/adr/0001-collapse-edge-auth-into-fastapi.md). No new
credential type. Pass the same `Authorization: Bearer <token>` header your
dashboard or REST client would send.

The MCP server extracts `client_id` from the token (via the same
`AuthIdentity` resolver the REST layer uses) and stamps every audit row
with it. Server-side internal REST calls reattach the same Bearer
unchanged — an agent can only do what its token already permits at the
REST layer. There is no privilege elevation across the MCP boundary.

### Memory private namespace header

The memory server honours one extra header to opt a single client into
**private-namespace** writes (ADR-0005 §3):

```
X-hal0-Private: 1
```

When set, the client's writes go to dataset `private:<client_id>` instead
of the default `shared` dataset, and searches see both `shared` and the
client's own `private:<client_id>` namespace. Default posture is shared —
private is opt-in per ADR-0005's "consistent with ADR-0001's trust
posture" reasoning.

## Tool catalog — `/mcp/admin`

The catalog mirrors [ADR-0004 §4](../internal/adr/0004-agents.md). Tools
are classified as **autonomous read**, **autonomous write**, or
**gated destructive**. Gated calls return `{"status": "pending_approval",
"approval_id": "..."}` immediately and enqueue an entry on the dashboard
approval inbox (see [Agents](./agents.md) for the inbox UX).

### Autonomous read — execute immediately

| Tool             | Underlying route                |
|------------------|---------------------------------|
| `slot_list`      | `GET /api/slots`                |
| `slot_status`    | `GET /api/slots/{name}`         |
| `model_list`     | `GET /api/models`               |
| `hardware_probe` | `GET /api/stats/hardware`       |
| `logs_tail`      | `GET /api/logs`                 |
| `capability_list`| `GET /api/capabilities`         |
| `provider_list`  | `GET /api/providers`            |
| `version_info`   | `GET /api/status`               |

### Autonomous write — execute immediately, mutation is recoverable

| Tool                   | Underlying route                        |
|------------------------|-----------------------------------------|
| `model_swap`           | `POST /api/slots/{name}/swap`           |
| `memory_add`           | hal0-memory (Cognee, in-process)        |
| `memory_search`        | hal0-memory (Cognee, in-process)        |
| `memory_list`          | hal0-memory (Cognee, in-process)        |
| `memory_delete` (1 id) | hal0-memory (Cognee, in-process)        |

### Gated destructive — enqueued for owner approval

| Tool                          | Underlying route                              |
|-------------------------------|-----------------------------------------------|
| `model_pull`                  | `POST /api/models/{model_id}/pull`            |
| `model_delete`                | `DELETE /api/models/{model_id}`               |
| `slot_create`                 | `POST /api/slots`                             |
| `slot_delete`                 | `DELETE /api/slots/{name}`                    |
| `slot_restart`                | `POST /api/slots/{name}/restart`              |
| `capability_set`              | `POST /api/capabilities/{slot}/{child}`       |
| `config_write`                | `PUT /api/settings`                           |
| `provider_credential_write`   | `POST /api/providers/{name}/credentials` (route landing in a follow-up PR; calls 404 until then) |
| `memory_delete` (>1 id)       | hal0-memory (Cognee, in-process)              |

### ADR vs live-route reconciliation

A few entries in the catalog have HTTP targets that differ from
[ADR-0004 §4](../internal/adr/0004-agents.md)'s original wording. The MCP
server keeps the tool *name* exactly as the ADR specifies (so agents see
documented identifiers) and routes to the live URL the dashboard already
calls. The table above shows the live URL.

The cells that differ from ADR-0004 §4's prose:

| ADR §4 wording              | Live target                              |
|-----------------------------|------------------------------------------|
| `model_swap → /api/slots/{n}/model` | `/api/slots/{name}/swap`         |
| `model_pull → /api/models/pull`     | `/api/models/{model_id}/pull`    |
| `capability_set → /api/capabilities`| `/api/capabilities/{slot}/{child}` |
| `version_info → /api/version`       | `/api/status` (already carries version) |

`provider_credential_write` has **no live route yet**. The tool is
registered for catalog completeness; calls succeed at the MCP layer but
the underlying REST endpoint returns 404 until the provider team lands
it.

### Future tools land via additive ADR amendment

New tools require an ADR amendment, not a quiet PR. The rule from
[ADR-0004 §4](../internal/adr/0004-agents.md) — "a tool ships iff it
maps to an existing `/api/*` route the dashboard already calls" —
deliberately constrains scope. Reviewers should push back on any tool
that grows a new privileged surface.

## Tool catalog — `/mcp/memory`

The memory server exposes the same four `memory_*` tools the admin
server proxies, with no admin surface. Schema is rich from day 1 per
[ADR-0005 §2](../internal/adr/0005-memory-engine-cognee.md) so we do
not pay a schema-versioning tax in Phase 9.

### `memory_add`

```
memory_add(
  text:     str,                # required, non-empty
  dataset:  str  = "shared",    # promoted to "private:<client_id>" when
                                # X-hal0-Private: 1 is set
  tags:     list[str] | str = [],  # CSV accepted for clients that lack
                                   # JSON array literals
  metadata: dict = {},          # opaque passthrough
)
→ { "id": str, "timestamp": iso8601_str }
```

`source` is **server-injected** from the Bearer-derived `client_id`.
Callers cannot supply `source` themselves — submitting one returns
`mcp.memory_schema` error. ADR-0005 §5 calls this out as the forensic
grounding for the audit trail.

### `memory_search`

```
memory_search(
  query:   str,                       # required, non-empty
  limit:   int  = 10,                 # 1..200
  dataset: str | list[str] = "shared",
  tags:    list[str] | str = [],      # AND-match
  before:  iso8601 | null  = null,    # date range upper bound
  after:   iso8601 | null  = null,    # date range lower bound
)
→ { "results": [
      { "id": str, "text": str, "score": float,
        "timestamp": iso8601, "dataset": str, "tags": [str],
        "source": str, "metadata": dict }, ... ] }
```

When `X-hal0-Private: 1` is set and no explicit `dataset` is passed,
search is broadened to `["shared", "private:<client_id>"]`.

### `memory_list`

```
memory_list(
  dataset: str = "shared",
  cursor:  str | null = null,
  limit:   int  = 50,                 # 1..200
)
→ { "items": [ ItemDict, ... ], "next_cursor": str | null }
```

### `memory_delete`

```
memory_delete(
  ids: list[str],                     # non-empty
)
→ { "deleted": int }                  # count of removed rows
```

`memory_delete` is **autonomous** when `len(ids) == 1` and **gated** when
`len(ids) > 1`. Bulk deletes route through the admin server's approval
queue per ADR-0004 §4. The single-id path is recoverable; the bulk path
is not autonomous-recoverable.

## Audit log

Every tool invocation emits a structured row through the
`hal0.mcp.audit` structlog logger. Fields:

```
client_id    str    — Bearer-derived caller identity
tool         str    — e.g. "model_swap"
args         dict   — exactly what the caller passed
gated        bool   — true for capital-D destructives
outcome      str    — "ok" | "enqueued" | "error" | ...
timestamp    float  — unix epoch seconds
```

The logger inherits the structlog config installed by the main FastAPI
app, which writes to stdout. Under systemd, that lands in **journald**
on the `hal0-api` unit. Pull a window with:

```sh
journalctl -u hal0-api --no-pager -o json -n 1000 \
  | jq 'select(.MESSAGE | fromjson? | .logger == "hal0.mcp.audit")'
```

The dashboard's per-agent Activity tab (`GET /api/agents/{name}/activity`)
walks the same journald stream and filters by `client_id`. See
[Agents](./agents.md#activity-tab).

## Worked example — curl

The two MCP servers speak the MCP SDK's Streamable-HTTP transport, but
the simplest way to see them work is to walk through the equivalent
REST call. Using `slot_list`:

```sh
# Direct REST — what the admin server forwards to
curl -H "Authorization: Bearer $HAL0_TOKEN" \
     http://localhost:8080/api/slots
```

```sh
# Equivalent MCP call — what an agent sees
mcp call --transport streamable-http \
         --url http://localhost:8080/mcp/admin \
         --header "Authorization: Bearer $HAL0_TOKEN" \
         tools/call --name slot_list --args '{}'
```

Both return the same JSON payload. The MCP layer is a thin name +
gating + audit wrapper around the REST surface — there is no parallel
privileged surface.

## Worked example — Python (mcp SDK)

```python
import asyncio, os
from mcp.client.streamable_http import streamablehttp_client
from mcp import ClientSession

HAL0_URL   = os.environ["HAL0_URL"]    # e.g. http://localhost:8080
HAL0_TOKEN = os.environ["HAL0_TOKEN"]

async def main():
    headers = {"Authorization": f"Bearer {HAL0_TOKEN}"}
    async with streamablehttp_client(
        f"{HAL0_URL}/mcp/admin", headers=headers
    ) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            # Autonomous read
            slots = await session.call_tool("slot_list", {})
            print(slots.structuredContent)

            # Autonomous write
            await session.call_tool("model_swap",
                                    {"name": "primary",
                                     "model": "qwen3:8b"})

            # Gated — returns {status: pending_approval, approval_id}
            pull = await session.call_tool("model_pull",
                                           {"model_id": "qwen3:14b"})
            print(pull.structuredContent)

asyncio.run(main())
```

For memory access, point the same client at `/mcp/memory` and call
`memory_add` / `memory_search` / `memory_list` / `memory_delete`.

## Worked example — private memory namespace

```sh
# Write to the caller's private namespace
mcp call --transport streamable-http \
         --url http://localhost:8080/mcp/memory \
         --header "Authorization: Bearer $HAL0_TOKEN" \
         --header "X-hal0-Private: 1" \
         tools/call --name memory_add \
         --args '{"text": "I prefer dark mode dashboards.",
                  "tags": ["preferences"]}'
```

The write lands in `private:<client_id>`. Subsequent `memory_search`
calls from the same client (with the same `X-hal0-Private: 1` header)
return matches from both `shared` and `private:<client_id>`. Other
clients only see `shared`.

## Error envelopes

Tool failures return a structured error dict so MCP clients can branch
on shape, not regex:

```json
{
  "status": "error",
  "error": {
    "code": "mcp.memory_schema",
    "detail": "text must be non-empty"
  }
}
```

Codes the MCP layer can emit directly (others come from the underlying
REST layer):

| Code                          | Meaning                                  |
|-------------------------------|------------------------------------------|
| `mcp.unknown_tool`            | Tool name not in the catalog             |
| `mcp.unmapped_tool`           | Tool has no REST route + no in-process dispatcher |
| `mcp.missing_arg`             | Required path-substitution arg missing   |
| `mcp.memory_schema`           | `memory_*` args failed schema validation |
| `mcp.memory_unconfigured`     | Memory tool called without dispatcher    |
| `mcp.memory_failed`           | Memory dispatcher raised                 |

REST-layer errors surface as `{"status":"error","http_status":<code>,
"error":<body>}` so the agent sees the same envelope shape the dashboard
sees.
