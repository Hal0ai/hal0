# hal0 Hermes Integration Suite Design

**Date:** 2026-07-18

**Status:** Approved; implementation gated on the Hermes compatibility prerequisite

**Scope:** hal0-related Hermes extensions only

## Purpose

Build a focused Hermes integration suite that makes hal0 slots and models appear and update automatically, routes Hermes voice through active hal0 STT/TTS slots, and replaces legacy Honcho wiring with a complete Hindsight-backed memory provider.

The suite preserves useful behavior observed on the live LXC105 installation without preserving its drifted implementation. Hermes owns agent sessions, profiles, prompts, approvals, and gateways. hal0 remains the sole authority for slots, models, routing, capabilities, memory visibility, media backends, and platform policy.

## Evidence and constraints

This design reconciles three sources:

1. The current `rework/descar` architecture and adjacent plans.
2. A read-only inspection of LXC105 (`10.0.1.142`) running hal0 0.9.8 and Hermes 0.18.2.
3. Current official Hermes documentation and commit-pinned upstream source, captured in [`docs/rework/hermes-official-integration-research.md`](../../rework/hermes-official-integration-research.md).

The official contracts that directly constrain this design are:

- [Adding Providers](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-providers)
- [Model Provider Plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/model-provider-plugin)
- [Provider Runtime Resolution](https://hermes-agent.nousresearch.com/docs/developer-guide/provider-runtime)
- [Memory Provider Plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/memory-provider-plugin)
- [Context Engine Plugins](https://hermes-agent.nousresearch.com/docs/developer-guide/context-engine-plugin)
- [Voice Mode](https://hermes-agent.nousresearch.com/docs/user-guide/features/voice-mode)

The current hal0 Hermes compatibility record points at an older fork/commit. Upstream reconciliation is a prerequisite, not part of the feature implementation.

## Product outcomes

After delivery:

- Hermes discovers all eligible hal0 models through the unified hal0 API.
- Slot creation, removal, readiness changes, model swaps, capability changes, and relevant config commits update Hermes without profile rewrites or Hermes restarts.
- Hermes main and auxiliary roles resolve through stable hal0 aliases rather than direct slot ports.
- Hermes voice uses the active hal0 STT/TTS slots and follows slot changes without restart.
- Hindsight supplies shared durable knowledge by default while raw conversation remains private.
- Every adapter degrades independently; provider, memory, or voice failure must not take down the Hermes agent loop.
- Installation, upgrade, verification, and rollback are deterministic and preserve unrelated Hermes configuration.

## Non-goals

- A new hal0 agent runtime.
- Moving slot lifecycle, model routing, or fallback policy into Hermes.
- A new Hermes transport or `api_mode`; hal0 remains OpenAI chat-completions compatible.
- Preserving LXC105's direct per-slot URLs or legacy Honcho configuration.
- Building a new hal0 voice stack or adding voice models/runtimes. Initial `hal0-voice` only routes Hermes through existing hal0 STT/TTS slots.
- A generic Hermes ecosystem bundle unrelated to hal0.
- A dashboard extension, observability plugin, streaming voice provider, or custom context engine in the initial suite.
- Silent cloud fallback for models, memory, STT, or TTS.

## Architecture

The initial suite contains three focused adapters and one internal support package:

```text
Hermes agent lifecycle                      hal0 platform truth

  hal0-provider   <---- hal0-hermes-core ----> /v1/models + slot events
  hal0-memory     <---- hal0-hermes-core ----> Hindsight memory API
  hal0-voice      <---- hal0-hermes-core ----> STT/TTS slot endpoints

Sessions, profiles, prompts,                Slots, routing, capabilities,
approvals, gateways, tool loop              visibility, backends, policy
```

The adapters may share packaging and release metadata, but each has an independent Hermes registration point, health result, failure policy, and enable/disable control. There is no monolithic `hal0` plugin.

### `hal0-hermes-core`

This internal library owns behavior that must not drift between adapters:

- hal0 endpoint discovery and compatibility negotiation;
- authenticated HTTP requests, using `HAL0_CLIENT_KEY` for inference/read and `HAL0_ADMIN_KEY` only for mutations;
- deny-by-default credential selection so an admin credential is never attached to a read-only or unrelated endpoint;
- OPEN liveness checks against `/api/health`; preflight must never use admin-gated `/api/status` as a reachability probe;
- connection and read timeouts;
- bounded retry classification;
- structured hal0 error decoding;
- correlation and idempotency identifiers;
- version/schema compatibility checks;
- redacted diagnostic output.

It does not own model selection, memory policy, voice fallback, Hermes registration, or user-facing tools.

## `hal0-provider`

### Role

`hal0-provider` is a Hermes model-provider plugin under the official specialized provider layout. It adds first-class discovery and Hermes UX around hal0; it does not adapt a new protocol.

The provider uses Hermes's standard `chat_completions` mode. Its existence is justified by live model discovery, aliases, auxiliary-role routing, health/setup UX, and refresh behavior. If those capabilities can be delivered by the chosen Hermes version's named-custom-provider contract without a plugin, the implementation may use that smaller seam, but it must satisfy the same contract and tests.

### Inventory contract

The provider fetches only the unified hal0 model inventory. It must not target or enumerate hard-coded per-slot ports.

Each normalized model entry includes:

- stable model identifier;
- owning opaque stable slot ID, with the slot name treated as a mutable display/routing label;
- readiness state;
- context length;
- capability labels such as tools, reasoning, vision, embeddings, reranking, STT, and TTS where applicable;
- local/external ownership;
- compatible Hermes roles;
- inventory generation or observation time.

The provider exposes only hal0-owned models unless an operator explicitly enables an external upstream in hal0's advertised inventory. Hermes must not independently broaden that selection.

### Stable role aliases

Hermes main and auxiliary work use stable hal0 aliases rather than concrete ports or model names. Initial supported roles are:

- `main`;
- `compression`;
- `vision`;
- `approval`;
- `session_search`;
- `memory_flush`;
- `skills_hub`;
- `mcp`.

hal0 resolves each alias to current platform truth. Alias ownership follows the opaque slot ID across a rename; a model swap changes the target behind the alias without rewriting every Hermes profile. If diagnostics surface a backend port, it comes from the live PortAuthority `port_claim`, never from a slot-name convention or copied slot config.

The current `_resolve_auxiliary_tasks()` implementation in `hermes_provision.py` is only the seed logic: it computes role assignments during provisioning and writes static Hermes config. Restart-free alias following requires a hal0 runtime contract before `hal0-provider` can ship. Promote that logic into `GET /api/agents/{agent_id}/role-slots`, returning one complete generation-stamped mapping whose entries include role, opaque slot ID, mutable slot label, advertised model/alias, readiness, and capability basis. The endpoint owns resolution; the provider consumes it and never reimplements role policy.

### Automatic refresh

The refresh protocol is invalidate-and-refetch:

1. Fetch a complete inventory at Hermes startup.
2. Subscribe to `GET /api/events/stream?since=<cursor>`, the existing SSE stream with monotonically increasing event IDs.
3. Treat relevant events as cache invalidations, never as partial authoritative inventory.
4. Refetch the complete inventory after slot create/delete, model swap, capability change, readiness transition, or relevant config commit.
5. On reconnect, backfill through `GET /api/events?since=<cursor>` and advance from its `next_since` cursor. An `events.gap` event forces immediate full inventory and role-map reconciliation.
6. Periodically reconcile to repair a missed stream or exhausted ring-buffer history.

The last valid inventory remains usable during a temporary event-stream or API outage and is marked stale. A selected model that no longer exists must not be silently replaced.

### Provider failure behavior

- Warming model: expose warming state and wait only within configured policy.
- hal0 API unavailable: keep last valid inventory for existing selections; block new selection with a clear error.
- Missing model: fail clearly or use only an explicitly configured fallback.
- Unauthorized response: distinguish from reachability and model absence.
- Schema incompatibility: disable the adapter and retain the previous known-good bundle.
- Cloud fallback: never implicit.

### Provider diagnostics

The provider's setup/doctor surface reports API reachability, authentication, schema compatibility, inventory freshness, empty inventory, missing role assignments, unavailable selected models, and tool-call capability. Reachability is established only through the OPEN `/api/health` endpoint; authenticated read and mutation checks are separate diagnostics so a missing credential cannot masquerade as a dead service.

## `hal0-memory`

### Role and loader contract

`hal0-memory` is the one active external Hermes memory provider. It lives under the official `plugins/memory/hal0-memory/` layout, implements the current `MemoryProvider` ABC, registers through the supported memory-provider mechanism, and is managed through `hermes plugins` and `memory.provider: hal0-memory`.

hal0 intentionally maintains two parity-tested packaging copies: an importable source tree and an installable hyphenated seed. The compatibility defect is not that pair; it is the seed's current top-level runtime destination. Provisioning must install it at `plugins/memory/hal0-memory/` for the selected Hermes target.

`is_available()` performs configuration-only checks and no network calls. Network health belongs in initialization/diagnostics. The provider implements the official setup schema, config persistence, lifecycle hooks, shutdown, and active-provider CLI contract.

### Memory visibility policy

Memory has two default behaviors:

- Raw conversation capture is private by default.
- Extracted durable facts and explicit “remember this” writes are shared by default.

Private durable memory remains available through profile policy or an explicit `visibility: private` selection.

Reads search the shared bank plus the caller's eligible private bank. Visibility is enforced by hal0 server-side; Hermes-supplied request fields or browser headers cannot expand access.

### Identity and bank resolution

The provider derives identity from server-controlled Hermes and hal0 context:

- agent identity;
- profile identity;
- Hermes session ID;
- stable platform user ID when present;
- delegated-parent identity;
- agent context such as primary, subagent, cron, flush, or gateway.

Raw primary turns use a private agent/profile bank. Delegated agents use a separate private namespace. Cron, flush, and synthetic maintenance prompts do not enter the primary conversational bank.

### Recall and prompt injection

Before a turn, `prefetch()` performs a fast, bounded recall over shared plus caller-private memory. It has a strict latency and token budget. `queue_prefetch()` may perform deeper next-turn retrieval outside the critical path.

Recalled material is historical context, not instruction. Each item is deduplicated, ranked, token-budgeted, and annotated with:

- provenance;
- visibility;
- confidence;
- verification status;
- observation time;
- supersession state.

Verification classifications are:

- `observed`: derived from a raw conversation or tool result;
- `user_asserted`: explicitly stated or saved by the user;
- `agent_inferred`: extracted or inferred by an agent/model;
- `system_verified`: confirmed against current authoritative system state;
- `superseded`: retained for audit and excluded from normal recall.

Even `system_verified` facts are historical. Time-sensitive facts must be rechecked before action.

### Capture and extraction

After a successful completed turn:

1. `sync_turn()` queues a non-blocking private raw capture.
2. The capture carries a stable source-event/idempotency key.
3. Hindsight asynchronously extracts candidate durable facts.
4. Durable facts default to shared unless policy or classification keeps them private.
5. Shared facts retain provenance back to the private source without copying the raw transcript into shared storage.

`sync_turn()` must not block the agent loop. A bounded worker/queue or equivalent non-blocking mechanism performs backend writes.

`on_pre_compress()` persists continuity information before Hermes discards context. `on_session_end()` flushes pending capture and writes a compact checkpoint. Built-in memory writes may be mirrored through `on_memory_write()` when policy enables it. Shutdown drains only within a fixed deadline.

### Memory tools

The provider may expose:

- search;
- consolidated recall;
- add/remember;
- correct/supersede;
- forget/delete;
- provider/bank status.

Explicit durable writes default to shared. Each write accepts an explicit private visibility option. Correction creates a superseding fact and excludes the old fact from normal recall while preserving audit provenance. Forget/delete is policy-gated and audited.

MCP remains an optional cross-agent facade. Hermes-native memory lifecycle uses the Hindsight/hal0 HTTP API directly. A capability must not be implemented twice unless the native-provider and cross-agent consumers require distinct surfaces backed by the same server implementation/schema.

### Memory failure behavior

- Slow Hindsight: prefetch returns within its budget and queues deeper recall.
- Hindsight unavailable: chat continues without injected memory.
- Capture failure: enqueue in a bounded, local, secret-free retry spool.
- Retry-spool exhaustion: drop oldest eligible records according to explicit policy and emit a visible diagnostic; never grow without bound.
- Duplicate retry: server idempotency prevents duplicate memories.
- Plugin crash: Hermes disables the provider while chat remains usable.
- Visibility or identity ambiguity: fail the memory operation closed.

## `hal0-voice`

### Initial implementation

The initial voice integration uses generated Hermes command-provider configuration and stable hal0-owned wrappers. Python provider registration is deferred until streaming audio, cancellation, progress, or metadata cannot be expressed safely through the command contract.

### Dynamic routing

For every invocation the wrapper:

1. asks hal0 for the active healthy STT or TTS slot;
2. validates input size, duration, format, and requested output;
3. calls the unified hal0 API;
4. writes or streams the required output;
5. emits structured correlation and diagnostic metadata.

No Hermes restart is required after a voice slot change.

### Fallback and privacy

Local fallback order is operator-configured, for example GPU/NPU voice to CPU voice. Cloud fallback is disabled by default and requires an explicit policy because it transmits audio off-host.

The wrapper returns structured failures. It must not fabricate silence or fake success when all engines fail.

## Configuration ownership

Hermes owns its configuration file and migrations. hal0 owns only a declared set of keys/fragments required by this suite.

Provisioning must:

- generate fragments from one hal0 schema;
- deep-merge only hal0-owned keys;
- preserve unrelated plugins, profiles, gateways, and operator edits;
- separate secrets from non-secret config;
- support plan/diff, apply, verify, and rollback;
- snapshot before-state and record applied content hashes;
- remain idempotent.

The managed bundle records compatible hal0 and Hermes version ranges, source commit, config/manifest schema versions, and file hashes.

Ownership follows the landed born-owned split:

- `$HERMES_HOME`, including config, runtime state, profiles, and the installed plugin tree, is created directly as `hal0:hal0`; provisioning must not write as root and chown back.
- The immutable distribution bundle, `/usr/local/bin/hermes`, and `/etc/hal0/agents/hermes.toml` remain root-owned.
- Required root residue is written only through the audited, literal-path, body-on-stdin seams `hal0-systemctl write-gateway-dropin` and `hal0-agentenv write-seed-toml`.
- The Hermes service remains sandboxed and receives only the credentials each adapter requires.

## LXC105 migration

The live host demonstrates desired user-facing behavior but contains legacy drift:

- global and most profile memory providers are `honcho`;
- only `hal0-brain` selects `hal0-memory`;
- several profiles target direct ports such as `:8081`, `:8082`, and `:8085`;
- the memory plugin is installed at the legacy top-level path;
- voice uses mutable scripts and backup files;
- service comments and effective behavior disagree about provider ownership.

Migration uses the existing `hal0 memory migrate --from honcho --to hindsight` implementation and the operator guidance in [`docs/guides/honcho-memory.mdx`](../../guides/honcho-memory.mdx); this suite must not create a second migration engine. The migration window is:

1. snapshots Hermes config, profiles, plugin files, memory state, and service metadata;
2. runs a per-workspace dry-run and records source counts;
3. migrates with the existing resumable watermark;
4. verifies destination counts and representative recalls;
5. takes a post-migration snapshot before reconfiguration;
6. verifies the selected Hermes compatibility target and stages the versioned integration bundle;
7. selects `hal0-memory`, converts direct-slot profiles to unified hal0-provider roles, and installs generated voice wrappers/config;
8. proves `Hal0Config` tolerates any persisted legacy `[honcho]` block during the compatibility window;
9. restarts Hermes only after dry-run validation and verifies provider, memory, and voice behavior;
10. deletes legacy state in dependency order only after rollback has been proven.

Live data is not treated as authoritative architecture. Useful behavior is retained through the new contracts.

## Compatibility and drift management

Before feature work, reconcile the hal0 compatibility target with current official Hermes lineage and select an exact supported tag/commit.

Expand drift detection beyond the current narrow file set to cover:

- plugin discovery and management;
- model-provider profile/runtime contracts;
- model metadata and auxiliary routing;
- memory ABC and manager;
- context-engine ABC;
- hooks used by memory;
- TTS/STT provider registries and command contracts;
- configuration schema/migration;
- gateway/session behavior used by hal0.

Production bundles are pinned and verified. hal0 must not blindly run an upstream plugin update.

## Optional future `hal0-context`

A Hindsight-backed context engine is a separate future project, not hidden inside `hal0-memory`.

If built, it must implement Hermes's `ContextEngine` contract, including token counters, compression thresholds, valid OpenAI message sequences, session start/end/reset, model-switch budget updates, preflight compression decisions, status, and optional context tools. It is explicitly selected through `context.engine` and never auto-activated.

It should proceed only if lossless/context-DAG behavior is needed beyond memory recall and after parity tests cover branching, compression, resume, tool-call/result pairing, and fallback to the built-in compressor.

## Verification strategy

### Provider

- startup discovery;
- slot create/delete/model swap;
- readiness and capability changes;
- missed, duplicated, and reordered invalidation events;
- periodic reconciliation;
- stale inventory;
- auxiliary role changes;
- malformed/incompatible inventory;
- warming, unavailable, unauthorized, and missing-model states;
- explicit fallback behavior;
- tool-calling and streaming smoke tests.

### Memory

- official ABC and loader discovery;
- shared/private isolation;
- private raw capture;
- shared extracted facts;
- private durable facts;
- provenance, confidence, and verification annotation;
- idempotent capture/retry;
- correction/supersession;
- forgetting and approvals;
- bounded prefetch and queued recall;
- session end and pre-compression;
- profile switch and isolation;
- delegation, cron, subagent, flush, and gateway contexts;
- slow/down recovery and bounded spool behavior;
- shutdown deadline;
- provider CLI and setup schema.

### Voice

- active-slot change without Hermes restart;
- GPU/NPU-to-CPU local fallback;
- unavailable backend;
- timeout and cancellation;
- invalid and oversized media;
- format validation;
- structured failure output;
- cloud-consent enforcement.

### Lifecycle and security

- fresh install, upgrade, disable, removal, offline restart, and rollback;
- failed migration and incompatible Hermes upgrade;
- plugin crash and name collision;
- secrets redaction;
- server-derived agent/profile/user identity;
- approval enforcement;
- hal0-side memory visibility enforcement;
- no arbitrary host shell authority;
- no browser-controlled identity header;
- no silent cloud use.

### Live acceptance

Use halo143 as the validation and soak target after unit and integration gates pass. Exercise all three adapters, confirm automatic slot/model updates, verify private raw/shared durable memory, switch voice slots without restarting Hermes, simulate each backend outage, and prove rollback. LXC105 remains the live reference/rollback box and must not be migrated until halo143 has passed the soak and the operator opens the production migration window.

## Delivery sequence

1. **Hermes compatibility prerequisite:** select a reviewed official release/tag or commit (the researched upstream `main` SHA is not automatically the production pin), reconcile fork lineage, add contract fixtures, and expand `hermes-sdk-diff`. No downstream adapter work begins before this lands.
2. **Shared core and bundle lifecycle:** endpoint/auth/error library plus plan/apply/verify/rollback packaging.
3. **Runtime role-resolution prerequisite:** promote `_resolve_auxiliary_tasks()` policy into the generation-stamped `GET /api/agents/{agent_id}/role-slots` runtime endpoint and emit invalidating events when its inputs change.
4. **Hindsight memory:** correct loader layout, implement full lifecycle, visibility policy, migration, tools, and failure spool.
5. **Dynamic provider:** unified inventory, live role-slot map, EventBus invalidation/backfill, reconciliation, and diagnostics. This phase is blocked on step 3.
6. **Voice:** managed dynamic wrappers, local fallback, privacy policy, and diagnostics.
7. **halo143 validation and soak:** seed deterministic synthetic Honcho workspaces and memories (or use a separately exported, read-only sanitized LXC105 fixture), stage the bundle, exercise the real migration path, verify, and complete the rollback drill and soak while LXC105 remains untouched.
8. **LXC105 production migration:** open an explicit migration window only after halo143 passes; snapshot, migrate, verify, and perform ordered legacy cleanup.
9. **Optional future work:** consider `hal0-context`, streaming voice, dashboard, or observability only from demonstrated requirements.

Each phase must produce independently testable software and may ship separately. The memory, provider, and voice adapters remain independently disableable.

## Success criteria

The suite is complete when:

- slots and eligible models appear and update automatically without profile rewrites or Hermes restart;
- stable Hermes roles follow hal0 model swaps through the unified API;
- voice follows active hal0 STT/TTS slots and never uses cloud without policy;
- raw conversation remains private while durable facts default to shared;
- recall combines shared and caller-private memory with provenance and visibility labels;
- corrections, supersession, forgetting, session/compression hooks, and failure recovery work;
- provider, memory, and voice outages degrade independently without taking down chat;
- halo143 passes validation and soak before LXC105's production migration window opens; LXC105 then migrates without losing approved useful data and retains a proven rollback path;
- official Hermes compatibility and drift are continuously verified.
