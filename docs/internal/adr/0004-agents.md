# ADR 0004 — Agents (Phase 8, v0.2)

- **Status:** Draft
- **Date:** 2026-05-22
- **Drivers:** `/grill-with-docs` session 2026-05-22; PLAN.md §15 Phase 8

## Context

Phase 8 v0.2 land three things together: a bundled agent app, a hal0 admin MCP server (the skill surface the agent uses to drive hal0), and a memory MCP whose engine is Cognee (see ADR-0005). This ADR scope is the first two. Memory engine is ADR-0005 problem.

PLAN.md §1 Strip historically list "Agents subsystem" as gone. Phase 8 Agents NOT revival of the haloai first-party agent runtime. Clean sheet. Bundle, not build. This ADR re-justify the re-entry on that basis so the Strip line stay honest.

Two candidate upstream agents in scope right now:

- `pi-coder` from `badlogic/pi-mono` — CLI shape. Terminal tool. No daemon.
- `Hermes-Agent` — service shape. User own it. Long-running. Web surface of its own.

Word "agent" overloaded inside hal0. CONTEXT.md carry the disambiguation table; this ADR mean "bundled third-party agent app the user run on top of hal0", not "slot", not "capability child", not "MCP client in general".

## Decision

### 1. Bundle, don't build

Bundle third-party agent apps. Each install from official upstream, byte for byte. hal0 ship per-agent setup scripts that prewire hal0 as the agent's local AI provider plus connect to hal0's MCP servers. Single-pick at install — one bundled agent at a time for v0.2. Future bundled agents follow same shape.

No first-party agent runtime. No hal0-authored prompts. No hal0-authored tool dispatch. The shim only do install + wire.

### 2. Picker + lifecycle

- Picker live in two places: first-run wizard step, plus `hal0 agent install <name>` CLI subcommand for after the fact.
- install.sh stay non-interactive. NO `--agent` flag. Non-interactive promise from ADR-0001 install path stay intact.
- Lifecycle ops: `hal0 agent install <name>`, `hal0 agent uninstall <name>`, `hal0 agent list`.
- `hal0 agent install <new> --switch` do atomic uninstall-then-install. Operator never end up with two bundled agents partially installed.
- Single-pick enforced for v0.2 by `hal0 agent install` refusing when an agent already bundled, unless `--switch`.

### 3. Runtime shape (honor upstream native, asymmetric)

Refuse to flatten the two candidate agents into one shape. They are different shapes upstream; pretending otherwise force a bad fit on one of them.

- `pi-coder` = CLI. No systemd unit. User invoke from terminal. No dashboard surface in v0.2.
- `Hermes-Agent` = service. Runs as `hal0-agent-hermes.service`, instance of `hal0-agent@.service` template that mirror existing `hal0-slot@.service`. Sidebar link-out OWUI-style in dashboard — same pattern as OWUI tile, no in-dashboard embedding.

Track latest upstream for BOTH agents. Diverges from PLAN.md §17 OWUI row's pin-per-release pattern, by intent. Phase 8 is best-effort, not release-gated.

Mitigation for track-latest churn: nightly CI smoke test re-runs `installer/agents/pi-coder.sh` end-to-end against current upstream and asserts an MCP round-trip through `/mcp/admin`. Same for Hermes-Agent shim once that lands. Smoke broke ⇒ Phase 8 README flag the agent as "upstream broke us, fix pending"; install path stay available but flag the risk.

### 4. hal0 admin MCP server

Endpoint: `/mcp/admin`. Auth = existing Bearer token from ADR-0001, no new credential type. Agent send `Authorization: Bearer <token>` same as dashboard would.

**Tool catalog rule:** a tool ships iff it maps to an existing `/api/*` route the dashboard already calls. No new privileged surface get invented for the agent. Things like `hal0 update` and restart-`hal0-api` deliberately do NOT ship — no API route exist, or the action is self-destructive (restart-api kill the MCP server itself mid-call).

