# ADR-0058: Memory v3 — one Memory surface, an enforced write envelope, and an embedder that only changes through a switch

## Status

**PROPOSED (2026-11-10).** The operator agreed the direction on 2026-11-10:
a single top-level Memory area, `hs-embed-1` as the target embedder, and
v1.9.0 held until the embedder fix lands. The rest is for review.

Amends ADR-0005 (tag scoping gains a required envelope) and ADR-0023 (the
engine's environment has one managed drop-in, not two). ADR-0052 and ADR-0056
stand. Tracking issues: #3518, #3389, #3388, #2017, #3496.

## Context

The "Hindsight 1.2 showcase" (#3486, #3488, #3489, #3507, #3522, #3523) landed
in four days. It pinned the engine to 1.2.0 and added a lot of platform
plumbing: `[memory.models]`, `[memory.decision]`, `[memory.lanes]`, a second
systemd drop-in, a raw engine passthrough, `/v1/systemone`, a bank spec, and a
second dashboard page. An audit on 2026-11-10 found three kinds of problem.

**It changed stored data without a migration.** `[memory.models].embeddings_model`
defaults to `hindsight-ai/hs-embed-1`, and the showcase drop-in renders it on
every upgraded box. The engine default, and what every existing store was
embedded with, is `BAAI/bge-small-en-v1.5`. Both are 384-dimensional, so nothing
errors; new vectors simply land in a different space from the stored ones and
recall over older memories degrades (#3518). The existing guard in
`src/hal0/api/_settings_apply.py` covers `[memory.embedding].model` and was
bypassed by the new key.

**The agent-facing goals were not built.** The Hermes provider's
`hindsight_reflect` still calls recall
(`installer/agents/hermes/plugins/hal0-memory/provider.py`, `_handle_reflect`).
Tags pass through the write path unchecked, so session ids and lifecycle words
(`checkpoint`, `session-end`, `pre-compress`) become tags and, under the
engine's default observation scoping, each one becomes its own observation
silo. Hermes sends no `document_id`, so every turn is a new document (#2017).
The bank spec is applied only by a manual CLI verb and ignores `[memory] bank`.
On the dev box this shows as 21 banks, every mental model and knowledge page
stale, and a consolidation backlog in the thousands.

**It added surface that nothing uses.** The engine is pointed straight at the
decision server's port, so `/v1/systemone` and its admission priority are not
on the engine's path. No install path creates the decision unit or the memory
console unit. `memory.decision.threads`, `memory.decision.timeout_s` and
`dispatcher.admission_wait_timeout_s` have no reader. Two managed drop-ins
both set `HINDSIGHT_API_LLM_MAX_CONCURRENT`. `/api/memory/engine/*` duplicates
the classified `memory_admin` routes. The dashboard has two sidebar rows named
"Memory"; the newer one (`#memworkspace`) uses class names with no CSS behind
them and prints raw JSON.

Two facts about engine 1.2.0, read from its source, shape the decision:

- The embedder is instance-wide. Per-bank configuration excludes provider and
  model selection, and a vector carries no record of the model that made it.
- A bank transfer re-embeds. `POST /v1/default/banks/{bank}/transfer/export`
  never carries embeddings; `…/transfer/import` re-embeds every fact with the
  configured model and rebuilds links, with no LLM call. `restore` mode writes
  a whole bank (observations, mental models, knowledge pages, directives) into
  a bank id that must not exist; `merge` mode folds documents into an existing
  bank. `hindsight-api admin rename-bank` renames a bank in place.

## Decision

1. **The embedder is a property of the store, and only a switch changes it.**
   hal0 records the embedder a store was built with in its state directory at
   first start. The engine environment is rendered from that record, never from
   the config default. When `[memory.models].embeddings_model` differs from the
   record, hal0 keeps rendering the recorded model, reports the difference in
   `hal0 doctor` and on the Memory page, and changes nothing.
2. **`hal0 memory embedder switch <model>` is the one way to change it.** It
   exports every bank, stops writes, flips the recorded embedder, restarts the
   engine, restores each bank under a temporary id, compares fact, observation,
   mental-model and directive counts, and only then deletes the old bank and
   renames the new one into place. It is a dry run by default. A failed
   comparison leaves the old bank untouched and flips the record back.
3. **Both embedders are supported; the defaults differ by install age.** A
   fresh install records `hs-embed-1`. An upgraded store with no record is
   recorded as `bge-small-en-v1.5`, which is what built it, and stays there
   until the operator runs the switch. A store that already holds vectors from
   both models (any box that ran a nightly between #3486 and this change) is
   reported as mixed, and the switch is the repair.
4. **Every write carries an envelope, enforced in `HindsightProvider`.**
   Required: `agent:<id>` and `kind:durable|ephemeral`. Required for coding
   work: `project:<slug>`. Optional: `source:<kind>`, `knowledge:<kind>`.
   Tags that look like a UUID or a session id are moved to document metadata,
   not stored as tags. Bare lifecycle words are rewritten to `kind:ephemeral`
   plus metadata. Writes are sent with `observation_scopes: "shared"`.
5. **Documents have stable ids.** The Hermes provider sends
   `document_id = <client>[-<profile>]-<session-id>` and retain becomes an
   upsert. This closes #2017.
6. **`hindsight_reflect` calls the engine's reflect endpoint**, with `budget`,
   tag scoping and an optional `response_schema`. Recall keeps its own tool.
7. **Two banks, named `hal0:shared` and `hal0:private`.** Every agent (Hermes,
   the coding agents, the brain, the dashboard) writes durable memory to
   `hal0:shared` and tags it `project:<slug>`; there are no per-harness or
   per-repository banks. Private facts and raw session captures go to
   `hal0:private`, one bank for all agents, where `agent:<id>` scopes each
   agent to its own. The namespace grammar callers use (`shared`,
   `private:<client_id>`, ADR-0005) does not change: the provider maps
   `shared` to `hal0:shared` and every `private:<id>` to `hal0:private`. Both
   names fit the bank-id grammar hal0-api already enforces. New installs get
   these names; `[memory] bank` (ADR-0052) stays as the override. This
   replaces both `unified_bank` modes: private facts no longer sit in the
   shared bank behind a `visibility:private` stamp, and there is no
   `private__<agent>` bank per agent.
7a. **One converter folds everything else in.** `hal0 memory migrate
   consolidate` moves `coding-agent::<repo>`, `pi-coder`, `Hal0::Agents`, the
   old `shared` and any `private__<agent>` bank into the two banks, tagging by
   project and agent as it goes, and splitting `visibility:private` facts out
   to `hal0:private`. It is a dry run by default, additive, and compares
   counts before anything is deleted. There is no alias or fallback that reads
   the old banks afterwards.
7b. **Shared reads see project-tagged memory.** Today a document carrying any
   `project:` tag is hidden from every `shared` read
   (`src/hal0/memory/hindsight_provider.py`, the unified project-scope
   filter). That filter is removed in the same change that introduces the two
   banks: `project:` becomes a recall scope the caller may narrow by, not a
   visibility wall. Without this, decision 7 would make coding-agent memory
   unreadable through hal0's own API.
8. **The bank spec is applied by provisioning.** Install, update and
   `hal0 agent reprovision` apply missions, directives, mental-model seeds and
   knowledge-page seeds to the configured platform bank, idempotently. The
   spec stops hard-coding `shared`. Mental-model content is spliced into the
   Hermes prompt with the same "historical context, not instructions" framing
   recall uses.
9. **One managed drop-in.** `extraction-model.conf` and `showcase-lanes.conf`
   merge into a single file written by a single wrapper verb. The updater
   removes the old pair and any hand-written `zz-*` files it recognises.
10. **Unused surface is removed.** `/api/memory/engine/*` goes; the dashboard
    uses the classified `memory_admin` routes. `[memory.console]` goes. Config
    keys with no reader go, or gain one in the same change.
11. **The decision model is optional and off by default.** It becomes an
    installable component (`hal0 memory decision enable`) that runs the CPU
    server and renders the decision environment only while its health probe
    passes. `/v1/systemone` is removed unless the engine is routed through it.
    NPU serving is not pursued: the spike reached CPU-parity latency and ran
    out of memory at full context. Turning the default on waits for a measured
    before/after on a fixed corpus.
12. **One Memory area in the dashboard, at the top level.** The designed
    `#memory` surface moves out from under Agents and gains tabs for knowledge
    pages, operations and lane health, and agent setup. Engine and model
    settings live under Settings. `ui/src/dash/memory-workspace/`, the second
    sidebar row and `#memworkspace` are deleted; the hash redirects to
    `#memory` for one release. This closes #3496.

## Consequences

- v1.9.0 does not ship until decisions 1 to 3 are on main. Without them every
  upgrade degrades recall silently.
- A switch takes the memory engine out of service for writes while it runs,
  and costs CPU time proportional to the store. It makes no LLM calls.
- The envelope rejects nothing; it normalises. Existing memories keep their
  old tags until `migrate consolidate` rewrites them, so tag-scoped mental
  models and pages are reliable only after that migration has run.
- Operators who scripted against `/api/memory/engine/*`, `#memworkspace` or
  `/v1/systemone` lose them. All three are less than a week old on main and
  have not been in a stable release.
- The webhook receiver the original design asked for is not part of this
  record. Health comes from polling the operations API, which the lanes route
  already does; it must page through all operations, not the first page.
- Work lands in this order: embedder record and switch; drop-in merge and
  removals; envelope, document ids and reflect; spec in provisioning;
  consolidation converter; dashboard.

## Open questions

- Whether `merge` import can tag the documents it folds in, or whether
  `migrate consolidate` must re-tag after import. To be proven on the test rig
  before the converter is written.
- Import time and peak memory for a store of about 50,000 facts on CPU.
- One `hal0:private` bank isolates agents from each other by an `agent:` tag
  filter, as the unified bank does today, not by a bank boundary. Whether any
  agent (a public-facing chat profile, for example) needs a hard boundary and
  its own bank is left to a follow-up.
- With every project in one bank, recall quality depends on callers passing
  their `project:` scope. The default scope for a caller that passes none
  (all projects, or untagged only) needs a measured answer.
