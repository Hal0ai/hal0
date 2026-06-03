# ADR 0019 — v0.3 Hermes integration: composer over xterm, plugin host, persona TOML, composite upstream

- **Status:** Accepted
- **Date:** 2026-05-28
- **Drivers:** MASTER-PLAN.md (in scratch:
  `docs/internal/scratch/hermes-research-2026-05-28/MASTER-PLAN.md`) +
  DA-arch / DA-sec-ops / DA-ux review findings
- **Related:** ADR-0004 (agents v0.2 bundling), ADR-0005 (memory
  engine — Cognee), ADR-0011 (agent identity card), ADR-0012 (auth +
  Caddy removal), ADR-0013 (MCP client allow-list), ADR-0018 (upstream
  Hermes pin + weekly drift detection), ADR-0020 (localhost-callback-only
  OAuth PKCE — OpenRouter Phase 0 prereq)

## Context

v0.3 binds Hermes-Agent into hal0 as a bundled, hal0-aware agent
runtime. The end-to-end shape involves a dozen interacting decisions
across UI, backend, systemd, and the MCP surface. Rather than scatter
those decisions across the per-stream ADRs (0011, 0012, 0013, 0014,
0015 each own one slice), this ADR consolidates the **integration**
decisions — the choices that only make sense when read together.

The master plan at `docs/internal/scratch/hermes-research-2026-05-28/MASTER-PLAN.md`
is the long-form record (PRs, sub-plans, DA review tables, security
baseline, generalisation policy). This ADR is the short-form record
the next maintainer reads first.

## Decisions

### 1. Composer + transcript over xterm + PTY

**Decision.** The dashboard chat surface is a React `<textarea>` + WS
`message.delta`/`message.complete` transcript. No xterm. No PTY.

**Driver.** DA-sec-ops MUST-FIX #1 + MASTER-PLAN §1 pivot #1. An
xterm-over-PTY bridge from a `0.0.0.0:8080` API (post-ADR-0012) is
LAN-RCE: any host on the LAN can attach to the PTY and inherit
hermes's process privileges. A React composer + JSON-RPC submit
constrains the input surface to validated frames.

**Alternatives rejected.**

| Option                                  | Reason rejected |
|-----------------------------------------|------------------|
| xterm-over-WS-PTY (original v0.3 plan)  | LAN-RCE class; auth gate is necessary but not sufficient when the data plane is an arbitrary tty |
| Restrict xterm to loopback only         | Forfeits the `hal0.thinmint.dev` use case; doesn't match the dashboard's existing reachability story |

**Where it lives.** `ui/src/dash/agents/chat/Composer.jsx`,
`ui/src/dash/agents/chat/Transcript.jsx`,
`src/hal0/api/agents/chat_proxy.py`.

### 2. Chat-proxy security baseline

**Decision.** WS upgrades on `/api/agents/{id}/{events,submit}` are
gated by an Origin allowlist **and** an HMAC session cookie. Embed
token from `runtime.json` rides outbound in
`Authorization: Bearer <token>` only — never the query string. uvicorn
access log scrubs query strings.

**Driver.** DA-sec-ops MUST-FIX #2 + #3.

**Where it lives.** `src/hal0/api/agents/_auth.py` (cookie),
`src/hal0/api/agents/chat_proxy.py` (token + header injection),
`src/hal0/api/middleware/log_scrub.py` (uvicorn scrub).

### 3. Plugin host with shadow-DOM isolation

**Decision.** hal0-api proxies upstream Hermes plugin manifests
(`/api/dashboard/plugins`) + per-plugin static assets
(`/dashboard-plugins/{name}/...`). Each plugin renders inside a
shadow-DOM iframe with the `__HERMES_PLUGIN_SDK__` global shimmed
from a hal0-owned SDK. v0.3 ships the kanban plugin from upstream.

