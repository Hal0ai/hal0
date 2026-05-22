# Phase 8 — Security Review

Scope: `feature/phase-8` (8 commits ahead of `main`, HEAD `45c88d2`). Read-only review per ADR-0001 (installer contract), ADR-0004 (Agents v0.2), ADR-0005 (memory engine).

Worktree: `/home/halo/dev/hal0`
Branch: `feature/phase-8`
Reviewer: security-review skill run, 2026-05-22

## Summary by severity

| Severity | Count |
|----------|-------|
| CRITICAL | 0 |
| HIGH     | 2 |
| MEDIUM   | 2 |
| LOW      | 2 |
| NOTE     | 4 |

Highest-priority items:
1. HIGH — Approval routes mounted on `require_token` instead of `require_writer` (`src/hal0/api/__init__.py:572-577`) — TINY fix
2. HIGH — `logs_tail` MCP tool returns unredacted journald with Bearer tokens / API keys (`src/hal0/api/routes/logs.py:161-226`) — SMALL fix
3. MEDIUM — `logs_tail` in `AUTONOMOUS_READ_TOOLS` set: agent reads raw logs with no approval (`src/hal0/mcp/admin.py:99-110`) — TINY fix (depends on #2 redaction landing)

No CRITICALs.

---

## HIGH

### HIGH-1: Approval inbox POST routes lack writer-scope check — privilege escalation

* File: `src/hal0/api/__init__.py:572-577`, `src/hal0/api/routes/approvals.py:23-31`
* Description: `app.include_router(approvals_routes.router, prefix="/api/agent/approvals", dependencies=_admin_auth)` passes `_admin_auth = [Depends(require_token)]` (line 470). The approve / deny POST routes inside `approvals.py` do NOT declare `require_writer` themselves — the docstring at lines 23-31 explicitly claims "Mounted under Depends(require_writer) by the API factory" but the factory only applies `require_token`. `docs/internal/phase-8-pending/mcp-backend.md` §6 also claims writer is applied; the code disagrees.
* Exploit: any token holder with `read-only` or `v1-only` scope (per `require_writer` scope table) can POST `/api/agent/approvals/{id}/approve` against a pending entry that an admin-scoped agent enqueued. The approval queue's executor closure carries the *originating agent's* Bearer (see `src/hal0/mcp/admin.py:353-360`) and reissues the REST call with that elevated credential — so a read-only credential effectively triggers `model_delete`, `slot_delete`, `slot_restart`, `config_write`, etc. via the approval surface. Independently, the bulk-memory_delete approval path bypasses REST entirely (`memory_dispatcher` runs in-process, `src/hal0/mcp/admin.py:399-400`), so the read-only approver can wipe Cognee memory rows even when the agent's Bearer would have been rejected at REST.
* Fix: change the include-router call to `dependencies=[Depends(require_writer)]`, OR add `dependencies=_writer` at the route level on the two POST routes. Reconcile the GET list/events surface separately if a read-only token should still observe the inbox.
* Size: TINY

### HIGH-2: `logs_tail` MCP tool exposes raw journald (Bearer tokens, API keys)

* File: `src/hal0/api/routes/logs.py:161-226`, `src/hal0/mcp/admin.py:166-175`
* Description: `logs_tail` is in `AUTONOMOUS_READ_TOOLS` (`src/hal0/mcp/admin.py:99-110`) and proxies through to `GET /api/logs`. Neither `list_logs` nor `stream_logs` runs any redaction over journalctl output. ADR-0004 §7 mandates redaction ("Agent never sees the credential it is authenticating with"); `docs/internal/phase-8-pending/mcp-backend.md` §2 documents the gap as OPEN.
* Exploit: any authenticated MCP client (read-only scope is sufficient — `logs_tail` is in the autonomous-read set, no approval needed) calls `logs_tail` and receives raw journald lines for any hal0 unit. journald already accumulates request lines with `Authorization: Bearer …`, `X-API-Key: …`, structured `bearer_token=…` audit context, provider keys logged via warning paths on upstream registry errors (`hal0.upstreams.entry_skipped` etc.), and the cognee `LLM_API_KEY` if a Phase 9 cognify ever fires. Disclosed secrets feed back into the agent's reasoning context.
* Fix: introduce `hal0.api.routes.logs.redact_line()` with a reusable regex set (Bearer header, X-API-Key, common provider prefixes `sk-`, `hf_`, `xai-`, `glsa-`, etc.) and apply over both `lines` in `list_logs` and per-yield in `journalctl_sse`. Tests covering each pattern. Doc §2 already sketches the shape.
* Size: SMALL

## MEDIUM

### MED-1: `logs_tail` classified autonomous-read despite secret-exposure risk

* File: `src/hal0/mcp/admin.py:99-110`
* Description: Even after HIGH-2 redaction lands, classifying a log-dump tool as no-approval autonomous-read is aggressive. Any new structured log key not covered by the redactor instantly becomes an exfiltration vector. Defence-in-depth would move `logs_tail` to `GATED_TOOLS` so the owner sees who is dumping logs.
* Fix: move `"logs_tail"` from `AUTONOMOUS_READ_TOOLS` to `GATED_TOOLS`. Approval prompt copy: "Agent foo wants to read hal0-api logs."
* Size: TINY

### MED-2: Approval-queue executor uses approve-time contextvar resolvers for memory tools

* File: `src/hal0/mcp/admin.py:349-369`, `src/hal0/mcp/memory.py:336-388`, `src/hal0/api/mcp_mount.py:62-77`
* Description: `_executor` captures `bearer + base_url + memory_dispatcher` at enqueue time but NOT the originating caller's `client_id` / `private` flags. `memory_dispatcher` re-reads those from the module-level contextvar via `client_id_resolver` / `private_resolver`. At approval time the approver's REST request is the active request, not the MCP request — `_caller.get()` returns the default `None`, so the dispatcher sees `client_id="anonymous"` and `private=False`. For a bulk-memory_delete enqueued by a `--private` client, the approved execution scopes against the wrong namespace (the wrapper's per-instance `_client_id` is "anonymous" anyway in current wiring, so in practice nothing breaks for v0.2 single-user, but the contract is incorrect and the gap will land Phase 9 multi-user as a confused-deputy bug).
* Fix: capture `(client_id, private)` in the gated branch and pass them to a memory-dispatcher entrypoint that doesn't read the contextvar.
* Size: SMALL

## LOW

### LOW-1: `provider_credential_write` advertised as a gated tool with no REST target

* File: `src/hal0/mcp/admin.py:186-190`, `docs/internal/phase-8-pending/mcp-backend.md` §1
* Description: tool is registered + routed to `POST /api/providers/{name}/credentials`, which doesn't exist. Approval succeeds and the executor 404s. Not a vulnerability today (no privilege gained); becomes one if a future provider-credentials route lands and silently accepts the schema the tool sends (e.g. credentials echoed back in the response). Risk = future drift; track + delete the tool registration until the route is real.
* Fix: comment-out / remove the `provider_credential_write` registration in `build_server` until the REST endpoint exists. The audit log already records "agent tried this", which is the value people might want to keep.
* Size: TINY

### LOW-2: `mcp.tool.invoked` audit row contains full `args` dict

* File: `src/hal0/mcp/admin.py:287-304`
* Description: `_audit` emits `args=args` verbatim. For `memory_add` calls `args.text` is the entire memory item, which may be PII the operator added. journald has the same retention as other system logs; the audit row is intended for forensics, but stamping raw memory text into journald means a journalctl-reading admin (or anyone with `logs_tail` post-redaction) sees memory contents that were filed under "private" namespace via MCP.
* Fix: truncate `args.text` (and other free-form fields) to ~80 chars + ellipsis for the audit row. Full text already lives in Cognee's own datastore.
* Size: TINY

## NOTE

### NOTE-1: PTY-tap chat endpoint absent in this branch

* File: `src/hal0/api/routes/` (no `agents.py` route for transcript)
* Description: Brief flagged `/api/agents/pi-coder/transcript` as a Wave-2 stub. Code grep finds no such route in this branch — neither stub nor handler. Confirms expectation (nothing to leak for v0.2).
* Fix: when transcript surface lands, treat per-user scoping as a P0 requirement before merge.
* Size: n/a

### NOTE-2: Memory wrapper single-instance hardcoded `client_id="anonymous"`

* File: `src/hal0/api/__init__.py:391`, `src/hal0/memory/cognee_wrapper.py:160-206`
* Description: `app.state.memory_wrapper = CogneeWrapper()` constructs one wrapper with default `client_id="anonymous"`. `memory.py` resolves a per-request `client_id` via `client_id_resolver` but the wrapper API itself does not accept a per-call client_id; it scopes everything via `self._client_id`. Net effect today: namespace isolation between clients is non-functional, but irrelevant for v0.2 single-user. ADR-0006 (multi-user) MUST address this — either per-request wrapper construction, or threading `client_id` through every wrapper method.
* Fix: deferred to ADR-0006 / Phase 9 — flag it as a blocker for multi-user.
* Size: LARGE (Phase 9)

### NOTE-3: `X-hal0-Private` header trusted from any client

* File: `src/hal0/api/mcp_mount.py:104`
* Description: Acceptable per brief for v0.2 single-user. A future multi-user world must scope private namespace to the *server's* idea of identity rather than letting any client toggle into `private:<their-bearer-label>`. Coupled with NOTE-2 the issue is moot today but should land in the multi-user ADR.
* Fix: defer to ADR-0006.
* Size: MEDIUM (Phase 9)

### NOTE-4: pi-coder supply chain — track-latest curl-pipe-style npm/cargo install

* File: `installer/agents/pi-coder.sh:59-86`
* Description: `npm install -g pi-mono`, `npm install -g pi-mcp-adapter`, and cargo equivalents are unpinned and unsigned. ADR-0004 §3 documents this as intentional ("track-latest", nightly smoke catches breakage). Supply-chain compromise of `badlogic/pi-mono` or its npm namespace yields code-exec as the hal0 service user on every install. No fix without abandoning the upstream-tracks-itself design choice; flagged for visibility.
* Fix: ADR-0004 §3 already accepted this risk. Compensating control: sign known-good upstream releases and verify, or pin minor versions with a bump cadence. Not a v0.2 blocker.
* Size: LARGE (architectural)

---

## Items examined and ruled OUT

* MCPAuthMiddleware contextvar leakage across concurrent requests — `BaseHTTPMiddleware` propagates context to inner task via anyio's `copy_context()` snapshot; `_caller.set/reset` is task-local. No leakage.
* Approval queue race conditions — `_lock` covers state transitions; dedup pop on approve/deny prevents stale dedup pointers.
* `installer/uninstall.sh` agent name traversal — `basename .toml` strips dirs; `bash "${COMPANION}"` is quoted; `_driver_for` rejects non-bundled names at the Python layer.
* CLI `agent_commands.py approve <id>` — local CLI invocation, untrusted-input precedent #3 (env / CLI args trusted).
* First-run wizard `agentChoice` shell injection — value is JSON-encoded, server validates against `BUNDLED_AGENTS` frozenset before any subprocess; no shell interpolation of the name.
* MCP tool argument injection into REST path templates — `_format_url` uses Python str.format; values are str()-cast. The REST routes themselves match literal segments via FastAPI; existing surface, out of scope for this PR.
* JSON injection / Bearer in pi-mcp-adapter.json — Bearer comes from `/etc/hal0/tokens.toml` (operator-controlled); file is `json.dumps`'d (no string concat). Adapter config file mode left to umask — handled by EXCLUSION #2 (secrets on disk).
* Approval SSE leakage — args may include slot names / model ids, but no credentials enter the gated args today (`provider_credential_write` 404s before any creds land). Re-evaluate when that route ships.
