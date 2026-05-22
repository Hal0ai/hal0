# Wave 1 (MCP backend) — pending items

Items the MCP-backend wave (this worktree) hit while building the admin
+ memory MCP servers per ADR-0004 / ADR-0005. They are NOT blockers for
the Wave-1 deliverables; they are coordination points other teams /
follow-up PRs need to close.

## 1. ADR-0004 §4 routes that don't match live REST surface

ADR-0004 §4's tool catalog names a few `/api/*` routes that don't
exist verbatim in `src/hal0/api/routes/`. The MCP admin server routes
to the live URL today and flags the divergence here so the next ADR
amendment (or a follow-up PR retitling the routes) can converge them.

| ADR §4 route                                | Live route                          | Resolution |
|---------------------------------------------|-------------------------------------|------------|
| `POST /api/slots/{name}/model` (model_swap) | `POST /api/slots/{name}/swap`       | Rename live route OR amend ADR — recommend amend, "swap" is the established UX verb |
| `POST /api/models/pull`        (model_pull) | `POST /api/models/{model_id}/pull`  | Live route is more RESTful; amend ADR to match |
| `POST /api/capabilities` (capability_set)   | `POST /api/capabilities/{slot}/{child}` | Composite key needed; amend ADR |
| `GET  /api/version`           (version_info)| `GET  /api/status`                  | Either add `/api/version` alias or amend ADR; `/api/status` already returns version |
| `POST /api/providers/{name}/credentials` (provider_credential_write) | **MISSING — no live route** | Provider-team / follow-up PR must land this endpoint |

The MCP tool catalog stays ADR-faithful (the names agents see are the
ones the ADR documents); only the HTTP target is adjusted in
`_REST_MAP`. `model_pull` calls succeed today; `provider_credential_write`
calls 404 until the route lands.

## 2. `logs_tail` Bearer-token redaction (ADR-0004 §7)

ADR-0004 §7 says `logs_tail` (the autonomous-read tool wrapping
`GET /api/logs`) must redact Bearer tokens and other obvious secrets
server-side before serving — "Agent never see the credential it is
authenticating with."

The live `src/hal0/api/routes/logs.py` does NOT do this today. Both
the `list_logs` (page) and `stream_logs` (SSE) handlers stream raw
journalctl lines through unchanged.

Adding the redaction needs more than the 5-line tweak the brief
allowed before stopping. A correct redactor needs:

1. A reusable regex set (Bearer headers, `X-API-Key`, common
   provider-key prefixes — `sk-`, `hf_`, etc.).
2. Application to BOTH endpoints (page + stream).
3. A test covering each redaction pattern.

Recommend a small follow-up PR adding `hal0.api.routes.logs.redact_line()`
plus a one-line mapping over `lines` in `list_logs` and per-yield in
`journalctl_sse`. ~30 lines of additions + ~20 lines of tests.

**STOPPED per brief rule; not modified.**

## 3. Bearer → client_id extraction surface

`src/hal0/api/middleware/auth.py` exposes `AuthIdentity` with an
`identity` field (token label / forwarded email / session subject).
The MCP admin server today receives `client_id` through the
`bearer_resolver` hook that the orchestrator-wave wiring is expected
to populate from the request's `AuthIdentity`.

The hook contract is:

```python
def bearer_resolver() -> tuple[str | None, str]:
    """Return (raw_bearer_for_internal_passthrough, client_id_for_audit)."""
```

The orchestrator-wave includes site needs to:

1. Pull `Authorization: Bearer <token>` off the incoming MCP request.
2. Resolve it through the existing token store (or re-use
   `AuthIdentity` from a dependency).
3. Pass both pieces to the resolver.

This is intentionally NOT wired in this wave because it touches
`src/hal0/api/__init__.py`, which is the orchestrator's file.

## 4. Cognee wrapper contract (Memory-engine wave dependency)

`hal0.mcp.memory` assumes `hal0.memory.cognee_wrapper.CogneeWrapper`
exposes:

```python
async def add(*, text, dataset, tags, source, metadata)
    -> {"id": str, "timestamp": str}     # ISO8601 timestamp
async def search(*, query, limit, dataset, tags, before, after)
    -> list[ItemDict]
async def list_items(*, dataset, cursor, limit)
    -> {"items": list[ItemDict], "next_cursor": str | None}
async def delete(*, ids)
    -> {"deleted": int}                  # count, per ADR-0005 §2
```

`ItemDict` per ADR-0005 §2:

```python
{
    "id":        str,
    "text":      str,
    "score":     float,            # only on search results
    "timestamp": str,              # ISO8601
    "dataset":   str,
    "tags":      list[str],
    "source":    str,              # server-injected from client_id
    "metadata":  dict[str, Any],
}
```

The wrapper module is owned by the Memory-engine wave; this wave
imports it lazily inside `make_dispatcher` so the import order doesn't
matter. If the wrapper lands with a different signature, only the
keyword names in `_memory_*` handlers need adjustment.

## 5. SDK dependency (`mcp`) — pyproject.toml owner

The MCP server modules import `mcp.server.fastmcp.FastMCP`. The
package is NOT yet in `pyproject.toml`; the Memory-engine wave owns
that change. Until then, hal0 boots fine (the MCP modules are only
imported when the orchestrator chooses to mount them), and tests use
a stub at `tests/mcp/conftest.py`.

Recommended dep line: `mcp >= 1.0` (latest minor; SDK is pre-1.0 but
the FastMCP class is stable on the >= 0.9 line).

## 6. Approval inbox SSE / REST wiring

`src/hal0/api/routes/approvals.py` defines the routes and depends on
`request.app.state.approval_queue`. The orchestrator wave needs to:

1. Instantiate `ApprovalQueue()` in the FastAPI lifespan.
2. Stash it on `app.state.approval_queue`.
3. `app.include_router(approvals.router, prefix="/api/agent/approvals",
   ...)` with `Depends(require_writer)` on the POST routes (the GET
   surface uses `require_token`).

The MCP admin server's `build_server()` accepts the queue instance, so
the orchestrator wires the same `ApprovalQueue` into both surfaces.

## 7. Audit log → journald

The MCP admin layer routes every tool invocation through the
`hal0.mcp.audit` structlog logger. The main `hal0.api.__init__` does
not yet call `structlog.configure(...)` (it pulls `get_logger` only),
so audit rows reach journald implicitly via Python's root logger
config and systemd's stdout capture. No extra wiring is required for
the audit story to work; the line in `admin.py` `_audit()` is
sufficient.

If a future audit consumer wants a dedicated stream (separate journald
SYSLOG_IDENTIFIER, e.g.), that's a one-line `structlog.configure()`
amendment in the API factory.
