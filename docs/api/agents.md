---
title: Bundled agents
description: Phase 8 bundled agent apps — pi-coder and Hermes-Agent. Picker, lifecycle, single-pick rule, approval inbox, track-latest policy.
sidebar:
  order: 7
---

hal0 v0.2 ships **bundled agent apps** — third-party agents installed
alongside hal0, prewired to use hal0 as their local AI provider and to
consume hal0's [MCP servers](./mcp.md). Two options ship at launch:

| Agent          | Shape   | Upstream                | Memory                      |
|----------------|---------|-------------------------|-----------------------------|
| `pi-coder`     | CLI     | `badlogic/pi-mono`      | `pi-memory-md` + hal0 MCP   |
| `Hermes-Agent` | Service | Hermes upstream (user-owned) | hal0 MCP                |

See [ADR-0004](../internal/adr/0004-agents.md) for the full design.

## Bundle, don't build

hal0 does **not** ship a first-party agent runtime. Each bundled agent
installs from its official upstream, byte for byte. hal0's role is the
**prewire** — per-agent setup scripts that point the agent at hal0's
admin + memory MCP servers and at hal0's local OpenAI-compatible API.

The previous haloai first-party agent runtime was stripped — Phase 8 is
not a revival of it. See CONTEXT.md ("agent" disambiguation) for the
two senses of the word.

## Picker

The picker lives in **two places**:

### First-run wizard

Step 7 of the first-run wizard offers `pi-coder`, `Hermes-Agent`, or
"no agent". The pick fires `POST /api/agents/install` with the chosen
name. The Hermes option is disabled (with a tooltip) when the host's
Hermes upstream does not yet ship hal0-awareness.

`install.sh` itself stays **non-interactive**. There is no `--agent`
flag on the installer — the wizard is the only first-run entry point
into the picker, honouring the ADR-0001 non-interactive promise.

### CLI subcommand

After install, the picker is the CLI:

```sh
hal0 agent install <name>            # pi-coder | hermes
hal0 agent install <name> --switch   # atomic uninstall-then-install
hal0 agent uninstall <name>
hal0 agent list
```

## Lifecycle commands

| Command                                | Behaviour                                                              |
|----------------------------------------|------------------------------------------------------------------------|
| `hal0 agent install <name>`            | Install the named bundled agent. 409 if another is already installed.  |
| `hal0 agent install <name> --switch`   | Atomic uninstall-then-install if a different agent is installed.       |
| `hal0 agent uninstall <name>`          | Idempotent. Returns `status="not_installed"` if nothing to remove.     |
| `hal0 agent list`                      | List installed bundled agents (zero or one for v0.2).                  |

REST mirror:

| Method  | Path                                | Notes                          |
|---------|-------------------------------------|--------------------------------|
| `GET`   | `/api/agents`                       | List installed agents          |
| `POST`  | `/api/agents/install`               | Body: `{"name": str, "switch"?: bool}` |
| `GET`   | `/api/agents/{name}/activity`       | Recent MCP audit rows          |
| `DELETE`| `/api/agents/{name}`                | Idempotent uninstall           |

## Single-pick rule

v0.2 enforces **single-pick**: only one bundled agent may be installed
at a time. Calling `hal0 agent install <new>` when another agent is
already installed raises `agent.already_installed` (HTTP 409).

To swap atomically:

```sh
hal0 agent install hermes --switch
```

`--switch` uninstalls the current agent first, then installs the new
one. The operator never ends up with two bundled agents partially
installed.

Power users who want both agents simultaneously have one escape hatch:
install via upstream paths directly and forgo hal0's prewire. This is
not pretty — it is the cost of v0.2's single-pick discipline.

## Asymmetric ownership

The two bundled agents are wired differently on purpose, reflecting
their upstream shapes:

### pi-coder — hal0-owned shim

hal0 owns `installer/agents/pi-coder.sh`. The shim:

- Installs `pi-mono` from upstream.
- Installs `pi-mcp-adapter` — the proxy-tool MCP routing layer that
  keeps each dispatch around 200 tokens instead of dumping the full
  tool catalog into context.
