# hal0 rework plan — reader's copy

> Reformatted from `hal0-rework-plan.md` for readability and copy/paste.
> This copy emphasizes the current, authoritative direction and removes most
> superseded decision history. The original plan remains the source record.

## Executive summary

hal0's architecture is fundamentally sound, but rapid iteration left systemic
scars: dead abstractions, duplicate sources of truth, oversized modules,
divergent implementations, stale documentation, and process machinery that is
too heavy for a single-box appliance.

The rework should simplify the platform around its actual core:

- A FastAPI control plane
- A dispatcher serving `/v1`
- A model registry and model store
- Podman-backed inference slots
- A dashboard
- One optional bundled agent: Hermes
- One memory engine: Hindsight

The main job is deletion and consolidation. New product features should not
delay the core rework.

## Finish line

The rework is complete when hal0 has:

- One authoritative model/config path
- One Hindsight memory path
- One tool-calling loop
- One slot-config apply engine
- One settings apply engine
- One model-store resolver
- One runner-image registry
- SQLite-backed machine state and model metadata
- Stable slot IDs and centrally managed ports
- Deny-by-default authentication and exposure classification
- A convergent installer with a clear privilege seam
- A small, optional Hermes integration
- Deployment validated on the new `halo` LXC

The following are post-core work unless explicitly promoted:

- Anthropic Messages compatibility
- OpenAI Realtime and streaming STT
- New voice models
- Grafana and Prometheus companion containers
- Shared profile registries
- New Hermes dashboard extensions
- Generic workflow or DAG authoring
- Document RAG
- Ollama compatibility

## Diagnosis

### 1. Speculative generality

Several abstractions wrap only one real implementation:

- Memory provider abstractions around Hindsight
- Provider methods unused by the container runtime
- Agent drivers that are not shipped
- Cloud-budget machinery for an unshipped paid path

Rule: one adapter does not justify a public seam. Generalize only when a second
working implementation ships.

### 2. Expired compatibility paths

Old fields and validators remain load-bearing long after their sunset:

- Deprecated backend/provider/runtime fields
- Dual-written capability fields
- Removed engines still represented in dependencies or schema

Rule: compatibility paths need an explicit release sunset and a CI-enforced
removal date.

### 3. Duplicate state

The same facts are recorded in multiple places:

- `capabilities.toml` and `slots/*.toml`
- Multiple slot apply paths
- Hindsight and Honcho with bidirectional synchronization
- Multiple runner-image resolution chains

Rule: every fact has one owner. Other representations must be derived views.

### 4. Oversized modules

The worst modules combine unrelated responsibilities:

- `agents/hermes_provision.py`
- `slots/manager.py`
- `config/schema.py`
- `api/routes/models.py`
- `api/routes/slots.py`
- `api/__init__.py`
- `ui/dash/slot-modals.jsx`

Splitting files is not sufficient. Each resulting module needs a small
interface that hides substantial behavior.

### 5. Divergent implementations

Known duplication includes:

- Multiple OpenAI tool loops
- Multiple model-store resolvers
- Multiple device/backend translators
- Multiple Hugging Face clients
- Multiple profile derivation paths
- Mirrored slot rosters

Rule: place each behavior behind one interface and make all callers use it.

### 6. Documentation drift

Historical documents and missing ADRs are treated as current authority even
when code disagrees.

Rule: a decision is either documented in a real ADR or explained inline.
References to nonexistent documents are not allowed.

### 7. Process scars

Too many worktrees, update paths, rituals, and long-lived partial efforts made
progress difficult to verify.

Rule: one integration branch, short-lived task branches, explicit checkpoints,
and deployment-shaped verification.

## Guiding principles

1. Delete before refactoring.
2. Collapse duplicate truths to one owner.
3. Keep human-authored config in TOML.
4. Keep machine-owned state in SQLite.
5. Prefer deep modules with small interfaces.
6. Preserve compatibility only through explicit, expiring shims.
7. Keep core hal0 functional without Hermes.
8. Make deployment and rollback part of the design.
9. Validate behavior through interfaces, not implementation structure.
10. Stop when the core finish line is met.

## Canonical architecture decisions

### Memory