**Driver.** MASTER-PLAN §1 pivot #2 (host upstream plugins without
inheriting their CSS or globals) + DA-arch #1 (upstream is hot;
isolate so a renamed export doesn't break the dashboard).

**Where it lives.** `src/hal0/api/plugins/`, vendored SDK shape pinned
by ADR-0018 + the `pyproject.toml [tool.hal0.upstream-hermes]` table.

### 4. Persona TOML store + hot-reload nudge

**Decision.** Personas are TOML files under
`/var/lib/hal0/.hermes/personas/`. The active persona is the
contents of `active.txt`. Switching personas writes `active.txt` and
POSTs a JSON-RPC reload nudge to hermes; the system-prompt scope
swaps on the next turn. No restart unless the persona changes the
tool allowlist.

**Driver.** MASTER-PLAN §4 PR-4. A TOML store keeps the operator's
edit surface human-readable; the hot-reload helper avoids the
"why is my new persona not active" surprise.

**Alternatives rejected.**

| Option                                       | Reason rejected |
|----------------------------------------------|------------------|
| JSON store under `/var/lib/hal0`             | TOML reads better for human edits; persona files include long system_prompt bodies that look terrible quoted in JSON |
| Single `personas.toml` (one file, N tables)  | Operator-friendly diff per file; per-file mtime gives the loader free invalidation hints |
| Personas exclusive to one agent              | The route + store are parameterised by agent_id; pi-coder lights up by adding a row in `_AGENT_PERSONAS_ROOTS` |

**Where it lives.** `src/hal0/agents/personas.py` (store + helper),
`src/hal0/api/agents/personas.py` (REST shim).

### 5. Composite `hal0` upstream

**Decision.** A single
`Upstream(name="hal0", kind="slot", url="http://127.0.0.1:8080/v1", slot_name=None)`
replaces per-slot upstream autoregistration. Aggregates every
chat-capable slot's model id through one `/v1/models` response, 5s
TTL cache.

**Driver.** MASTER-PLAN R4 H2. Lemonade serialises chat loading on a
single port, so per-slot Upstream rows pointed at the same URL and
`/v1/models` deduped on id — the second slot looked empty in the
dashboard.

**Where it lives.** `src/hal0/api/__init__.py::_autoregister_slot_upstreams`,
`_fetch_hal0_composite_models`, `_HAL0_MODEL_CACHE`.

### 6. v0.4-ready route shape

**Decision.** Every new endpoint is parameterised by `agent_id` in
the path. v0.3 only resolves `"hermes"`; v0.4 pi-coder lights up by
adding a row to the per-module registry. The route bodies do not
hard-code `"hermes"`.

**Driver.** MASTER-PLAN §5 generalisation. Single-pick (ADR-0004)
forced v0.2 to ship hermes-only; v0.3 cleans up the shape so the v0.4
swap is additive.

**Concrete spots:**

* `_AGENT_PERSONAS_ROOTS` in `hal0.api.agents.personas`
* `_KNOWN_AGENT_IDS` in `hal0.api.agents.restart`
* `_KNOWN_AGENT_IDS` in `hal0.api.agents.memory_stats`
* `hal0-agent@.service` is a template — adding an instance is one
  `hal0-agent@piccoder.service.d/override.conf` away

### 7. Final missing endpoints (PR-11)

**Decision.** Three endpoints land in PR-11 to close the loops PR-6 +
PR-8 + PR-10 flagged:

* `POST /api/agents/{id}/restart` — systemctl restart wrapper. Audit
  log on every invocation (`hal0.agents.audit`). 30s subprocess
  timeout. Typed envelopes for missing-systemctl /
  unit-not-found / timeout / spawn-failed cases.
* `GET /api/agents/skills` — static catalog mirroring the upstream
  Hermes `tools/registry.py` shape + the two hal0-bundled MCP
  servers. Bumps ride ADR-0018's weekly drift PRs.