#### Autonomous read

| Tool | Maps to |
|---|---|
| `slot_list`, `slot_status` | `GET /api/slots/*` |
| `model_list` | `GET /api/models` |
| `hardware_probe` | `GET /api/stats/hardware` |
| `logs_tail` | `GET /api/logs` (with Bearer-token redaction server-side) |
| `capability_list` | `GET /api/capabilities` |
| `provider_list` | `GET /api/providers` |
| `version_info` | `GET /api/version` |

#### Autonomous write

| Tool | Maps to | Why autonomous |
|---|---|---|
| `model_swap` | `POST /api/slots/{name}/model` | Routine; recoverable by another swap |
| `memory_add` / `memory_search` / `memory_list` | hal0-memory MCP | Memory is the agent's primary surface; gating each write defeat the feature |
| `memory_delete` (single record, `len(ids)==1`) | hal0-memory MCP | Recoverable; single-row scope |

#### Gated destructive (requires user approval)

| Tool | Maps to | Why gated |
|---|---|---|
| `model_pull` | `POST /api/models/pull` | Long-running, disk-consuming, holds a slot busy |
| `model_delete` | `DELETE /api/models/{id}` | Data loss |
| `slot_create` | `POST /api/slots` | Spawns systemd unit + consumes port + GPU memory |
| `slot_delete` | `DELETE /api/slots/{name}` | Removes user-configured slot |
| `slot_restart` | `POST /api/slots/{name}/restart` | Interrupts in-flight requests |
| `capability_set` | `POST /api/capabilities` | Reconfigures multiple slots via orchestrator |
| `config_write` | writes to `/etc/hal0/*.toml` | Persistent config change |
| `provider_credential_write` | `POST /api/providers/{name}/credentials` | Stores upstream API keys |
| `memory_delete` (`len(ids) > 1`) | hal0-memory MCP | Bulk delete; not autonomous-recoverable |

**Future tools land via additive ADR amendment.** Rule constrains scope deliberately. New tool ⇒ new ADR or amendment to this one, not a quiet PR.

### 5. Approval UX (gated destructives)

Pipeline: agent call MCP tool → server detect tool is gated → server enqueue approval request and return `pending_approval` response immediately → user approve or deny via dashboard or CLI → server execute the original call or cancel.

- **Bell+inbox in dashboard header.** Badge count, modal list of pending requests, approve/deny inline. This is the source of truth. Always visible regardless of which view the user is on.
- **Inline pending indicators** on Models / Slots / Capabilities pages. Context-rich nudge ("1 pending: model_delete `qwen3:0.6b`"), link out to inbox. Inline view never the sole surface — bell is canonical, inline is convenience.
- **Pending forever.** No auto-expire timer. If the user ignore a request for a week, it sit there waiting. "Clear all" button for cleanup when the queue get noisy.
- **NO per-agent trust mode toggle.** Gating must be unconditional. Power user who want full autonomy must amend this ADR's destructive list, not flip a toggle that bypass review. Toggle would be the prompt-injection footgun this whole ADR exist to prevent.
- **CLI parity** for headless workflow: `hal0 agent approvals list`, `hal0 agent approvals approve <id>`, `hal0 agent approvals deny <id>`. Same queue the bell read from.

Server-side store the pending queue. Agent's MCP loop decide whether to wait synchronously on the `pending_approval` response, poll, or abandon and try again later — that is the agent's policy, not hal0's.

### 6. Ownership (asymmetric)