- Hindsight is the only memory engine.
- Honcho is removed after a one-time, verified migration.
- Each private workspace must be migrated independently.
- Migration state is snapshotted and never rerun after deletion.
- Persisted Honcho config must be tolerated or scrubbed during the transition.

### Agents and brain

- Hermes is the only bundled agent and remains optional.
- hal0-brain is a hal0 subsystem, not a Hermes persona.
- Brain, board, and core administration must work without Hermes.
- Hermes may be an optional executor for heavier background work.
- Turnstone, pi-coder, and opencode are not part of the core.

### Configuration and state

- Operator-authored configuration stays in TOML.
- Machine-owned runtime state and model metadata move to SQLite.
- `SlotConfigStore` remains the one slot-config apply engine.
- `_settings_apply.REGISTRY` remains the separate settings apply engine.
- These are distinct concepts and must not be merged.

### Runtime

- Podman is the supported container runtime.
- Slot services migrate to Quadlet `.container` units.
- Docker fallback behavior is removed.
- The API runs as `hal0`, with narrowly validated privileged operations routed
  through `hal0-systemctl`.

### Updates

- Keep signed stable-channel updates.
- Keep atomic version-directory swaps and rollback.
- Remove nightly, detached-signature fallback, editable drift handling, and
  parallel production update mechanisms.

### Deployment

- Do not replace the current lxc105 installation in place.
- Deploy to the new `halo` LXC side by side.
- Preserve lxc105 as rollback and reference until migration is proven.

## Core workstreams

### A. Security and exposure

Use one deny-by-default exposure table for runtime enforcement, CI, and UI.

Credential classes:

- `HAL0_ADMIN_KEY`: administrative and mutating operations
- `HAL0_CLIENT_KEY`: inference and permitted read operations
- Browser HMAC session: admin-equivalent dashboard access

Rules:

- Unclassified routes default to ADMIN.
- Non-loopback binding requires authentication.
- Loopback without keys may remain development-open.
- Installer routes are BOOTSTRAP only until an admin key exists.
- WebSocket authentication may use `?api_key=`; query strings must never enter
  access logs.
- Exposure CI fails if a route is unclassified or the OPEN set expands.

### B. Model registry and model store

The model owns its inference requirements and file set.

Canonical model data includes:

- Source repository
- Resolved revision
- File set and file roles
- Shards and mmproj
- Architecture
- Modalities
- Runtime capabilities
- Preferred runner
- Profile and launch defaults

Storage rules:

- One resolver decides where model files live.
- Read and write precedence must be identical.
- Layout is repository/revision-addressed.
- `by-id/<model-id>` is the stable pointer used by slots.
- Revision updates use an atomic pointer flip.
- File rows are content-addressed and refcounted.
- Delete may remove bytes only when references permit it.
- GC reconciles database rows and filesystem state.
- Concurrent pulls reserve disk capacity before downloading.
- NFS mounts omit SELinux relabel flags entirely.

### C. Model taxonomy

Use four clear axes:

- Modality: what request type the model serves
- Capabilities: typed runtime/routing booleans
- Tags: inert UX descriptors
- Device and runner: where and how the model runs

Canonical modalities:

```text
chat, vision, embed, rerank, asr, tts, image, video
```

Normalize aliases at ingest:

```text
stt/transcription -> asr
embedding -> embed
reranking -> rerank
img -> image
```

Routing reads typed capabilities, never free-text tags or labels.

### D. Runner registry and argument resolution

`RUNNER_IMAGES` is the one code registry for runner properties:

- Image and digest
- Runtime family
- Supported features
- Device class
- Required HIP architecture
- Public and internal ports/URLs
- Cold-start timeout

Resolution precedence:

```text
runner defaults
  -> profile tune
  -> architecture defaults
  -> model metadata
  -> slot instance overrides
```

Managed arguments such as model, host, port, context, and GPU layers cannot be
overridden through free-form `extra_args`.

### E. Slot lifecycle

Every slot gets a stable opaque ID. Its name becomes a mutable display label.

The slot module should expose a small intent-oriented interface:

```text
inspect(slot_id)
apply(slot_id, desired_state)
delete(slot_id)
subscribe()
```

Internally it owns:

