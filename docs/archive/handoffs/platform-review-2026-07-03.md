# hal0 platform review — reliability, config surface & UI

**Date:** 2026-07-03
**Scope:** `src/hal0` (~75k LOC) · `ui/src` (~32k LOC)
**Method:** five parallel deep-read passes (slot config, model registry, profiles/stacks/bundles, dispatch/runtime, UI editing drawers) + spot-verification of the headline findings against source.
**Status:** assessment only — no code changes were made.

Interactive version of this report (filterable by severity):
<https://claude.ai/code/artifact/00ab159f-e821-4244-88d4-1577f7589ef0>

Every finding cites a real `file:line`, a concrete failure scenario, and a fix. Findings are ranked `critical > high > medium > low` within each subsystem. IDs (e.g. `SC-1`, `DR-2`) are stable references used by the through-lines and the fix sequence at the bottom.

---

## TL;DR — four born-broken / silent-failure bugs to fix first

| ID | Severity | One-liner |
|----|----------|-----------|
| **SC-1** | critical | Disabling a capability never writes `enabled=false` to the slot TOML → the disable silently doesn't stick; a later request wakes the "disabled" slot. |
| **SC-2** | critical | NPU picker probes `docker` on a podman-only runtime → the NPU trio is never offered on the reference platform. |
| **PS-1** | critical | `DEVICE_DEFAULT_PROFILES['cpu'] = 'tts'` → GPU-less installs get a chat slot bound to the Kokoro TTS engine (cannot serve chat). |
| **DR-1** | critical | Idle-evicted non-chat slots (embed/rerank/tts) never wake on request → they 404 until a manual load. |

All four are small, localized, and each unbreaks a user-visible flow that currently fails silently.

---

## Through-lines (recurring root causes)

Fixing these retires whole clusters of findings at once.