- Leaves `pi-memory-md` in place (project-scoped markdown memory, pi's
  native extension; distinct from hal0's cross-app memory MCP).

Both memory layers coexist: `pi-memory-md` is project-scoped (and what
pi-coder benchmarks well at), hal0's memory MCP is cross-session,
cross-agent, cross-app.

### Hermes-Agent — upstream-owned integration

The Hermes integration grows **upstream**. Hermes is user-owned;
hal0-awareness lands in Hermes itself, not in a hal0-owned shim.
hal0's `installer/agents/hermes-agent.sh` is a one-liner calling
Hermes's own install command and pointing it at the local hal0 admin
MCP endpoint. Hermes runs as `hal0-agent-hermes.service`.

Shape rule for future bundled agents: shim-first, promote to upstream
integration when the upstream maintainer cooperates.

## Track-latest policy

Both bundled agents **track latest** upstream — no version pin. This
diverges from PLAN.md §17's OWUI pin-per-release pattern by intent.
Phase 8 is best-effort, not release-gated.

### Nightly smoke test

The track-latest churn is mitigated by a nightly CI workflow at
`.github/workflows/agent-shim-smoke.yml`. It re-runs the shim install
end-to-end against current upstream and asserts an MCP round-trip
through `/mcp/admin`. If the smoke test breaks, the Phase 8 README
flags the agent as "upstream broke us, fix pending"; the install path
stays available with the risk surfaced.

## Approval inbox

Capital-D destructive MCP tool calls — `model_pull`, `model_delete`,
`slot_create`, `slot_delete`, `slot_restart`, `capability_set`,
`config_write`, `provider_credential_write`, and bulk `memory_delete` —
do not execute immediately. The MCP server returns `pending_approval`
and enqueues an entry the owner must approve before the call runs.

See [MCP servers](./mcp.md) for the full catalog of gated tools.

### Dashboard surfaces

- **Header bell + modal inbox** — canonical per ADR-0004 §5. Badge
  count, modal list of pending requests, approve / deny inline.
  Always visible regardless of which dashboard view you are on.
- **Inline pending chips** — `AgentPendingChip` on the Models / Slots /
  Capabilities pages where a request targets that resource ("1 pending:
  `model_delete qwen3:0.6b`"). Click links to the inbox modal.
- **`/agent` page** — four tabs: Overview, **Inbox**, Activity, Chat.
  The Inbox tab mirrors the bell modal for operators who want a
  dedicated surface.

The bell is the source of truth; inline chips and the `/agent` Inbox
tab are convenience views over the same queue.

### CLI parity

For headless workflows:

```sh
hal0 agent approvals list
hal0 agent approvals approve <id>
hal0 agent approvals deny <id>
```

Same queue the bell reads from. Same approval entries.

### Pending forever, no per-agent trust toggle

- **No auto-expire.** Pending requests sit there until the owner
  decides. "Clear all" cleans the queue when it gets noisy.
- **No per-agent trust mode.** ADR-0004 §5 forbids a "trust this agent
  with destructives" toggle — it would be the prompt-injection footgun
  this whole design exists to prevent. Power users who want full
  autonomy must amend the destructive list in ADR-0004 §4, not flip a
  toggle that bypasses review.

### REST + SSE

| Method | Path                                       | Notes                  |
|--------|--------------------------------------------|------------------------|
| `GET`  | `/api/agent/approvals`                     | List pending entries   |
| `POST` | `/api/agent/approvals/{id}/approve`        | Approve + execute      |
| `POST` | `/api/agent/approvals/{id}/deny`           | Deny (no execution)    |
| `GET`  | `/api/agent/approvals/events`              | SSE: snapshot + live tail |

The SSE stream replays the current pending set on subscribe (so a tab
reopened mid-flight sees the same inbox state) then emits
`enqueued | approved | denied | executed | failed` frames as the queue
mutates.

## Activity tab

The dashboard's `/agent` Activity tab (and `GET /api/agents/{name}/activity`)
walks journald for the `hal0-api` unit and filters by `client_id`
matching the agent name. Each row carries `client_id, tool, args, gated,
outcome, timestamp` — the same audit shape documented in [MCP servers →
Audit log](./mcp.md#audit-log).

Audit rows survive uninstall: removing an agent and reinstalling it
later still surfaces its earlier actions in the Activity tab, so
operators can audit a removed agent's last actions.

## Chat tab (read-only)

The `/agent` Chat tab is a **read-only** transcript view for pi-coder.
It opens an EventSource against `/api/agents/pi-coder/transcript` to
tap pi-coder's PTY output. Sending input from the dashboard is
explicitly out of scope for v0.2 and would need its own ADR.

The component degrades cleanly when the transcript endpoint is not yet
wired: "Transcript stream unavailable — backend tap not yet wired."