- State transitions
- Reconciliation
- Port claims
- Unit generation
- Runtime launch
- Idle and pressure eviction
- Failure watching
- Model resolution

All slot types use the same create, edit, run, unload, and delete lifecycle.

### F. Port authority

SQLite `port_claim` is the single authority for ports.

It must:

- Allocate from configured ranges
- Reserve ports by stable owner ID
- Reject duplicate claims
- Check active listeners
- Reconcile claims on startup
- Release claims on complete deletion

No route or installer path may hand-assign a slot port independently.

### G. Tool loop and brain

One tool-loop module owns:

- Native tool-call extraction
- Text fallback extraction
- Thinking separation
- Known-tool gating
- Tool message construction
- Per-call events and results

Consumers:

- hal0-brain
- Board chat
- OmniRouter

Brain uses `/api/brain/chat` as its primary route. `/api/board/chat` becomes a
thin compatibility alias.

### H. Installer and permissions

Target structure:

- Thin shell bootstrap
- Thick Python provisioner
- One profile authority
- One slot roster
- One ownership table
- Convergent, idempotent subsystem installation

Ownership model:

- `/usr/lib/hal0`: root-owned, read-only
- `/etc/hal0`: hal0-owned and setgid, except secrets
- `/var/lib/hal0`: hal0-owned and setgid
- Secret stores: root-owned with narrow read/write helpers
- System units and Quadlets: root-owned, written through `hal0-systemctl`

Installer reruns must detect already-converged state and avoid rebuilding
unchanged environments and images.

### I. Hermes simplification

Prerequisites:

1. Permissions and privilege seam landed
2. Brain responsibilities moved out
3. Plugin contract finalized

Then replace the phase/checkpoint pipeline with one convergent installer:

```text
resolve Python
  -> install pinned Hermes SDK
  -> install plugin trees
  -> apply specific config keys
  -> render context files
  -> install service unit
  -> wire secrets
  -> smoke test
```

Never replace Hermes-owned `config.yaml` wholesale. Apply specific keys through
Hermes config commands and an overrides deep merge.

`hal0-memory` remains two byte-identical, parity-tested copies because one path
is importable source and the other is the installable hyphenated seed.

### J. Application composition and routers

Move dependency construction, startup/shutdown ordering, router manifests, and
app-state installation behind one composition interface.

`create_app()` should become a small consumer. Route-collision tests must reject
literal paths shadowed by parameterized paths.

Routers should only:

```text
parse request -> call module -> render response
```

Hugging Face access, pull jobs, metrics collection, cgroup inspection, and
systemd behavior belong in modules outside route files.

### K. UI settings

The settings UI should use:

- One typed settings client
- One settings schema
- Per-domain hooks/modules
- Pages that submit typed intents
- One explicit reload/restart classification source

Moving code into more page files is only the first step. Backend behavior and
window-global compatibility knowledge must not remain distributed through the
pages.

### L. Observability

Core observability is local and dependency-free:

- Per-request latency and throughput
- Per-slot resource samples
- Lifecycle events
- Benchmark baselines and regressions
- Bounded retention and rollups

Metrics writes must be off the inference hot path and batched. Registry and
configuration transactions must not be delayed by high-frequency telemetry.

Prometheus, Grafana, Langfuse, and OTLP remain optional exports after the core
measurement seam is stable.

## SQLite rules

Use stdlib `sqlite3` with:

```sql
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;
PRAGMA synchronous=NORMAL;
```

Rules:

- Forward-only numbered migrations
- `schema_migrations` table
- Fresh connection per request/task
- `BEGIN IMMEDIATE` for writes
- Short, bounded transactions
- Idempotent first-boot imports
- Export commands for human inspection
- Backup through `.backup` or `VACUUM INTO`
- Retention and batching for metrics

If metrics contention becomes measurable, move telemetry to a separate SQLite
file without changing the repository interfaces.

## Dependency order

```text
Security exposure classification
  -> authentication enforcement
  -> Security settings UI

SQLite foundation
  -> model registry
  -> model file sets
  -> model store and by-id pointers
  -> port authority
  -> runtime state and metrics

Model file sets
  -> preferred runner
  -> runner registry
  -> flag resolution
  -> benchmark parity

Tool-loop module
  -> first-class brain
  -> thin board alias

Permissions and hal0-systemctl
  -> Quadlet runtime
  -> Hermes simplification
  -> installer simplification

Model taxonomy and runner registry
  -> metrics dimensions
  -> introspection endpoints
  -> doctor evidence
```

