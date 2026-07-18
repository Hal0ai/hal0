# hal0 Hermes Integration Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship independently operable Hermes adapters for live hal0 model/role discovery, Hindsight memory, existing hal0 voice slots, board execution, and scheduled agent automation, with deterministic compatibility, installation, migration, and halo143 acceptance.

**Architecture:** Focused R4 lanes share a small `hal0_hermes_core` transport package but retain separate registration, configuration, health, and degradation boundaries. hal0 owns runtime role resolution, canonical board state, and appliance scheduling; Hermes supplies optional worker execution and scheduled agent sessions. Memory enforces visibility server-side, while voice reuses the existing OpenAI-compatible audio routes.

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, httpx, Hermes Agent provider/memory/voice contracts, Hindsight through hal0 REST, SSE, pytest, Podman/systemd on halo143.

## Global Constraints

- Scope is hal0-related Hermes extensions only; no dashboard, generic context, image, or unrelated ecosystem plugin.
- Hermes remains optional; importing or operating hal0 core must not require Hermes.
- Use Hermes `chat_completions`; do not introduce a new API mode.
- Use `HAL0_CLIENT_KEY` for inference/read and `HAL0_ADMIN_KEY` only for mutations.
- Liveness uses OPEN `GET /api/health`, never `/api/status`.
- Stable opaque slot ID is identity; slot name is a mutable label; PortAuthority alone owns ports.
- Raw conversation is private by default; durable extracted/explicit memory is shared by default; explicit private durable memory remains supported.
- Recalled memory is untrusted historical context even when `system_verified`.
- Keep the two `hal0-memory` packaging copies byte-identical and parity-tested.
- Preserve Hermes-owned configuration by applying narrow keys and an overrides deep merge; never replace `config.yaml` wholesale.
- Never mutate or push to LXC105; validate on halo143 and keep LXC105 as read-only reference/rollback evidence.
- Every new route must be classified in `src/hal0/security/exposure.py`.
- Local verification is capped and targeted; GitHub CI through the open PR is the full-suite gate.
- hal0's SQLite board is canonical; Hermes Kanban is an optional executor ledger, never a synchronized second board.
- Hermes cron is only for scheduled agent work; systemd/hal0 retains all appliance maintenance scheduling.
- Use supported Hermes Kanban/Jobs APIs; never read or write `~/.hermes/kanban.db`, `~/.hermes/cron/jobs.json`, or Hermes internal state.

---

## Delivery graph

```text
HP-compat ──> HP-core ──┬──> HP-memory ──> P2-memory rehearsal
                        ├──> HP-voice
                        ├──> HP-executor <── KB-4 hal0 board
                        └──> HP-automation <── HP-provider role aliases
§11.1/2 + KB-1 ─> HP-role-api ─> HP-provider
                                  └────────> halo143 suite acceptance
```

Each `###` task is a reviewer-sized gate. Stages are checkpoint boundaries: do not start a later stage until the preceding exit gate is satisfied. Within Stage 2, the memory, provider, and voice lanes may run in parallel because their file ownership is disjoint; serialize work that touches `hermes_provision.py`, shared plugin packaging, or the HERMES collision class.

## Stage 0 — Compatibility freeze

**Purpose:** Establish the exact upstream contract before feature code is written.

**Entry:** R2 is on `main`; §7.4/F.7 is landed; current Hermes evidence is available.

**Work:** Task 1 (`HP-compat`).

**Exit gate:** Reviewed immutable Hermes pin, copied contract fixtures, plugin discovery smoke, targeted tests and CI green. If compatibility cannot be proven, stop the suite here and keep the current bundle active.

### Task 1: Pin and fixture the supported Hermes contract

**Files:**
- Create: `tests/fixtures/hermes/contracts/provider_profile.py`
- Create: `tests/fixtures/hermes/contracts/memory_provider.py`
- Create: `tests/fixtures/hermes/contracts/voice.py`
- Create: `tests/agents/hermes/test_contract_compatibility.py`
- Modify: `installer/agents/hermes/requirements.txt`
- Modify: `docs/rework/hermes-official-integration-research.md`

**Interfaces:**
- Produces: one reviewed immutable Hermes tag/commit; import fixtures matching its provider, memory, and voice callable signatures.

