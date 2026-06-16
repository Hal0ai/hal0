# AGENTS.md

Agent-facing entry point: what kind of work happens here, what shape it
takes in v0.3, and where to find the contracts a teammate AGENT needs.

## hal0 in one paragraph

hal0 is an open-source home AI inference platform: a single FastAPI
service (`hal0-api`) that orchestrates Lemonade-served chat / embed /
voice / image models, plus a v0.3 bundled-agent surface where a
third-party agent runtime (Hermes-Agent today, pi-coder in v0.4) runs
as a sibling systemd unit with hal0 wired in as its local AI provider.

## v0.3 agent surface (current)

A v0.3 bundled agent is:

* A systemd unit `hal0-agent@<id>.service` (template, parameterised by
  agent id). v0.3 ships `hermes` only — see ADR-0004 single-pick.
* Provisioned by `hal0 agent provision hermes` → the 12-phase
  `src/hal0/agents/hermes_provision.py` orchestrator
  (preflight → install → env_probe → home_init → config_write →
  mcp_wire → context_link → namespace_register → model_automap →
  voice_wire → smoke_tests → self_report). Idempotent + checkpointed
  via `/var/lib/hal0/state/agents/hermes/provision.json`.
* Reachable through hal0-api's chat surface — `/api/agents/{id}/{events,
  submit,session/*}` (PR-9 WS proxy + REST shim) — never directly
  exposed to the browser. Hermes itself binds 127.0.0.1:9119 inside the
  hal0-agent unit's sandbox.
* Rendered in the v3 dashboard's `<AgentView>` tab with two surfaces:
  the SidebarAgentBlock (service status, persona picker, memory chip,
  skills list, approvals bell) and the HermesChat composer +
  transcript (PR-10 — React composer + WS transcript, no xterm / PTY).

### Install + lifecycle

```
sudo hal0 agent provision hermes        # one-shot 12-phase bootstrap
sudo systemctl status hal0-agent@hermes  # unit health
hal0 agent personas                      # list personas (TOML store)
hal0 agent personas activate coder       # swap active persona
```

The provisioner is the authoritative install path for a hal0-bundled
agent. The PROVISIONER renders config.yaml, hermes.env, persona TOMLs,
the MCP server entries (`hal0-admin`, `hal0-memory`), the system prompt
addendum, and the composite `hal0` upstream config — all idempotently.

### Personas

A persona is a TOML file under `/var/lib/hal0/agents/hermes/personas/`
declaring (`id`, `display_name`, `summary`, `system_prompt`,
`tools_allowed`, `memory_namespace`, `preferred_upstream`,
`preferred_model`, `approval.{default_policy, auto_approve,
require_approval}`). v0.3 seeds two: `hermes` (general) and `coder`.
The active persona is the contents of `active.txt`; switching personas
swaps the system-prompt scope on the next turn without restarting the
agent process. Configured per-agent via `GET/POST
/api/agents/{id}/personas[/{pid}/activate]` (PR-4).

### MCP wiring

`hermes_provision` registers `hal0-memory` and `hal0-admin` as MCP
servers in hermes's config.toml. The plugin slot for memory is
implemented by the hal0-bundled hermes plugin at
`src/hal0/agents/hermes/plugins/memory_cognee/` — a `MemoryProvider`
subclass that turns memory into part of the system prompt
(`system_prompt_block`) rather than a tool the agent has to remember
to call. Plugin host (PR-7) lets the dashboard mount upstream Hermes
plugin bundles (the v0.3 kanban plugin today) inside an iframe with a
shadow-DOM-isolated SDK shim.

### Approvals + audit

Every gated tool invocation (model_pull, slot_delete, config_write,
memory_delete >1) goes through the approval inbox at
`/api/agent/approvals`; the SidebarAgentBlock's approvals bell + the
`hal0 agent approvals` CLI subcommand share the same lifespan-scoped
`ApprovalQueue`. Audit rows flow through journald via the
`hal0.mcp.audit` logger; `GET /api/agents/{name}/activity` reads them
back for the dashboard Activity tab.

## Agent skills

### Issue tracker

GitHub Issues on `Hal0ai/hal0` via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary — `needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context. `CONTEXT.md` at root; ADRs at `docs/internal/adr/`. See `docs/agents/domain.md`.

## v0.3 agent contracts (for deeper dives)

* [`docs/agents/hermes/CONFIG.md`](docs/agents/hermes/CONFIG.md) —
  persona TOML, overrides.yaml, allowlist.toml, runtime.json,
  hermes.env, plugin manifests, hot-reload-vs-restart semantics.
* [`docs/agents/hermes/SERVICE.md`](docs/agents/hermes/SERVICE.md) —
  hal0-agent@.service unit shape, sandboxing, restart endpoint
  reference.
* [`docs/internal/adr/0004-agents.md`](docs/internal/adr/0004-agents.md)
  — bundling decision + single-pick.
* [`docs/internal/adr/0011-agent-identity-cards.md`](docs/internal/adr/0011-agent-identity-cards.md)
  — agent identity card schema.
* [`docs/internal/adr/0013-mcp-client-allow-list.md`](docs/internal/adr/0013-mcp-client-allow-list.md)
  — server-axis + tool-axis default-deny.
* [`docs/internal/adr/0018-upstream-hermes-pin-and-upgrade.md`](docs/internal/adr/0018-upstream-hermes-pin-and-upgrade.md)
  — upstream pin + weekly drift detection.
* [`docs/internal/adr/0019-v0_3-hermes-integration.md`](docs/internal/adr/0019-v0_3-hermes-integration.md)
  — v0.3 integration roll-up (composer over xterm, plugin host,
  persona TOML, composite upstream).

## Shipping: deploy + PR workflow (how teammate agents land work here)

This repo is worked by **multiple parallel Claude sessions** against one
shared runtime (CT 105, `/opt/hal0`). Follow this so two agents never
collide and nothing reaches CT 105 by hand-guessing.

**1. Isolate every change in a worktree off `main`.** Never edit on a
branch another agent owns, and never stack new work on an unmerged feature
branch unless you intend a stacked PR. Pin to `main` so your diff is
reviewable independently:

```bash
git fetch origin --prune
git worktree add -b <type>/<slug> ~/dev/wt/<slug> origin/main
```

If your changes were authored on top of someone else's branch, re-base them
onto `main` with `git apply --3way` (production regions are usually
disjoint; only test mocks tend to conflict — adapt the assertion to main's
fixtures, don't pull in the other branch's unmerged mock).

**2. Claim before you touch the shared tree.** Local board:
`~/.claude/bin/wip claim "<intent>" <files…>`. For CT 105 itself:
`~/.claude/bin/wip hal0 claim "<intent>" /opt/hal0` — and check
`wip hal0 status` first; if it's not on `main` or has tracked edits,
another session is mid-deploy, so coordinate, don't reset over it.

**3. Verify on the branch before deploying.** `tsc --noEmit` (ui),
targeted `pytest` (not the whole suite — it hangs on this dev box), and the
relevant `playwright … --project=chromium` spec (forced-mock). Build the UI
clean (`rm -rf node_modules/.vite dist && npm run build`) — `ui/dist` is
gitignored, so a stale bundle hides UI changes.

**4. Deploy / preview to CT 105 with `scripts/deploy.sh` — never by hand.**
A bare `git reset` updates source but leaves the served bundle stale; the
script folds in the UI rebuild, the group-share perms re-assert, the
`hal0-api` restart, and a health check. To preview an **unmerged** branch:

```bash
ssh hal0 'cd /opt/hal0 && sudo bash scripts/deploy.sh --ref origin/<your-branch>'
```

It refuses to reset over another session's uncommitted tracked edits unless
`--force`. After this, CT 105 is **ahead of `main`** until your PR merges.

**5. PR against `main`; merge base-first.** Open the PR (`gh pr create
--base main`), let CI go green, get approval. Stacked PRs merge their base
first. After merge, reconcile CT 105 back to trunk:
`ssh hal0 'cd /opt/hal0 && sudo bash scripts/deploy.sh --ref origin/main'`,
then clean `[gone]` branches.

**6. Record memory-worthy outcomes** (PR/merge, gotcha, decision) to the
hal0 Hindsight engine via the `hal0-memory` skill — see the standing rules
in `CLAUDE.md`.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

When the user types `/graphify`, invoke the `skill` tool with `skill: "graphify"` before doing anything else.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- Dirty graphify-out/ files are expected after hooks or incremental updates; dirty graph files are not a reason to skip graphify. Only skip graphify if the task is about stale or incorrect graph output, or the user explicitly says not to use it.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
