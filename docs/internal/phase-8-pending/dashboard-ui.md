# Phase 8 dashboard-UI — pending items

Wave 2 (this branch) landed the dashboard surface for ADR-0004 +
ADR-0005:

- `/agent` route + 4-tab host (Overview / Inbox / Activity / Chat)
- Header bell `AgentApprovalBell` + modal `AgentApprovalInbox` (canonical
  per ADR-0004 §5)
- Pinia store `useAgentStore` with single shared SSE on
  `/api/agent/approvals/events`
- Sidebar "Agent" link with conditional service-shape link-out via
  `/api/config/urls.hermes`
- First-run wizard step 7 (Agent picker) — pi-coder / Hermes / no-agent
- Inline `AgentPendingChip` on Models / Slots / Capabilities rows
- New REST route `GET /api/agents/{name}/activity` (journalctl + filter)
- γ spec `tests/ui/agent-flow.spec.ts` (3 cases, all green)

## Coupling captured for Wave 3 / future waves

Items resolved after the orchestrator-wave merge are crossed out;
**OPEN** items still need follow-up.

### 1. `/api/config/urls` needs a `hermes` field — OPEN

The sidebar Agent link goes to `urls.hermes` when an installed Hermes
agent is detected. Backend currently exposes `openwebui` + `api` (see
`apiMock.ts` defaults). Until backend grows the `hermes` field, the
sidebar link silently falls through to `/agent` for Hermes too — works
fine, but loses the OWUI-style link-out fidelity.

Owner: backend / `routes/config.py`.

### 2. PTY-tap transcript endpoint — OPEN

`AgentChatTab.vue` currently opens an EventSource against
`/api/agents/pi-coder/transcript` which does not yet exist. The
component degrades cleanly ("Transcript stream unavailable — backend
tap not yet wired.") so this is not a UX bug, but a real PTY tap is
needed before the Chat tab is more than an empty-state surface.

Per the Wave 2 brief's "Chat surface caveat" + ADR-0004 the tab is
read-only display only; sending input is a separate ADR if pursued.

Owner: backend / new route + a `pi-tail` shim spawning a screen/tmux
session.

### 3. Hermes hal0-awareness probe — OPEN

The first-run wizard step 7 disables the Hermes option when
`s.form.hermesHal0Aware === false`, with a tooltip explaining why. The
flag is currently always `true` because no probe endpoint exists. Once
Wave 1's `HermesNotHal0AwareError` is reachable from a probe route, wire
the wizard load step to set the flag based on it.

Owner: backend / `routes/agents.py` (extend with a probe GET).

### 4. Audit row shape may need normalisation — OPEN (watch on first real install)

`/api/agents/{name}/activity` parses journald `MESSAGE` blobs as JSON
and pulls out `event`, `tool`, `args`, `client_id`, `outcome`. The
exact field names + nesting depend on how `hal0.api.__init__` configures
structlog's JSON renderer. If the renderer wraps the payload under a
different key the route filter will silently produce empty results — a
smoke test once a real bundled agent is installed will confirm.

Owner: backend (structlog config) + this route (`agents.py:agent_activity`).

### 5. Pending-chip arg matching — OPEN (watch on first real gated tool)

`agentStore.pendingForResource(kind, target)` matches by `args.model_id
/ args.id / args.name` for models and `args.slot / args.name` for
slots, plus `args.slot + '/' + args.child` for capabilities. The exact
arg shape is whatever the MCP server passes through — fine for today's
audit-shape but worth a contract pin once a real gated tool fires
against the inbox. If the args key changes, the pending chip on the
matching row goes invisible (degrades safely, but loses the inline cue).

Owner: shared — backend's MCP tool definitions + this store.

### 6. Bulk "Clear all" semantics — DEFERRED (Phase 9)

`AgentApprovalInbox.onClearAll()` iterates `deny(id)` per entry — there
is no bulk endpoint per ADR-0004 (audit row per decision). On a queue
of 50+ entries this is 50+ REST calls. Latency is fine for the home-LAN
use case but should be reconsidered if multi-user lands in Phase 9.

Owner: out of scope until Phase 9.

## Out-of-scope (intentional)

- **Per-agent trust toggle.** ADR-0004 §5 forbids this. Don't add it
  even when an operator asks.
- **In-dashboard chat send for pi-coder.** Brief explicitly stops at
  read-only transcript. Sending input needs its own ADR.
- **Hermes-Agent embedded UI.** Sidebar link-out only, OWUI-style.