- [ ] **Step 1: Write a failing compatibility test** that imports each copied contract and asserts provider `api_mode == "chat_completions"`, memory hooks `prefetch`, `system_prompt_block`, `sync_turn`, and the selected voice registration callable.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src ./.venv/bin/pytest tests/agents/hermes/test_contract_compatibility.py -q` and expect failure because fixtures/pin are absent.
- [ ] **Step 3: Select a reviewed official Hermes tag/commit**, record tag, commit SHA, release date, and source links in the research note, pin it exactly in `requirements.txt`, and copy only the minimal ABC/signature fixtures under the test-only path with upstream license headers.
- [ ] **Step 4: Run the compatibility test** and `./.venv/bin/python scripts/check_sunset.py`; expect PASS and scar baseline unchanged.
- [ ] **Step 5: Commit** with `test(hermes): pin supported plugin contracts`.

## Stage 1 — Shared platform seams

**Purpose:** Build the small reusable transport and move role resolution to live hal0 truth.

**Entry:** Stage 0 accepted.

**Work:** Task 2 (`HP-core`), then Tasks 3–4 (`HP-role-api`). Task 2 may begin independently; Tasks 3 and 4 remain sequential.

**Exit gate:** Shared client passes auth/redaction/retry tests; role endpoint is generation-stamped and CLIENT-classified; slot/config changes emit invalidations; core still starts with Hermes absent; combined CI green.

### Task 2: Build the shared authenticated transport

**Files:**
- Create: `src/hal0/agents/hermes/core/__init__.py`
- Create: `src/hal0/agents/hermes/core/client.py`
- Create: `src/hal0/agents/hermes/core/errors.py`
- Create: `src/hal0/agents/hermes/core/types.py`
- Create: `tests/agents/hermes/core/test_client.py`

**Interfaces:**
- Produces: `Hal0HermesClient(base_url, client_key=None, admin_key=None)`, `request_read()`, `request_mutation()`, `health()`, and typed `Unauthorized`, `Unavailable`, `IncompatibleSchema`, `MissingResource` errors.

- [ ] **Step 1: Write failing tests** proving `health()` sends no key, reads send only `HAL0_CLIENT_KEY`, mutations send only `HAL0_ADMIN_KEY`, 401 differs from connection failure, diagnostics redact keys, and retries occur only for connect/timeout/502/503/504 with a bounded attempt count.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src ./.venv/bin/pytest tests/agents/hermes/core/test_client.py -q`; expect import failure.
- [ ] **Step 3: Implement** a sync `httpx.Client` wrapper with `Authorization: Bearer <HAL0_*_KEY>` (the canonical KB-1 programmatic-auth contract), generated `X-Request-ID`, optional idempotency key for mutations, `(2.0, 15.0)` connect/read timeouts, three attempts, and status/error-code decoding. Keep model, memory, and voice policy out of this package.
- [ ] **Step 4: Run the targeted test, ruff check, and ruff format check**; expect all PASS.
- [ ] **Step 5: Commit** with `feat(hermes): add shared hal0 adapter transport`.

### Task 3: Extract runtime role resolution from provisioning

**Files:**
- Create: `src/hal0/agents/role_slots.py`
- Create: `tests/agents/test_role_slots.py`
- Modify: `src/hal0/agents/hermes_provision.py`

**Interfaces:**
- Produces: `resolve_role_slots(agent_id: str, slots: Sequence[SlotView]) -> RoleSlotMap`; roles `main`, `compression`, `vision`, `approval`, `session_search`, `memory_flush`, `skills_hub`, `mcp`.

