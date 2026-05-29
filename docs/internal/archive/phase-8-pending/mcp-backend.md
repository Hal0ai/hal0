# Wave 1 (MCP backend) — pending items

Items the MCP-backend wave hit while building the admin + memory MCP
servers per ADR-0004 / ADR-0005. Resolved items are crossed out for the
historical record; **OPEN** items still need follow-up.

## 1. ADR-0004 §4 routes that don't match live REST surface — DOCUMENTED, partial OPEN

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
calls 404 until the route lands. The public docs at `docs/api/mcp.md`
also call out the ADR-vs-live reconciliation table so external readers
see the same source-of-truth.

**OPEN — `provider_credential_write` REST route still missing.** The
provider team needs to land `POST /api/providers/{name}/credentials`.
Until then, that one gated tool 404s when the user approves it.

## 2. `logs_tail` Bearer-token redaction (ADR-0004 §7) — OPEN

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

**STOPPED per brief rule; not modified.** Still open after the
orchestrator-wave merge — `logs_tail` continues to stream raw lines.

## 3. ~~Bearer → client_id extraction surface~~ — RESOLVED

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

~~This is intentionally NOT wired in this wave~~ — landed via the
orchestrator-wave merge (`108d1fb`); the `bearer_resolver` hook is
populated from `AuthIdentity` on every MCP request.

## 4. ~~Cognee wrapper contract (Memory-engine wave dependency)~~ — RESOLVED

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

Landed in `77effca feat(phase-8): Cognee memory engine + wrapper
(ADR-0005)`. The lazy import inside `make_dispatcher` resolves the
wrapper at call time, so import ordering remains a non-issue.

## 5. ~~SDK dependency (`mcp`) — pyproject.toml owner~~ — RESOLVED

The MCP server modules import `mcp.server.fastmcp.FastMCP`. The
package is NOT yet in `pyproject.toml`; the Memory-engine wave owns
that change. Until then, hal0 boots fine (the MCP modules are only
imported when the orchestrator chooses to mount them), and tests use
a stub at `tests/mcp/conftest.py`.

Landed via the orchestrator wave (`108d1fb`); `mcp` SDK is now a
declared dependency in `pyproject.toml` and the MCP modules import
unconditionally.

## 6. ~~Approval inbox SSE / REST wiring~~ — RESOLVED

`src/hal0/api/routes/approvals.py` defines the routes and depends on
`request.app.state.approval_queue`. The orchestrator wave needs to:

1. Instantiate `ApprovalQueue()` in the FastAPI lifespan.
2. Stash it on `app.state.approval_queue`.
3. `app.include_router(approvals.router, prefix="/api/agent/approvals",
   ...)` with `Depends(require_writer)` on the POST routes (the GET
   surface uses `require_token`).

Landed via the orchestrator wave (`108d1fb`); a single `ApprovalQueue`
is instantiated in the FastAPI lifespan and stashed on
`app.state.approval_queue`, then wired into both the MCP admin
server's `build_server()` and the approvals REST router (under
`Depends(require_writer)` on the POST routes).

## 7. Audit log → journald — RESOLVED

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