* `GET /api/agents/{id}/memory/stats` — per-agent memory chip data
  (`writes`, `reads`, `last_write`, `available`). Pulls from the
  in-process `CogneeWrapper`; falls back to `available=false` when
  the wrapper isn't initialised.

**Skills source choice.** Static catalog rather than a live JSON-RPC
query (or `tools/list` MCP call). Rationale:

* The sidebar shows "what could this agent do if running" — a static
  catalog matches that intent; a live tools/list shows the runtime
  view (different).
* Live tools/list requires a session + the chat-proxy auth path —
  too much coupling for a catalog read.
* Bumps are coalesced with ADR-0018's weekly drift cadence so the
  catalog stays in sync with the pin.

### 8. Vendor / proxy / shim policy

**Decision.** From MASTER-PLAN §5:

* **VENDOR** what hal0 deeply integrates with (hal0-cognee
  MemoryProvider, hal0-admin MCP, persona definitions, system prompt
  addendums).
* **PROXY** what upstream hosts (kanban + future plugins, hermes
  REST/WS APIs, model catalogs).
* **SHIM** what bridges the contract (`__HERMES_PLUGIN_SDK__`).

**Why.** Vendored surfaces let hal0 own the integration contract;
proxied surfaces let upstream churn without breaking hal0; shimmed
surfaces are explicit drift candidates (ADR-0018 watches them).

## Status of master plan

The 12-PR master plan landed in this sequence on
`docs/v0.3-agents-mcp-memory`:

| # | PR  | Subject                                              | ADR refs |
|---|------|------------------------------------------------------|----------|
| 1 | #393 | Agent plumbing hot-fix bundle                        | —        |
| 2 | #394 | hal0-cognee MemoryProvider                           | 0005, 0011 |
| 3 | #396 | hermes_provision overhaul                            | 0011, 0013 |
| 4 | #399 | `/api/agents/{id}/personas` endpoints                | this ADR §4 |
| 5 | #395 | `hal0-agent@.service` template + CLI shim            | 0011     |
| 6 | #397 | Plugin host                                          | this ADR §3 |
| 7 | #398 | Chat WS proxy + session REST shim                    | this ADR §2 |
| 8 | #400 | SidebarAgentBlock                                    | —        |
| 9 | #401 | Dashboard v3 agents refactor                          | —        |
| 10 | #404 | HermesChat composer + transcript                     | this ADR §1 |
| 11 | (this PR) | Tests + docs + missing endpoints                 | this ADR §7 |
| 12 | #403 | Upstream pin + weekly hermes-sdk-diff CI             | 0015     |

## Consequences

* **Cleaner separation between hal0 and the bundled agent.** Hermes
  no longer needs hal0-specific patches — every hal0-specific surface
  is in a plugin (memory_cognee), a config table
  (`[tool.hal0.upstream-hermes]`), or a proxy (chat-proxy / plugin
  host). Upstream bumps go through ADR-0018's weekly job.
* **One single-pick agent.** v0.3 ships hermes only (ADR-0004). The
  v0.4 pi-coder swap is additive thanks to §6's route shape.
* **No xterm anywhere.** The composer + transcript will outlive the
  v0.3 milestone; a future "let me drop into a real tty" power-user
  feature has to come back through the auth gate, not through the
  default chat surface.
* **One ADR per future drift event.** When ADR-0018's weekly job
  opens a drift issue, the bump PR records the change in CHANGELOG +
  bumps the pin — no new ADR unless the upstream surface change is
  structural (a renamed event taxonomy field, a shifted plugin SDK
  shape).

## See also

- `docs/internal/scratch/hermes-research-2026-05-28/MASTER-PLAN.md`
  — long-form integration plan with DA review tables, sub-plans, and
  PR sequencing.
- `tests/harness/FINDINGS.md` §43–§45 — δ-harness rows pinning the
  integration round-trip.
- `AGENTS.md` — top-level agents narrative for v0.3.
- `ARCHITECTURE.md` — v0.3 agents subsystem section + module map.