- [ ] **Step 1: Move the existing `_resolve_auxiliary_tasks()` examples into failing policy tests** covering utility-slot preference, main fallback, NPU virtual addressing, rename preserving opaque ID, and model swap changing advertised alias.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src ./.venv/bin/pytest tests/agents/test_role_slots.py -q`; expect missing module.
- [ ] **Step 3: Implement immutable Pydantic records** `RoleSlotEntry(role, slot_id, label, model, ready, capabilities, basis)` and `RoleSlotMap(agent_id, generation, entries)`. Make provisioning call the new resolver temporarily; do not duplicate its policy.
- [ ] **Step 4: Run new tests plus** `tests/agents/test_hermes_provision.py -q`; expect PASS.
- [ ] **Step 5: Commit** with `refactor(agents): extract runtime role-slot policy`.

### Task 4: Expose the generation-stamped role map and invalidations

**Files:**
- Create: `src/hal0/api/routes/agent_role_slots.py`
- Create: `tests/api/test_agent_role_slots.py`
- Modify: `src/hal0/api/__init__.py`
- Modify: `src/hal0/security/exposure.py`
- Modify: slot/config event emitters identified by `rg 'events.emit|\.emit\(' src/hal0/slots src/hal0/config`

**Interfaces:**
- Produces: CLIENT-classified `GET /api/agents/{agent_id}/role-slots`; event `agent.role_slots.invalidated` carrying `agent_id` and new generation.

- [ ] **Step 1: Write failing API tests** for complete ordered output, stable generation for unchanged inputs, changed generation after readiness/model/capability changes, CLIENT auth, and no mutation route.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src ./.venv/bin/pytest tests/api/test_agent_role_slots.py tests/security/test_exposure.py -q`; expect route/classification failures.
- [ ] **Step 3: Implement the thin router** (`parse -> resolve_role_slots -> model_dump`) and an invalidation publisher called after committed slot create/delete/rename/model/capability/readiness changes. Events are hints only; the endpoint remains authoritative.
- [ ] **Step 4: Run API tests, event tests, exposure tests, import smoke, ruff, and sunset**; expect PASS.
- [ ] **Step 5: Commit** with `feat(api): add live Hermes role-slot map`.

## Stage 2 — Independently shippable adapters

**Purpose:** Deliver memory, provider, and voice as separate vertical slices before combining installer wiring.

**Entry:** Stage 1 accepted.

**Parallel lanes:**

- **Stage 2A — Memory:** Tasks 5–8. Tasks 5 and 6 establish server policy; Task 7 upgrades the plugin; Task 8 rehearses the existing migrator.
- **Stage 2B — Provider:** Tasks 9–10. Requires the Stage 1 role endpoint and event contract.
- **Stage 2C — Voice:** Task 11. Requires shared transport and existing unified audio endpoints, but not provider completion.

**Integration step:** Task 12 begins only after 2A, 2B, and 2C pass their targeted gates.

**Exit gate:** Each adapter independently passes its contract and failure tests; specialized plugin destinations and two-copy memory parity are correct; repeated installation converges without overwriting unrelated Hermes configuration; combined CI green.

### Task 5: Add server-enforced Hermes memory identity and visibility

**Files:**
- Create: `src/hal0/memory/hermes_policy.py`
- Create: `tests/memory/test_hermes_policy.py`
- Modify: relevant request models/routes under `src/hal0/api/routes/memory*.py` found with `rg 'recall|retain' src/hal0/api/routes`

**Interfaces:**
- Produces: `HermesMemoryIdentity`, `banks_for_read(identity)`, `bank_for_write(identity, kind, visibility)`.

- [ ] **Step 1: Write failing table tests**: raw primary/delegated turns write private namespaces; durable writes default shared; explicit private durable writes private; cron/flush/synthetic raw writes are rejected; request fields cannot name another private bank.
- [ ] **Step 2: Run** the new test and expect missing policy.
- [ ] **Step 3: Implement policy from server-controlled agent/profile/session/user/delegation context**, returning shared plus caller-private banks on read and never trusting browser/request bank expansion.
- [ ] **Step 4: Run memory policy, namespace, recall-route, and security exposure tests**; expect PASS.
- [ ] **Step 5: Commit** with `feat(memory): enforce Hermes visibility policy`.

### Task 6: Normalize ranked recall as fenced historical context

**Files:**
- Create: `src/hal0/memory/recall_context.py`
- Create: `tests/memory/test_recall_context.py`
- Modify: memory recall response model used by the Hermes client

**Interfaces:**
- Produces: `build_recall_context(items, max_tokens) -> RecallContext` with provenance, visibility, confidence, verification, observed time, supersession, and `untrusted_historical_context=True`.

- [ ] **Step 1: Write failing tests** for duplicate collapse, score ordering, hard token cap, superseded exclusion, malicious instruction text preserved only inside a fenced historical block, and all five verification classifications.
- [ ] **Step 2: Run** the new test; expect missing module.
- [ ] **Step 3: Implement deterministic normalization**: canonical-text dedupe, stable score/time tie-break, token estimator, metadata prefix per item, and an explicit system preamble that memories are evidence rather than instructions.
- [ ] **Step 4: Run targeted memory tests and ruff**; expect PASS.
- [ ] **Step 5: Commit** with `feat(memory): produce bounded provenance recall context`.

