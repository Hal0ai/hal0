# hal0 — Glossary

Project terminology. Update inline as new terms get resolved during design sessions or PR reviews. NOT a spec — this is just the canonical names + short disambiguators. For decision rationale, see `docs/internal/adr/`.

---

## agent

Two distinct senses in this repo. Disambiguate by context.

1. **internal dev sense** — a Claude teammate (the multi-agent fan-out pattern used in `docs/models-slots-impl-plan.md` and `CONTRIBUTING.md:93`). Never user-facing. About *how we build hal0*, not what hal0 is.
2. **product sense** — a Phase 8 bundled agent app (`pi-coder` or `Hermes-Agent`). User-facing. About *what users do with hal0*. See ADR-0004.

When in doubt, ask which sense applies before writing the word.

## Agents subsystem

**Stripped.** Previously a haloai-style first-party agent runtime (PLAN.md §1 Strip listed it as gone). The Phase 8 product-sense agents (above) are NOT a revival — they're third-party bundled apps with a fundamentally different architecture. Do not reintroduce the first-party runtime.

## bundled agent

Phase 8 product feature. A third-party agent application installed alongside hal0, prewired to use hal0 as its local AI provider and to consume hal0's MCP servers. v0.2 supports `pi-coder` (CLI shape) and `Hermes-Agent` (service shape). Single-pick at install. See ADR-0004.

## Cognee

The embedded memory engine adopted from v0.2 (Apache 2.0). Defaults: SQLite + LanceDB + Kuzu — all embedded, no external services. Powers `/mcp/memory`. See ADR-0005.

## dataset (Cognee)

Cognee's namespace primitive. hal0's namespace rule (v0.2): default `shared` for all clients; per-client `--private` toggle promotes that client's writes to `private:<client_id>`. Multi-user revisits the rule in Phase 9 (ADR-0006 pending). See ADR-0005 §3.

## MCP server (hal0-exposed)

hal0 exposes two MCP servers (Phase 8, v0.2):
- `/mcp/admin` — wraps existing `/api/*` routes (slot/model/hardware/log admin). Tool catalog rule: ships iff it maps to an existing route. See ADR-0004 §4.
- `/mcp/memory` — wraps Cognee's Python API. See ADR-0005 §2.

Both reachable by any MCP-speaking client: bundled agents, Claude Code, future RAG services. Auth via existing Bearer token (ADR-0001).

## memory

Two distinct memory surfaces coexist on a hal0 box. They serve different scopes — don't displace one with the other.

- **pi-memory-md** — project-scoped markdown files in the repo. Pi-coder's native extension, kept in place by the hal0 pi-coder shim. NOT touched by hal0's memory MCP.
- **hal0 memory MCP** — cross-session, cross-agent, cross-app. Backed by Cognee. Default namespace `shared`. See ADR-0005.

## pi-coder

Bundled agent option (CLI shape). Upstream: `badlogic/pi-mono`. Minimal-by-design (4 tools: read/write/edit/bash; no native MCP, no native memory). hal0's pi shim adds `pi-mcp-adapter` (MCP routing) + leaves `pi-memory-md` in place. Track-latest upstream (NOT pinned). See ADR-0004 §3, §6.

## Hermes-Agent

Bundled agent option (service shape). User-owned upstream — grows native hal0-awareness on the Hermes side rather than via a hal0-owned shim. Runs as `hal0-agent-hermes.service`. Sidebar link-out OWUI-style in dashboard. See ADR-0004 §3, §6.

## skills

Overloaded THREE ways. Default to sense (3) in hal0 product context.

1. **Claude Code skills** — the markdown + YAML-frontmatter format Claude Code itself uses (e.g. `~/.claude/skills/`). Internal tooling for dev sessions; not a hal0 product feature.
2. **stripped haloai skills subsystem** — historical, gone (PLAN.md §1 Strip section). Do not reintroduce.
3. **hal0 platform skills** = MCP tools exposed by the admin MCP server (Phase 8). An agent calling `/mcp/admin` sees `slot_list`, `model_swap`, etc. as its "skills." This is the sense used in hal0 product copy and ADR-0004.

(Possible Phase 9+ stretch: agent-side skills in the `cognee-integrations/openclaw-skills` style — `SKILL.md` + YAML frontmatter + self-improving loop. If we ever ship that, it's a separate noun and gets its own gloss entry.)

## slot

Existing concept (PLAN.md §2). `hal0-slot@.service` template unit, parameterized by slot name. Runs a model under a provider. NOT a memory or RAG primitive — slots serve inference, memory lives in `/mcp/memory`.

## two-tier scope

Access-control pattern for the admin MCP per ADR-0004. Routine ops (slot status, `model_swap`, `hardware_probe`, `memory_add`, etc.) = autonomous. Capital-D destructives (`model_pull`, `slot_delete`, `config_write`, `memory_delete` >1 record, etc.) = gated via the dashboard approval inbox. No per-agent trust toggle (destructives must always be approved).
