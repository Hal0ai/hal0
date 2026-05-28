# ADR 0017 — Bell + inbox approval UX for destructive MCP calls (v0.3)

- **Status:** Accepted (post-implementation; UX shipped in Epic #322
  via PRs #321 / #328 / #329 / #330 / #332)
- **Date:** 2026-05-27
- **Drivers:** ADR-0004 §5 sketched the two-tier approval pattern.
  Code + `release-manifest.md` carry the contract, but the pattern
  was never given its own ADR. Epic #322 then shipped the dashboard
  surface (footer bell + inbox modal) on 2026-05-25. This ADR
  documents the shipped rule so future contributors (including
  third-party MCP server authors per ADR-0015) have a single
  reference for "how do I classify my tool?"
- **Related:** ADR-0004 (agents — original two-tier sketch), ADR-0013
  (per-agent MCP-client allow-list — `tools.gated` tier maps onto
  this), ADR-0015 (MCP as host platform — third-party MCP servers
  inherit this gating contract).

## Context

ADR-0004 §5 introduced the two-tier scope:

- **Autonomous read / routine write** — agent calls proceed without
  user intervention.
- **Gated destructive** — call enqueues, returns `pending_approval`
  immediately, user must approve via dashboard or CLI before the
  call executes.

The intent was to gate prompt-injection footguns: untrusted text in
an agent's context can ask it to call `model_delete`, but only the
user clicking approve makes that delete happen.

The implementation landed across the v0.2 + v0.3 cycle:

- ADR-0004's destructive list (model_pull, model_delete, slot_*,
  capability_set, config_write, provider_credential_write, bulk
  memory_delete) → enforced server-side in
  `src/hal0/mcp/approval_queue.py` + `src/hal0/mcp/admin.py`.
- Epic #322 (PRs #321 / #328 / #329 / #330 / #332) shipped the
  dashboard UX: footer bell with badge count + inbox modal +
  per-card approve/deny + journal stream.
- CLI parity (`hal0 agent approvals {list,approve,deny}`) shipped
  alongside.

What was missing: a single document a tool author can read to
understand the classification rule. ADR-0004's two-tier list is
specific to hal0-admin tools; ADR-0013 added `tools.gated` per
agent; ADR-0015 (this PR) extends MCP hosting to third-party
servers. Without a unifying ADR the rule is split across three
places and the **MCP `annotations` block** is the actual carrier in
the protocol.

This ADR collapses those scattered references into one rule, names
the protocol carrier, and writes down the default for unclassified
tools.

## Decision

### 1. Every MCP tool is classified READ-ONLY or DESTRUCTIVE

Two classes, set per tool, exposed in the tool's MCP `annotations`
block:

```jsonc
{
  "name": "memory_search",
  "description": "Search hal0-memory for relevant items.",
  "inputSchema": { /* ... */ },
  "annotations": {
    "destructive": false,        // READ-ONLY by classification
    "destructiveReason": null
  }
}
```

```jsonc
{
  "name": "model_delete",
  "description": "Delete a model from the registry + disk.",
  "inputSchema": { /* ... */ },
  "annotations": {
    "destructive": true,         // DESTRUCTIVE
    "destructiveReason": "Permanently removes the model + GGUF bytes."
  }
}
```

- **READ-ONLY** calls proceed without approval. They are journaled
  to the per-agent journal (per Epic #322 / `src/hal0/journal/`) so
  the user can audit afterwards. No bell, no inbox entry.
- **DESTRUCTIVE** calls enqueue an approval request, return
  `pending_approval` immediately to the MCP client, increment the
  bell badge, and add an inbox entry. The server executes the call
  only after the user approves; deny cancels with a `denied` reason
  surfaced back to the agent.

### 2. The bell + inbox is the canonical surface

| Surface | What it shows | Source of truth? |
|---|---|---|
| **Footer bell** (every dashboard page) | Badge count of pending DESTRUCTIVE requests | Yes |
| **Inbox modal** (opened from the bell) | List of pending requests with approve/deny | Yes |
| **Inline pending indicators** on Models / Slots / Capabilities pages | Context-rich nudge ("1 pending: model_delete `qwen3:0.6b`") | No — convenience link to the inbox |
| **CLI** `hal0 agent approvals {list,approve,deny}` | Same queue | Yes — parity with the bell |

The bell is always visible regardless of which view the user is on.
Inline indicators are a convenience layer.

### 3. Default for unclassified tools: DESTRUCTIVE

If a tool ships **without** an `annotations.destructive` field, hal0
treats it as DESTRUCTIVE and gates it. This is the safe default:

- Third-party MCP server authors (per ADR-0015) may not always
  follow the convention. The cost of misclassifying as DESTRUCTIVE
  is a click-to-approve; the cost of misclassifying as READ-ONLY is
  unrecoverable side effects.
- A curator can override per-tool in
  `/etc/hal0/mcp/servers/<name>.toml` if a tool genuinely is
  side-effect-free and the author neglected the annotation.
- Bundled MCP servers (`hal0-admin`, `hal0-memory`) are
  fully-classified at code level — no unannotated tools exist in
  hal0's own MCP surface, by repo policy.

### 4. Pending forever — no auto-expire

Approval requests sit in the queue until the user acts on them. No
TTL, no auto-deny.

- If the user ignores a request for a week, it remains. The bell
  badge surfaces the backlog.
- A "Clear all" action on the inbox flushes the queue (categorised
  as user-initiated deny of all pending).
- The agent's MCP loop decides whether to wait synchronously on
  `pending_approval`, poll for the resolution, or abandon and try
  again later. That's the agent's policy, not hal0's. (ADR-0004 §5
  is explicit about this; restated here for completeness.)