### Task 7: Upgrade both hal0-memory plugin copies to the pinned ABC

**Files:**
- Modify in parity: `src/hal0/agents/hermes/plugins/memory_hindsight/{provider.py,_client.py,__init__.py,plugin.yaml}`
- Modify in parity: `installer/agents/hermes/plugins/hal0-memory/{provider.py,_client.py,__init__.py,plugin.yaml}`
- Modify: `tests/agents/hermes/plugins/test_memory_hindsight_plugin.py`
- Modify: `tests/agents/hermes_plugins/test_seed_parity.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 5, 6.
- Produces: pinned `MemoryProvider` lifecycle; config-only `is_available`; bounded `prefetch`; `queue_prefetch`; private raw `sync_turn`; tools `hindsight_retain`, `hindsight_recall`, `hindsight_reflect` with `visibility`.

- [ ] **Step 1: Write failing contract tests** for setup schema/persistence, initialization diagnostics, shutdown, prefetch budget/fencing, deeper queue prefetch, private raw capture, shared-default durable retain, explicit private retain, and independent transport failure degradation.
- [ ] **Step 2: Run plugin and parity tests**; expect contract/name/layout failures.
- [ ] **Step 3: Implement the source copy**, copy it byte-for-byte to the seed, update `plugin.yaml` to `kind: exclusive`, and install to `$HERMES_HOME/plugins/memory/hal0-memory/`.
- [ ] **Step 4: Run contract, parity, provision-install-artifact, and Hermes-absent import tests**; expect PASS.
- [ ] **Step 5: Commit** with `feat(hermes): complete Hindsight memory provider`.

### Task 8: Validate existing Honcho migration with seeded fixtures

**Files:**
- Create: `tests/fixtures/honcho/hermes_workspace.json`
- Modify: `tests/cli/test_memory_migrate.py`
- Modify: `docs/guides/honcho-memory.mdx`

**Interfaces:**
- Consumes: existing `hal0 memory migrate --from honcho --to hindsight` only; no second migration engine.

- [ ] **Step 1: Add a deterministic fixture** containing public durable facts, private raw conversation, duplicate facts, and one superseded fact; add dry-run and real-run assertions for counts and representative recalls.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src ./.venv/bin/pytest tests/cli/test_memory_migrate.py -q`; expect fixture expectation failure.
- [ ] **Step 3: Make only compatibility fixes required by the existing migrator**, document halo143 seeding/export/import/verify commands, and document that LXC105 export is sanitized/read-only.
- [ ] **Step 4: Run CLI migration and persisted-`[honcho]` boot tests**; expect PASS.
- [ ] **Step 5: Commit** with `test(memory): cover Hermes Honcho migration fixture`.

### Task 9: Implement provider inventory and stale-cache behavior

**Files:**
- Create: `installer/agents/hermes/plugins/model-providers/hal0/{__init__.py,provider.py,plugin.yaml}`
- Create: `src/hal0/agents/hermes/plugins/provider_hal0/{__init__.py,provider.py}`
- Create: `tests/agents/hermes/plugins/test_provider_hal0.py`

**Interfaces:**
- Consumes: `Hal0HermesClient`; `/v1/models`; `/api/agents/{agent_id}/role-slots`.
- Produces: normalized `Hal0Model` inventory and Hermes provider profile in `chat_completions` mode.

- [ ] **Step 1: Write failing tests** for hal0-owned filtering, stable slot IDs, mutable labels, readiness/capabilities/context, external-upstream opt-in, empty inventory, warming, missing selected model, unauthorized, unavailable, and incompatible schema.
- [ ] **Step 2: Run provider tests**; expect missing plugin.
- [ ] **Step 3: Implement complete inventory fetch and last-known-good cache**. Permit stale cache only for already selected models; block new selection while unavailable; never silently replace a missing model.
- [ ] **Step 4: Run provider tests, plugin discovery fixture, parity/install tests, and ruff**; expect PASS.
- [ ] **Step 5: Commit** with `feat(hermes): add live hal0 model provider`.

### Task 10: Add invalidate-and-refetch refresh