- **T1 — The two "reconciled truths" still drift.** `capabilities.toml` and `slots/*.toml` are meant to be one truth (#697), but `enabled` is never projected onto the slot TOML, reconciliation content is copy-pasted, and raw reads skip normalization. *(SC-1, SC-3, SC-8, SC-11, SC-12)*
- **T2 — Validation lands at dispatch/boot, not at the write.** Double-default, port collisions, disable-drift, and stack-apply all surface when a request routes or the box reboots — not on the POST/CLI that caused them. *(SC-4, SC-7, PS-5, UI-6)*
- **T3 — The same logic is re-implemented in parallel, and copies drift.** device→profile ×3, model classification ×4, ready-set ×3–4, the drawer/form shell ×4, the name regex ×3, the compatible-model filter ×3. Several already ship the wrong answer. *(PS-4, MR-3, DR-8, UI-12, UI-7, UI-3)*
- **T4 — Wake-on-request only covers chat slots.** The idle sweeper evicts non-pinned slots assuming transparent reload, but the reload path exists only for `type=llm` chat. *(DR-1, DR-7)*
- **T5 — Overloaded single fields carry two meanings.** `enabled` = autostart *and* routable; `backend`/`device` coexist with coherence guards that only cover a subset. *(SC-8, PS-3, PS-6)*
- **T6 — Destructive & disruptive actions lack friction and honesty.** Model swap cold-restarts a live container from a dropdown; "Pause" destroys a download; "fits in memory" is faked; slot delete uses a raw browser confirm; no editing drawer has a dirty guard. *(UI-1, UI-5, UI-9, UI-6, UI-16)*

---

## Slot config — capabilities ↔ slots reconciliation

### SC-1 · critical · Disabling a capability never writes `enabled=false` to the slot TOML
`slot_config/__init__.py:271-312` · `capabilities/orchestrator.py:474-478`
`_reconciled_slot` short-circuits on disable (returns `raw_before`) and never writes `enabled` on either path; the orchestrator disable branch only calls `unload()`. So `capabilities.toml` says `enabled=false` while `slots/<name>.toml` still says `enabled=true` with a model bound. A later `/v1/embeddings` request hits `resolve_for_request`, sees `enabled=true`, wakes the slot on-request, and serves from the "disabled" slot. (The NPU-trio path had to special-case writing `enabled` via `update_config` precisely because the reconcile path doesn't — `orchestrator.py:756`.)
**Fix:** make `_reconciled_slot` the single owner of `enabled` and write it unconditionally on enable and disable — or have routing read enablement from `capabilities.toml`, not the slot TOML.

### SC-2 · critical · NPU picker probes `docker` on a podman-only runtime
`capabilities/catalog.py:168-224`
`_flm_image_present()` shells `docker image inspect`, and `available_backends()` gates the whole NPU picker row on it. Every slot runs under podman (ContainerProvider), so `docker` is absent → `FileNotFoundError` → `False` → NPU never advertised even with XDNA + the FLM image present. The module docstring also forbids subprocesses in these helpers, yet this runs a 2s-timeout subprocess on every `GET /api/capabilities`.
**Fix:** probe via podman (or the shared container-provider image-inspect path) and cache the result; align with the stated no-subprocess contract.

### SC-3 · high · Raw TOML reads skip backend→device promotion
`slots/manager.py:2617-2682` · `slots/manager.py:2033`
`iter_configs()`/`_load_slot_config()` read slot TOML with raw `tomllib` and never run `SlotConfig`'s `_promote_backend_to_device`. A slot carrying only legacy `backend='flm'` yields a dict with `device` absent, so `_check_npu_exclusivity`'s `peer.get('device') != 'npu'` skips it → two enabled NPU LLM anchors can both be written, and the trio embed/asr modality toggles find no anchor to write to.
**Fix:** normalize through `SlotConfig` / `map_backend_to_device` in `_load_slot_config` before any consumer reads `device`.

### SC-4 · high · "One default per type" refuse-to-save rule is unimplemented
`slots/manager.py:1386-1409` · `slots/manager.py:1687,1786`
CONTEXT promises two same-type `default=true` slots is a config error refused at save/load. No write-time check exists; the only enforcement is `default_slot_for()` raising at routing time, so a hand-edited double-default 400s every request of that type rather than being rejected at the POST.
**Fix:** validate default-uniqueness-per-type in `create`/`update_config`; add an explicit "make default" op that atomically clears the prior default.

### SC-5 · high · `create()` has no existence guard → overwrites a custom slot, orphans its container
`slots/manager.py:1687-1758`
The unique-name rule only rejects seeded names; `create()` then writes the slot TOML unconditionally and force-resets `state.json` to OFFLINE. Re-adding an existing custom slot clobbers config + state, and if the port changed the previously-running container leaks (nothing tracks the old unit/port).
**Fix:** reject `create()` when the config path exists (or route through update); the "unique name" rule must cover existing customs, not just seeded names.

### SC-6 · high · `rerank` slot-name split — seeded `rerank` is vestigial
`slots/manager.py:74-83` · `capabilities/orchestrator.py:67-74`
`SEEDED_SLOTS` includes `rerank`, but the capabilities bridge maps `('embed','rerank') → 'embed-rerank'` and auto-creates that. The seeded rerank slot is never used by the capabilities layer; ownership of "which slot is the reranker" is ambiguous across two modules.
**Fix:** pick one name — seed `embed-rerank`, or map the capability onto the seeded `rerank` slot.

### SC-7 · medium · Port allocation races across an await
`capabilities/orchestrator.py:848-876` · `slots/manager.py:1722`
`_next_free_slot_port()` scans TOMLs (sync), then `create()` awaits `_resolve_model_info` before writing the TOML. Two concurrent first-enables interleave at that await and both claim the same "free" port; the second container fails to bind.
**Fix:** allocate + persist the port under the per-slot lock, or add global port-uniqueness validation on write.

### SC-8 · medium · `enabled` is overloaded: "autostart on boot" vs "routable now"
`config/schema.py:302-305` · `slots/manager.py:1422`
`SlotConfig.enabled` is documented as "started on hal0 startup" (autostart), but routing, the omni tool filter, and the composite upstream all treat `enabled=false` as "not routable." Two concepts on one field with no single owner — the root cause of SC-1.
**Fix:** separate autostart from capability-enabled/routable, or make the capabilities layer the sole owner and document the routing semantics.

### SC-9 · medium · `SlotConfigStore.commit()` rollback is best-effort
`slot_config/__init__.py:200-220`
On a later-file failure the rollback loop suppresses `OSError` and re-raises the original error. If rollback itself fails after file 1 was rewritten, disk is left half-reconciled while the caller sees only the first error — contradicting the module's advertised atomicity invariant.
**Fix:** chain rollback failures into the raised error and surface a distinct "config left inconsistent" signal a next-boot reconciler can heal.

### SC-10 · medium · No cross-process lock on `capabilities.toml`
`capabilities/config.py:291-307`
Three independent writers touch `capabilities.toml` (API commit, first-boot seed, CLI `capabilities migrate`). Writes are atomic so no corruption, but with no file lock a CLI migrate racing an API apply silently drops one update.
**Fix:** advisory `flock` around the shared capabilities/slot-config critical section, used by both API and CLI.

### SC-11 · medium · Slot-projection reconciliation is duplicated by hand
`slot_config/__init__.py:297-312` · `slots/manager.py:1809-1820`
#697 unified the byte write but not the content: the nested-dict merge + `ctx_size→context_size` fold is copy-pasted in both places (the store docstring even says it "mirrors update_config exactly"). The `enabled` omission (SC-1) is exactly where the two silently disagree.
**Fix:** one shared "project selection onto slot dict" function consumed by both.

### SC-12 · medium · Three parallel migration mechanisms for one transform
`config/migrations/` · `capabilities/config.py` · `config/schema.py:536`
The versioned MIGRATIONS registry (identity v1 only, wired to `hal0.toml`), the hand-rolled capabilities v1→v2 with its own `.bak` dance, and `SlotConfig`'s on-load `_promote_backend_to_device` each re-implement the same backend→device migration. Easy to fix one and miss the others — see SC-3.
**Fix:** route the capabilities migration through the versioned registry; make `map_backend_to_device` the single normalize applied on every read.

### SC-13 · low · `auto_migrate` clobbers the live capabilities file despite its "leave untouched" contract
`capabilities/config.py:291-307`
When a `.v1.bak` already exists (a prior crashed migration) the code logs `backup_exists` then falls through and rewrites the target anyway. Harmless (transform is idempotent) but contradicts the docstring and is untested against the promised contract.
**Fix:** return early when the backup exists, or delete the misleading comment.

### SC-14 · low · Three inconsistent slot-port ranges
`capabilities/orchestrator.py:868` · `config/schema.py:94-95` · img slot uses `8188`
The allocator scans `8081-8099`, the `SlotConfig.port` validator allows `8081-8200`, and ComfyUI's img slot uses `8188` — outside the allocator window. A custom slot can validly hold a port the allocator will never reconsider.
**Fix:** single source of truth for the slot-port window.

### SC-15 · low · Dead docs reference the retired `primary→chat` alias
`slots/manager.py:472,2629`
`SLOT_ALIASES` only contains `agent-hermes → agent`, but `_resolve_alias`'s docstring and `_load_slot_config`'s comment still describe a `primary→chat` alias ADR-0023 retired.
**Fix:** delete the stale doc references.

### SC-16 · low · `_profile_for_fit` / `_CAPABILITY_TO_SLOT_TYPE` duplicated
`capabilities/catalog.py:705-735` · `capabilities/orchestrator.py:629-663`
Near-identical copies; `_CAPABILITY_TO_SLOT_TYPE` is defined in both. The #695 model_meta consolidation left these behind.
**Fix:** hoist to a shared module (model_meta or a capabilities helper).

---

## Model registry — registry, pulls, classification

### MR-1 · critical · Installer / bundle-tier pulls bypass the #626 disk-persistence layer
`api/routes/installer.py:384,436` · `install/orchestrate.py:204-223`
The durable pull-job store lives only in `routes/models.py`'s wrappers; the installer calls `run_pull` directly via `background.add_task`, bypassing `_persist_pull_job`. Pick `hal0-Max` (tens of GB), api restarts mid-install → every `/pull/status` and `/pull/stream` 404s — the exact bug #626 fixed, on the path where it hurts most (fresh install, no other UI state). The response even tells the client to reattach the streams that now 404.
**Fix:** route installer pulls through `_run_pull_with_events`, or call `_persist_pull_job` in `apply_setup` + a terminal persist in a `finally` around the background task. Cleanest: lift `_persist_pull_job` into `registry/pull.py` so `run_pull` itself persists terminal state.

### MR-2 · high · A pull that actually completed can be reported "failed" after a restart
`api/routes/models.py:112-127` · `api/routes/models.py:70-93`
`_persist_pull_job` is fail-soft (swallows `OSError`), so the terminal snapshot may never hit disk; `_reconcile_persisted_pull_job` then blindly rewrites any non-terminal on-disk state to `failed`. If the terminal write failed but the model is installed + registered, the user is told it failed and re-pulls a model they already have.
**Fix:** before reconciling `queued/running → failed`, cross-check ground truth: if `registry.has(model_id)` and the file exists, report `completed`. The registry is right there on `app.state`.

### MR-3 · high · Reranker auto-scan misclassifies as "chat"
`registry/discover.py:126-143` · `model_meta/__init__.py:47-76` · `registry/detect.py:101-110`
`discover._guess_capability` (the startup auto-scan path, the default) checks only `('embed','nomic')` — no rerank, no bge token — so `bge-reranker-v2-m3.gguf` falls through to `return 'chat'` and registers with `capabilities=['chat']`. Meanwhile `model_meta.classify` checks `rerank` first and `detect()` reads `pooling_type`. Three code paths, three answers for one file, and the default path is wrong → the reranker is unroutable as a reranker.
**Fix:** have discover and detect delegate to one shared token table (extend model_meta with `capability_from_filename`); add rerank/bge coverage.

### MR-4 · medium · No disk-space preflight before multi-GB pulls
`registry/pull.py:342-398` · `registry/model_store.py:73-116`
Pulls stream until `ENOSPC` then fail with a raw `OSError` string; the known `content-length` and curated `size_gb` are never checked against `disk_usage().free`. Pull a 40GB model onto 12GB free → downloads for minutes, fills the disk (starving other services), fails at ~12GB. `describe_store_state()` already computes `free_bytes` but isn't wired to the pull.
**Fix:** after reading `content-length`, compare to `disk_usage(tmp_dir).free` and fail fast with a structured `model.insufficient_disk` error naming required vs available.

### MR-5 · medium · No cross-process registry write serialization → lost update
`registry/store.py:303-377`
The `threading.RLock` serializes writers within one process only; no file lock. api running (registry in memory) while an operator runs CLI `registry import` → both read the same base, both atomic-write the full map, the later `os.replace` wins and silently drops the other's rows. For "the sole source of truth", a lost update is a real integrity hazard.
**Fix:** advisory `flock` around the read-modify-write in `add`/`update`/`remove`, or re-stat-and-reject if mtime advanced between read and write.

### MR-6 · medium · Model-delete cascade is non-atomic with no rollback
`api/routes/models.py:989-1073` · `api/routes/models.py:904-963`
Cascade order is unload → clear `[model].default` in each slot TOML → `registry.remove()` → event, with no transaction. If `registry.remove()` raises after 3 slots' defaults were cleared, the model row survives but three slots lost their default — silently — and `_clear_slot_default` is itself best-effort (`suppress(OSError)`), so a slot can be left pointing at a deleted id with no surfaced warning until its next load.
**Fix:** remove the registry row first (the authoritative delete), then clear slot defaults; record which slots were mutated in the audit record so partial failure is diagnosable.

### MR-7 · medium · No resume / partial-download support
`registry/pull.py:342-398`
The pull streams to a fresh `.part` each attempt with no HTTP `Range`. A network blip at 39GB of 40 discards everything and re-pulls from 0. Painful on exactly the flaky-home-connection hardware class hal0 targets, even though the HF CDN supports ranged requests.
**Fix:** keep the `.part` on transient failure and issue a ranged continuation; rehash the existing prefix or checkpoint a partial hash.

### MR-8 · medium · Pull-job JSON files are never garbage-collected
`api/routes/models.py:59-93` · `api/routes/models.py:989`
`/var/lib/hal0/model-pull-jobs/<id>.json` is written on every pull; nothing deletes it — not even `delete_model`'s cascade. Over months, hundreds of orphan files including for long-deleted models. The updater has a GC path; this store has none.
**Fix:** unlink the pull-job file in `delete_model`'s cascade, and/or sweep terminal snapshots older than N days on startup.

### MR-9 · medium · No startup sweep of orphaned `.part` partials
`registry/pull.py:328-331,436-464`
`run_pull` cleans `.part` on cancel/error, but an OOM/SIGKILL mid-stream leaves a multi-GB `.part` under `.tmp/` with nothing to sweep it. Repeated crashes during large pulls (common on memory-tight Strix Halo) silently consume the model store.
**Fix:** on startup, delete `.part` files older than a threshold from the tmp dir.

### MR-10 · medium · Reconcile never rewrites disk → the on-disk snapshot lies indefinitely
`api/routes/models.py:96-127`
`_load_persisted_pull_job` reconciles in memory only; the queued/running file on disk is never updated to failed, so every poll re-derives failed and external tooling sees a forever-running job. Contrast `updater.py:547` which writes the reconciled snapshot back.
**Fix:** persist the reconciled snapshot back once (atomic) so the file is self-consistent.

### MR-11 · low · Concurrent double-pull guard isn't atomic across an await
`api/routes/models.py:1410-1446`
The "already queued/running" guard is separated from the `jobs[model_id]=job` insert by `await request.json()`. Double-click Download → both requests pass the guard, both `make_job`, the second overwrites the first; two `run_pull` tasks stream the same file, both `os.replace` onto the same path (wasted bandwidth), the first job's SSE stream is orphaned.
**Fix:** insert a placeholder job before the await, or guard with a per-model_id `asyncio.Lock`.

### MR-12 · low · GGUF embed detection misses `pooling_type` when it precedes `general.architecture`
`registry/gguf_header.py:270-296` · `registry/detect.py:188-197`
The parser speculatively captures `*.context_length` before arch is known but has no equivalent for `<arch>.pooling_type`. An embedding GGUF whose KV block lists `pooling_type` before `general.architecture` (spec permits any order) skips it → `detect()` sees `pooling=None → is_embed=False →` misclassified as chat unless the filename saves it. (Docstring also claims a 1 MiB window while the constant is 8 MiB.)
**Fix:** add `.pooling_type`-suffixed keys to the speculative-capture branch, symmetric with `.context_length`.

### MR-13 · low · Registry atomic write doesn't fsync the parent directory
`registry/store.py:220-254` · `api/routes/models.py:86`
`_atomic_write` fsyncs the tmpfile and `os.replace`s but never fsyncs `target.parent`, so a crash immediately after rename can lose the dir entry on some filesystems. Same omission in `_persist_pull_job`.
**Fix:** `os.open(parent, O_DIRECTORY)` + `os.fsync(dirfd)` after `os.replace`.

### MR-14 · low · Failed-pull errors collapse distinct causes; alias blocklist can hide real models
`registry/pull.py:455-464` · `api/routes/models.py:134-161`
The generic `except` stringifies as `'Type: msg'` with `error_code=model.pull_failed`, collapsing DNS/TLS/disk/decode into one code. Separately, `_ALIAS_NAMES` includes generic words (`coder`, `coding`, `medium`, `tiny`), so an upstream model literally named `coder` is filtered from the Models view and silently disappears.
**Fix:** map `httpx`/`OSError` subclasses to distinct `error_code`s; tighten the alias blocklist so it can't swallow real upstream ids.

---

## Profiles / stacks — profile/stack/bundle layering

**How the concepts relate.** *Model* = a weights artifact. *Profile* = a reusable runtime template (container image + bench-tuned flags + MTP). *Slot* = a concrete named runtime instance (a port) referencing one profile + one model + a `device`. *Stack* = a portable snapshot of many slot→(model, profile, device) assignments applied at once. *Bundle* = an install-time download tier (RAM floor + slot→model assignments). The central tension: three tables independently map a `device → profile`, and two concepts (bundle, stack) both express "slot → model" — the root of the recent churn.

### PS-1 · critical · `DEVICE_DEFAULT_PROFILES['cpu'] = 'tts'` → GPU-less installs get a chat slot on the TTS engine
`config/schema.py:838-843` · `hardware/recommend.py:203`
#834 fixed `derive_profile` (`cpu→cpu-llm`) but the OTHER device→profile table still maps `cpu→tts`, and the hardware recommender uses THAT one. On a GPU-less host the seeded chat slot is built with `profile='tts'` (Kokoro, `supported_slot_types=('tts',)`) — the primary chat slot literally cannot serve chat. Born-broken on fresh install, and the coherence guard doesn't catch it (see PS-3).
**Fix:** set `DEVICE_DEFAULT_PROFILES['cpu']='cpu-llm'`. Better: collapse `derive_profile` and `DEVICE_DEFAULT_PROFILES` into one function so they can't drift again (see PS-4).

### PS-2 · high · Seed-profile *definition* changes never reach existing installs
`config/loader.py:426-457` · `installer/etc-hal0/profiles.toml`
#838's additive merge only injects seed keys that are ABSENT. But the installer ships a full `profiles.toml` with every seed materialized, so `'key not in cfg.profile'` is false on every real install; and the first custom-profile create/update rewrites the entire resolved catalog back to disk, freezing seed definitions forever. Re-tune `rocm-moe` flags or bump a toolbox image in a release → every existing box keeps the stale flags. Directly contradicts #838's stated goal.
**Fix:** treat seeds as virtual — overlay at load, never persist them (exclude in `save_profiles_config`) so a seed always reflects code. Also fixes PS-7.

### PS-3 · medium · The device/profile coherence guard is blind to `backend=None` profiles
`slots/manager.py:3104-3114`
`_reconcile_device_profile` early-returns whenever the profile's `backend` is falsy — so only rocm/vulkan GPU profiles are ever checked. Incoherent pairs slip through: `device=cpu + profile=tts` (PS-1's broken slot is NOT rejected), `device=gpu-rocm + profile=cpu-llm`, `device=npu + a GPU profile`. The guard's docstring claims it makes device and profile "agree" — it only does for the rocm/vulkan subset.
**Fix:** compare `device_class ↔ device` for non-GPU profiles too, and reject a chat/LLM slot bound to a profile whose `runtime_family` isn't llama-server/flm.

### PS-4 · medium · Three parallel device→profile derivations (root cause of the #834 churn)
`install/profile_derive.py:91-106` · `config/schema.py:838` · `slots/manager.py:3074-3087`
`derive_profile` (install), `DEVICE_DEFAULT_PROFILES` (recommender + create-modal), and `_base_profile_for_backend` (slot reconcile) each answer "which profile for this device?" with different rules. They disagree on CPU (PS-1) and on MTP. A user picking device via the create modal vs first-run vs a device-flip gets three different profiles for the "same" choice.
**Fix:** one `profile_for(capability, device)` consumed by all three; `DEVICE_DEFAULT_PROFILES` becomes a fallback inside it.

### PS-5 · medium · Stack apply reports "Applied · clean" while runtime silently diverges
`stacks/apply.py:131-157,273-303`
`plan()` never validates that `entry.profile` exists or `entry.model` is resolvable, so a stack referencing a missing profile applies "cleanly" then the slot fails at start with `profile '' not found`. And `converge()` records per-slot load failures in `errors` but never raises — `record_active()` already fingerprinted the config, so `drift_status` reports clean even when half the slots failed to load.
**Fix:** validate referenced profiles/models in `plan()` so the dry-run shows them; return a degraded status when `converge.errors` is non-empty.

### PS-6 · medium · `ProfileConfig` has no cross-field validation between `device_class` and `backend`
`config/schema.py:846-922` · `ui/src/dash/profiles.jsx:31-53`
You can create `device_class=cpu + backend=rocm`, or `device_class=gpu + backend=None`. `runtime_family` classifies off `device_class`+image while the coherence guard reasons off `backend`, so an inconsistent profile behaves differently in different subsystems. The UI's single Backend control papers over it, but an API-created or imported profile can violate it.
**Fix:** `model_validator` on `ProfileConfig`: `backend` set iff `device_class=='gpu'`; non-GPU classes force `backend=None`.

### PS-7 · low · First custom-profile write silently rewrites `profiles.toml`
`config/loader.py:432-457`
`save_profiles_config` emits pure TOML (no comment support). The first "New profile" click replaces the well-documented installer-shipped `profiles.toml` with a comment-free file containing every seed inline. Operators who hand-tune flags via comments lose them; this is also the mechanism that freezes seeds on disk (feeds PS-2).
**Fix:** covered by PS-2 (don't persist seeds); optionally add a generated-file header.

### PS-8 · low · Pre-existing incoherent device/profile pairings never self-heal and never warn
`slots/manager.py:3151-3154`
When an unrelated update touches a slot that already has an incoherent device/profile on disk (written before the guard, or hand-edited), the "neither changed" branch leaves both untouched — reasonable to avoid surprise mutation, but the broken pairing keeps launching wrong until someone re-edits, with no surfaced signal.
**Fix:** log a coherence warning (not a mutation) on the neither-changed branch so the drift is visible.

### PS-9 · low · Bundle and Stack both express "slot → model" with different slot vocabularies
`bundles/schema.py` · `stacks/__init__.py`
Bundles use `chat.primary`/`chat.coder`/`embed` (mapped via a hardcoded installer map) while stacks use the ADR-0023 canonical `agent`/`utility`. A user reading a bundle manifest and a stack sees two incompatible "slot" vocabularies for the same slots; bundles derive profiles while stacks embed them, so "install the Pro bundle" and "apply the Forge stack" are surprisingly different operations that both look like "load a set of models."
**Fix:** unify the slot vocabulary, or have bundles emit a Stack as their install artifact so the two share one apply path; document bundle=install-tier vs stack=runtime-loadout in the UI.

---

## Dispatch / runtime — routing, arbiter, lifecycle

### DR-1 · critical · Idle-evicted non-chat slots (embed/rerank/tts) never wake on request
`slots/manager.py:2454-2496` · `api/routes/v1.py:403-459` · `dispatcher/router.py:911-942`
The idle sweeper unloads any non-pinned slot past its TTL, asserting the dispatcher "reloads transparently on next request." That wake path doesn't exist for embed/rerank/tts: `unload` deregisters the upstream, the only lazy-load in v1 (`_ensure_backend_for_model`) is explicitly chat-only, and the container gate (`_check_container_slot_ready`) only probes and raises — it never kicks a load. So an idle embed slot → evicted → next `/v1/embeddings` gets `NoRouteFound` 404, and never reloads.
**Fix:** add a generic wake-on-request for capability slots — kick `SlotManager.load(slot)` inside `_check_container_slot_ready` before raising, or extend `_ensure_backend_for_model` to all resolvable slot types keyed off the resolved slot.

### DR-2 · high · GpuArbiter drain is racy — a slot can be unloaded under an in-flight request
`slots/arbiter.py:450-467` · `dispatcher/router.py:801-831` · `slots/manager.py:2267-2283`
`ensure_img()` drains by polling `in_flight_count` (the serving counter) and unloads once it reads 0. But the counter only increments in `_serving_enter`, which runs AFTER the request passed `_guard_gpu_image_mode` and traversed awaits. A chat request that passed the guard while `mode=llm` but hasn't reached `_serving_enter` is invisible to the drain → the arbiter unloads the llm slot and the request forwards to a dead port (502 / `gpu.image_mode`).
**Fix:** increment the in-flight counter (or take a per-slot dispatch ticket) at the point the guard is checked, before any await, so the drain sees committed requests.

### DR-3 · high · `_await_ready` reports READY on health-probe timeout
`slots/manager.py:2742-2769`
On `wait_ready` timeout, `_await_ready` returns READY anyway ("let the fail watcher detect it"). `load()` then transitions to READY; the fail watcher needs 2 strikes × 2s to correct it. For any `kind='slot'` upstream the dispatch gate is FSM-state-only and forwards to the wedged server during that window. (Container slots are shielded by a live probe, so blast radius is non-container slot upstreams — but the FSM is actively lying.)
**Fix:** return WARMING (or IDLE/ERROR) on health timeout so the slot isn't advertised as dispatchable until a probe actually passes.

### DR-4 · medium · The same backend failure surfaces as two contradictory error envelopes
`dispatcher/router.py:944-978` · `dispatcher/router.py:911-942` · `slots/manager.py:806-823`
The fail-watcher force-sets a slot to ERROR on health failure and comments it's recoverable. But the `kind=slot` gate maps ERROR → `SlotLoadFailed` (502, non-retryable, "manual recovery required") while the container gate ignores FSM state, re-probes, and raises the retryable `SlotLoading` 503 — and per DR-1 an ERROR container slot is never actually reloaded, contradicting the "recoverable" comment.
**Fix:** reconcile the ERROR semantics across both gates and ensure something re-drives the load.

### DR-5 · medium · NPU single-context exclusivity is enforced at config-write time only, not at load time
`slots/manager.py:1983-2047` · `slots/manager.py:950-1093`
`_check_npu_exclusivity` runs on create/update_config but `load()` has no exclusivity guard. The AMDXDNA admits one NPU LLM context; nothing serializes concurrent NPU loads or blocks loading a second anchor that reached an enabled state via a path other than the validated write (direct load, adoption/reconcile force-transitions, or an out-of-band unit start adopted at `status()`). Adoption uses `force=True` and never checks exclusivity.
**Fix:** add a runtime single-context check in the NPU branch of `load()`/adoption (refuse or unload the incumbent) rather than trusting config-time validation.

### DR-6 · medium · Chat-slot load failures are swallowed
`api/routes/v1.py:450-459`
`_ensure_backend_for_model` catches all load exceptions and only logs them. The slot is left ERROR but the request continues to dispatch, where the model now has no live upstream → the client gets `NoRouteFound` 404 or a mismatched fallback, not the actual load error (bad model path, OOM, spawn failure).
**Fix:** when the resolved backing slot ends in ERROR, raise the typed `SlotLoadFailed`/`SlotSpawnFailed` envelope with the load message instead of swallowing.

### DR-7 · medium · Two overlapping, inconsistent lazy-load strategies (root cause of DR-1)
`api/routes/v1.py:403` · `dispatcher/router.py:853`
`v1._ensure_backend_for_model` blocks to READY (chat-only) while `Dispatcher._ensure_slot_loaded_backend_aware` kicks + returns a 503-to-retry (kind=slot only). They cover disjoint slot sets, neither covers container non-chat slots, and they give divergent UX (synchronous wait vs retry-503) for "slot not loaded."
**Fix:** consolidate into one `SlotManager`-owned wake-on-request keyed off the resolved slot; both layers call it.

### DR-8 · medium · Ready-set semantics duplicated in three (four) places
`slots/manager.py:513` · `slots/arbiter.py:89` · `slot_view/__init__.py:159-180`
`_DISPATCHABLE_STATES`, `arbiter._DISPATCHABLE`, and `slot_view._READY_STATES` each independently define `{ready,serving,idle}`; #696 centralized this in `is_ready_for_dispatch` but the arbiter and slot_view re-hardcode it (slot_view as bare strings), and slot_view adds a fourth notion ("serving with empty model_cache → idle").
**Fix:** centralize on the enum-based `is_ready_for_dispatch` helper everywhere.

### DR-9 · low · Arbiter `guard_dispatch` does blocking file I/O on the event loop in the hot path
`slots/arbiter.py:648-677,261-350`
`guard_dispatch` runs on every slot dispatch. On a cold cache it calls `_load_state()` (sync json read) and `_read_slot_toml` (sync open + tomllib.load) directly on the loop. Steady-state is memory-cached, but the first llm dispatch after process start (or any uncached slot) blocks the loop on disk.
**Fix:** async/executor read, or warm the caches at startup.

### DR-10 · low · In-flight / idle bookkeeping is keyed on unresolved slot names
`slots/manager.py:2267-2307` · `slots/manager.py:517-532`
`serving()`, `bump_last_used`, `in_flight_count`, `_serving_count`, `_last_used` key on the raw `slot_name` without `_resolve_alias`, whereas `state()`/`load()`/`unload()` resolve aliases. If a slot is addressed by both alias and canonical name, the counters split across two keys — corrupting SERVING transitions, idle tracking, and the arbiter drain. Latent today but a real trap.
**Fix:** resolve aliases uniformly in the serving/idle helpers.

### DR-11 · low · A hung streaming client pins a slot SERVING and stalls every image-mode switch for 2 minutes
`dispatcher/router.py:1042-1093` · `slots/arbiter.py:452-464`
SERVING is released only when the stream iterator drains; the read timeout doesn't apply to open streams. A client that opens an SSE stream and stops reading holds `in_flight_count>0` until GC, forcing `ensure_img` to burn the full 120s drain timeout before force-unloading.
**Fix:** add an idle-stream watchdog.

### DR-12 · low · `router.py` (1592 lines) mixes five concerns; a stale docstring implies a private-state reach-in that no longer exists
`dispatcher/router.py:94` · `dispatcher/router.py:187-231,1441-1577`
Typed errors, `UpstreamCall`, the 4-step resolution algorithm, transport, GPU/dead-port guards, and the `resolve_by_capability` heuristics all live in one module. The capability heuristics are self-contained and the natural extraction. Separately, the `router.py:94` docstring still says the gate calls the private `_current_state('hal0')` though the code already uses the public `state()`/`is_ready_for_dispatch()`.
**Fix:** extract the capability heuristics into their own module; update the stale docstring.

---

## UI drawers — slot/model/profile editors

### UI-1 · high · No unsaved-changes guard on any editing surface
`ui/src/dash/primitives.jsx:51-58` · `ui/src/dash/slot-modals.jsx:323-497`
The Edit-slot drawer collects `ctx_size`, `profile`, `chat_template`, `extra_args` behind a Save button, but a single backdrop click or Esc silently drops all of it — no dirty check, no "discard changes?" prompt. Same for the profile and stack editors. The drawer is wide and easy to mis-click outside of.
**Fix:** compute a dirty flag (baselines already tracked for `extra_args`) and intercept onClose/Esc/backdrop with a confirm when dirty; disable backdrop-dismiss while dirty.

### UI-2 · high · The Edit-slot drawer mixes instant-apply and batched-save controls with no visual distinction
`ui/src/dash/slot-modals.jsx:546-946`
Enable toggle, Reasoning, MTP, and the model-swap `<select>` all fire instantly (model swap cold-restarts the container the instant you release the mouse) — yet `ctx_size`, `profile`, `chat_template`, `extra_args` wait for Save. Users can't predict which controls are live; the "edit form then Save" mental model is broken.
**Fix:** pick one model per drawer: fold model/thinking/MTP into Save, or visually mark instant-apply controls (an "applies now" badge) distinct from the batched fields.

### UI-3 · high · Three subtly different "compatible models" filters — one ships incompatible ids
`ui/src/dash/slot-modals.jsx:132,727-734,1171-1178`
The create modal filters type-only (no backend/rocmfp4 check), the edit drawer filters type + rocmfp4 vs the selected profile's backend, the popover filters type + rocmfp4 vs `slot.backend`. So the create modal offers a rocmfp4-tagged model for a Vulkan profile; the orchestrator then rejects it at load. One conceptual filter, three implementations, one wrong.
**Fix:** extract a single `compatibleModels(models, {type, backend})` helper called from all three.

### UI-4 · high · The Edit-slot drawer exposes all four ownership layers (base / profile / model / slot) at once
`ui/src/dash/slot-modals.jsx:616-1142`
Profile (GPU-only editable), image + status (read-only), `n_gpu_layers`/`rope_freq_base` read-only "defined by profile", per-slot `extra_args`, model-level `chat_template` with an Override path, and a provenance legend. The operator must internalize which layer owns what; the provenance badges exist specifically to explain this leakage — a symptom, not a fix.
**Fix:** lead the drawer with the 2–3 fields a user actually changes (model, ctx, reasoning); collapse profile-owned fields behind the Advanced disclosure.

### UI-5 · medium · Model swap in the drawer = unconfirmed immediate cold restart
`ui/src/dash/slot-modals.jsx:754-772`
`swapMut` fires directly from the `<select>` onChange with only a toast — no confirm — restarting a live container (seconds-to-minutes model reload) from a casual dropdown change, with no undo. The InlineSwapPopover at least shows a "cold restart" chip and a fit test.
**Fix:** confirm before swapping a running slot, or route the dropdown through the same confirm affordance as the popover.

### UI-6 · medium · "✓ fits in available memory" in the create modal is a fake check
`ui/src/dash/slot-modals.jsx:267-269` · `ui/src/dash/slot-modals.jsx:1215`
The create modal renders "✓ fits in available memory (X GB free)" whenever a model is merely selected — it never compares model size to free RAM. The InlineSwapPopover does a real fit test, so the two surfaces disagree and a model that won't fit still shows the green ✓ — false reassurance.
**Fix:** run the popover's `parseSizeGB` comparison, or drop the claim.

### UI-7 · medium · Slot-name validation is stricter than the backend and inconsistent across the three drawers
`ui/src/dash/slot-modals.jsx:120` · `ui/src/dash/stacks.jsx:42` · `ui/src/dash/profiles.jsx:39`
The create-slot modal uses `/^[a-z][a-z0-9-]{0,30}$/` (no leading digit, no underscore) while stacks/profiles use the real API regex `^[a-z0-9][a-z0-9_-]{0,31}$`. Valid names like `2b-coder` or `my_slot` are rejected only in the create-slot modal, and the error copy doesn't describe the leading-letter/length rules. Three drawers, two rule sets, three error strings.
**Fix:** hoist one shared `NAME_RE` + one error-message helper used everywhere.

### UI-8 · medium · The first-run "configure your slots" empty state seeds the wrong slot identities
`ui/src/dash/slots.jsx:597-604` · `ui/src/dash/primitives.jsx:361`
The skip-path SEEDED list still contains the retired `primary` name and only six slots, and the banner hard-codes "Six seeded slots" — while the canonical seeded set is `(utility, embed, rerank, stt, tts, img, vision, agent)`. The onboarding surface contradicts the platform's own catalog.
**Fix:** drive SEEDED from the same source of truth (or the real `/api/slots` payload) rather than a hand-maintained literal.

### UI-9 · medium · The download "Pause" button silently cancels (destroys) the pull
`ui/src/dash/model-modals.jsx:582,629`
`const onPause = doCancel; // engine has no pause` — but the button is still labeled "Pause". A user clicking Pause on a multi-GB download expecting to resume instead loses all progress.
**Fix:** remove the Pause button (keep only Cancel), or label it honestly. (Pairs with MR-7 resume support.)

### UI-10 · medium · "Used by" model→slot matching has a dead branch → the delete-cascade warning under-reports
`ui/src/dash/model-modals.jsx:450,499`
`s.model_id || s.model?.default` — `Slot.model` is a string, so `.default` on it is always undefined (dead branch). A slot referencing a model only via `model` (not `model_id`) won't be counted as using it, so the delete warning under-reports.
**Fix:** match on `s.model_id === model.id || s.model === model.id`.

### UI-11 · medium · Two `normalizeApiModel` implementations applied on top of each other
`ui/src/dash/slot-modals.jsx:47-89` · `ui/src/api/hooks/useModels.ts:17`
slot-modals defines a local `normalizeApiModel`; `useModels` already imports and applies `@/lib/normalizeApiModel`. The drawers then re-map already-normalized rows with a different normalizer — double work and a real divergence risk (tag/type/device computed twice).
**Fix:** delete the local copy; consume `useModels` output directly.

### UI-12 · medium · Massive drawer/form duplication between `stacks.jsx` and `profiles.jsx`, shadowing the shared primitive
`ui/src/dash/stacks.jsx:410-572` · `ui/src/dash/profiles.jsx:173-218`
Both define their own local `Drawer` (shadowing `primitives.jsx`'s `Drawer`), `FormRow`, `DeleteConfirm`, `ImportModal`, `toast`, and `NAME_RE`. The create/edit form-drawer shell is reimplemented per view; fixes land in one and not the other, and the naming collision with the real primitive is a footgun.
**Fix:** promote one `FormDrawer` + `FormRow` + `DeleteConfirm` + `ImportDialog` into `primitives.jsx`.

### UI-13 · medium · Component wiring via window globals instead of imports — documented load-order fragility
`ui/src/dash/slot-list.jsx:16` · `ui/src/dash/slots.jsx:891-893`
Every dash module ends with `Object.assign(window, {...})` and references siblings via globals (`Modal`, `Drawer`, `Icons`, `ConfirmDialog`, `window.ProfilesView`…). slot-list.jsx even documents "Import order: must come after cards-shell.jsx". No tree-shaking, no cross-boundary type-checking, genuine load-order fragility the comments admit.
**Fix:** convert to real ES imports/exports; the Vite alias is already in place for the `.ts` hooks.

### UI-14 · medium · Modal/Drawer claim focus management they don't implement; field labels aren't real `<label>`s
`ui/src/dash/primitives.jsx:12,49-80` · `ui/src/dash/slot-modals.jsx:196-799`
The primitive comment says "Focus restored on close" but nothing restores focus, traps focus, or autofocuses the first field — keyboard/screen-reader users can tab into the page behind the open drawer, and focus is lost to `<body>` on close. Throughout, `.form-lbl` labels are `<span>`s with no `htmlFor`/`id`, so clicking a label doesn't focus its control and screen readers don't announce the field. A broad, systemic a11y gap.
**Fix:** add a focus trap + initial/return focus; use `<label htmlFor>` + `id` (or `aria-labelledby`) consistently.

### UI-15 · medium · The Stack editor lets you build a device/profile pair the create flow makes impossible
`ui/src/dash/stacks.jsx:541-547` · `ui/src/dash/slot-modals.jsx:150-158`
The create modal derives device from the chosen profile's backend (one selector). The Stack editor exposes both an independent device select AND a profile select per slot, so a stack slot can declare `device=gpu-vulkan` with a `rocm` profile — the same slot concept presented two different ways, one of which builds contradictory pairs (see PS-3/PS-6).
**Fix:** derive device from profile in the stack editor too, or drop the device column there.

### UI-16 · low · Destructive slot delete uses a raw `window.confirm`, unlike every other delete in the app
`ui/src/dash/slot-modals.jsx:524` · `ui/src/dash/model-modals.jsx:472-490`
Slot delete uses `if(!window.confirm(...))`; model delete uses the styled `ConfirmDialog` with type-to-confirm; profiles/stacks use styled `DeleteConfirm`. A jarring unstyled native dialog for a cascading destructive action, with no type-to-confirm.
**Fix:** reuse `ConfirmDialog` (it already supports `typeToConfirm` + `destructive`).

### UI-17 · low · `ctx_size` inline messaging contradicts what Save actually does
`ui/src/dash/slot-modals.jsx:789,491-494`
The field label says "⟳ restarts the container (~model-load seconds)", but a ctx-only Save does not restart — it toasts "restart required to apply changes" and leaves the user to find Restart on the card. Mixed signals about whether the change is live.
**Fix:** align the copy — auto-restart on ctx change (like profile/template) or label it "requires a manual restart."

### UI-18 · low · `EditSlotDrawer` is an ~825-line component built from five inline IIFEs
`ui/src/dash/slot-modals.jsx:323-1148`
Five `{(()=>{…})()}` blocks (profile, model, chat template, MTP, provenance) hide real logic (backend derivation, compatibility filtering) inside JSX — untestable in isolation and hard to read.
**Fix:** extract `ProfileField`, `ModelField`, `ChatTemplateField`, `MtpToggle`, `ResolvedCommand` subcomponents.

### UI-19 · low · Three unrelated form-state patterns for four near-identical editors
`ui/src/dash/slot-modals.jsx:93-398` · `ui/src/dash/profiles.jsx:238-259` · `ui/src/dash/stacks.jsx:426-446`
Slot edit uses ~12 `useState` + manual reset effects; profiles use a form object + touched/submitted; stacks use form + slug + submitting. Validation is re-hand-rolled in each. No shared validation/dirty/touched machinery — every new field re-invents plumbing.
**Fix:** a small `useForm({initial, validate})` hook shared across all four (this also unlocks UI-1's dirty guard).

### UI-20 · low · MTP toggle lags while Reasoning is optimistic — same section, different behavior
`ui/src/dash/slot-modals.jsx:886,922`
Reasoning sets local state immediately; MTP reads `slot.mtp` off the up-to-5s-stale prop with no optimistic state, so after toggling MTP the pill appears not to move until the next poll.
**Fix:** give MTP the same optimistic-local + revert-on-error pattern Reasoning uses.

### UI-21 · low · Changing Type after selecting a Model leaves a stale, now-incompatible model id in create state
`ui/src/dash/slot-modals.jsx:92-169`
No effect resets `model` on type change: the filtered `<select>` shows blank but the create body still sends the stale id.
**Fix:** reset model when type changes.

---

## Suggested sequencing

Ordered by correctness-per-effort.

1. **Ship the four born-broken / silent-failure fixes** *(~1 day each)* — SC-1 (write `enabled` on disable), SC-2 (podman probe for NPU), PS-1 (`cpu→cpu-llm` default), DR-1 (generic wake-on-request). All small, localized; each unbreaks a user-visible flow that currently fails silently.
2. **Close the install-path reliability gaps** *(correctness)* — MR-1 (installer pulls persist), MR-2 (don't report completed pulls as failed), DR-2 (arbiter drain race), DR-3 (don't publish READY on health timeout).
3. **Add write-time validation (Theme T2)** *(ease of use)* — default-uniqueness (SC-4), existence guard on create (SC-5), disk-space preflight (MR-4), stack profile/model existence in `plan()` (PS-5).
4. **Unify the parallel implementations (Theme T3)** *(cleanup)* — one `profile_for()` (PS-4), one `capability_from_filename()` (MR-3), `is_ready_for_dispatch` everywhere (DR-8), one shared reconcile projection (SC-11).
5. **Rework the editing drawers** *(UI)* — a shared `FormDrawer` + `useForm` hook with a dirty guard (UI-1, UI-12, UI-19), one `compatibleModels()` filter (UI-3), instant-vs-batched clarity (UI-2), lead with the fields users actually change (UI-4), and the a11y pass (UI-14).
6. **Cross-process safety & housekeeping** *(hardening)* — advisory `flock` on registry + capabilities writes (MR-5, SC-10), pull-job + `.part` GC (MR-8, MR-9), resume support (MR-7), parent-dir fsync (MR-13), router.py extraction (DR-12).

---

*Generated from a five-agent parallel review pass. Headline findings (SC-1, SC-2, PS-1, DR-1) were spot-verified against source before inclusion; the remainder are grounded in the cited lines but not each individually reproduced at runtime.*
