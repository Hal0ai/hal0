# ADR 0011 — Agent identity cards (Phase 8, v0.3)

- **Status:** Draft
- **Date:** 2026-05-23
- **Drivers:** `/grill-me` session 2026-05-23 against `docs/internal/hermes-bootstrap-plan-2026-05-23.md`; bundled Hermes agent v0.3 needs to publish itself + discover peers
- **Implementing PRs:** PR-1 (this ADR + new MCP admin tools) per the bootstrap plan §23
- **Related:** ADR-0004 (Agents v0.2), ADR-0005 (Memory engine = Cognee), **ADR-0012 (Remove auth and Caddy entirely)** — supersedes the bearer-token identity model assumed in Draft 2 of the bootstrap plan; identity now flows from an `X-hal0-Agent` header (no auth). ADR-0013 (MCP-client allow-list) defines the per-agent server + tool allow-lists at `/etc/hal0/agents/<name>.toml`; a future card schema (v2+) may surface a derived `allowed_tools` projection from that allow-list (the allow-list is the source of truth, not the card).

## Identity sourcing update (post-ADR-0012)

The `metadata.namespace` field in a card (`"private:<client_id>"`) is unchanged in shape but the **source of `client_id` changed**:

- **Was (Draft 2 assumption):** `client_id` resolved from `Authorization: Bearer` token via `MCPAuthMiddleware`.
- **Is (post-ADR-0012):** `client_id` is the value of the `X-hal0-Agent` request header, read by `MCPIdentityMiddleware` (renamed from `MCPAuthMiddleware`; rename + header swap are tracked as v0.3 stream-4 follow-up in ADR-0012).

Schema v1 is otherwise unaffected — `agent_id` is still the canonical identifier, `namespace` is still `"private:<agent_id>"` by convention. Bootstrap configures the header in `mcp_servers.*.headers` (see bootstrap plan §8).

## Context

The bundled Hermes-Agent install in v0.3 makes the agent hal0-aware: it
probes the host, wires local models, connects to the memory MCP, and
adopts a "right-hand homelab admin" persona. Once that's done it needs
to **announce itself** so other agents (Claude Code, pi-coder, future
agents bundled or installed-out-of-band) can discover that Hermes is
running here and what it can do.

We have one shared substrate every agent on a hal0 host already
talks to: the `hal0-memory` MCP (Cognee). The question is whether to:

1. Reuse `hal0-memory` as a service registry by storing
   "identity card" memory items under a convention.
2. Build a separate `/api/agents` endpoint with its own JSON store.

Option 2 is structurally cleaner (service registry and episodic memory
are different concerns). Option 1 is zero new endpoints and rides on
the substrate every agent already needs to authenticate against.

For v0.3 we go with Option 1, but **carve out a dedicated dataset** so
the conflation doesn't bleed into episodic recall. The dashboard's
"Agents" panel (v0.3 follow-up) will read this same surface.

## Decision

### 1. Identity cards live in a dedicated `agents` dataset

- Storage: `hal0-memory` MCP.
- Dataset: **`agents`** (not `shared`, not `private:*`).
- Tag: `agent-identity` (additional tag for the agent's name allowed,
  e.g., `hermes`, `pi-coder`).
- Written by: the agent itself, during its bootstrap / first-run flow.
- Discovery: `memory_search({dataset: "agents", tags: ["agent-identity"]})`.

**Why dedicated dataset (not shared):**
- Service registry and episodic memory are different concerns.
- The `agents` dataset is small (5-10 cards forever) so embed cost is
  irrelevant.