**Files:**
- Create: `src/hal0/agents/hermes/core/events.py`
- Create: `tests/agents/hermes/core/test_events.py`
- Modify: provider files from Task 9

**Interfaces:**
- Produces: `EventInvalidator.run(cursor, on_invalidate)` consuming SSE, REST backfill, `epoch`, `next_since`, and `events.gap`.

- [ ] **Step 1: Write failing tests** for subscribe-first startup, duplicate event suppression, relevant-event coalescing, reconnect backfill, epoch reset, gap full reconciliation, periodic reconciliation, and stale timestamp.
- [ ] **Step 2: Run event tests**; expect missing class.
- [ ] **Step 3: Implement the invalidator** so every relevant event triggers a complete inventory plus role-map refetch; never patch authoritative state from event payloads.
- [ ] **Step 4: Run core event, API event, and provider hot-swap tests**; expect PASS.
- [ ] **Step 5: Commit** with `feat(hermes): refresh provider from hal0 events`.

### Task 11: Implement hal0 voice transport and guards

**Files:**
- Create: `src/hal0/agents/hermes/plugins/voice_hal0/{__init__.py,provider.py}`
- Create: `installer/agents/hermes/plugins/voice/hal0/{__init__.py,provider.py,plugin.yaml}`
- Create: `tests/agents/hermes/plugins/test_voice_hal0.py`

**Interfaces:**
- Consumes: `POST /v1/audio/transcriptions`, `POST /v1/audio/speech`, role/capability readiness, `Hal0HermesClient`.
- Produces: pinned Hermes voice callables for transcription and synthesis.

- [ ] **Step 1: Write failing tests** for multipart STT, binary TTS, MIME/size/duration limits, empty audio, missing/warming slot, unauthorized, timeout, cancellation/interruption, and no cloud fallback.
- [ ] **Step 2: Run voice tests**; expect missing plugin.
- [ ] **Step 3: Implement bounded transport** to the unified `/v1` endpoints, not slot ports; resolve capability/readiness immediately before each call so swaps require no restart.
- [ ] **Step 4: Run voice plugin, dispatcher audio, and harness unit tests**; expect PASS without requiring live audio hardware.
- [ ] **Step 5: Commit** with `feat(hermes): route voice through hal0 slots`.

### Task 12: Wire narrow config, environment, and diagnostics

**Files:**
- Modify: `src/hal0/agents/hermes_provision.py`
- Modify: `installer/agents/hermes/requirements.txt`
- Modify: `tests/cli/test_agent_install_hermes.py`
- Modify: `tests/agents/test_hermes_provision_install_artifacts.py`

**Interfaces:**
- Consumes: all three plugin trees and shared-core configuration.
- Produces: idempotent enable/disable, exact runtime destinations, preserved unrelated Hermes config, redacted doctor report.

- [ ] **Step 1: Write failing install/upgrade tests** proving specialized destinations, exact pin, unrelated `image_gen`/gateway/profile config preservation, client-key-only default, plugin-by-plugin disable, rollback to prior known-good bundle, and repeated install convergence.
- [ ] **Step 2: Run the targeted provision tests**; expect destination/config failures.
- [ ] **Step 3: Extend the convergent installer** with directory-copy and narrow `hermes config set`/overrides merge operations. Write secrets only through the existing privileged seam; never render the whole config.
- [ ] **Step 4: Run provision, CLI, seed parity, import smoke, ruff, format, and sunset checks**; expect PASS.
- [ ] **Step 5: Commit** with `feat(hermes): install hal0 integration suite`.

## Stage 3 — Optional orchestration adapters

**Purpose:** Add deeper board and cron integration without making either a second source of hal0 truth.

**Entry:** Stage 2 accepted. `HP-executor` additionally waits for KB-4/5/6's canonical hal0 board dispatch/ETag seam. `HP-automation` additionally requires stable provider role aliases from Tasks 9–10.

**Parallel lanes:** Task 13 (`HP-executor`) and Task 14 (`HP-automation`) may run independently once their individual prerequisites are met. If KB-4 is not ready, defer Task 13 without blocking automation or the core adapter suite.

**Exit gate:** hal0 remains canonical for board state and appliance scheduling; executor reconciliation is idempotent; cron pins stable hal0 aliases, isolates cron memory, rejects maintenance jobs, and never accesses Hermes internal storage; CI green.

