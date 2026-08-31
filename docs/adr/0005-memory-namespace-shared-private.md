# ADR-0005: Memory namespace grammar cut to `shared` + `private:<agent>`

## Status

**ACCEPTED (operator decision, 2026-08-31).** Shipped together with the
server-side bank consolidation performed the same night.

## Context

The memory namespace grammar was a closed four-value set: `shared` |
`agents` | `project:<id>` | the caller's own `private:<client_id>`
(`src/hal0/memory/namespace.py`). In practice:

- `agents` held only the agent identity cards written by Hermes
  provisioning (`hermes_provision.py`) and read back by
  `agent_commands.py` — always scoped by the `agent-identity` tag, so
  the dedicated namespace added a bank without adding a boundary.
- `project:<id>` had no writer anywhere in the tree. Under the default
  `unified_bank = true` it was already implemented as a tag inside
  `shared` (#1300), not a real bank; per-repository **coding** memory
  had meanwhile moved outside the namespace system entirely, into
  `coding-agent::<repo>` banks written directly against the Hindsight
  engine by the coding-agent clients (hindsight-coding-agents plugin).
- Each extra namespace was one more bank for consolidation to pay for
  and one more place for operators to lose facts. A live audit
  (2026-08-31, CT105) found 14 banks across three generations of naming
  conventions; see issues #2153/#2154.

## Decision

The grammar is two-valued:

- **`shared`** — the default for every write. Scoping *within* shared is
  done with tags (`agent-identity` for the identity cards; `project:<id>`
  as a provenance tag where wanted).
- **`private:<client_id>`** — reachable only through the private-mode
  toggle, for memories an agent or the user explicitly wants scoped to
  one identity.

Per-repository coding memory stays outside the namespace grammar in
`coding-agent::<repo>` engine banks, owned by the coding-agent clients.

Writes naming `agents` or `project:<id>` now fail with a
`MemoryNamespaceError` carrying a pointed remedy; reads keep the
established degrade contract (unknown entries dropped from a list,
all-unknown lists fail closed per #1451).

## Consequences

- `hermes_provision.py` / `agent_commands.py` identity cards moved to
  `shared` (the `AGENT_IDENTITY_TAG` scoping they already had is the
  boundary).
- The provider-side `project:<id>` **tag** ACL in
  `hindsight_provider.py` is kept as defense-in-depth for pre-existing
  tagged data; only the front-door grammar shrank.
- Existing `agents` / `hal0:projects` / `hal0:system` banks on the
  operator's box were transferred into `coding-agent::hal0-mono` (with
  `origin:` tags) or archived, then deleted, the night this was decided.
  Other deployments upgrading past this change with data still in an
  `agents` bank must migrate it by hand (document-transfer to `shared`);
  nothing recreates or reads that bank after this change.
- A rejected write to a retired namespace is loud (400 with remedy), so
  any out-of-tree caller still naming `agents` surfaces immediately
  rather than silently forking a bank.