- Search-by-similarity is a free side-benefit ("find an agent that can
  do X" works naturally if cards have descriptive text).
- `private:*` is wrong — identity is **public-by-design**; anything
  that wants to delegate has to discover the agent.
- `shared` is wrong — that's where episodic facts live; conflating
  concerns invites schema rot.

### 2. Cards are immutable

- Written once at bootstrap.
- **Not refreshed per session.** Liveness is not a stored field.
- Re-bootstrap (`hal0 agent bootstrap <agent> --repair`) and Hermes
  version upgrade rewrite the card. Those are the only legitimate
  writes besides install.
- Cleanup: `hal0 agent uninstall <agent>` calls `memory_delete(ids=[card_id])`.

**Why immutable:**
- Clean audit trail — every card has one source-of-truth write event.
- Liveness-as-stored-field is a bug magnet (TTL questions, concurrent
  writes, "when's it stale"). The dashboard can ping `endpoint.url`
  for liveness; that stays separate.
- Stale-card-on-uninstall is solved by the uninstall hook, not by
  TTL.

### 3. Card payload: summary in `text`, structured fields in `metadata`

The `text` field carries a short human-readable summary that surfaces
well in `memory_search` ranking. Programmatic readers consume the
structured payload from `metadata`.

```json
{
  "text": "I am Hermes, the hal0 admin agent. I have read/write access to the slot lifecycle and the memory store on this host. I can do generalist chat and code review on the LAN.",
  "tags": ["agent-identity", "hermes"],
  "dataset": "agents",
  "metadata": {
    "agent_id": "hermes-agent",
    "display_name": "Hermes (hal0 admin)",
    "namespace": "private:hermes-agent",
    "roles": ["homelab-admin", "generalist-chat", "memory-curator"],
    "endpoint": {
      "type": "mcp-serve",
      "url": "http://127.0.0.1:8081/mcp",
      "transport": "streamable-http"
    },
    "delegation": {
      "accepts_tasks_from": ["claude-code", "pi-coder", "user"],
      "max_concurrent": 3
    },
    "hal0_state": {
      "registered_at": "2026-05-23T14:00:00Z",
      "bootstrap_version": 1,
      "hal0_version": "0.2.0-alpha.3",
      "hermes_version": "0.14.0"
    }
  }
}
```

### 4. Minimal schema (required fields)

Card MUST include in `metadata`:

| Field | Type | Description |
|---|---|---|
| `agent_id` | `str` | Stable, slug-cased. Globally unique per host. |
| `display_name` | `str` | Human-facing label. |
| `namespace` | `str` | Where the agent's *working* memory lives in hal0-memory (e.g., `private:hermes-agent`). |
| `hal0_state.registered_at` | iso8601 | Bootstrap completion timestamp. |
| `hal0_state.hal0_version` | `str` | Snapshot of hal0 at bootstrap time. |
| `hal0_state.bootstrap_version` | `int` | Schema version of the card itself. v0.3 ships `1`. |

Recommended (writer SHOULD include):

| Field | Type | Description |
|---|---|---|
| `roles` | `list[str]` | Capability rollup the agent self-asserts. |
| `endpoint.type` | enum | `mcp-serve`, `http`, `none`. |
| `endpoint.url` | URL | If reachable. Dashboard pings for liveness. |
| `endpoint.transport` | enum | Per MCP spec or HTTP method family. |
| `delegation.accepts_tasks_from` | `list[str]` | Agent IDs allowed to delegate. `["user"]` is the minimum. |
| `delegation.max_concurrent` | `int` | Concurrency hint. |

Forward-compat: readers MUST ignore unknown keys in `metadata`.

### 5. Reserved card schema versions

| Version | Status | Scope |
|---|---|---|
| `1` | v0.3 alpha | Fields documented above. |
| `2` | future | Will likely add capability assertions for embed/rerank exposure (deferred from v0.3 — see Consequences §3). |
| `3+` | unallocated | |

### 6. Other agents must follow this convention

The convention is agent-agnostic. When the pi-coder bootstrap lands
(Phase 8 follow-up after Hermes), it writes its own card under the same
schema. Same for any future bundled or aftermarket agent.

The hal0 CLI ships a `hal0 agent list` command in v0.3 that
enumerates identity cards (a thin wrapper over the `memory_search`
call shown above).

## Consequences

### 1. Dashboard can render an "Agents" panel cheaply

The dashboard's existing `hal0-memory` client only needs to learn the
`agents` dataset name. No new endpoint, no new schema, no new auth.

### 2. Cards are searchable by content

Because cards embed in Cognee like any other memory item, a user query
like "find an agent that can do code review" can hit the dedicated
agents dataset and surface relevant cards by semantic similarity. The
`roles` list gives precise filtering; the `text` summary gives fuzzy
matching.

### 3. Embed/rerank exposure deferred to v0.4 (schema v2)

Card schema v1 does not include capability flags for whether the agent
exposes `embed()` / `rerank()` tools (per the bootstrap plan Q6 — both
explicitly NOT wired in Hermes v0.3). When that wiring lands in v0.4,
card schema v2 adds a `tools.{embed,rerank}: {available: bool, dataset_required: bool}`
block.

### 4. Federation invalidation risk

When hal0-memory federation lands (deferred to ADR-0006-equivalent in
Phase 9), the `agents` dataset will need a `host_id` qualifier — two
hal0 hosts in a federation will each have a `hermes-agent` and the
`agent_id` collision will need scoping. The forward-compat ignore-unknown-keys
rule on `metadata` means schema v1 cards survive the upgrade unmodified
once readers learn `metadata.host_id`.

### 5. No external "agent registry" surface in v0.3

We deliberately do NOT expose `GET /api/agents` as a REST endpoint in
v0.3. Anyone wanting to enumerate agents calls `memory_search` on the
`agents` dataset with the documented tag filter. If the registry use
case grows beyond what `memory_search` covers (latency, complex
filtering, write-validation), v1.0 can promote the surface to a
dedicated endpoint.

### 6. Cleanup is the bootstrap's responsibility

If an agent crashes mid-bootstrap without writing its card, no card
exists — that's fine.

If an agent crashes mid-uninstall before deleting its card, a ghost
card remains. Mitigation: `hal0 agent list --prune` (v0.4) flags cards
whose `endpoint.url` is unreachable AND whose `hal0_version` no longer
matches any installed agent. User confirms; entries deleted.

## Alternatives considered

### A. Store cards in the `shared` dataset

Rejected. Conflates service registry with episodic memory. Schema
hygiene degrades over time.

### B. Dedicated `/api/agents` REST endpoint with its own JSON store

Rejected for v0.3. Cleaner conceptually but doubles the surface every
agent has to discover and authenticate against. v1.0 can promote if
warranted.

### C. Refresh cards on every session start

Rejected. Liveness-as-stored-field has known failure modes (TTL
ambiguity, concurrent writes). Dashboard pings the endpoint for
liveness; card stays authoritative for "what's installed."

### D. YAML/JSON blob in `text` field, ignore `metadata`

Rejected. Forces every reader to parse a text blob. `metadata` is
exactly the right surface for structured data.

## Open questions

- Does `memory_search` with `dataset="agents"` work without further
  changes to the MCPAuthMiddleware namespace logic? Per the
  `hal0-memory` map, custom dataset names are accepted opaquely when
  not in private mode. Verify in PR-1 that "opaque" includes a write
  to a name like `agents` without surprise coercion.
- Should the `hal0 agent list` CLI live in `src/hal0/cli/agent_list.py`
  or as a subcommand of an existing module? Convention check during
  PR-1.

## References

- `docs/internal/hermes-bootstrap-plan-2026-05-23.md` §11 — phase
  `namespace_register`.
- `docs/internal/hermes-upstream-map-2026-05-23.md` §4 — upstream
  memory provider lifecycle (`Hal0MemoryProvider.initialize()` is the
  natural place to publish the card).
- ADR-0004 — bundled-agent decision frame.
- ADR-0005 — Cognee as the memory engine; namespace model.