### Task 13: Add the hal0-board Hermes executor bridge

**Files:**
- Create: `src/hal0/agents/hermes/core/executor.py`
- Create: `tests/agents/hermes/core/test_executor.py`
- Modify: the KB-4 board dispatch seam selected when that lane lands

**Interfaces:**
- Consumes: authenticated Hermes Kanban/worker API; canonical hal0 task and immutable attempt IDs.
- Produces: `HermesExecutor.dispatch(task) -> ExternalRun`, `inspect(run_id)`, `cancel(run_id)`, and `reconcile(cursor)`.

- [ ] **Step 1: Write failing contract tests** mapping hal0 ready/attempt state to Hermes dispatch, heartbeat, dependency block, needs-input block, completion handoff, failure, cancellation, retry as a new attempt, and reconnect reconciliation.
- [ ] **Step 2: Run** `PYTHONPATH=$PWD/src ./.venv/bin/pytest tests/agents/hermes/core/test_executor.py -q`; expect missing executor.
- [ ] **Step 3: Implement the narrow adapter** using supported authenticated APIs. Carry hal0 task/attempt and Hermes board/task/run/session correlation IDs; persist only summaries, verification metadata, and pointers in hal0. Never let Hermes mutate canonical dependencies, owner, approval, or completion directly.
- [ ] **Step 4: Run executor, board concurrency/ETag, approval, and event tests**; expect duplicate callbacks to be idempotent and Hermes outage to leave a reconcilable hal0 attempt.
- [ ] **Step 5: Commit** with `feat(hermes): add optional board executor bridge`.

### Task 14: Add scheduled agent automation through Hermes Jobs

**Files:**
- Create: `src/hal0/agents/hermes/core/automation.py`
- Create: `tests/agents/hermes/core/test_automation.py`
- Modify: provider role-alias code from Tasks 9–10
- Modify: memory policy tests from Task 5

**Interfaces:**
- Consumes: authenticated Hermes Jobs API and stable provider models such as `provider=hal0`, `model=role:main`.
- Produces: `HermesAutomation.list/create/update/pause/resume/run/remove`; normalized run events and optional approval-aware board-task creation.

- [ ] **Step 1: Write failing tests** for CRUD/lifecycle, stable role alias pinning, restricted cron toolsets, no recursive scheduling, job/run correlation, delivery failure, and API reconciliation without touching `jobs.json`.
- [ ] **Step 2: Write memory tests** proving cron raw turns use a dedicated private namespace, never primary raw memory, while extracted durable facts default shared with job/run provenance.
- [ ] **Step 3: Run automation and memory tests**; expect missing adapter/policy behavior.
- [ ] **Step 4: Implement Jobs API calls and normalized events** `hermes.cron.fired`, `skipped`, `completed`, `failed`, and `delivery_failed`. Permit board creation/advancement only through authenticated approval-aware hal0 APIs. Reject appliance-maintenance job kinds in this adapter.
- [ ] **Step 5: Run automation, provider alias, memory, exposure, and degradation tests**; expect fail-closed behavior when a pinned provider/role disappears and no implicit cloud fallback.
- [ ] **Step 6: Commit** with `feat(hermes): add scheduled agent automation`.

## Stage 4 — Resilience and optionality gate

**Purpose:** Prove that no adapter failure compromises another adapter or hal0 core.

**Entry:** All promoted Stage 2 and Stage 3 lanes are merged. A deferred `HP-executor` is excluded explicitly rather than represented as partially complete.

**Work:** Task 15.

**Exit gate:** Failure matrix passes for API, events, memory, voice, executor, and automation surfaces that are present; Hermes-absent import/start passes; capped combined verification and CI green.

### Task 15: Prove independent degradation and core-without-Hermes

**Files:**
- Create: `tests/agents/hermes/test_suite_degradation.py`
- Modify: `tests/harness/integration/test_voice_roundtrip.py`

**Interfaces:**
- Produces: acceptance matrix showing provider, memory, and voice failures do not terminate the Hermes agent loop or hal0 core.