### 5. No per-agent trust override

There is **no** "trust this agent fully" toggle that bypasses the
gating queue. Power users wanting full autonomy must amend the
DESTRUCTIVE classification for the specific tools they want
unattended, not flip a global bypass. The toggle would be the
prompt-injection footgun this whole pattern exists to prevent.

A bundled agent (`/etc/hal0/agents/<name>.toml`, per ADR-0013) can
list a DESTRUCTIVE tool in its `tools.allow` set — but the tool's
annotation still drives the gating decision at call time. The
allow-list governs *whether the agent can reach the tool at all*;
this ADR governs *whether the call proceeds without a click* once
reached.

### 6. Third-party MCP servers inherit the contract

Per ADR-0015 §7: external MCP servers run under hal0's supervision
and route through the same approval queue. A third-party MCP that
ships a `delete_*` or `write_*` tool **without** DESTRUCTIVE
annotation still gates by default (per §3 above). Catalog-curated
entries in `installer/manifests/mcp-catalog.toml` may carry a
`[overrides.tools.<name>.destructive]` block when the curator has
audited the tool and confirmed it is side-effect-free; user-added
servers do not get this convenience and gate everything not
explicitly annotated.

### 7. Approval record envelope

```jsonc
{
  "id": "approval_01HXYZ...",
  "agent_name": "hermes",
  "client_id": "hermes-agent",          // from X-hal0-Agent (post-rename)
  "mcp_server": "hal0-admin",           // or "filesystem", "github", etc.
  "tool": "model_delete",
  "args": { "id": "qwen3:0.6b" },
  "enqueued_at": "2026-05-25T14:23:11Z",
  "destructive_reason": "Permanently removes the model + GGUF bytes."
}
```

Identical envelope across hal0-admin, hal0-memory bulk delete, and
external MCP servers — the dashboard renders one card type
regardless of source.

## Consequences

### Positive

- One classification rule across all MCP surfaces (bundled +
  third-party). Tool authors have a single reference.
- DESTRUCTIVE-by-default for unclassified tools means a third-party
  MCP without annotations is *safe by default* — the worst case is
  user click-fatigue, not data loss.
- The bell + inbox shipped in v0.3 has a canonical design doc to
  reference when future contributors ask "why is everything
  destructive by default?" or "where's the trust toggle?"
- ADR-0015's host-platform decision can now reference a single
  contract for inheriting gating into third-party MCPs.

### Negative / costs

- Click-fatigue on DESTRUCTIVE-misclassified READ-ONLY tools. Real;
  mitigated by per-server override in
  `/etc/hal0/mcp/servers/<name>.toml` (curator-set) and by `tools.allow`
  pre-vetting from ADR-0013 (which doesn't bypass the gate, but
  does scope what the agent can reach).
- No auto-expire means the queue can grow unboundedly on a system
  with a chatty agent and an absent user. Mitigated by "Clear all"
  + the bell badge that surfaces the backlog at all times.
- DESTRUCTIVE-by-default means well-intentioned third-party MCP
  authors who shipped genuine read tools without annotations get
  punished UX-wise on first install. We accept this; the curated
  catalog's override block is the structural answer for
  high-traffic curated MCPs.

### Neutral

- The wire-level protocol carrier (MCP `annotations.destructive`) is
  the standard MCP spec extension shape. We're not inventing a new
  carrier; we're documenting our use of the existing one and
  formalising the default-when-absent.

## Pending items

(All complete for v0.3 except per-server-curated-override plumbing,
which depends on ADR-0015 landing first.)

- The `[overrides.tools.<name>.destructive]` block in
  `installer/manifests/mcp-catalog.toml` and the runtime read path
  in `src/hal0/mcp/registry.py` (depends on ADR-0015
  implementation).
- A linter pass that warns hal0-bundled MCP tools without an
  explicit `annotations.destructive` set. Belt-and-suspenders so
  the safe default isn't quietly inherited inside hal0's own
  surface.

## References

- ADR-0004 §5 — original two-tier sketch + destructive tool list.
- ADR-0013 — per-agent allow-list (`tools.gated` tier maps onto
  this contract).
- ADR-0015 — MCP as host platform (third-party MCP servers inherit
  this gating contract).
- Epic #322 — implementation epic (PRs #321 / #328 / #329 / #330 /
  #332).
- `src/hal0/journal/` — per-agent journal stream (READ-ONLY calls
  log here without bell-gating).
- `src/hal0/mcp/approval_queue.py` — queue + state machine.
- `src/hal0/mcp/admin.py` — hal0-admin tool catalog, destructive
  flags wired per ADR-0004.
- `hal0_epic_322_footer_journal_shipped` auto-memory — shipped
  state at 2026-05-25.