## Migration windows

### Honcho to Hindsight

1. Snapshot the source system.
2. Run dry-run migration for every private workspace.
3. Run the real migration.
4. Verify counts and representative recalls.
5. Snapshot migration state.
6. Reconfigure agents to Hindsight.
7. Remove Honcho surfaces in dependency order.
8. Verify old persisted config cannot prevent boot.

### Capability/config collapse

Release N:

- Add the one-shot migrator.
- Add create-on-select behavior.
- Continue reading old and new state.

Release N cutover:

- Derive capability state from slots.
- Stop writing the old source.

Release N+1:

- Remove obsolete readers, CLI paths, and permission rows.

### Permissions migration

1. Install the new ownership table and privileged helper.
2. Run `doctor perms --fix` once before starting the new daemon.
3. Start `hal0-api` as `hal0`.
4. Verify configuration and runtime writes.
5. Verify slot lifecycle through the helper.
6. Keep `doctor perms` as an audit command.

## Release checkpoints

### R1 — Secure and installable

- Exposure classification and auth
- Permissions migration
- Installer rerun safety
- Compatibility-preserving deletions
- Fresh and repeat install on `halo`

### R2 — Model layer

- SQLite registry
- File-set pulling
- Unified model store
- Runner registry
- Canonical model taxonomy
- Store migration and GC verification

### R3 — Slot runtime

- Stable slot IDs
- Port authority
- Uniform lifecycle
- Quadlet units
- GTT-aware capacity and eviction

### R4 — Brain and Hermes

- Shared tool loop
- First-class hal0-brain
- hal0-owned board state or a clearly deferred board migration
- Slim Hermes installer
- Core verified with Hermes absent

### R5 — Surface and launch

- Settings data seam completed
- Introspection and doctor evidence
- Documentation collapsed
- Migration rehearsal
- Side-by-side validation and cutover plan

Each checkpoint should be deployable and mergeable to `main`. Avoid allowing
the integration branch to grow indefinitely.

## Golden-path verification

These scenarios should run before the final process/documentation phase:

1. Fresh install on a clean LXC
2. Installer rerun over a healthy installation
3. Upgrade from the current stable release
4. Authentication bootstrap and key rotation
5. Model pull, slot assignment, and inference
6. Multi-shard and mmproj model pull
7. Model revision update with atomic pointer swap
8. Model delete and refcount-safe byte cleanup
9. Slot rename without broken references
10. Slot delete with unit, state, and port cleanup
11. Permissions drift, repair, and rollback
12. Old config containing removed fields or tables
13. NFS-backed model storage
14. API restart without stopping running slots
15. Core operation with Hermes disabled or removed

## Definition of done for each lane

- Merged to `rework/descar`
- Targeted tests pass
- Import and packaging smoke tests pass
- `check-sunset` remains green
- Scar baseline decreases or remains neutral
- Public compatibility names are narrow delegators or expiring shims
- Cross-lane seam contracts remain intact
- Touched documentation matches code
- Surface impacts are implemented or explicitly deferred
- CI is green
- Deployment behavior is exercised where relevant
- Tracker status and verification evidence are updated

## Deferred roadmap

These items are valuable but should not block the core rework:

- Anthropic `/v1/messages`
- `hal0 launch claude`
- OpenAI Realtime and streaming STT
- New TTS and STT models
- Prometheus and Grafana companions
- Hermes dashboard panels and themes
- Client config writers
- Profile sharing registry
- AI issue triage automation
- Optional per-persona cloud fallback

Explicitly out:

- Silent cloud failover
- Generic workflow/DAG platform
- Document-RAG platform
- Ollama-compatible server surface
- Separate privileged host-agent daemon
- Automatic Proxmox host tuning
- Core dependency on Hermes

## Final operating rule

The rework succeeds when hal0 becomes easier to understand, operate, test, and
change—not when every attractive adjacent feature has been added.

Finish the simplification, validate it on `halo`, merge it, and start the next
product wave from a clean architecture.