- [ ] **Step 1: Add parametrized tests** that independently fail model API, events, memory, STT, and TTS; assert unaffected adapters remain usable and errors identify auth/reachability/schema/resource correctly.
- [ ] **Step 2: Add a subprocess import/start test** with Hermes packages and plugin paths absent; `from hal0.api import create_app; create_app()` must succeed.
- [ ] **Step 3: Run** `PYTHONPATH=$PWD/src ./.venv/bin/pytest tests/agents/hermes/test_suite_degradation.py tests/harness/integration/test_voice_roundtrip.py -q`; fix only integration seams exposed by these scenarios.
- [ ] **Step 4: Run the capped lane gate**: ruff check, ruff format check, import smoke, sunset, and all targeted Hermes plugin/API/memory tests under 90 seconds per group.
- [ ] **Step 5: Commit** with `test(hermes): prove adapter isolation and optionality`.

## Stage 5 — halo143 deployment and R4 acceptance

**Purpose:** Validate deployment-shaped behavior, migration, upgrade, rollback, and restart-free updates on the clean target box.

**Entry:** Stage 4 accepted at a specific CI-green commit; wheel and Hermes pin recorded; halo143 snapshot available.

**Work:** Task 16. Run only the acceptance sections for promoted lanes, explicitly recording deferred lanes.

**Exit gate:** Fresh and repeated install, provider hot-swap, memory migration/privacy, voice roundtrip, promoted orchestration adapters, and rollback all pass; canonical board rows contain commit, CI, verification, and halo143 evidence; LXC105 remains untouched.

### Task 16: Rehearse and accept on halo143

**Files:**
- Create: `docs/rework/hermes-suite-halo143-acceptance.md`
- Modify: `/home/mint/REWORK_BOARD.md` (orchestrator only, after each evidenced state transition)

**Interfaces:**
- Produces: deployment-shaped evidence and rollback record; no code contract.

- [ ] **Step 1: Confirm CI is green through the open PR**, record commit SHA, wheel contents, and Hermes pin.
- [ ] **Step 2: Snapshot halo143**, install the candidate twice, and record born ownership, exact plugin destinations, systemd health, and preserved Hermes config. Do not touch LXC105.
- [ ] **Step 3: Exercise provider acceptance**: create/rename/delete a slot, swap its model, change readiness/capability, and verify Hermes inventory/role aliases update without restart; disconnect SSE and verify REST backfill; inject `events.gap` and verify full reconciliation.
- [ ] **Step 4: Exercise memory acceptance** with seeded Honcho fixtures: dry-run, migrate, compare counts, recall shared plus caller-private, verify another identity cannot read private raw turns, verify explicit private durable memory, and verify prompt-injection text remains fenced historical context.
- [ ] **Step 5: Exercise voice acceptance**: TTS then STT roundtrip through active slots, swap each slot/model, repeat without Hermes restart, interrupt an in-flight operation, and prove no cloud request occurs.
- [ ] **Step 6: Exercise executor acceptance**: dispatch one hal0 attempt, observe heartbeat, needs-input, completion and cancellation, restart Hermes mid-attempt, reconcile without duplicate completion, and verify hal0 remains canonical.
- [ ] **Step 7: Exercise automation acceptance**: create/pause/resume/run/remove an agent job pinned to `hal0/role:main`, swap the backing model without editing the job, verify cron-private raw memory and shared durable provenance, and prove appliance maintenance cannot be scheduled through this adapter.
- [ ] **Step 8: Exercise rollback** to the prior plugin bundle/config and verify unrelated Hermes settings remain unchanged.
- [ ] **Step 9: Update board rows** with exact commit, commands/results, CI URL, halo143 deploy state, and any discovered follow-up as a separate lane; mark `✔` only where CI and deployment evidence both satisfy the lane DoD.
- [ ] **Step 10: Commit the acceptance record** with `docs(hermes): record halo143 suite acceptance`.

## Self-review record

- Spec coverage: compatibility, core, runtime role resolution, provider refresh/failure/diagnostics, memory identity/visibility/capture/recall/migration, voice, board execution, scheduled agent automation, install/upgrade/rollback, independent degradation, and halo143 acceptance each map to an explicit task.
- Deliberate deferral: `HP-context` remains a separate post-core board lane; it is not smuggled into memory.
- Type consistency: Tasks 2, 3, 6, and 10 define the shared names consumed by later tasks.
- Placeholder scan: implementation choices left to workers are bounded by named contracts and concrete assertions; no open-ended feature placeholders are present.
