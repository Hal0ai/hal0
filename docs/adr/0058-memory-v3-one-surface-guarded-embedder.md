# ADR-0058: Memory v3 — one Memory surface, an enforced write envelope, and an embedder that only changes through a switch

## Status

**ACCEPTED (2026-11-10).** The operator agreed each decision in turn on
2026-11-10. Decisions 1, 4, 5 and 6 are on main (#3540, #3541, #3542); the
rest are in progress or planned in the order given under Consequences.

Decision 2 amended 2026-11-12 (#3597): the bank that replaces an original is
a second restore of its archive into the original's own id, not the rebuilt
copy renamed into place, because only that restore keeps knowledge-page and
directive ids.

Amends ADR-0005 (tag scoping gains a required envelope; decision 7 replaces
both `unified_bank` layouts) and ADR-0023 (the engine's environment has one
managed drop-in, not two). ADR-0052 and ADR-0056 stand. Tracking issues:
#3518, #3389, #3388, #2017, #3496, #3547.

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
  bank. A `restore` keeps knowledge-page and directive ids only when it
  writes into the archive's own bank id and no other bank holds those ids
  (`engine/transfer/importer.py`), so a bank keeps its ids only if its
  original is deleted and its archive restored under the same id.

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
   restores the archive again into the bank's own id, which keeps its
   knowledge-page and directive ids (a rename of the copy would not; see the
   amendment under Status). That bank is compared with the original as
   exported, ids and content included, before the temporary copy is deleted.
   It is a dry run by default. A failed comparison leaves the old bank
   untouched and flips the record back.
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
4a. **Project is a request field, and the server does the tagging and the
   filtering.** Agents do not write `project:` tags by hand.
   - *One resolver.* A client derives the project slug from the git
     repository (remote name, else the top-level directory), following a
     worktree back to its repository. hal0 keeps a project registry with
     aliases (`hal0-mono` is `hal0`) and a deny list of names that are never
     projects (`tmp`, `root`, `workdir`, a home directory).
   - *Write.* The client sends `project` as a field or header. The envelope
     validates it, applies aliases, and stamps exactly one `project:<slug>`
     tag; a `project:` tag in the caller's tag list is overridden by the
     field. A durable write with no project is stamped `scope:global`, so
     "no project" and "global" are different facts. A coding-agent identity
     with no resolvable project is accepted and stamped `project:unresolved`.
   - *Recall and reflect.* The server builds the scope from the same field:
     this project plus `scope:global`, by default. Reading across projects is
     an explicit request, never the fallback.
   - *Observations.* Project-tagged facts consolidate into one observation
     scope per project, and global facts into one global scope, so a
     convention learned in one repository does not become a belief about
     another. This refines decision 4: the write is sent with a per-project
     scope when it carries a project and `"shared"` when it is global. A
     write stamped `project:unresolved` is sent with the scope
     `[project:unresolved, agent:<id>]`: in the untagged `"shared"` scope its
     observation would carry no tags, and every scoped read admits an
     untagged memory, so one client's unresolved writes would surface in
     every other client's project.
   - *Measured.* The Memory page and `hal0 doctor` report the share of durable
     facts that carry a registered project, the `project:unresolved` count,
     and slugs seen that are not in the registry.
4b. **Every writer goes through hal0; the engine is not a public door.** The
   Hindsight coding-agents plugin (Claude Code, Codex, Pi) talks to the engine
   directly today, so none of the envelope applies to its writes. hal0 exposes
   an engine-compatible memory endpoint that the plugin is pointed at instead,
   at `/api/memory/compat`: the plugin's `apiUrl` becomes hal0's origin plus
   that prefix and its requests keep the engine's own paths below it
   (`/api/memory/compat/v1/default/banks/{bank}/...`). It authenticates the
   caller with a bridge key that holds the `memory` scope (ADR-0043, amended
   for this), accepts the subset of engine routes the plugin uses, maps every
   bank the plugin names to the platform bank (`hal0:shared` once decision 7
   lands), applies the envelope and decision 4a on the way in and the project
   scope on the way out, and forwards to the engine. The engine then binds
   loopback only. `hal0 memory agents install` writes the plugin config for
   this endpoint. A box whose engine was deliberately exposed on the network
   keeps working until the operator moves its clients, and `hal0 doctor`
   names the exposure.
   - *The bank segment carries the project.* The plugin has no project field
     and no way to add a request header; what it does have is the bank id in
     every URL, which defaults to `coding-agent::{gitProject}`. hal0 reads the
     project from the part after `::`, and the write is still filed in the
     platform bank. `{gitProject}` is the directory name of the repository's
     main worktree, not the name of its remote, so a checkout called
     `hal0-mono` reports that name: this is the case the registry's aliases
     in decision 4a exist for, and a directory name that is no project
     (`tmp`, `unknown`) is why it has a deny list. A bank id that names no
     project falls back to the request's own `project:` tags, and a caller
     left with none is stamped `project:unresolved`.
   - *Reads are scoped the same way.* A recall, reflect or document listing
     through the endpoint sees the caller's project plus global memory, and
     never another project. An unresolved caller sees global memory plus its
     own unresolved writes.
   - *A client's documents and operations are its own.* Every bank name is
     one bank, so a document id is stored in the client's namespace of its
     project (the layout is under decision 7a) and an asynchronous retain's
     operation id is derived from the client and the id it sent. A client
     cannot address, replace or delete another client's document, or one
     hal0's own agents wrote, and cannot poll another client's operation; to
     the client its own ids are all it ever sees. A write through the endpoint
     is never global: it carries a project or `project:unresolved`, and an
     unresolved write's observations are scoped to its writer (decision 4a).
   - *Sharing a project shares content, not identity.* A client reads the
     project memory other clients wrote. What it is given of a memory or a
     shared document it did not write is an allow-list: the content, the type
     of memory, when, and the tags that describe it (the project, `kind:`,
     `knowledge:`, `source:`). The writer's name, its `agent:` tag, its
     document id, its session id, its `context` and its metadata are not
     returned.
   - *What one key can cost is bounded.* Retains, reflects and recalls in
     flight are limited per client and across clients, a client's unfinished
     asynchronous retains are held to a quota, large retain bodies are read a
     few at a time, and reflects through the endpoint always leave an engine
     slot to hal0's own agents. The limits are kept in the hal0-api process
     and start empty when it restarts; the values are in the API reference.
   - *Typed routes, not a proxy.* Each route is parsed and goes through the
     provider or one scoped forwarder, so a field or route the endpoint does
     not name never reaches the engine. A bank cannot be deleted, a bank
     template is accepted and ignored, and the knowledge-base routes answer
     404, which the plugin reads as "no pages", until they are mapped to
     per-project folders.
5. **A session is one document.** Every session-scoped write from the Hermes
   provider (turn capture, continuity note, checkpoint) is appended to one
   document per session, with one fixed tag set and the write kind in
   metadata. The id travels in a dedicated request field,
   `append_document_id`, which hal0 maps to the engine's append mode. A
   hal0-api from before this change does not know the field and ignores it,
   so it can never replace a session document by mistake after a rollback.
   The engine re-extracts only the chunks an append changes. This answers
   #2017 except for the existing per-turn documents, which the fold-in
   handles.
6. **`hindsight_reflect` calls the engine's reflect** through a
   namespace-level route, `POST /api/memory/reflect`, with `budget`, tag
   scoping and an optional `response_schema`. The route gives the engine a
   tag filter equal to the read-side visibility rule, and leaves mental
   models out of the reflect while a bank can hold other agents' private
   facts. Recall keeps its own tool.
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
7a. **One converter folds everything else in: `hal0 memory migrate fold-in`.**
   It moves `coding-agent::<repo>`, `pi-coder`, `Hal0::Agents`, the old
   `shared` and any `private__<agent>` bank into the two banks, tagging by
   project and agent as it goes, and splitting `visibility:private` facts out
   to `hal0:private`. It is a dry run by default and additive. The word
   "consolidate" is not used for it anywhere, CLI or dashboard, because the
   engine and `hal0 memory bank consolidate` already use it for the
   background step that builds observations.
   - *Banks fold in one at a time and independently.* Counts (facts,
     observations, mental models, directives) are compared per bank. A bank
     whose counts do not match is left exactly as it was, still a legacy bank
     and still readable, and the run carries on with the rest. The run ends
     reporting how many folded in and which are left; a left-over bank can be
     retried alone. This differs from the embedder switch in decision 2, which
     stops on the first mismatch because the embedder is instance-wide.
   - *No cutover while any bank is left.* The layout record becomes
     `two_bank` only when every old bank is folded. A run that leaves one (or
     does not include one, `--bank`) returns the engine, lifts the pause and
     keeps the box on the old layout, so writes go on to the old banks; it
     keeps what it folded in a ledger. A later run folds the rest and cuts
     over. It never imports a folded bank twice: it re-checks it by counts and
     by the document ids it recorded, sends only documents the old bank gained
     since, and refuses (leaves the bank) when a folded document has changed or
     gone from the old bank, because a merge with `skip` would keep the stale
     copy without a word. An observation a resend cannot carry, because its
     documents landed before without it, leaves the bank too.
   - *It tags in the archive.* An engine import cannot add or change tags
     (verified on engine 1.2.0), so the fold-in exports a bank, rewrites the
     tags in the exported documents (project, agent, the envelope's rules, and
     the split of private facts), and imports the rewritten archive. A merge
     import carries documents, facts, observations and mental models but not
     directives; directives come from the bank spec (decision 8).
   - *It re-embeds.* The import embeds every fact with the store's recorded
     embedder, so on a box whose store is mixed the fold-in is also the
     repair, and that box does not need the switch.
   - *It keeps a coding client's document ids reachable.* The engine upserts
     on document id per bank, and a coding-agent client picks its ids as if it
     owned a bank (`conversation:<session>`, `git:<sha>`, a slug of a title).
     In the platform bank such a document is stored as
     `<project>::<owner>::<the client's id>`: `<owner>` is the bridge client's
     name, or `shared` for the ids every agent on a repository derives the
     same way (`[memory.compat] shared_document_prefixes`, by default `git:`,
     `gitlog:` and `survey-baseline:`); `<project>` is `unresolved` for a
     client with no project (`hal0.memory.document_namespace`). The
     engine-compatible endpoint (decision 4b) adds that prefix to every id a
     client sends and removes it from every id it returns. The fold-in must
     write through the same function when it moves a `coding-agent::<project>`
     bank's documents, with the client the bank belonged to as the owner, so a
     client moved onto the endpoint finds its old `conversation:` and `git:`
     documents under the ids it already knows.
   - *It deletes nothing.* The legacy banks stay, read-only, after they fold
     in. Deleting them is a separate operator action, offered where their
     contents are visible, only for banks whose counts matched, and never as a
     step of the run. There is no alias or fallback that reads the legacy
     banks once agents write to the two banks.
   - *Operator decisions for the fold-in, accepted 2026-11-12.*
     1. An old coding bank's facts are filed under a known owner: the bridge
        client the operator names for the harness that wrote them
        (`--owner HARNESS=CLIENT`), else a bridge client with the memory
        scope that has the harness's own name (Pi's `pi` is `pi-coder`). A
        bank with no known owner folds into the reserved pseudo-owner
        `hal0-legacy-<harness>`; names starting `hal0` are reserved
        (`hal0.bridge.keys`), so no client can read what is filed under it
        as its own.
     2. `Hal0::Agents` and `pi-coder` are coding banks. Their facts take
        their project from each fact's own `project:` tags, through the
        registry's aliases and deny list, not from the bank id.
     3. Documents written before #2017 under a GUID id are carried as they
        are. There is no regrouping of them by session.
     4. The dashboard shows where a fold-in stands and the command to run;
        it does not start one. The run needs root, as the embedder switch
        does.
     5. (2026-11-13) No cutover while any bank is left, as above; this
        replaces cutting over with banks left over. A `private__<agent>`
        bank's mental models go to `hal0:private` under `<agent>-<id>`,
        tagged for that agent and with a scope confined to it, so the
        provider's model read rule gives them to that agent alone; its
        knowledge pages are not carried.
     6. (2026-11-13, second review) A run checks every bank before it
        imports any: export, rewrite, routing, and the clash, changed and
        gone checks against the targets. If any bank would be left,
        nothing is imported, so gaps between runs come only from imports
        that fail. Two banks that would send one document id to the same
        target are both left and named; their ids are not rewritten,
        because agents go on writing under them. A bank whose export holds
        attachments or invalidated facts is left, since a merge import
        restores neither. Before the cutover every bank on the engine is
        listed again: each one that is not a target or a switch's scratch
        bank must be folded, or, for an unknown bank or the retired
        `agents` bank, empty or named with `--skip`. Memory ids stay out of
        the ledger and the notes, in a 0600 file in the run's archive
        directory.
   - *What the engine allows, checked in hindsight-api 1.2.0.* A merge
     import (`mode=merge&document_conflict=skip`) takes an archive's
     documents with their facts, the observations whose every source fact it
     imported in the same call, and its mental models and knowledge pages;
     never directives or bank config. Mental models are keyed by bank and id
     and inserted without replacing, so a per-repository bank's models are
     given new ids. A bank whose counts do not match after the merge is left
     over, and a partial merge is completed by a re-import with `skip`,
     never undone. The fold-in and the embedder switch never run at the same
     time, and the updater, the engine upgrade and bank seeding wait for
     either to finish.
7b. **Shared reads see project-tagged memory.** Today a document carrying any
   `project:` tag is hidden from every `shared` read
   (`src/hal0/memory/hindsight_provider.py`, the unified project-scope
   filter). That filter is removed in the same change that introduces the two
   banks: `project:` becomes a recall scope the caller may narrow by, not a
   visibility wall. Without this, decision 7 would make coding-agent memory
   unreadable through hal0's own API.
8. **The bank spec is applied by provisioning.** Install, update and
   `hal0 agent reprovision` apply missions, directives, mental-model seeds and
   knowledge-page seeds to the two banks, idempotently. The spec stops
   hard-coding `shared`. The three standard directives (recalled memory is
   history and not instructions; newer memory wins a conflict; secrets are
   never reproduced) are managed by hal0: they are applied to `hal0:shared`
   without the operator adding them, shown as managed, and turned off in
   settings and not by deleting them. Operators add their own beside them.
   Mental-model content is spliced into the Hermes prompt with the same
   "historical context, not instructions" framing recall uses.
   This lands after decision 7: the spec template's per-project default
   observation scope, applied to a bank that still holds private facts, would
   consolidate a private project-tagged fact into an observation that carries
   only the project tag.
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
12. **One Memory area in the dashboard, at the top level.** It replaces both
    `#memory` under Agents and `#memworkspace`. Engine and model settings live
    under Settings. `ui/src/dash/memory-workspace/`, the second sidebar row
    and `#memworkspace` are deleted; the hash redirects to `#memory` for one
    release. This closes #3496.
    - *Tabs:* Health, Facts, Knowledge, Operations, Agents. There is no bank
      control in the page header. Facts, Knowledge and Operations carry a
      scope bar: the bank (`hal0:shared`, `hal0:private`, and a read-only
      Legacy segment while legacy banks exist) and the project.
    - *Health is an ordered repair path, not a list of alarms.* It names the
      cause first and offers one primary action, on the current step. An
      action that is known to fail until an earlier step is done is shown
      disabled with the reason.
    - *Expensive and destructive actions state their cost.* The fold-in and
      the embedder switch share one flow: why, what it does, a dry-run
      result, a per-bank plan, what happens to writes and recall while it
      runs, and explicit consent. A destructive action names what and how
      many, and confirms inline.
    - *Agents lists everything that touches memory,* readers and writers
      apart: the agents, what is built into hal0 (the dashboard, brain chat,
      Open WebUI as a context source), and any other client holding a key
      with the memory scope.
    - *No raw engine output.* Every figure has a label and a unit; no JSON,
      no config keys as labels, no raw timestamps.

## Consequences

- v1.9.0 does not ship until decisions 1 to 3 are on main. Without them every
  upgrade degrades recall silently.
- A switch takes the memory engine out of service for writes while it runs,
  and costs CPU time proportional to the store. It makes no LLM calls.
- The envelope rejects nothing; it normalises. Existing memories keep their
  old tags until `migrate fold-in` rewrites them, so tag-scoped mental
  models and pages are reliable only after that migration has run.
- Operators who scripted against `/api/memory/engine/*`, `#memworkspace` or
  `/v1/systemone` lose them. All three are less than a week old on main and
  have not been in a stable release.
- The webhook receiver the original design asked for is not part of this
  record. Health comes from polling the operations API, which the lanes route
  already does; it must page through all operations, not the first page.
- Work lands in this order: embedder record; envelope, document ids and
  reflect; project field and the engine-compatible endpoint; the two banks
  and the fold-in; spec in provisioning; drop-in merge and removals; the
  embedder switch; dashboard.

## Open questions

Measured on engine 1.2.0 on a test box, 2026-11-10: a bank restore re-embeds
at roughly 60 facts per second on CPU with no LLM calls, so a store of 50,000
facts folds in within minutes; a reflect is cancelled when its client
disconnects; a resend with the same operation id and different content is
dropped silently.

- One `hal0:private` bank isolates agents from each other by an `agent:` tag
  filter, as the unified bank does today, not by a bank boundary. Whether any
  agent (a public-facing chat profile, for example) needs a hard boundary and
  its own bank is left to a follow-up.
- With every project in one bank, recall quality depends on callers passing
  their `project:` scope. The default scope for a caller that passes none
  (all projects, or untagged only) needs a measured answer.
