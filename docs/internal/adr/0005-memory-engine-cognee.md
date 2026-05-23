# ADR 0005 — Memory engine = Cognee (Phase 8 + Phase 9)

- **Status:** Draft
- **Date:** 2026-05-22
- **Drivers:** `/grill-with-docs` session 2026-05-22; PLAN.md §15 Phase 8 + Phase 9; ADR-0004 (agents)

## Context

- Phase 8 v0.2 ships a basic memory MCP server alongside the admin MCP (per ADR-0004).
- Phase 9 expands memory: graph, RBAC, audit, federation, source connectors.
- **Cross-compat requirement:** future RAG services, other agent apps (Claude Code, etc.), bundled agents must all consume the SAME memory.
- Term "memory" overloaded: `pi-memory-md` (project-scoped, markdown, pi-coder's native extension) coexists with hal0 memory MCP (cross-app). They serve different scopes. See CONTEXT.md.
- Home-AI box identity: single-box, no extra services if avoidable, bundle-don't-build.

## Options considered

| Option | Reason rejected (or accepted) |
|---|---|
| **mem0** (Postgres + Neo4j + FastAPI default; LLM-required for fact extraction) | Footprint too heavy; framework lock-in; default opinion forces deps we don't want |
| **Zep / Graphiti** (Neo4j required) | Wins LongMemEval benchmark (63.8% vs mem0 49.0%) but JVM footprint contradicts home-box identity |
| **Letta** (OS-style memory, opinionated agent loop) | Conflicts with ADR-0004 bundle-don't-build — Letta wants to BE the agent, not be a memory source for one |
| **LanceDB + custom SQLite (build ourselves)** | Minimal, but reinvents wheel. Phase 9 delta from "we built a stub" to "we ship graph + Memify + RBAC + connectors" is massive |
| **Cognee** (SQLite + LanceDB + Kuzu defaults, embedded, Apache 2.0) | ACCEPTED — same stack we'd build; opinionated but composable; Phase 9 features come for free |

## Decision

### 1. Engine = Cognee
- **License:** Apache 2.0 (verified 2026-05-22 by reading LICENSE on topoteretes/cognee). Free for self-hosted commercial use. No paid OSS feature gates. The hosted "cogwit" tier is a separate managed-service product, not a license restriction.
- **Defaults:** SQLite (relational + documents) + LanceDB (vector) + Kuzu (graph). All embedded, file-based, zero external services. Optional swap-out support for Postgres / Neo4j / Qdrant / etc. — hal0 stays on the defaults.
- **Adopted from v0.2** (not deferred to Phase 9). The "minimal stub we throw away" approach was rejected because Cognee's basic API is no larger than what we'd write ourselves.
- **Cognee's Python API is the internal contract**; hal0's MCP server (next section) is the public contract on top.

### 2. Public MCP server contract
Endpoint: `/mcp/memory`. Auth = existing Bearer token (ADR-0001). Client identity extracted from Bearer.

**v0.2 tools** (rich schema from day 1 — forward-compat for Phase 9 features):

`memory_add`:
- `text: str` (required)
- `dataset: str = "shared"` — Cognee dataset name; per-client `--private` promotes to `private:<client_id>`
- `tags: list[str] = []` — free-form, open vocabulary, used as filter target
- `source: str` — auto-extracted from Bearer's `client_id`; clients can't lie about their identity
- `metadata: dict = {}` — opaque passthrough
- Returns: `{id: str, timestamp: iso8601}`

`memory_search`:
- `query: str` (required)
- `limit: int = 10`
- `dataset: str | list[str] = "shared"`
- `tags: list[str] = []` (AND-match)
- `before / after: iso8601 = null` (date range)
- Returns: `list of {id, text, score, timestamp, dataset, tags, source, metadata}`

`memory_list`:
- `dataset: str = "shared"`
- `cursor: str = null`
- `limit: int = 50`
- Returns: `{items: [...], next_cursor: str | null}`

`memory_delete`:
- `ids: list[str]` (required)
- If `len(ids) > 1`, gated per ADR-0004; if `len(ids) == 1`, autonomous
- Returns: `{deleted: int}`

### 3. Namespace rule (v0.2)
- **Shared by default.** All writes go to Cognee dataset `"shared"` unless the calling client toggled `--private`.
- **Per-client `--private` toggle** promotes that client's writes to `private:<client_id>` dataset; reads see both `shared` + own private.
- Rationale: consistent with ADR-0001's trust posture (home-LAN open by default; password opt-in). Forcing per-client isolation would contradict that.
- **Migration shared → private is harder than reverse.** Bar for adding to shared is therefore lower than the bar for promoting to private. Reviewers should err shared unless there's a specific reason.

### 4. Multi-user (Phase 9 re-litigation)
- v0.2 assumes single-user box (matching ADR-0001).
- Phase 9 RBAC ADR (ADR-0006 pending) will revisit the namespace rule: likely `shared-by-default within one user, private-across-users`. The v0.2 `--private` toggle is then promoted to a per-user setting.

### 5. Audit log
- Cognee ships built-in rotating audit log (file + console mirror in dev).
- hal0 MCP server enriches each call with `client_id` from Bearer token before the write hits Cognee.
- Mirrored to journald for unified hal0 log inspection.

### 6. Cognee features deferred to Phase 9 (graph-extraction model gate settled in ADR-0014, 2026-05-23)
- **Graph extraction** (Kuzu) — requires structured-output-reliable LLM. Gate behind a configurable model. Default in Phase 9 will route graph builds to OpenRouter (or 70B-class local model if available) because small local models (qwen3:8b-class) flake on Cognee's structured-output prompts. **See [ADR-0014](0014-cognee-graph-extraction-model-gate.md) for the v0.3 decision** — graph defaults OFF, opt-in via dashboard toggle, route enum `upstream` / `primary` / `agent`, eval suite deferred to v0.4.
- **Memify pipeline** — periodic memory hygiene (stale node cleanup, association strengthening, fact reweighting).
- **Source connectors** — 30+ available (Slack, Notion, docs, images, audio). Optional bolt-ons in Phase 9.
- **RBAC + granular permissions** — Cognee's built-in dataset-scoped permissions surface in dashboard.
- **Federation** with external memory MCP servers (Supermemory, Hindsight, mem0 if anyone has one). Pluggable Provider pattern.

### 7. Cross-app integration patterns referenced
- `topoteretes/cognee-integrations/integrations/claude-code` — Claude Code plugin using SIX lifecycle hooks (SessionStart, UserPromptSubmit, PostToolUse, Stop, PreCompact, SessionEnd) + `node_set` tagging (user-context / project-docs / agent-actions). Gives Claude Code users a SECOND path into the same Cognee store, complementary to MCP transport.
- `topoteretes/cognee-integrations/integrations/openclaw-skills` — `SKILL.md` + YAML-frontmatter format + Ingest→Execute→Observe→Amendify self-improving loop. Reference shape if hal0 ever grows agent-side skills (Phase 9+ stretch). Format matches Claude Code's own skill format.

## Consequences

### Positive
- Same Cognee store powers memory in v0.2 AND the eventual RAG corpus in Phase 9 — no data migration when RAG re-enters.
- Adopt-now-extend-later beats build-stub-then-replace.
- License is permissive (no future surprise; safe for `Hal0ai/hal0`'s Apache 2.0).
- Cross-app reach automatic — MCP server is the public contract regardless of internal engine.
- Rich schema from day 1 avoids schema-versioning tax (which we'd hit within months otherwise).
- Built-in audit + RBAC + dataset isolation = features we'd otherwise build.

### Negative / costs
- Cognee's Python API becomes hal0's upstream contract — same upstream-tracking risk as the pi-coder shim per ADR-0004, but for a more central path. Mitigation: pin Cognee version per hal0 release, ship CI smoke test that exercises `memory_add` → `memory_search` round-trip against the pinned version.
- Structured-output reliability on small local models — Phase 9 graph builds may flake on qwen3:8b-class. Mitigation listed in section 6.
- Locked into Cognee's data layout from day 1 — migration off later is painful but possible (SQLite, Lance, and Kuzu are all open formats; an exporter is writable in a week if ever needed).
- One more upstream dependency on a central path. Justifiable because everything we'd build to replace it would itself depend on LanceDB + SQLite + Kuzu underneath.

## Pending items
- Cognee version pin in `pyproject.toml`.
- hal0-memory MCP server implementation (wraps Cognee Python API, exposes the v0.2 schema above).
- Cognee CI smoke test (`memory_add` → `memory_search` round-trip).
- Phase 9 ADR-0006 for advanced memory features (graph, RBAC, federation, multi-user namespace rule).
- Migration importers for Phase 9: `migrate-pi-memory-md.py`, `migrate-hermes-mem.py`, `migrate-mem0.py`.