- **pi-coder shim**: hal0 owns `installer/agents/pi-coder.sh`. Script install `pi-mono` upstream, install `pi-mcp-adapter` (a proxy-tool MCP routing layer — ~200 tokens per dispatch instead of dumping the full tool catalog into context), and leave `pi-memory-md` extension in place (project-scoped markdown memory, distinct from hal0's cross-app memory MCP per ADR-0005). Both memory layers coexist; they target different scopes.
- **Hermes-Agent**: native hal0-awareness grow upstream. User own Hermes — the integration get done in Hermes itself, not in a shim. hal0's Hermes shim is a one-liner calling Hermes's own install command and pointing it at the local hal0 admin MCP endpoint.
- **Default for future bundled agents**: shim-first. Promote to upstream integration when the upstream maintainer cooperate. Shim is the always-available fallback; native integration is the goal where reachable.

### 7. Server-side hardening

- `logs_tail` is autonomous-read. Redact Bearer tokens (and other obvious secrets — provider API keys in env dumps, etc.) from log lines server-side before serving. Agent never see the credential it is authenticating with.
- Audit log entry enriched with `client_id` extracted from Bearer token on every MCP call. Same audit stream also feed ADR-0005's memory audit, so "who wrote this memory" answer is forensically grounded.

## Consequences

### Positive

- Bundle approach let v0.2 ship without a multi-month agent-runtime engineering project. Phase 8 become a wiring job, not a build job.
- MCP is the cross-app contract. Claude Code, future RAG service, other agents integrate without hal0-specific glue. The admin MCP server is the public surface; bundled agents are just the first consumers.
- Single source of truth for what an agent can do: the admin MCP tool catalog wraps existing `/api/*`. No parallel privileged surface to keep in sync.
- Destructive gating prevent prompt-injection footguns. Untrusted text in agent context can't talk the agent into nuking a slot — the user has to click approve.
- Two-tier scope (autonomous read+routine write vs gated destructive) match the actual user trust posture: trust the agent to do its job, don't trust it to nuke the install.

### Negative / costs

- Track-latest divergence from OWUI's pin-per-release pattern. Name this in PLAN.md §17 risks as a new row when this ADR land — readers should not have to grep the ADR set to discover the divergence.
- Asymmetric per-agent UX (CLI pi-coder vs service Hermes) is accepted as honesty of bundling. Bundling means inheriting upstream shape, not laundering it through a hal0 standard wrapper.
- Power user who want both bundled simultaneously is blocked by single-pick. Escape hatch: install via upstream paths and forgo hal0 prewire. Not pretty, but it's the cost of v0.2 enforcement.
- pi-coder shim is a recurring maintenance burden. Upstream release-note tracking; realistic burden a few months on average. Nightly smoke test catch the breakage; human still have to fix the shim.
- Adopting an opinionated destructive list means we will be wrong at least once. Easy to revise via ADR amendment, but real surface area to revisit. First wrong-call is most likely on `model_pull` (is it really worth gating a routine pull?) or `slot_restart` (interrupts vs routine).

## Pending items

- Exact REST routes for approval inbox — hal0-api PR work. Shape: `GET /api/agent/approvals`, `POST /api/agent/approvals/{id}/approve`, `POST /api/agent/approvals/{id}/deny`. Names not load-bearing yet.
- `hal0-agent@.service` systemd template — mirrors `hal0-slot@.service`. Sketch only at this point.
- Nightly smoke-test CI workflow at `.github/workflows/agent-shim-smoke.yml`. Re-runs the shim install + asserts an MCP round-trip on a fresh LXC.
- `pi-mcp-adapter` version-pin policy. Currently inheriting pi-mono's "track-latest" by default. May need its own pin if pi-mcp-adapter ship breaking changes more often than pi-mono.

## References

- [ADR 0001 — Installer contract](./0001-installer-contract.md) — Bearer token + non-interactive install promise this ADR honor.
- [ADR 0005 — Memory engine = Cognee](./0005-memory-engine-cognee.md) — the other half of Phase 8 v0.2; `memory_*` tools in this ADR's catalog live there.
- PLAN.md §15 Phase 8 — scope and sequencing.
- PLAN.md §17 — risks register; track-latest divergence row to be added.
- CONTEXT.md — "agent" disambiguation table.
