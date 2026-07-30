# Changelog

All notable changes to hal0 are recorded here. The format loosely
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
the project adheres to semver pre-1.0 caveats: minor releases (v0.1 →
v0.2) may carry breaking changes; patch releases inside a minor line
(v0.2.0 → v0.2.1) won't.

Tags older than v0.2.0 ship release notes inside the GitHub release
page; this CHANGELOG starts at v0.2.0 (the Lemonade migration cut).
ADR-level architecture decisions are kept internal (the `docs/internal/`
tree is gitignored, #638) and referenced by number throughout the code.

**Release automation.** On a tagged release the matching `## [<version>]`
section below is bundled into the release tarball as `RELEASE_NOTES.md`, and its
optional `### Highlights`, `### Breaking`, and `### Migrations` subsections are
extracted into `release.json`. `hal0 update` shows both — rendering
breaking/migrations as callouts — from the cosign-verified tarball, before
applying. Add those subsections to a version's section to surface them; see
`scripts/gen_release_notes.py`.

## [Unreleased]

### Breaking

- **`SlotConfig.enabled` is gone — a bound model is the activation signal** (#1369, follows #1367). The field claimed to control "whether this slot is started on hal0 startup" and never did: boot autostart is the Quadlet `[Install] WantedBy=hal0.target` stanza, which only exists because `SlotManager.load()` refuses to write a unit for a model-less slot — so `[model].default` was always the real gate. Every one of the ~8 routability checks that consulted `enabled` (`slots/routing.py`, the four `/v1` listing helpers in `api/__init__.py`, `slot_view`, `api/routes/v1.py`, `dispatcher/_npu_common.py`, `npu_swap_status.py`, `omni_router/{filter,route_to_chat}.py`) was immediately followed by an `if not model_id` gate; the shipped seeds had already hand-maintained the two signals 1:1 (all ten ship `enabled = false` + no model pin, except `brain`, which ships both). Two truths for one question is what forced the UI to render a "disabled but running" escape hatch and let `PUT /config` carry a hidden `unload()`. The single predicate now lives in `hal0.slots.activation` (`is_activated` / `claims_npu_anchor` / `npu_modality_active`). `enabled` is dropped from the `GET /api/slots` payload and from the `Slot` TS type; `PUT /api/slots/{name}/config {"enabled": …}` is now a **400** `slot.removed_key_denied` naming the replacement, rather than silently persisting inert debris (the same treatment `backend` got). Untouched: the `enabled` fields on `Upstream`, MCP clients, providers, metrics, memory, auth, and the `[npu]` modality toggles — different models entirely. `CapabilitySelection.enabled` also survives in `capabilities.toml`; only its *projection* onto the slot changed (see Migrations).
- **`PUT /api/slots/{name}/config` no longer has a lifecycle side effect.** It used to unload a running slot on an `enabled: false` body so the faded card matched reality. Config edits and lifecycle are now separate verbs: stopping a slot is `POST /api/slots/{name}/unload`, which #1367 pin-gates (409 `slot.pinned`, `?force=true` bypass).
- **The NPU-exclusivity 409 moved from the toggle to the model write.** The AMDXDNA chat context still admits exactly one `device=npu, type=llm` slot, but the discriminator is now "has a model configured": `PUT /config {"model":{"default":X}}` on a second NPU LLM anchor returns 409 `slot.npu_exclusivity_violation` ("only one NPU LLM slot may have a model configured at a time"; hint: clear the incumbent's model first). Model-less NPU LLM slots — including the shipped `flm` seed — coexist freely.
- **NPU trio dispatch now reads the anchor's `[npu]` table, not the shadow's own flag.** `flm-stt` / `flm-embed` are display+dispatch records for the anchor's single `flm serve` process and always carry a placeholder `[model].default`, so they never had activation state of their own to express. `_is_npu_trio_request` gates on `npu.asr` / `npu.embed` on the `device=npu, type=llm` anchor — which is literally the flag set FLM was launched with, so a modality that was never launched can no longer advertise itself as routable and then 503 on the liveness probe. The slot payload gains `npu_modality_active` (resolved server-side) for the shadow card's ON/OFF pill.

### Migrations

- **One-shot `enabled` sweep, boot-integrated** (`hal0.config.migrations.slot_enabled_removal`). Runs **first** in the `slot_reconcile` boot phase, before every pass that reads `model.default` to decide what is configured. Rules: `enabled = false` **with** a `[model].default` → the model is cleared (under the new rules a bound model reads as "on", so an in-place upgrade would otherwise silently activate a slot the operator had switched off — this is the only shape that needed more than a key drop); every other shape → the key is dropped and nothing else changes. NPU trio shadows (`device=npu` + `type` transcription/embedding) keep their placeholder model — it is structural, not an operator pick — and only lose the key. Sibling `[model]` keys (`context_size`, `labels`, …) survive a clear; a slot that doesn't carry the key is left byte-identical, so a second run is a genuine no-op. Idempotent (`"enabled" in raw` **is** the check), best-effort per file (one corrupt TOML is logged and skipped, never fatal at boot). `hal0 slot migrate-enabled-removal` runs the same sweep on demand, dry-run by default; unlike the other `slot migrate-*` commands it is safe to run live and needs no deploy window.
- **Disabling a capability now clears the slot's model** instead of writing `enabled = false` (`SlotConfigStore._reconciled_slot`, SC-1). The operator's pick is not lost — it stays in `capabilities.toml` as `CapabilitySelection.model`, so a re-enable rebinds it. A pure disable still leaves device/provider/profile untouched.
- **Guided install gates on the model id, not a flag.** `_build_slot_cfg` creates slots with an empty `model.default` and `run_pull_and_activate` stamps the id only after the bytes land (`_activate_slot_model`); a failed pull leaves the slot model-less and marks `[meta].pull_failed`. Withholding the id **is** the start gate now — pre-stamping it was the one way a slot could have started before its model existed.
- **Rollback is one-way.** Pre-#1369 code reads a missing `enabled` as `True`, so slots this migration cleared come back model-less rather than re-enabled. Restore from a config backup if you need the prior state.

### Fixed

- **`PUT /api/models/{id}` merges the nested `defaults` one level deep instead of replacing it wholesale** (#1413, server-side sibling of #1378/#1381). The route documents itself as "body accepts any subset of … `defaults`", but `registry/store.py::merge_update` was flat — `{**existing, **updates}` — so a body that so much as *named* `defaults` replaced the entire `ModelDefaults` table. Every sibling the client didn't resend (`context_size`, `rope_freq_base`, `chat_template`, `profile`, `mtp`, `jinja`, `enable_thinking`, `vision`) reset to `null` behind an HTTP 200, with `changed_fields: ["defaults"]` as the only trace and a response body that looked correct because it *was* the new (lossy) state. Following the documented contract destroyed config; the dashboard escaped it only by accident, because its drawer resends the whole object. `defaults` and `capability_flags` — the two schema'd subtables `_model_to_toml` already special-cases — now merge per sub-key with the tri-state semantics those fields already carry: absent keeps the stored value, an explicit `null` clears that one value, and `{"defaults": null}` still drops the whole table. `metadata` is deliberately excluded (freeform bag, no schema, so "absent = keep" would leave no way to drop a key) and every other field stays a flat replace. The three UI writers that cleared a field by `delete`-ing its key (model drawer, recipe editor, MODELS ▸ Defaults) now send an explicit `null`, since under the new merge an omitted key means "keep", not "clear".
- **A malformed `defaults.context_size` returns 400, and 0 / negative / absurd values are rejected** (#1414, server half of #1378). `{"context_size": "abc"}` escaped as an **HTTP 500** `model.registry_error` carrying a raw pydantic traceback in `details.reason` — `merge_update` wraps *any* `Model.model_validate` failure in `RegistryError`, so plain client garbage produced a 5xx. Meanwhile `0`, `-1` and `99999999` all persisted with a 200 and round-tripped through `GET` into the database, and `POST /api/models/validate` reported `{"ok": true}` for every one of them, because `screen_model_write` only ever inspected `preferred_runner` and `defaults.extra_args`. The client-side guard was therefore the *only* guard: the CLI, a script, or a stale dashboard build could still write a row guaranteed to fail at launch. `screen_model_write` now parses the incoming `defaults` through `ModelDefaults` (→ `400 model.defaults_invalid`) and range-checks `context_size` against `[128, 2**24]` (→ `400 model.context_size_out_of_range`); the floor mirrors the model and slot drawers' `≥ 128`, the ceiling is an absurdity screen ~16× the largest advertised window on any shipping model. Screened at the write boundary rather than as a `Field(ge=…, le=…)` constraint on purpose — a model-level constraint also applies on the READ path, so any already-persisted out-of-range row would stop loading, which is the exact "policy applied to data that predates it" trap #1411 documents on the profile side. Create, update and `/validate` share the one screen so the three cannot drift, the way `extra_args` already couldn't; MODELS ▸ Defaults gains the matching inline `≥ 128` gate.

- **The single-file pull path now writes `store_blob` / `model_file` rows, so refcounting and store GC are no longer inert** (#1412). `run_pull` has two branches, and only the fileset one (`_run_pull_fileset`) did the blob accounting — `_register_blob_after_install` per file plus one `model_file` row each in `_register_pulled_fileset`. The single-file / mmproj-pair branch — the one the Add-by-HF-coords modal and `POST /api/models/{id}/pull` with `hf_repo`+`hf_filename` actually take — went straight to `_register_pulled`, which only upserts the `model` row. On a box whose models all came through that path both tables were **empty box-wide** (63 registered models, zero rows on lxc105), which silently disabled every consumer of them: `store_blob.refcount` never existed, so the store GC had no record of any pulled bytes; `_maybe_hardlink_from_blob` could never hit, so an identical re-pull always re-streamed the whole file; and `duplicate_model`'s documented safety — "bumps each shared blob's refcount, so a later delete of either row never orphans bytes the other still uses" — was a no-op returning `files_refcounted: 0` with no signal. Worse, `gc.reconcile_store_tree` classifies anything under the store root that no `store_blob`/`model_file` row tracks as reap-eligible *bare bytes*, so a live pulled model looked like debris to a real (`dry_run=False`) GC pass. Pull completion now registers the blob and the file row for the main file and the WS-11 mmproj sidecar, keyed on the repo-relative filename, through one new `_register_installed_files` helper. It holds the invariant *refcount == number of `model_file` rows referencing the blob*, which makes the write idempotent — a re-pull of unchanged bytes leaves the count alone rather than ratcheting it upward forever (an inflated count never falls back to 0, so the bytes would become unreclaimable), and a re-pull whose digest changed drops the superseded blob's reference first. No-op on the TOML registry escape hatch, same as the fileset path. Not included: a backfill for models pulled before this fix — the GC stays blind to those until one lands.
- **`add-from-path` no longer rejects every `.gguf` symlink into the HuggingFace hub cache** (#1415). The `[models].file_extensions` allow-list was applied to `path.resolve()`, and the hub stores bytes as extensionless sha-named blobs with the real filename existing only as a symlink (`snapshots/<rev>/<name>.gguf -> ../../blobs/<sha256>`). So "Add by path" failed with `400 model.unsupported_format` — *"file extension `''` not in [models].file_extensions"* — for a path that visibly ends in `.gguf`, quoting an opaque sha blob in `details.path` the operator never typed. It fired on hal0's own conventions: the curated `/mnt/ai-models/local/*` symlink farm and the `/mnt/ai-models/huggingface/hub` tree the `hf` tooling writes into — i.e. the most obvious "register the model I already have" case. The check now runs on the literal path the operator supplied, falling back to the resolved suffix only when the literal has none (so pointing straight at a bare blob behaves exactly as before), and the rejection echoes the operator's path. `detect()` and the stored `Model.path` still use the resolved target; the derived id and display name now come from the literal stem too, so a hub symlink no longer registers under 64 hex characters. The scan walker shares the allow-list but already tests the un-resolved walk entry (`registry.discover.find_candidates`), so it needed no matching change.
- **The model drawer's Context size field validates instead of corrupting or deleting the stored value** (#1378). The field was a plain text input run through a bare `parseInt`, with no error slot and no save gate — and `parseInt` is lenient in both directions. Typing `32k` PUT `defaults.context_size: 32`, a 1000× context collapse; `8.9` landed as `8` and `16384abc` as `16384`. Typing `abc` was worse: `parseInt` returned `NaN`, the code `delete`d the key, and since `PUT /api/models/{id}` merges `defaults` **wholesale** (`registry/store.py` `merge_update`) an absent key is a deletion, not "unchanged" — the stored `context_size` was destroyed, `--ctx-size` vanished from the launch line, and every slot bound to the model silently fell back to the llama-server default. All four cases finished with a green "Updated" toast. Save now demands a clean integer (`/^\d+$/` on the trimmed value) at or above the `≥ 128` floor the slot drawer already enforced, surfacing the reason inline (`model-ctx-error`) and disabling Save so no PUT fires — derived from the field like the existing `flagsError` gate, so a correction releases it on the next keystroke. Empty stays an explicit clear of the override; only *malformed* text is an error. `model-ctx-validation-v3` pins the wire for `32k`, `abc`, `8.9`, sub-floor `64`, a valid `16384`, and the empty-clear path.
- **The slot drawer's "Clear override" never removed a persisted `chat_template`** (#1372). Clearing an override issued no request at all: the template stayed on disk and kept feeding `llama-server`, while the drawer rendered the model default as though the removal had taken. It was not even counted as an unsaved change, so the discard guard stayed silent too — and there was no other way to drop a per-slot override from the dashboard. Both the save body and the dirty aggregate gated on `overrideOpen`, which the Clear button itself sets to `false` before either predicate runs, so both evaluated false. Both predicates now share one normalized baseline-vs-desired comparison (landed with the #1401 drawer rework), so they cannot drift apart again — duplication is how they diverged. The removal rides as `null`, never `""`: `reconcile_slot_updates` implements None-means-delete, so `null` drops the key from the slot TOML while `""` would persist an empty-string override (a different, still-broken state). Verified end-to-end at the route level — no test covered None-deletion of *any* slot key before, so `tests/api/test_slot_config_validation.py` gains two: `null` removes the key and leaves siblings (`[model].default`, `port`) intact, and `""` is explicitly *not* a removal.

- **Slot drawer Save is no longer silently dead on an NPU slot with a malformed persisted `extra_args`** (#1389). The freeform extra_args field and its error surface live in the Model group, which is unmounted for `device === "npu"` — but the Save validator still computed `extraArgsErr` from the persisted `llamacpp_args`. A slot whose stored override carried an unbalanced quote blocked every Save on the slot with zero feedback: no request fired, no error rendered, the drawer just did nothing. The validator now only vetoes Save when the field is actually mounted; an operator who flips the device off `npu` still sees (and must fix) the error before the field's value can ride a write.

### Added

- **`hal0 slot migrate-flags` — the flags-fold migrator finally has an operator entry point** (#1396). `hal0.config.migrations.slot_flags_fold` has existed since the flags-ownership lane, but nothing ever exposed it: no CLI, no installer hook, no boot wiring — only tests referenced it. Its sibling folds both had commands (`slot migrate-hw`, `slot migrate-caps`). Meanwhile the launch-side readers were already deleted (`providers.container` drops `profile_flags`/`slot_parallel`/`extra_args`; `resolve_chat_template` no longer consults the slot), so an upgraded box with a bench-tuned slot silently launched **without** that tune and had no supported way to recover it — the exact ordering hazard spec-flags-ownership §5.4 flagged ("readers become expired shims with a sunset"). The new command mirrors `migrate-hw`: dry-run by default, `--apply` takes a timestamped backup and passes the `deploy_window=True` ack, refuses to run while any hal0 unit is live (`--stop-services` to stop them), and is never wired into an automatic path. Divergent-share conflicts (two slots folding different tunes onto one model) are surfaced as a clean non-zero exit listing every conflict — on the dry-run path too, which previously would have raised an unhandled `RuntimeError` at an operator merely *previewing* a conflicted box.

### Fixed

- **NPU modality toggles rewrote the configured chat tag with a stale `model_id`** (#1388). Every Chat/ASR/Embed toggle routes through `applyNpu`, which attaches `[model].default` alongside the `[npu]` table — and it sourced that tag from `slot.model_id`. `useSlots.ts` documents `model_id` as stale for exactly this slot class ("trio slots never load as their own process, so `model_id` never reconciles" off the pre-trio GGUF) and already exposes the configured value as `modelDefault`, lifted from `[model].default` by `config_enrichment` — the drawer never read it. So flipping ASR or Embed, controls with no business touching the chat model, rewrote the slot's configured FLM tag to an unrelated GGUF id and cold-restarted it: silent config corruption on NPU boxes, with the operator unable to see the value being sent because the chat `<select>` has no out-of-vocabulary option for it. The seed (and re-seed) now prefer `modelDefault`, keeping the live id only as a fallback for a slot with no configured default on disk.
- **The flags fold silently dropped every slot's `[server].extra_args`** (#1396). `collect_inputs` feeds the planner `SlotConfig.model_dump(by_alias=True)`, and SlotConfig's `_tuck_server_into_extra` model_serializer re-parks the server sub-table under `extra["server"]` so the loader round-trips a proper `[server]` TOML table. `_slot_flag_tokens` read only a **top-level** `server` key, so against real input the freeform tune — the single value the migrator exists to preserve — never entered the fold; only `parallel` and the typed ngl/ctx survived. The same gap defeated the divergent-share guard: two slots differing *only* in `extra_args` folded to an identical tune, so the planner saw no conflict and would have silently picked a winner instead of refusing. The token reader now accepts both shapes. Caught by the new CLI tests before the command shipped, so the unreachable migrator was never made reachable-and-wrong.

- **Parallel-worktree e2e runs silently tested the wrong branch** (#1399). `ui/playwright.config.ts` defaulted every run to port 5173 with `reuseExistingServer: !CI`, so a second git worktree running Playwright locally attached to the first worktree's Vite server and exercised *its* code. The results were not flaky but confidently wrong in both directions — a real verification reported `7 failed` that became `34 passed` on a unique port with identical commits, and a second run of the same tree disagreed with itself (`50 passed` vs `42 passed / 6 skipped`). CI was never affected (`CI=1` disables reuse, and CI has one checkout); every local parallel run was. The default port is now derived from the worktree's own path — stable per worktree, so `reuseExistingServer` keeps its iteration speedup, but distinct across worktrees so cross-contamination is structurally impossible. `HAL0_E2E_PORT` still wins explicitly (and now ignores an unparseable value instead of passing `NaN` to `vite --port`); CI keeps the fixed 5173.


- **`memory_degraded` now tracks the live engine, not just the boot probe** (#1301, runtime half). The boot `/health` probe added in 1.0.0-rc.1 makes the hindsight→pgvector ladder fire when the daemon is down *at boot*, but it answers one question once and the answer goes stale immediately: a daemon that dies afterwards leaves the `HindsightProvider` in place, and `/api/status.memory_degraded` plus `hal0 memory status` went back to reporting healthy while every recall came back empty and every retain raised. `HindsightProvider.degraded` is now a live property fed by a single `_call` wrapper every engine round-trip funnels through — it flips on an observed transport failure and, unlike a boot probe, clears itself when the daemon comes back. A 4xx does not degrade (the daemon answered; the delete sweep's routine per-bank 404s must not flap it), a 5xx does — the same rule `probe_health` uses, so boot and runtime cannot disagree. The `hal0 memory status` line no longer claims "in-memory fallback" for what may be a failing durable engine.

- **Config drift false-warns forever when the TOML id and the registry key are spelled differently** (fixes #1226). The drift comparator substitutes a model's on-disk path for a bare registry id before comparing, but matched the two ids with `==`. The slot TOML keeps the catalog spelling (`Qwopus3.5-4B-Coder-MTP-Q6_K`) while the registry key — and the running container's `--alias` — is the slug (`qwopus3-5-4b-coder-mtp-q6-k`), so the substitution never fired and every status read printed `config drift: --model: running=/mnt/ai-models/….gguf rendered=Qwopus3.5-4B-Coder-MTP-Q6_K`. The comparison now normalises both ids. Separately, `compute_config_drift` looked up the raw TOML id while `load()` launches the container from `_resolve_servable_model(...)` (a catalog id that landed locally under a different id), so for exactly the slots this matters for the registry lookup missed, the renderer emitted the bare id, and the warning was permanent; the drift check now resolves the model the same way the launch path does. A genuinely different running `--model` path is still flagged.
- **Bound the slot teardown on the seamed (hal0-service-user) route too** (#1224, worker side). `SlotManager.terminate`'s timeout releases the *caller*, but it cannot touch the executor thread sitting on `systemctl stop`; on a real install that thread stayed blocked and `unload_sync` never reached the Quadlet-source removal + `daemon-reload` that lets the next load converge. `SystemCtlSeam.systemctl` now forwards a `timeout` on both the direct and the seamed route (default `None` = unbounded, unchanged), and `ContainerProvider.unload_sync` bounds the stop at 20s — under the caller's budget so the worker unwinds first — logs `container.unit_stop_timeout`, and continues the teardown rather than aborting it.

### Added

- **Field-wiring and rejected-write contract specs for the slot + model drawers** (#1371). Every editable drawer field routes its value somewhere specific — the batched `PUT /config`, its own POST, or an instant-apply write — and several of those hops had no assertion that the value reached the wire with the right key, so a silent rewiring regression was invisible. Three new e2e specs close that: `slot-drawer-field-wiring-v3` (model swap → `POST /swap {model_id}` and its live-container confirm gate; `parallel` → `PUT /config {parallel}` incl. empty→`null`, untouched-never-rides, and sub-1 validation; the NPU Chat/Embed modality writes; the NPU chat-model pick; and the not-installed pull-then-apply flow, which must not write config while the download is in flight), `model-drawer-duplicate-v3` (`POST /api/models/{id}/duplicate` body with and without a device template, id suggestion and pinning, invalid-id block, and the 409 path), and `drawer-save-errors-v3`. That last one closes a whole missing axis: a grep for `status: 4`/`status: 5` across every `slot-*` spec previously returned nothing, so nothing covered what happens when the server says no — precisely where an operator loses work. It pins that a rejected write never closes the drawer, the operator's edits survive, the backend envelope message is surfaced verbatim, a failed `PATCH /defaults` short-circuits the `/config` PUT rather than leaving a half-applied save, and a server-rejected NPU toggle reverts to server truth instead of displaying a state the backend refused.

- **`hal0 slot migrate-flags` — the flags-fold migrator finally has an operator entry point** (#1396). `hal0.config.migrations.slot_flags_fold` has existed since the flags-ownership lane, but nothing ever exposed it: no CLI, no installer hook, no boot wiring — only tests referenced it. Its sibling folds both had commands (`slot migrate-hw`, `slot migrate-caps`). Meanwhile the launch-side readers were already deleted (`providers.container` drops `profile_flags`/`slot_parallel`/`extra_args`; `resolve_chat_template` no longer consults the slot), so an upgraded box with a bench-tuned slot silently launched **without** that tune and had no supported way to recover it — the exact ordering hazard spec-flags-ownership §5.4 flagged ("readers become expired shims with a sunset"). The new command mirrors `migrate-hw`: dry-run by default, `--apply` takes a timestamped backup and passes the `deploy_window=True` ack, refuses to run while any hal0 unit is live (`--stop-services` to stop them), and is never wired into an automatic path. Divergent-share conflicts (two slots folding different tunes onto one model) are surfaced as a clean non-zero exit listing every conflict — on the dry-run path too, which previously would have raised an unhandled `RuntimeError` at an operator merely *previewing* a conflicted box.

### Changed

- **The slot drawer's header toggle is now Pinned/Unpinned, and an explicit `pinned = false` un-pins a default anchor** (#1367). The §21.10 operator pin (`SlotConfig.pinned`) shipped with no UI surface at all, while the drawer header exposed the enable/disable flag — the toggle an operator actually reaches for when they mean "keep this slot resident". The header toggle now reads/writes `pinned` (instant-apply `PUT /config {pinned}`, same shape as the old enable wire). Two backend halves make it honest: `reaper.is_pinned()` treated an authored `pinned = false` as indistinguishable from absent (`is True` OR anchor-set), so `agent`/`utility`/`npu` could never be un-pinned by config — the raw-TOML key now wins in both directions and the anchor set only applies when the key is absent; and the slot list (`GET /api/slots`) lifts the *effective* pin per entry, so a fresh install's `utility` renders Pinned without a per-slot `/config` fetch. The unload/delete guards are unchanged (409 `slot.pinned`, `?force=true` bypass) and are now covered by route tests, including the un-pinned-anchor path. Enable/disable is untouched backend-side; its drawer toggle is gone (removal of `enabled` itself is staged separately).

### Security

- **Pin the destructive `/api/memory` routes as ADMIN, above the generic prefix rule** (closes the last open slice of #1024). #1024's incident was a single unauthenticated `DELETE /api/memory/banks/{bank_id}` that cascade-deleted ~632 live records; its echoed-`?confirm=` guard (#1028) and audit row (#1030) landed, but the classification itself was only ever ADMIN *by generic prefix* and nothing asserted it. That matters because #1024's own follow-up proposes "keep reads open if desired", and the natural expression of that — a `_prefix("/api/memory")` CLIENT rule — silently takes the bank wipe with it under first-match-wins. Two narrow rules (`any DELETE under /api/memory`, `POST /api/memory/delete`) now sit *above* the generic memory row, and `exposure.DESTRUCTIVE_MEMORY_ROUTES` enumerates the irreversible surface so a new memory delete route, or a reclassification, has to touch the constant in the same diff. `tests/security/test_memory_delete_auth.py` asserts the constant tracks the live route table, that each route resolves via a pinned rule (not the ADMIN fallback), that all of them survive a simulated reads-are-CLIENT widening, and — against the real `AuthEnforcementMiddleware`, armed — that an anonymous call gets `401`, a client/inference key gets `403`, and the operator's admin key clears the gate.

### Fixed

- **The model drawer's "vision requires an mmproj sidecar" error now blocks the save instead of decorating it** (#1380). Toggling the `vision` capability on a model with no projector rendered a red inline message and then let the PUT through, so the registry row advertised `vision` with nothing for the launch path to hand `--mmproj`. The invariant now lives in a `mmprojError` memo folded into a single `saveBlocked` gate alongside the existing `flagsError` — the one gate both `onSave`'s early return and the Save button's `disabled` consult — and the message carries a `data-testid` so it is assertable. Nothing else in the drawer's validation surface moved; the flags editor's `invalid` styling still keys on `flagsError` alone.
- **An emptied Display name now clears the stored name instead of being silently dropped** (#1381). `if (trimmedName && trimmedName !== model.name)` collapsed "unchanged" and "deliberately emptied" into the same skip branch, so the `name` key never reached the PUT: the old name survived while `dirty` armed the discard guard and the drawer closed with a success toast naming the value you had just tried to remove. The guard now diffs on the value alone and sends `name: ""`, matching the `mmproj` / `hf_repo` / `hf_filename` fields beside it — `Model.name` is `str` with `default=""`, and `normalizeApiModel` already falls back to `model.id`, which is exactly the affordance the field's "empty keeps the model id" help text advertises.
- **The flags fold silently dropped every slot's `[server].extra_args`** (#1396). `collect_inputs` feeds the planner `SlotConfig.model_dump(by_alias=True)`, and SlotConfig's `_tuck_server_into_extra` model_serializer re-parks the server sub-table under `extra["server"]` so the loader round-trips a proper `[server]` TOML table. `_slot_flag_tokens` read only a **top-level** `server` key, so against real input the freeform tune — the single value the migrator exists to preserve — never entered the fold; only `parallel` and the typed ngl/ctx survived. The same gap defeated the divergent-share guard: two slots differing *only* in `extra_args` folded to an identical tune, so the planner saw no conflict and would have silently picked a winner instead of refusing. The token reader now accepts both shapes. Caught by the new CLI tests before the command shipped, so the unreachable migrator was never made reachable-and-wrong.
- **`memory_degraded` now tracks the live engine, not just the boot probe** (#1301, runtime half). The boot `/health` probe added in 1.0.0-rc.1 makes the hindsight→pgvector ladder fire when the daemon is down *at boot*, but it answers one question once and the answer goes stale immediately: a daemon that dies afterwards leaves the `HindsightProvider` in place, and `/api/status.memory_degraded` plus `hal0 memory status` went back to reporting healthy while every recall came back empty and every retain raised. `HindsightProvider.degraded` is now a live property fed by a single `_call` wrapper every engine round-trip funnels through — it flips on an observed transport failure and, unlike a boot probe, clears itself when the daemon comes back. A 4xx does not degrade (the daemon answered; the delete sweep's routine per-bank 404s must not flap it), a 5xx does — the same rule `probe_health` uses, so boot and runtime cannot disagree. The `hal0 memory status` line no longer claims "in-memory fallback" for what may be a failing durable engine.

- **Config drift false-warns forever when the TOML id and the registry key are spelled differently** (fixes #1226). The drift comparator substitutes a model's on-disk path for a bare registry id before comparing, but matched the two ids with `==`. The slot TOML keeps the catalog spelling (`Qwopus3.5-4B-Coder-MTP-Q6_K`) while the registry key — and the running container's `--alias` — is the slug (`qwopus3-5-4b-coder-mtp-q6-k`), so the substitution never fired and every status read printed `config drift: --model: running=/mnt/ai-models/….gguf rendered=Qwopus3.5-4B-Coder-MTP-Q6_K`. The comparison now normalises both ids. Separately, `compute_config_drift` looked up the raw TOML id while `load()` launches the container from `_resolve_servable_model(...)` (a catalog id that landed locally under a different id), so for exactly the slots this matters for the registry lookup missed, the renderer emitted the bare id, and the warning was permanent; the drift check now resolves the model the same way the launch path does. A genuinely different running `--model` path is still flagged.
- **Bound the slot teardown on the seamed (hal0-service-user) route too** (#1224, worker side). `SlotManager.terminate`'s timeout releases the *caller*, but it cannot touch the executor thread sitting on `systemctl stop`; on a real install that thread stayed blocked and `unload_sync` never reached the Quadlet-source removal + `daemon-reload` that lets the next load converge. `SystemCtlSeam.systemctl` now forwards a `timeout` on both the direct and the seamed route (default `None` = unbounded, unchanged), and `ContainerProvider.unload_sync` bounds the stop at 20s — under the caller's budget so the worker unwinds first — logs `container.unit_stop_timeout`, and continues the teardown rather than aborting it.

## [1.0.0-rc.1] — 2026-07-27 (R5 · the rework release)

The R5 rework puts the platform back together as a genuine 1.0: memory and
Hermes finished, dead surface swept, launch flags re-homed onto **models**, the
boot path split into named observable phases, and the installer hardened against
real-world hosts (validated live on privileged/podman-4.9.3 and
unprivileged/podman-5.7 substrates — clean install and in-place upgrade, exit 0).
Full operator walkthrough: `docs/hal0-install-migration-guide.html`.

### Highlights

- **FLAGS-own — flags belong to models.** Launch flags, device, and chat-template now live on the model; a slot is just `(id, name, model, port, state)`. Profiles became copy-on-stamp templates. The argv resolver stops reading profile/slot overrides at launch; `model.defaults` carries the materialized tune. The managed-arg denylist now also screens a model's `defaults.extra_args` (closing a bypass where a denied flag reached the container).
- **Memory is Hindsight-only.** Honcho removed. Tools renamed to the upstream surface — `hindsight_recall` / `hindsight_retain` / `hindsight_reflect` (old `hal0_memory_*` kept as aliases); `reflect` implemented; config moved to `~/.hermes/hindsight/config.json` (`local_external`).
- **Hermes brain-lane relocated into the api boot lifespan** — persona seed + identity/brain-profile registration + self-report now run on every restart (the one hook fresh/update/dev share), reached in-process rather than over loopback HTTP.
- **Install & runtime hardening** — accurate container-runtime preflight diagnostics (keyring-quota exhaustion surfaced instead of a misleading nesting/keyctl message); GPU group resolution derives the render GID from the device node's owner (not the group name); slot units emit `StartLimit*` in `[Unit]` so restart limiting applies; Hermes gateway install drops to the `hal0` user (no stray root-owned `~/.hermes`); `hal0 agent status --json` emits real JSON; `reconcile_listeners` wired into `/api/ports`.
- **Vulkan is the default for shared-APU models** — benchmark-backed (RADV +40% prefill / +16% gen / −30% TTFT vs ROCm on Strix Halo).
- **Boot split into 14 named, observable phases** with a typed `BootState`; drift-watch fixtures + a `hermes-bump` runbook added.

### Breaking

- **Launch flags/device/chat-template moved off slots onto models.** Existing slot TOMLs with those fields still load (config is `extra="allow"`) and are ignored at launch until you run the fold migrator — but the slot-level surface is deprecated and `HAL0-SUNSET`-stamped for removal.
- **Honcho removed** as a memory engine; **`hal0_memory_*` tools renamed** to `hindsight_*` (aliases retained this release).
- Deprecated surfaces machine-stamped `HAL0-SUNSET: v1.0.0` for scheduled removal: the `--backend` flag (use `--provider`), `SlotConfig.runtime`/`workers`, the `cognee` engine literal, and several legacy CLI aliases.

### Migrations

- **Upgrade in place** — re-run the installer (or `hal0 update`); it is idempotent + non-destructive and never clobbers existing config. No reinstall.
- **Honcho → Hindsight** (only boxes that ran Honcho): `hal0 memory migrate --from honcho --to hindsight` before upgrading. Boxes already on Hindsight are a no-op.
- **Slot-flag fold** (operator-run): the migrator folds slot tunes into model defaults; it **refuses the whole run** (no partial write) if slots share a model with divergent tunes — resolve each shared model (canonicalize or split) first. Dry-run by default; back up `hal0.db` + slot dirs before applying.
- **Slot id-keying** (operator-run, optional): `hal0 slot migrate-id-keying` in a downtime window (takes a pre-flight backup). The runtime reads either layout; the flip is deliberate and reversible.

### Fixed

- **A FastAPI minor bump silently removed the entire MCP surface.** FastAPI 0.138 stopped flattening `include_router`: `app.routes` now holds one `fastapi.routing._IncludedRouter` per included router instead of that router's `APIRoute` objects, and the wrapper carries neither `.path` nor `.methods`. `build_admin_route_map`'s flat one-level walk skipped every one of them, returned an empty map, and `install_admin_route_map` raised "catalog drift" for ~85 classified routes — which `create_app` caught and logged, so **`/mcp/admin` and `/mcp/memory` never mounted while the API kept reporting healthy**. Found live on halo: 21 boots, 0 successful mounts. CI could not see it, because the test venv resolves `fastapi` from `uv.lock` (0.136.1, still flat) while the installer resolves `pyproject.toml`, which pinned `fastapi>=0.115` with no upper bound — so every fresh install got the restructured version and lost its agent control surface. Three-part fix: the walker descends through wrappers via `effective_candidates()` (duck-typed, depth-bounded, verified against real 0.138 — 315 route-map entries, both servers mounted); `pyproject.toml` gains `<0.140`; and a mount failure is now an `error`-level log recorded on `app.state.mcp_mount_error`.
- **A failed MCP mount is now visible instead of only logged.** `create_app` still survives the failure (a serving API with no MCP beats no API), but `/api/health/system` reports a `mcp_mount` check that names the reason and flips the whole payload to `degraded`. The blocker above hid behind a single warning line for 21 boots with every health endpoint saying `ok`; this makes that class of failure surface within a boot.
- **An in-place upgrade left the OLD code serving.** The installer ended with `systemctl enable --now hal0-api`; `--now` starts a stopped unit but is a no-op on an active one. So an upgrade over a live box replaced the venv, swapped `/usr/lib/hal0/current`, printed its success banner and exited 0 while the running process kept serving the code it had already imported — `hal0 --version` and `/api/health` disagreed until someone restarted by hand. `start_or_restart_api` now enables for boot either way and issues an explicit `restart` when the unit is already active. Deliberately the opposite policy from slot units, which are still not bounced (a slot restart costs a model reload; hal0-api is the thing being replaced).
- **Slot drawer — restore the per-slot `Reasoning` and `MTP` controls** (fixes γ-suite, #1333). The `feat(ui): slot drawer & model drawer rework` landed with Reasoning + MTP removed from the slot drawer under "now model-owned"; the model drawer carries model-level defaults but operators need a per-slot override. Re-added the Reasoning pill (llm slots only, instant-apply via `PUT /config { enable_thinking }`) and the MTP pill (llm slots only, tri-state Auto/On/Off, instant-apply via `PUT /config { mtp }` + non-blocking cold restart) into the `Inference` FieldGroup. Renamed the HW grid `Hardware` FieldGroup → `Slot` (the drawer is now Slot / Model / Inference) and the runner-binary select label `Profile` → `Binary` (the bound runner stays on the slot card chip — no editable profile select in the drawer). The 12 γ-suite slot-drawer tests that were red on main since 2026-07-20 (#1333) all pass.
- **Hermes gateway install runs as root** — fix installer `sudo -u hal0` → root call (PR #1337). The installer was dropping to the `hal0` user before running `hermes gateway install --system`, which checks `os.geteuid() == 0` and refuses non-root. The `--run-as-user hal0` flag already tells hermes which runtime user to bake into the systemd unit. Validated on halo150 (10.0.1.150, podman 4.9.3).
- **`/mcp/memory` bulk delete was an ungated bypass of the admin approval queue** (#1302). `mcp__hal0-admin__memory_delete` gates `len(ids) > 1` through the approval queue, but the standalone `/mcp/memory` mount ran the same tool against the same provider with no gate — an agent holding only the narrow memory surface could bulk-delete without an operator ever seeing a prompt. The memory dispatcher now takes the process-wide `ApprovalQueue` and enqueues bulk deletes itself, delegating the *classification* to `hal0.mcp.admin.is_gated` so the two mounts cannot drift. The dispatcher handed to the admin server deliberately stays ungated (admin gates first, then runs the approved call through it — double-gating would re-enqueue an approved call forever). Single-id deletes stay autonomous.
- **`/api/memory/*` auth posture ratified as perimeter-only** (#1302). `X-hal0-Agent` is self-asserted and hal0 does not authenticate it — there is no credential to check it against (ADR-0012 removed auth platform-wide). hal0 validates the header's *shape* and rejects a body-supplied `source`, so audit can never disagree with the namespace a write landed in, but `private:<agent>` is an isolation boundary between cooperating agents, **not** a security boundary against a hostile LAN caller. Documented for operators in `docs/concepts/security.mdx` (with the reverse-proxy header-injection pattern for multi-tenant deployments) and next to the code in `api/routes/memory.py`.
- **Hindsight→pgvector degrade ladder was inert; `degraded` reported healthy while memory was broken** (#1301). `_build_hindsight_client` only called `HindsightRestClient.from_env()`, which builds an httpx client and does no I/O — so a daemon that was down never raised at boot and `provider_from_config`'s degrade branch never fired. Operators got a live-but-broken `HindsightProvider` with `degraded=False`: failures surfaced only as empty recalls while `GET /api/status.memory_degraded` and `hal0 memory status` both said healthy. Construction now probes `/health` (the same endpoint `install.sh` waits on), timeout-bounded via `HAL0_HINDSIGHT_PROBE_TIMEOUT_S` (default 2s). A `401`/`403` passes — that proves the daemon is answering, and a wrong API key is not a reason to silently drop the durable engine; `5xx`, connect errors, and timeouts degrade.
- **`project:<id>` isolation was a silent no-op in `unified_bank` mode** (#1300). Under the default config every namespace except `agents` collapses onto the single `shared` bank. `private:` survives that collapse because `add` stamps `visibility:private` + `agent:<id>`; `project:<id>` got no compensating marker, so a `project:foo` write became an ordinary shared write and a `project:bar` recall returned it — project scoping did not exist. Writes now stamp a `project:<id>` tag on collapse, and recall / list / delete filter by it, reproducing the bank isolation legacy multi-bank mode gets for free: a project read sees only that project, a `shared` read does not see project docs, and the filter composes with (rather than shadows) private visibility. Delete is gated on the same predicate, so a project-scoped delete cannot reach outside its scope. Legacy multi-bank mode is untouched — no tag, isolation still by bank.
- **`slot load` no-op'd on a live slot, so config edits never converged** (#1224 part 2). The unit file claims to be "regenerated on every slot load", but a load on a ready/serving/idle slot short-circuited to a status snapshot. `PUT /api/slots/ops/config {"port": 8091}` → `slot load ops` returned the stale snapshot with the container still on `--port 8089`, and the next implicit reload cycled warming → error with nothing listening on either port; recovery needed `systemctl reset-failed` plus a second load *from the error state* — the only path that regenerated. An explicit load now compares the running argv against what a restart would render (reusing the drift comparator, which cannot be lost across an api restart and self-heals a unit drifted by any route) and converges when they disagree. Unchanged slots stay a no-op, and an unreadable comparison counts as "no drift" rather than bouncing a healthy container. `--port` joins the compared keys — it is the field the issue was reported against.
- **`slot restart` could hang forever on a wedged unit** (#1224 part 1, completing the earlier fix). `terminate` ran `systemctl stop` as a blocking executor call with no timeout; against an already-`failed` unit that never returns, so `restart`'s best-effort `suppress(Exception)` could never fire and the CLI ReadTimeout'd with the unit never relaunched. The stop is now bounded (`SlotManager._terminate_timeout_s`, default 30s) and raises `SlotTerminateTimeout` on expiry. We cannot kill the executor thread, so this does not cancel the stop — it hands control back so the caller converges instead of hanging; the abandoned thread retires on its own.

## [0.9.8] — 2026-07-13

Turnstone lands as a second heavyweight bundled agent alongside Hermes and
becomes a first-class companion service; the unified **hal0-rocmfpx** runner
becomes the default image for AMD GPUs (with an automatic slot migration on
update); and a memory security fix stops one agent deleting another's private
memories.

### Highlights

- **Turnstone — a second heavyweight bundled agent** joins Hermes: a native `turnstone-server` on loopback :9129, installed into its own managed PyPI venv, coexisting with Hermes via relaxed single-pick (#1299).
- **Turnstone is a first-class companion service** — it shows in the Services pane and the Overview health card with start/stop/restart controls, next to Hermes/Hindsight/OpenWebUI.
- **hal0-rocmfpx is now the universal default runner for AMD GPUs** — one unified image (Vulkan/RADV + HIP) replaces the per-lane toolboxes; CUDA and CPU-only lanes keep their lean images (#1297).
- **Memory: private-visibility is now enforced on delete** — in unified-bank mode one agent could delete another agent's `visibility:private` memory by id; `delete` now applies the same fail-closed ACL as read/search/list.

### Migrations

- `hal0 update` automatically re-pins existing AMD-GPU slots from the old `amd-strix-halo-toolboxes` images to the unified `hal0-rocmfpx` runner (no-op on CUDA/CPU lanes) (#1297).

### Added

- **Turnstone bundled agent** — provisioning pipeline (managed PyPI venv, JWT-secret generation, model automap) plus hal0 provider/memory wiring, coexisting with Hermes (#1299).
- **Turnstone companion-service registration** — a `ServiceDef` + systemd health probe, so turnstone appears in `/api/services` (Services pane, full lifecycle actions) and `/api/services/health` (Overview card + sidebar status).

### Changed

- **hal0-rocmfpx as the default AMD-GPU image** — basic seed profiles defer to a manifest-driven resolver that returns the unified runner for AMD lanes, the CUDA image for NVIDIA, and the lean toolbox for CPU-only (#1297).
- PyPI distribution is published under the name **`hal0ai`** (the import package and `hal0`/`hal0-agent` console scripts are unchanged) (#1298).

### Fixed

- **Memory delete ACL** — `delete` enforces the `visibility:private` owner check in unified-bank mode, so an agent can no longer delete another agent's private memory by (guessable) document id; unresolved ids are withheld fail-closed (#1302).

## [0.9.7.3] — 2026-07-12

A robustness release. Fresh installs now adapt to the host — provisioning their
own Python and Node, tolerating podman **or** docker, and surviving hardened
umasks and non-root operation — and the self-hosted Honcho memory stack stands
up cleanly alongside Hindsight. Plus the pi-coder and opencode bundled agents,
the unified per-agent memory model, and a large batch of install/setup/agent
fixes surfaced by end-to-end reinstall testing on a clean box.

### Highlights

- **Installs self-heal across environments** — auto-provision Python 3.12 and Node 20 LTS when missing, tolerate podman or docker, and survive hardened/root umasks (#1291, #1289).
- **`hal0 doctor` and bundled-agent installs work on packaged installs** — FHS-aware resolution fixes the "packaged without scripts" and "could not locate preflight.sh" failures on non-editable installs (#1284, #1285).
- **Crash-safe agent switching** — a failed `agent install --switch` no longer bricks the running agent; it verifies the target first and rolls back on failure (#1285).
- **Self-hosted Honcho as a per-agent memory provider** — unified memory, swappable and migratable with Hindsight per agent, with a clean opt-in standup (#1243, #1294, #1295).
- **pi-coder and opencode join Hermes** as bundled, single-pick agents (#1254, #1271).

### Added

- **Node.js LTS auto-provisioning** in the installer (the dashboard build and pi-coder/opencode all need npm) plus a curated `qwen3-embedding-0-6b` model for the memory pipeline (#1291, #1294).
- **Unified per-agent memory** — self-hosted Honcho v3 provider and a unified-bank model with server-side tagging, plus `hal0 memory bank/ops/mm/recall` and `migrate unify` CLI (#1243, #1244, #1257).
- **pi-coder** agent (provisioning, hal0 provider/memory plugins, live dashboard card) and the **opencode** bundled agent (#1254, #1271).
- **hal0-brain** as a first-class profile (#1258), **upstream controls** CLI and UI (#1279), and a **bench queue** dropdown with lane/tool-eval/tune options (#1255).

### Fixed

- **Installer/preflight robustness** — Python floor raised to 3.12 with auto-install, hardened-umask permissions, a container-runtime smoke test that no longer false-fails, and Node/disk/graphroot preflight gaps (#1291, #1292).
- **Honcho standup** — unbound-var abort, pgvector embedding-dim reconcile, compose-provider and migration ordering, AppArmor-in-LXC, and full `--purge` teardown (#1293, #1294, #1295, #1287).
- **docker/podman portability** — runtime-appropriate slot units and a PATH-based runtime probe (#1289).
- **Slots** — errored-slot restart recovery and the WARMING watchdog, plus drift/image_status false positives (#1278, #1269).
- **Memory** — private-visibility enforcement on read, migrate-unify retag scoping, and Hermes plugin/bank identity consolidation (#1260, #1262, #1245).
- **CLI** — editable-install `hal0 update` refusal, footgun confirmation gates, and command consolidation (#1274).

### Changed

- **comfyui**, **doctor**, and **agent** helpers resolve bundled scripts and assets FHS-aware so they work on packaged (non-editable) installs (#1286, #1284, #1285).
- Docs — ADR/Cognee→Hindsight sweep, `hal0/chat`→`hal0/agent` alias, fresh-box first-run journey, and a loud LAN-only bind warning (#1280, #1276, #1275).

## [0.9.7.1] — 2026-07-11

Hotfix for a fresh-install blocker — Hermes never auto-provisioned on a clean
`curl | bash` install — bundled with the dashboard, provider, and memory
improvements merged since 0.9.7.

### Highlights

- **Hermes now auto-provisions on a fresh install** — no more manual `--adopt`/`--repair`; all bootstrap phases complete and the gateway comes up managed, idle until a bot token is added (#1239).
- **MiniMax and DeepSeek** are now one-click options in the upstream provider catalog (#1236).
- **Keyboard-driven `hal0 setup`** — the guided setup TUI gets real arrow-key navigation and a clearer model picker (#1237).
- **Memory subsystem toggle** — `hal0 memory enable` / `disable` / `status` replace the old install-time env flag (#1240).

### Added

- **MiniMax + DeepSeek** upstream catalog entries (OpenAI-compatible, bearer auth) — selectable in Slots ▸ Endpoints ▸ Add upstream with prefilled URL and auth (#1236).
- **Arrow-key navigation in the guided-setup TUI**: ↑↓/jk move, space toggles apps/agents, enter selects; scaffold/skip are clean navigable rows; numbered entry still works over a pipe or in CI (#1237).
- **`hal0 memory enable` / `disable` / `status`** commands, plus the `[memory].enabled` config flag (#1240).
- **Retry failed graph extractions** — a Memory-tab button that re-runs Hindsight's failed extraction operations, plus mental-model delete (#1235).

### Fixed

- **Hermes fresh-install provisioning** (#1239, closes #1238). Two chained bugs aborted provisioning on every clean install: the api-lifespan seed populated `HERMES_HOME` before the bootstrap could claim it ("unclaimed HERMES_HOME"), and the gateway installer started a unit that hal0 then flagged as its own "foreign" gateway. The lifespan now stamps `.hal0-managed` before seeding and pre-writes the gateway secrets drop-in before the unit starts.
- **Model-filters spacing** in the Endpoints upstream pane — the filter inputs no longer sit flush against the panel border (#1236).
- **Graph-extraction consolidation reliability** in the Memory tab (#1235).

### Changed

- The memory subsystem is now gated by **`[memory].enabled` in `hal0.toml`** (default on) instead of the installer-written `HAL0_MEMORY_ENABLED` env var; toggle it with `hal0 memory enable` / `disable` (#1240).

### Migrations

- If you had disabled memory with `HAL0_MEMORY_ENABLED=0`, that env var is now ignored (memory defaults on) — run `hal0 memory disable` to keep it off (#1240).

## [0.9.7] — 2026-07-11

The steward release. The dashboard's agent chat graduates from a
side-panel into a real control surface for the whole platform, external
LLM providers get a first-class management surface, and the FLM/NPU
stack settles onto canonical names with an automatic migration.

### Highlights

- **hal0-brain steward.** The top-bar agent chat now drives every platform surface: it runs the full 74-tool `hal0-admin` MCP catalog under a per-persona tool policy, **pauses turns on gated tools** for inline approve/deny, replays tool history across turns, and renders reasoning + tool cards inline (#1208, #1215, #1221, #1222, #1223).
- **Upstream model controls.** A full management surface for external providers (OpenRouter, Anthropic, OpenAI, Google AI Studio, Ollama, custom) — reactive CRUD, per-upstream model filters, an `enabled` kill-switch, and CLI + MCP + dashboard parity (#1228).
- **Graceful restarts keep your downloads.** Model pulls no longer block a clean `hal0-api` shutdown, so restarting mid-download no longer trips the 90s SIGKILL that was killing in-flight pulls (#1225).
- **FLM / NPU canonicalization.** The NPU trio's shadow slots settle onto `flm-stt` / `flm-embed`, `/v1/models` alias routing is finalized, and a naming migration + `hal0 doctor` audits move existing installs onto the new scheme (#1210, #1229, #1231, #1214).
- **Agent is the new anchor.** Seeded `agent` and `brain` slots replace `chat` as the default LLM anchor, and are seeded on every startup — not just fresh installs (#1204, #1217, #1218, #1230).
- **`hal0 update` verification actually works again.** Release signing now dual-emits a Sigstore bundle (with an embedded Rekor timestamp) so cosign verification survives the short-lived Fulcio cert's expiry — `curl … | bash` installs and in-app updates were failing verification 100% of the time on v0.9.2/0.9.3/0.9.5. Fully back-compatible with already-deployed clients (#1159).

### Added

- **hal0-brain steward / agent chat as a control surface.** The dashboard's top-bar agent chat becomes a first-class operator for the whole platform:
  - **Full `hal0-admin` platform surface (#1208).** The admin MCP catalog grew from **28 to 74 tools**, replacing the slide-out's old hardcoded ~25-tool list, so the Brain can drive every surface end-to-end:
    - *Models* — `model_inspect` (read an HF repo before pulling), register / add-from-path, metadata edit (PUT), in-place HF re-pull (`model_update`), pull status/cancel, scan (+preview), catalogue, update-check, and store show/set/migrate. Pulls always land in the operator's configured `[models].store` (re-read per call; the tool descriptions state the contract).
    - *Slots* — load / unload / edit-config (PUT) / metrics / capacity / logs, on top of create / delete / restart / swap.
    - *Stacks* — create / update / export / snapshot, joining apply / import / delete.
    - *Profiles* — create / update, joining import / export / delete (author a profile straight from a model card).
    - *Settings & platform* — `settings_get` / `schema` / `apply_plan` / `reload`, `upstream_list`, and benchmark runs/status/queue reads plus gated enqueue/control.
    - Two new guards make catalog drift impossible to reintroduce: **import-time catalog validation** (classification ↔ REST-map ↔ annotations ↔ path-args must cohere) and a **`build_server` registration-completeness check**.
  - **Per-persona tool policy (#1215).** The persona TOML's `tools_allowed` + `[persona.approval]` tables — previously decorative on the sidebar path — become an enforced server-side overlay (`admin.ToolPolicy`, fnmatch globs over tool names): `tools_allowed` hides tools from the surface entirely; `require_approval` tightens an autonomous tool behind the approval queue; `auto_approve` / `default_policy=auto-approve` grants standing approval to gated tools; `default_policy=never` refuses gated calls outright. Precedence is **hide > tighten > loosen > server verdict**. A **`POLICY_NO_LOOSEN` floor** means `model`/`slot`/`stack`/`profile_delete`, bulk `memory_delete`, `config_write`, and `provider_credential_write` can **never** be loosened by a persona edit; denials are typed (`mcp.tool_not_allowed` / `mcp.gated_tool_refused`) with audit rows.
  - **Tool-use hardening (#1222)** — fixes for four failure modes seen live:
    - *Runaway generation* — every round now carries `max_tokens` (default **4096**, payload-overridable); an uncapped completion against a slow local slot used to burn ~25k tokens and the 300s transport window, killing the turn before the first tool call.
    - *Invisible approvals* — gated calls no longer park silently on the queue; the loop emits an `approval_required` SSE frame and **pauses (with keepalive pings) until the operator approves/denies**, then streams a second `tool_result` so the *same* turn continues (timeout falls back to the pending result).
    - *Guessed argument names* — high-traffic tools ship real parameter schemas + explicit descriptions (no more `model_inspect` without `hf_repo` → 400, or `model_pull` with `model_id='org/repo'` → 405); path args containing `/` are rejected with an actionable hint.
    - *Round budget* — `_MAX_ROUNDS` raised **8 → 90** (with per-round caps it's now a runaway backstop, not a working limit multi-step sessions kept hitting).
    - *UI* — `useBoardChat.send()` now **replays full tool history** (calls + results) in the outgoing conversation; previously it rebuilt from user/assistant *text only*, so after one tool chain the model saw no tool calls in its own history and learned to skip tools and hallucinate results. Adds inline **approval cards**, a **Stop** button, an **auto-approve** toggle, and **new-session**.
    - A **global port-claim registry** — the single authority for which slot owns which port — lands alongside in the same PR.
  - **`[brain_chat]` server-side guardrails (#1221)**, enforced independently of the persona TOML (a persona edit can loosen the persona, never these): **`enabled`** is a hard kill switch (the endpoint refuses every turn — no LLM call, no board request); **`read_only`** lets reads through but refuses every mutating/admin-write tool at the single `_dispatch_tool` chokepoint (unknown tools fail closed); **`max_rounds` / `completion_timeout_s`** move the loop budget + per-round transport timeout out of module constants into config, with the schema as the single source of truth.
  - **Agents / Brain settings + slot override (#1223).** A dashboard **Settings → "Agents / Brain"** section surfaces `[brain_chat]` (enabled + read-only toggles, `max_rounds` / `completion_timeout_s` inputs) plus a **slot-override picker** built from the live slots list — point the steward at any slot (e.g. `hal0/npu` to run it on the NPU chat slot). Model precedence is explicit: per-request model > `[brain_chat]` override > persona/default; the rows carry a live badge (`brain_chat.*` is apply-plan `immediate`).
- **Upstream model controls** — full management surface for external LLM providers (OpenRouter, Anthropic, OpenAI, Google AI Studio, Ollama, custom):
  - Reactive CRUD: `POST/PATCH/DELETE /api/upstreams` (create prefills from the provider catalog via `catalog_id`); `upstreams.toml` stays canonical — every write rewrites it atomically before touching the running registry.
  - `hal0 upstream` CLI group (`list/show/create/update/delete/test/set-credentials`); `create --catalog openrouter --api-key` wires a provider end-to-end in one command.
  - MCP admin tools `upstream_create`/`upstream_update`/`upstream_delete` (gated) + `upstream_test`.
  - Dashboard **Upstream providers** panel (Slots → Endpoints / Connections): add-from-catalog form, write-only key entry, test-connection with latency + model count, enable/advertise toggles, filter editor with live preview, delete-with-confirm.
  - Per-upstream `model_filters` (`models` allowlist + `include`/`exclude` globs, exclude wins) curating `/v1/models` and `/api/models` advertising — dispatch stays unfiltered so hidden models remain addressable by name (per the 2026-07-06 upstream-model-filters spec).
  - `enabled` kill-switch on every upstream: `false` removes it from dispatch routing and the model catalog while retaining config + credentials.
  - `auth_key_present` in upstream serializations — whether the declared env-var actually holds a key (drives the dashboard auth badge), distinct from `auth_configured`.
- **Install / lifecycle:**
  - Safe capture of an existing Hermes install — `--adopt`, a fatal claim abort, foreign-gateway preflight, and ownership reconcile so hal0 can take over an already-running gateway without clobbering it (#1220).
  - Static slot TOMLs plus the `agent` + `brain` slots are now seeded on **every `hal0-api` startup**, not only on fresh install (#1217, #1218, #1230).
- **`hal0 doctor`** grows FLM / migration / profile audits and a `--force` delete for seeded slots (#1214).
- **Dashboard / UI** — slots-page polish: unified headings + status indicators, an NPU activity tint, a full-height slot-logs drawer surfaced from the Logs page, an image-gen header pass, and a Documentation button in the topbar (#1205, #1207, #1209, #1213, #1216).
- **`scripts/push-dev.sh`** — a push-based inner-loop deploy onto an editable box (#1193).

### Changed

- The default LLM anchor is the seeded **`agent`** slot; the `coder` seed is retired and `hal0/chat` resolves to `hal0/agent`, on every install path (#1204, #1217).
- The NPU trio's shadow slots are canonicalized to `flm-stt` / `flm-embed`, with a startup reconcile that migrates legacy names (#1210).
- FLM host pulls land in the operator's resolved model store (#1211).

### Fixed

- Model pulls no longer block a graceful `hal0-api` restart — the shutdown path drains pulls instead of being SIGKILLed at the 90s deadline, which was killing in-flight downloads (#1225).
- FLM model pulls that died instantly in production — uvloop rejects the user/group spawn kwargs the pull worker passed (#1192).
- FLM host pulls now land in the resolved store with robust 0-byte progress reporting (#1211).
- NPU-trio alias routing is finalized in the `/v1/models` route (#1231).
- Static slot seeding no longer pollutes every test's zero-slot baseline (#1219).
- Dashboard: real update-banner actions, NPU-grid colours keyed to the primary FLM slot, and a single unified "Needs attention" + bell notifications source (#1194, #1195).
- `upstreams.toml` schema drift: `auth_style = "anthropic"`/`"google_query"` (long implemented by the dispatcher) now validate; `auth_header` is a real schema field so `auth_style = "header"` works; warmup vocabulary canonicalized to `none|ondemand|always` with `lazy`/`eager` accepted as normalizing aliases — a TOML authored in the runtime vocabulary no longer fails validation and silently empties the upstream registry.
- `/api/models` no longer stamps slot-backed advertisements as `origin="upstream"`: the composite `hal0` aggregate and container slots serve local models, but a raw GGUF id whose casing differed from the registry id (e.g. `Qwopus3.5-4B-Coder-MTP-Q6_K`) surfaced in the Models page Upstream tab as "via hal0". Only genuine remotes contribute upstream rows, honoring `enabled`/`advertise_models`/`model_filters`.
- `hal0 upstream` first cut sent field names the API rejects (`openai_base_url`, `kind`, `allow`/`deny`, `api_key`) — every write 422'd; request bodies now match the route contracts exactly and are pinned by tests.
- **Release signing survives Fulcio cert expiry (#1159).** Keyless cosign signing issues a ~10-minute Fulcio certificate; the old detached `.sig` + `.crt` carried no Rekor Signed Entry Timestamp, so `curl … | bash` installs and in-app `hal0 update` — which run hours or days after signing — had no trusted timestamp to anchor the signature and failed `verify-blob` **100% of the time** on v0.9.2/0.9.3/0.9.5 (the in-CI self-verify passed only because it ran seconds after signing). Releases now **dual-emit** a Sigstore bundle (`.tar.gz.bundle`, embedding the cert + signature + Rekor SET) alongside the legacy pair; the manifest carries `bundle_url` next to `sig_url`/`cert_url`, and `installer/bootstrap.sh` + the updater prefer `cosign verify-blob --bundle`, falling back to the legacy pair on manifests without it — so already-deployed (≤ v0.9.6.1) clients keep verifying through the transition. The legacy fields drop once fleet adoption is confirmed.

### Migrations

- **FLM / NPU naming migration (#1229).** Existing NPU-trio installs move from the legacy shadow-slot names to canonical `flm-stt` / `flm-embed`. The migration runs automatically on startup, and `hal0 doctor` audits + repairs any slot left on the old scheme. Custom `profiles.toml` overrides that reference the old names should be updated.
- **Agent replaces chat as the LLM anchor (#1204, #1217).** The `coder` seed is retired and `hal0/chat` now resolves to `hal0/agent`. Operators who pinned `hal0/chat` in a custom config should confirm the `agent` slot is seeded (it is, on startup) or repoint to `hal0/agent`.

## [0.9.6.1] — 2026-07-11

### Added

- Model updates surface in the topbar notification bell: the update check now runs app-level, so "N model updates available" appears (with an Update all action) without opening the Models page, and the row self-clears once updates land. The Models page gains an always-visible "Check updates" button that bypasses the check's TTL cache when nothing is currently flagged.
- `hal0-brain` agent profile: a third seeded persona (alongside `hermes` and `coder`) that stewards the platform from the dashboard's agent-chat slide-out — its own memory namespace (`private:hal0-brain`), a hal0-heavy system prompt (slot lifecycle, model setup, benchmarking), and the dedicated `brain` slot (`hal0/brain`) as the default model.
- Board/agent chat streams a `{type:"thinking"}` SSE frame: explicit `reasoning_content` and inline `<think>…</think>` blocks are split out of the reply and rendered as a folded "thinking" section instead of raw tags.

### Changed

- The top-bar agent chat now embodies the `hal0-brain` profile: it runs on `hal0/brain` (falls back to the `agent` slot via the resolver chain), and an operator-edited `hal0-brain` persona TOML overrides its system prompt/model without a code change.
- Agent-chat suggestion chips are now platform-steward starters ("Help me create a new slot", "Download and set up a model", "Benchmark the model on a slot", "How's the hardware doing?").
- Agent-chat replies render markdown (fences, lists, headings, bold/italic/inline code, links); tool calls render as structured cards with args, live status, and a folded result — replacing the raw `→ tool({json})` text rows.

### Fixed

- FLM NPU slots no longer wedge in `warming` forever: the warm→ready inference sentinel now probes the slot's **assigned** model instead of `models[0]` from FLM's full catalogue. Probing an arbitrary other model forced FLM to reload the wrong weights onto its single NPU context mid-gate and deadlocked the load (#1171).
- `hal0 setup` no longer aborts with a raw `HTTPStatusError` traceback when `apply-selections` returns `409 Conflict`: the setup CLI now treats a 409 (install already applied / a concurrent apply in flight) as a recoverable no-op with a clean message, and still raises on genuine errors (#1158).
- Unified the dashboard warming-state color to a single canonical `--warn: #f2792b` token in `dashboard.css`, removing the divergent local redefinitions in `engine-panes.css`/`overhaul.css` so every warming indicator renders the same orange-yellow (#1156, #1155).
- Added an ESLint `no-undef` guard (enforced in CI) over the dash `.jsx` prototype so an undefined identifier like the one behind the Create Slot crash can't ship again (#1170).

## [0.9.6] — 2026-07-10

### Highlights

- FLM NPU trio: the edit-slot drawer's Chat/ASR/Embed toggles now drive the running `flm serve` process, with a full-catalogue chat model picker that downloads on demand.
- FLM can run embed- or STT-primary (chat disabled) — a modality-aware readiness gate promotes the slot instead of wedging on a chat probe.
- Real FastFlowLM v0.9.44 toolbox image (`ghcr.io/hal0ai/hal0-toolbox-flm:0.9.44`), rebuilt from the actual v0.9.44 binary.
- NPU occupancy cards now glow purple when a slot is running (they used to read flat green regardless of state).
- hal0-bench is now in-tree: the `hal0.bench` engine, `/api/benchmarks`, and a Benchmarks dashboard page.

### Added

- NPU trio drawer wiring: ASR/Embed on-off toggles + a Chat model picker that lists the full FLM catalogue and pulls a not-yet-downloaded model on select (auto-applies on completion).
- `FLMProvider.verify_embed` — a one-shot `/v1/embeddings` readiness sentinel used when a slot serves embeddings without chat.
- hal0-bench in-tree port: `hal0.bench` engine, `/api/benchmarks` routes, and the Benchmarks dashboard tab (roster / runs / evals / run-queue).

### Changed

- FLM toolbox pinned to `0.9.44` across `manifest.json`, the flm seed profile, and the capabilities catalog (contains FastFlowLM binary v0.9.44).
- The warm→ready gate for FLM slots picks its sentinel by served modality: chat → `/v1/chat/completions`, chat-off+embed → `/v1/embeddings`, ASR-only → `/v1/models` liveness.
- `/api/slots/flm/models` returns the full FLM catalogue (installed + downloadable) with accurate `installed` flags, container-exec first with a host-probe fallback.

### Fixed

- Chat toggle now actually gates the container: `container_spec` no longer passes the positional chat tag when `[npu].chat=false` (it was cosmetic on the container path).
- Editing an NPU modality no longer clobbers `[model].default`/`context_size` — the drawer sends the model as a nested `[model]` table so the backend merge preserves sibling keys.
- `/api/npu/occupancy` no longer 500s while the FLM slot is offline (the degraded single-tenant fallback's `zip(..., strict=True)` mismatched `slots_out` vs `flm_slots`).
- NPU cards read a clear purple "running" glow for up/resident slots and dim for offline; the coresident STT/embed sub-cards reflect the anchor's `[npu]` toggles instead of the legacy shadow-slot `enabled` flag.

## [0.9.5.2] — 2026-07-09

### Fixed

- **Models → Downloads tab: React invariant 310 ("Rendered more hooks than during the previous render").** The Downloads pane in the Models view called `useStateM(false)` inside a `jobs.map((j) => { … })` callback. React counts hook calls per render, so the moment the downloads list changed length (a pull started, finished, failed, or was cleared) the hook count differed across renders and the entire `<ModelsView>` crashed behind the v3 error boundary — masking the rest of the dashboard. Fix: extract the per-row logic into a `DownloadRow` component that owns its own `cancelling` state, so the hook lives at row-component top level and its count is stable across renders. Pinned by `ui/src/dash/__tests__/react-hooks-order.test.mjs`, a new AST-based static check that walks every JSX/JS file under `src/dash/` and flags any useXxx (incl. the `useStateM`/`useEffectM`/etc. aliases used in this dashboard) called inside an array-iteration callback (`.map`/`.forEach`/`.filter`/`.reduce`/`.some`/`.every`/`.flatMap`/`.find`/`.findIndex`).

## [0.9.5.1] — 2026-07-09

### Highlights

- First green build on the 0.9.5 line — v0.9.5 shipped with a red CI (stale tests + lint); this hotfix greens the gate and closes v0.9.5's incomplete consolidations.

### Added

- **HF inspect recognizes FastFlowLM (NPU) repos.** `POST /api/models/inspect` now detects the FLM model shape (a `config.json` + tokenizer + `…nx` NPU-quant weight directory, e.g. `model.q4nx`) and surfaces one whole-repo variant flagged `flm` routed to the `flm pull` path — previously such repos inspected as "no variants" because the filter only admitted `.gguf`/`.mmproj`. Detection is shape-based (requires the `nx` weight blob) so a plain safetensors/GGUF repo is not misread as FLM.

### Fixed

- **Profile test suite realigned to the 2×2 seed grid.** v0.9.5 consolidated the retired `rocmfpx-rocm` / `vkfpx-*` slugs into the `{rocm,vulkan} × {dense,moe}` grid but left the tests asserting the old names, so `main` was red. Tests now assert the shipped grid.
- **Role-retirement cleanup finished.** v0.9.5 retired `SlotConfig.role` but left stale tests/docstrings referencing it (`test_slot_role`, `test_chat_normalization`, `test_llm_slot_views`, `hal0_llm_slot_views`). Removed/updated — slot identity is the name.
- **Curated catalogue guard for FLM/NPU entries.** FLM-served curated models (empty `hf_repo`) no longer surface in the `pullable` catalogue bucket (nothing to HF-pull), and a new `CuratedModel` validator requires every entry to be deployable — HF coords (`hf_repo` + `hf_file`) or an `npu` tag with `recommended_slot="flm"`.
- **FLM provider `load_chat` UnboundLocalError.** The legacy `defaults` branch no longer leaves `load_chat` unset; chat defaults on (`NpuConfig.chat=True`).
- **CI lint gate.** Cleared 12 `ruff check` errors + reformatted 7 files (import sorting, duplicate imports, placeholder-less f-strings, dead `result` binding, `SIM103`/`SIM110`/`RUF034`) that had blocked release CI since v0.9.5.

## [0.9.5] — 2026-07-08

### Added

- **NPU chat-first seed (FLM container shape).** `NpuConfig.chat` now
  defaults to `True` so a bare `[npu]` section in a slot TOML is a chat-ready
  NPU slot out of the box. Operators opt out by setting `chat = false` when
  they want an asr-only or embed-only NPU slot. A new seed
  `installer/etc-hal0/slots/npu.toml` is included so `hal0 setup` can
  register a clean (no FLM model pinned, `enabled = false`) `hal0/npu`
  tile on fresh boxes. The EditSlotDrawer now hides the Model field for
  `device = "npu"` slots (the NPU capability matrix replaces it).

### Changed

- **Slot routing key is now the slot `name`, not `role` (ADR-0023 §2.1).**
  The legacy `role` field on `SlotConfig` is gone — slot identity IS the
  routing key for `hal0/<slot>` aliases. Resolution chains in
  `src/hal0/normalize/resolver.py` now use `_slot_matches_name` (case-
  insensitive exact match on `name`), with one silicon-class escape
  hatch: the special name `npu` additionally matches any slot with
  `device == "npu"`, so a container that calls the trio's chat edge by
  a different slot name (`flm`, `npuchat`, …) still answers `hal0/npu`.
  Operator-custom seeds (`saber-fpx`, `deckard-fpx*`) and live slot
  TOMLs that used the old `role` tag now resolve by name. Live installs
  with the old `role` field in any slot TOML continue to parse (the
  field is parked under `extra` via Pydantic) and resolve to the slot
  by its `name`.

### Highlights

- **New canonical ROCmFPX runner: `ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5`** —
  built from `Hal0ai/Hal0_ROCmFPX@5b395660` plus a 4-line upstream cherry-pick
  that wires the **minicpm5** pre-tokenizer (upstream PR #23384). Unblocks
  loading the BRAINTRAIN-1B GGUFs on Strix Halo (the `tokenizer.ggml.pre =
  minicpm5` field now resolves correctly). The new image serves both ROCm/HIP
  and Vulkan backends from a single artifact; this consolidates the
  previously-3-tag ROCmFPX image family into one.
- **Slot-level image override** — `slot.image` (top-level string in the slot
  TOML) now overrides the profile's `image`. Resolution order is
  `slot.image` → `profile.image` → `DEFAULT_ROCMFPX_IMAGE`. Future image
  bumps will be a code-only release (one constant in `schema.py`); operators
  no longer have to chase profile clones to keep their stack current. The
  EditSlotDrawer now exposes an **Image** form-row (free-form text input
  with a live `will use:` preview of the effective ref, plus a Reset
  button) so operators can pin/clear the override without editing TOMLs.

### Breaking

- **SEED_PROFILES reshuffled to a clean 2x2 grid.** The three old ROCmFPX
  runner slugs (`rocmfpx-rocm`, `vkfpx-moe`, `vkfpx-dense`) are replaced by
  four explicitly-named profiles that match the 2x2 (backend x {dense,moe})
  matrix. The names are self-describing — the backend is the prefix, the
  weight format is the suffix:

    | Old slug         | New slug         | Backend | Format   |
    |------------------|------------------|---------|----------|
    | `rocmfpx-rocm`   | `rocm-dense`     | ROCm0   | ROCmFP4  |
    | (new)            | `rocm-moe`       | ROCm0   | ROCmFPX  |
    | `vkfpx-dense`    | `vulkan-dense`   | Vulkan0 | ROCmFP4  |
    | `vkfpx-moe`      | `vulkan-moe`     | Vulkan0 | ROCmFPX  |

  All four reference `ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5`
  (the new canonical runner). `rocm-moe` is a NEW profile — pre-0.9.5 the
  ROCmFPX MoEQuality format was Vulkan-only; this adds the ROCm0/HIP
  variant for operators who want a single-backend fleet.
- `profile.image` is now **deprecated** for new code paths. It still works as
  a fallback when no slot override is set, but the plan is to drop it from
  `SEED_PROFILES` entirely in **0.9.6**. Operator-custom profiles that
  override `image` (e.g. `saber-fpx`, `rocmfpx-rocm-custom`, `vkfpx-moe-custom`,
  `deckard-fpx*`) should migrate to per-slot `image` instead — a single
  slot TOML is a single grep hit, a profile clone is a maintenance liability.

### Migrations

- **Re-pin your custom profiles' images via the slot, not the profile.** Edit
  `/etc/hal0/slots/<name>.toml` and add a top-level `image = "ghcr.io/hal0ai/
  hal0-rocmfpx:vulkan-minicpm5"` line. The slot-level value wins on next
  `hal0 slot <name> restart`. Once your slots are migrated, you can remove
  the `image` line from your operator-custom profile in `profiles.toml`.
- **Rename operator-custom profile references in slot TOMLs.** If your slot
  points at one of the old slugs (`profile = "rocmfpx-rocm"`, `"vkfpx-moe"`,
  `"vkfpx-dense"`, or a `-custom` clone of those), update it to the matching
  new name (`"rocm-dense"`, `"vulkan-moe"`, `"vulkan-dense"`, or your
  custom variant of the new name). Example: `nano.toml`'s `profile` field
  should change from `"vkfpx-moe-custom"` → `"vulkan-moe-custom"` (and your
  `vulkan-moe-custom` profile in `profiles.toml` should mirror the new
  `vulkan-moe` seed's image + flags).
- **Verify the new image pulls cleanly on a non-production slot first**:
  `hal0 slot <name> image ghcr.io/hal0ai/hal0-rocmfpx:vulkan-minicpm5 && hal0
  slot <name> restart`. Then `curl :PORT/v1/models` to confirm the slot
  launched the new image (`system_fingerprint` carries the upstream commit
  hash for traceability).
- **The `image_mismatch` warning clears naturally** — it was a symptom of the
  old `profiles.toml` carrying stale image refs; once slots own their image,
  the mismatch can only happen on a manual `image` edit.

## [0.9.4.1] — 2026-07-08

### Changed

- **Idempotent `hal0 setup`** — removed the install-closed guard that blocked
  re-running `hal0 setup` after the first-run sentinel was written (#1161).
  The three provisioning endpoints (`/apply`, `/apply-selections`, `/complete`)
  are now idempotent and can be invoked at any time, fixing a Python traceback
  when `install.sh` re-launched the interactive setup after the `--auto` seed.

### Docs

- **README updated** — corrected version, slot list, setup flow description,
  and agent selection docs to match v0.9.4 reality.

## [0.9.4] — 2026-07-08

### Added

- **Downloads pane** in footer with pull job tracking (#1165).
- **Settings page reorganisation** with improved layout and navigation (#1163).
- **Configurable `direct_read_timeout`** via `[dispatcher]` TOML section (#1160).

### Fixed

- **NPU double-free**: stop FLM health poll from double-freeing the NPU slot (#1077).
- **Vision models**: add missing `mmproj_file` to curated vision model entries (#1162).
- **Memory tools UI**: make documents card full-width (#1166).
- **CI repair**: fix test failures post-settings-reorg (#1163).
- **Lint**: ruff formatting and `__all__` ordering across several files.

## [0.9.3] — 2026-07-06

**The Guided Setup.** hal0 setup grows a review-gated TUI, headless answer
files, hardware preflight, and a pick-free first-run workflow. The installer
no longer ships model recommendations — every box starts clean and the
operator chooses models interactively.

### Highlights

- **Guided Stage-2 setup TUI** with a review gate and two-stage handoff:
  hardware detection, model selection, capability scaffolding — all
  reviewed before writing config.
- **Headless answer files** — `hal0 setup --emit-answers` captures
  selections as JSON; `hal0 setup --answers <file>` replays them on
  another box. Combine with `--yes` for fully automated provisioning.
- **Pick-free install.** The installer no longer seeds model slots.
  Instead, the guided TUI (or headless answers) scaffolds capability
  slots and walks the operator through model selection interactively.
- **Hardware preflight.** GPU detection runs during install with an
  LXC smart-block and dev0 remedy; NPU functional state is persisted
  in a hardware.json written at install time.
- **hal0 doctor --verify** — a structured report card checking config,
  slots, models, and service health.

### Added

- **Guided Stage-2 setup TUI** with review gate + two-stage handoff (#1144).
- **Headless answer files** — `hal0 setup --answers` / `--emit-answers`
  round-trip (#1119, #1120).
- **`hal0 setup --plan` / `--dry-run` preview** (#1121).
- **GPU preflight during install** — LXC smart-block + dev0 remedy (#1135).
- **Authoritative hardware.json** persisted at install incl. NPU functional
  result (#1118).
- **Network coherence** — one `HAL0_BIND_HOST` + seeded origins (#1130).
- **HF_TOKEN gathered + persisted** to `secrets/` EnvironmentFile, threaded
  through in-process apply_setup (#1136, #1122).
- **`hal0 doctor --verify` report card** — config, slots, models, service
  health (#1145).
- **Post-update drift surfacing** + `hal0 update --restart-slots` (#1142).
- **Slot-render reconcile seam** shared by install + update (#1138).
- **Safe slot activation** — enable-on-pull-success + clamp context (#1137).
- **Clean seeded slots** — no model pins, derived device (#1140).
- **NPU opt-in** threaded through suggest+apply; NPU introduction gated on
  hardware present + healthy (#1134).
- **Capability + NPU slot scaffolding** — pick-free, guide models
  interactively (#1091).
- **ComfyUI gen branch** — scaffold-only default + per-variant download
  picker; repaired model fetch + shipped curated workflow JSONs (#1146,
  #1128).
- **First-run model workflow** — `hal0 models scan` / `add` / `store` /
  `run` + `hal0 doctor models`.
- **Pick-free install** — installer seeds zero model slots; the operator
  chooses via the guided TUI or headless answers (#1104, #1140).
- **Co-locate `[models].flm_store`** + free-space validation (#1132).
- **Honor `Selections.storage_dir`** — thread to `[models].store` (#1127).
- **Close `/api/install/*` provisioning** after first-run sentinel (#1126).
- **Apps skip/defer parity** — `openwebui` install verb + gateway in
  deferred hermes (#1125).
- **Converge `/apply` onto `/apply-selections`** + write first-run sentinel
  from endpoint (#1124).
- **Platform-gate hardening** — bootstrap-prereq parity, disk-on-store,
  early hal0 user (#1139).
- **Runtime `advertise_models` toggle** for upstream catalog entries (#1152).

### Fixed

- Dashboard URL no longer leaks `hal0.thinmint.dev` as default (#1092).
- Advertise all enabled LLM slots in `/v1/models` discovery, not just loaded
  ones (#1153).
- Support flat slot TOML shape in profile in-use scanning (#1129).
- Don't persist hardware.json on `--plan`/`--emit` preview paths (#1131).
- Delete stale `avahi/hal0.service` systemd unit (#1123).
- HF vision mmproj sidecars (`.mmproj`) + remove backend-switch surface (#1089).
- Seed `[models].store`, create FLM cache dir, add GPU/NPU preflight.
- Rescan models immediately when the store path changes.
- Honor `[models].flm_store` config + make NPU slot bind source
  reboot-durable.
- Auto-resolve a Hindsight-compatible Python instead of a raw pip wall.
- Don't write through the seeded uname symlink in prereq-parity tests (#1143).
- **HERMES.md.j2 rendering** — fix crash on empty env snapshots (Jinja2
  `Undefined.__getattr__` raises immediately, bypassing `|default` filters).

### Migrations

- First-run sentinel closes `/api/install/*` — a one-way gate at install
  time. The guided TUI or headless answer files are the canonical path
  for future configuration changes.

## [0.9.2] — 2026-07-05

Hotfix: restore the full model listing in every slot's model picker.

### Highlights

- Fix collapsed slot model dropdowns — `/api/models` rows again advertise the dispatcher-vocab `type` (`llm`/`embedding`/`reranking`) the pickers join on.

### Fixed

- **Slot model dropdowns (and the model→slot compatibility list) showed only the currently-assigned model.** `/api/models` stamped local- and upstream-registry rows' `type` with `classify()`'s coarse modality bucket (`chat` / `embed` / `rerank`) instead of the dispatcher vocabulary (`llm` / `embedding` / `reranking`) that the FLM path already emitted and that the UI joins on (`model.type === slot.type`), so every local model failed the picker filter and each dropdown collapsed to its default. Map the modality bucket → dispatcher type at both stamp sites; adds a local-row regression test (the FLM path was already covered, which is how this slipped through).

## [0.9.1] — 2026-07-05

ROCmFPX llama.cpp runner support, plus a safer notes-aware self-update.

### Highlights

- ROCmFPX runner support: GGUF quant-family detection, `rocmfpx-rocm` / `vkfpx-moe` seed profiles, and a build/quantize agent skill.
- `hal0 update` now shows cosign-verified release notes and asks before applying (staged prepare → commit).

### Added

- **Updater `prepare` / `commit` split.** `hal0 update` downloads + cosign-verifies + extracts a release and shows its notes — with breaking/migration callouts — *before* activating anything; `--yes` skips the prompt for headless/cron. Adds `POST /api/updates/prepare` + `/commit` (#1075).
- **Release notes in the update.** The release build bundles `RELEASE_NOTES.md` + `release.json` into the cosign-verified tarball; a CHANGELOG section's `### Highlights` / `### Breaking` / `### Migrations` subsections become the `hal0 update` callouts (#1078).
- **ROCmFPX quant detection** — the registry classifies the `ROCmFPX` / `ROCmFP{3,4,6,8}` quant family from a GGUF filename so FPX slots resolve their launch command (#1068).
- **ROCmFPX seed profiles** `rocmfpx-rocm` (ROCm0 dense) and `vkfpx-moe` (Vulkan0 MoE) for the custom ROCmFPX runner (#1069, renamed in #1076).
- `vkfpx-dense` seed profile — the Vulkan0 lane for DENSE ROCmFP4, for prefill-bound dense workloads (Vulkan wins prompt-processing); complements the decode-optimal ROCm0 `rocmfpx-rocm`.
- **`hal0-quantize` agent skill** — build the ROCmFPX toolchain and quantize a model to ROCmFP4/FPX (#1071).
- ROCmFPX bench tooling: server-ab (MTP / concurrency) aggregation in the benchmark SUMMARY, FPX sweep cells, run provenance (#1072).
- **Continuous batching — per-slot `parallel` field.** A slot can now set
  llama-server's `--parallel` / `-np` sequence-slot count so concurrent
  requests share the once-loaded weights instead of serializing through a
  single sequence and thrashing one prompt cache (the win the shared-slot
  architecture already earns but never harvested — every seed profile pins
  `--parallel 1`). `None` inherits the profile; a value >1 also emits
  `--kv-unified` so `--ctx-size` stays a SHARED pool (each request may use the
  full context) rather than being silently split to ctx/N per slot. Emitted as
  a slot override (beats the profile, loses to hand-authored `extra_args`);
  surfaced in the slot drawer with a shared-pool hint. The dead haloai
  `workers` field is deprecated (inert; a non-default value now logs at
  launch). MTP x batching runs but logs `mtp.batched_speculation` (unproven on
  gfx1151, bench-gated). Seed-profile defaults stay `--parallel 1` pending the
  on-box `-np` sweep (`server_ab.py --mode batch`). See the
  concurrency-batching plan handoff.

### Changed

- The plain `rocm` / `vulkan` seed profiles are reduced to basic flags
  (`-ngl 999 -fa on --jinja`); per-model KV/batch tuning now lives in the
  model's `defaults.extra_args` (#1076).
- Seed-profile `intent` labels normalised to terse structural tags
  (e.g. `ROCmFPX · DENSE · MTP`, `VULKFPX · MOE · MTP`, `ROCm`, `Embeddings`) —
  no served-model names, no filler.
- Slot units are re-rendered through the new code during an update's `commit`
  step, so a subsequent restart uses current argv (#1075).

### Removed

- Legacy MTP toolbox seed profiles `rocm-moe` and `rocm-dnse` (superseded by the
  ROCmFPX profiles); `rocmfpx-moe` renamed to `vkfpx-moe` to indicate its Vulkan
  lane (#1076).

### Migrations

- Slots pinned to a removed/renamed seed profile (`rocm-moe`, `rocm-dnse`, `rocmfpx-moe`) auto-fall-back to the backend's basic profile (`rocm` / `vulkan`) on launch — existing slots keep working with no operator action (#1076).

## [v0.9.0] — 2026-07-04

**The first public-beta cut.** hal0 graduates from the b-tagged 0.8.x
line: the dashboard gets its redesigned fixed-band layout and a live
telemetry header, seed profiles carry flags from a measured Strix Halo
bench matrix (plus a new per-model-family override layer), MCP servers
are manageable from the CLI, and the Memory view becomes a per-bank
workspace. **Safe upgrade from v0.8.5b2** — no on-disk migrations in
this cut. Profile flag changes (re-tune, `FAMILY_DEFAULTS`) land on each
slot's next restart; thanks to the v0.8.5b2 auto unit re-render, any
restart path picks them up.

### Added

- **Dashboard redesign — fixed-band layout with swap-in-place widgets
  (#1061).** The free-form drag/resize grid is replaced by a fixed
  vertical band stack: hero strip (steady-on + quick actions), 5-cell
  health strip, a full-width **Unified Memory hero** (slot allocations
  drawn inside the pool bar, striped system block, Proxmox-host block
  when configured), Throughput / Utilization / Requests band, locked
  dense slot rows, and an Activity / Services / Needs-Attention band
  with inline actions. Customization is swap-in-place per cell (layout
  v3 via the existing `PUT /api/user/dashboard-layout`, fail-soft to
  defaults on old payloads). Ships a new **Requests & Latency** widget
  against a new `/api/stats/requests` endpoint (gates to "source
  pending" until the dispatcher rollup ships).
- **Telemetry header on the Slots page (#1059, #1062, #1064).** One
  combined live-metrics card replaces the old hero band: throughput
  hero + 20-bucket spark, GPU semicircle gauge (sclk/temp/watts), CPU +
  memory gauge, and the NPU 4×8 occupancy grid with per-slot owner
  hues — above a full-width memory rack ruler in the #1061 memory-hero
  style (in-bar allocations, live tok/s on serving segments, click-through
  to the slot). Honest-data rules throughout: missing metric → em-dash,
  measured zero renders as 0.0 with the serving count, GPU util captioned
  "pinned" when forced high. Container queries keep the 4→3→2→1 column
  wrap gap-free.
- **`hal0 mcp` CLI surface** (#504) — `hal0 mcp {list,status,install,uninstall,restart,catalog}`
  backed by the existing `/api/mcp/*` routes. Rich tables with `--json` flag for
  machine output. The `restart` subcommand surfaces the 501 supervisor-stub
  gracefully until ADR-0015 lands.
- **`FAMILY_DEFAULTS` — per-model-family launcher-flag overrides.** A new
  resolution layer between a profile's generic flags and a slot's own
  `[model].defaults`, keyed on model family (matched from the id/filename).
  Applied automatically at slot resolution (launch + preview parity) and
  collapsed by `normalize_argv` last-wins, so a family override beats the
  profile but a per-slot `[server].extra_args` still beats the family. First
  tenant: **gemma → `-ctk f16 -ctv f16 --cache-reuse 0`** — any gemma model on
  any q8 profile is pinned back to f16 KV (gemma iSWA regresses on quantized KV:
  measured -28.5% pp on RADV / -10% tg on rocm, plus SWA+cache-reuse bugs
  #21468/#21749). This fixes the live gemma-on-`rocm-dnse` regression and makes
  adopting Vulkan q8 KV safe as a follow-up.
- **Configurable slot publish host — `[slots].publish_host` (#1058).**
  Slot containers published on 127.0.0.1 only; raw slot ports were
  reachable solely through hal0-api/Traefik. A first-class, UI-settable
  config key (default `127.0.0.1`, unchanged behavior) lets an operator
  widen to `0.0.0.0` or a specific interface IP. Fail-soft: an
  unresolvable value falls back to loopback, never opens the box. Baked
  into ExecStart, so live slots re-bind on their next restart; the
  Settings row carries a loud LAN-exposure warning.

### Changed

- **Memory Overview is a per-bank workspace (#1057).** Selecting a bank
  drives one combined primary card — retained-memories spark, graph
  extraction panel, embedded Tools (recall · reflect · documents ·
  mental models · directives), async operations, danger zone. The
  standalone `#memory/tools` route is retired; documents paginate and
  show composed titles instead of raw UUIDs; mental models and
  directives gain create forms.
- **The MTP control is always visible on llm slots (#1054).** Hiding the
  row for ineligible models made the tri-state undiscoverable. The slot
  drawer now always renders it, with a reason line for Auto·off ("model
  has no MTP heads" / "profile doesn't enable MTP") and a
  launch-will-fail warning when forcing On for a model without
  advertised heads. Stack editor rows follow the same contract.
- **Activity sidebar rework (#1063).** Taller pane, stacked
  timestamp/actor meta reclaiming horizontal space, and the free-text
  search replaced by a slot filter dropdown (exact target match,
  server-side pre-narrowed).
- **Seed profiles: bench-driven flag re-tune (Strix Halo matrix, 2026-07-04).**
  `rocm-moe` micro-batch `-ub 2048` → `-ub 1024` (+30% prompt-processing on
  Qwen3.6-35B-A3B-MTP: 1165 vs 895 t/s pp2048, consistent across all `-b`;
  token-gen flat ~47). `vulkan` `-ub 512` → `-ub 256` (+5.4% pp; the reported
  1024 sweet spot measured *worse*). Dropped `--threads-batch 32` and
  `--poll 100 --poll-batch 1` from the rocm chat profiles (measured within noise
  at full offload — simpler flags win ties). Added explicit `-ngl 999` to all
  GPU LLM profiles (GTT/unified free-mem autodetect is unreliable) and `--jinja`
  to all LLM profiles. Decode throughput is unchanged (all wins are prefill), so
  `PROFILE_BENCH` hero numbers stand. MTP draft depth measured `n-max 4` optimal
  (+23% decode vs n-max 2 on dense MTP) — seeded default kept.
- **Vulkan seed adopts symmetric q8 KV** (`-ctk q8_0 -ctv q8_0`): +45% pp at 32k
  depth on qwen (168 vs 116 t/s) and halves KV memory. It is the mirror image on
  gemma (gemma-4-12B @32k: q8 costs -28.5% pp on RADV), so the vulkan profile is
  no longer intrinsically gemma-safe — it relies on `FAMILY_DEFAULTS["gemma"]`
  pinning gemma slots back to f16 KV. (The upstream "~10x pp cliff" did NOT
  reproduce on this fork.)

### Fixed

- **OpenRouter OAuth callback route is gated behind
  `HAL0_OPENROUTER_OAUTH_ENABLED` (#775).** The callback endpoint no
  longer registers unless the flow is explicitly enabled, closing an
  unauthenticated surface on installs that never use OpenRouter OAuth.
- **Capability slot mini-cards regained Logs/Edit buttons (#1055).** The
  utility-tier (embedding/reranking/tts/transcription) cards rendered
  compact controls with no way to edit or tail a capability slot created
  via the UI.
- **Hermes memory identity defaults to `hermes` (#1056).** The upstream
  plugin base defaulted to `hermes-agent` on env-less code paths,
  spawning a stray duplicate `private:hermes-agent` bank alongside the
  correct `private:hermes`. Reads/writes now consistently target
  `private:hermes`.
- **Telemetry throughput no longer flaps to "source pending" on an idle
  box (#1062).** An empty 100-second history window is a measurement
  (0.0 with the live serving count), and the spark keeps the last 20
  measured buckets on screen; "source pending" is reserved for a
  genuinely missing source.
- Removed a dead duplicate `SELF_MANAGED_PROVIDERS` constant from
  `kokoro.py` (#982).

## [v0.8.5b2] — 2026-07-04

Hotfix over v0.8.5b1, closing the three findings from the first live
update+verification pass on Strix Halo hardware (CT105): stale `mtp = true`
overrides crashing slots on re-render, slot units not re-rendering on update,
and the gateway missing `POST /v1/rerank`. **Safe upgrade from v0.8.5b1** —
the one migration (crash-only MTP override defuse) touches exactly the slot
configs that could not have loaded anyway, with a loud per-slot log.

### Added

- **Slot units re-render automatically on update.** A slot's systemd unit
  bakes the launch argv at load time, so updating hal0 changed the code that
  WOULD render but not the file that DID — `systemctl restart`, crash
  restarts, and reboots kept running pre-update flags until an operator did a
  hal0-level slot restart (field finding). The updater (post venv-reinstall,
  via a fresh interpreter so the NEW code renders) and `install.sh` now
  rewrite every existing unit through current code plus one `daemon-reload` —
  running services are never bounced; fresh argv applies on each slot's next
  start from any path. Per-slot failures log and skip. The dashboard drift
  indicator still covers the "process running old argv until next restart"
  window.

### Fixed

- **Gateway now serves `POST /v1/rerank`.** The dispatcher's capability path
  map already resolved `/rerank` to the rerank slot, but the gateway only
  registered `/v1/rerankings` — so clients using llama-server's / Jina-style
  `/v1/rerank` got 405 and had to hit the slot port directly (field finding).
  `/v1/rerank` is now an alias of `/v1/rerankings` through the same dispatch
  path.
- **Crash-only `mtp = true` overrides are defused automatically.** A forced
  MTP override pointing at a model with no MTP heads crashes llama-server at
  load once the slot's unit re-renders under the v0.8.5b1 MTP separation
  (field-confirmed: pre-separation `mtp = true` debris on a headless MoE
  model). Two mechanisms now clear exactly that combination: an updater
  migration over the slot TOMLs (`updater.mtp_force_on_cleared` log; force-off,
  eligible force-on, and unresolvable models untouched) and a swap-path guard
  (swapping onto an ineligible model drops a forced `true` → AUTO, so the
  staleness can't regenerate). The false-negative escape hatch — forcing MTP
  on for an untagged-but-capable model — is preserved.

## [v0.8.5b1] — 2026-07-04

Everything landed on `main` since the v0.8.4b1 cut. The headlines: hal0
generalizes beyond the Strix Halo iGPU (experimental CUDA + multi-GPU
pinning), companion services get one management surface (registry +
`/api/services` + mDNS + dashboard page), the settings-completeness plan
finishes (phases 3–5 + an Advanced section with full `hal0.toml` parity),
and seed profiles go virtual with dedicated embed/rerank lanes and a
proper model×profile×slot MTP decision (#1045). **Safe upgrade from
v0.8.4b1** — the one on-disk migration (virtual-seed prune) backs up
`profiles.toml` first and rescues divergent operator content to `-custom`
names; note the MTP auto behaviour below if you run untagged local MTP
builds.

> **Upgrade note — re-render slot units after updating.** A container
> slot's systemd unit bakes the launch argv at load time, so after an
> update the running slots (and their unit files) still carry the
> PRE-update flags. A bare `systemctl restart hal0-slot@<name>` re-runs
> the stale ExecStart — restart slots **through hal0** (dashboard restart,
> or unload→load) so the unit re-renders through the new code. The
> dashboard's resolved-command drift indicator shows which slots are
> stale. Automatic unit re-rendering on update (without bouncing serving)
> is planned as the follow-up.
>
> **Upgrade note — stale `mtp = true` slot overrides crash on re-render.**
> An explicit `mtp = true` in a slot TOML is honored literally (it is the
> escape hatch for MTP-capable models the eligibility heuristics miss). If
> a stale override — typically left behind by the old binary MTP pill or a
> pre-#1045 stack apply, surviving a later model swap — points at a model
> with NO MTP layers, llama-server exits at load ("context type MTP
> requested but model doesn't contain MTP layers") once the unit
> re-renders. Fix: set the slot's MTP to Auto (`{"mtp": null}`) or Off in
> the drawer and restart. An updater migration that clears provably-stale
> force-ons (with a loud log) ships in the follow-up.

### Added

- **GPU generalization — experimental CUDA + multi-GPU.** A dedicated
  `cuda` seed profile (upstream `llama.cpp:server-cuda` image, preferred
  by the installer when NVIDIA CDI is present, Vulkan fallback otherwise)
  and per-slot `gpu_index` pinning for multi-GPU hosts. Ships alongside
  dead-path retirement and **multi-file pulls** (a model's mmproj/vision
  sidecars download with the main GGUF in one job).
- **Unified companion-service management (#1037).** A code-level service
  registry (Open WebUI, ComfyUI, Hermes, Hindsight, n8n), `GET
  /api/services` + allow-listed lifecycle actions, **mDNS advertisement**
  of addon services (`hal0-addon-<id>.service` files, avahi inotify
  pickup, `HAL0_HOSTNAME` precedence), and a dashboard **Services** page
  (cards, logs drawer, ComfyUI queue drawer, fail-soft probes).
- **Settings completeness, phases 3–5.** TTS request defaults
  (`default_voice` / `default_speed` / `default_response_format`) seeded
  into `/v1/audio/speech` plus a live voice list proxied from the `tts`
  slot (#1038); a Settings **NPU** section (#1040); ComfyUI
  `idle_restore_minutes` hot-reload (no API restart) and a workflow
  listing endpoint + dynamic strip (#1043).
- **Advanced settings section.** Full `hal0.toml` parity in the dashboard
  (every config key editable, grouped, with descriptions), a memory-graph
  panel, and an API restart button; an AWS secret-pair preset and a
  reload-config-from-disk button; previously-inert config keys wired
  through, and the memory schema aligned to the Hindsight era.
- **Dedicated `embed` and `rerank` seed profiles (#1045).** GPU llama-server
  templates that bake in the serving flags (`--embedding` / `--reranking`,
  `-ub 8192` so a full input fits one physical batch) so an embedding or
  reranking slot no longer hand-wires them in `extra_args`. On a `gpu-rocm`
  box, embed/rerank capabilities derive onto these lanes automatically
  (install path and picker/apply fit path both updated); Vulkan/CPU boxes
  keep falling back to the `vulkan` / `cpu-llm` profile until
  backend-specific variants ship.
- **Profile bench matrix tooling (#1045).** `installer/bench/profile-matrix.sh`
  scripts the seed-profile re-tune matrix as `hal0-benchctl` seam sweeps, and
  `installer/bench/server_ab.py` measures the server-level levers llama-bench
  can't see — MTP draft depth (with acceptance %), `--cache-reuse` on a
  shared-prefix trace, poll, and embed/rerank endpoint sanity — via hal0-api
  as the unprivileged user, always restoring the slot's original config.
  Supersedes the ad-hoc `/root/bench_mtp.py`; an on-box runbook ships at
  `handoffs/bench-profile-matrix-local-session-2026-07-04.md`.
- **Catalog UX finish (#1042).** Sort / tag-filter / quant chip wiring in
  the Models view, and a chat-template pick at pull time.
- **Canonical device/backend taxonomy.** One enum source at `GET
  /api/meta/enums` (ETag/cache-friendly), consumed by the dashboard —
  plus stacks fixes and dialog guards that rode the same change.

### Changed

- **Seed profiles are now virtual (#1045).** The built-in profile catalog
  (`SEED_PROFILES`) is overlaid from code on every load and never persisted to
  `/etc/hal0/profiles.toml`. Previously the installer materialised every seed
  inline and the loader only injected *missing* seeds, so a re-tuned seed (new
  flags, a bumped toolbox image) never reached an existing install. Now the code
  definition always wins: `load_profiles_config` overlays seeds over any on-disk
  copy, `save_profiles_config` strips seeds before writing, and the updater's
  `ensure_seed_profiles()` prunes any materialised seeds left by an older
  install (self-heal on upgrade). Seed profiles remain immutable — clone to
  customise. Operator (non-seed) profiles are untouched. **Data-safe
  migration**: the pre-prune file is backed up once
  (`profiles.toml.pre-virtual-seeds.bak`) and any seed-named entry whose
  content differs from the code seed (a hand-edited seed table, or an operator
  profile whose name only became a seed in this release, e.g. `embed`) is
  rescued to `<name>-custom` instead of deleted, with a loud log.
- **MTP is now a model × profile × slot decision (#1045).** Model
  eligibility (`mtp` registry tag or name marker) × profile opt-in
  (`profile.mtp` now means "enable for eligible models", not "append the
  bundle regardless") × a tri-state per-slot override (Auto/On/Off, Auto
  = profile opts in AND model eligible). A non-MTP model on an MTP
  profile no longer launches with dead `--spec-draft-*` flags, and the
  draft device tracks the profile backend (ROCm/Vulkan/CUDA) instead of
  hardcoded ROCm. The slot drawer swaps the binary MTP pill for the
  tri-state control with a live "Auto · active/inactive" hint; stack
  editor rows default to Auto, and an Auto row now clears a forced
  override on apply (the config write layer treats an explicit `null` as
  delete-key — TOML has no null — which is also what makes "back to
  Auto" work from the dashboard instead of 500ing). **Behaviour note:**
  an MTP-capable model that carries neither the registry `mtp` tag nor an
  MTP name marker stops speculating under Auto after this upgrade — tag
  the model or force the slot On; the launch log says
  `mtp.auto_off_model_ineligible` when this bites.

### Fixed

- **MTP auto-off breadcrumb is launch-gated.** The
  `mtp.auto_off_model_ineligible` hint lives inside the shared launch/preview
  scalar resolver, so it fired on every dashboard `GET /api/slots` poll
  (~0.4/s per client, forever) for any AUTO slot pairing an MTP profile with a
  non-MTP model. It now logs only on a real container launch; preview/status
  renders stay silent, and launch/preview argv parity is unchanged.
- **Upstream-advertised models are clearly identified as remote (#1035)**,
  not local, across the dashboard model surfaces.
- **Operator Board (#1032).** Hermes-contract repairs, honest UI state,
  and platform-assistant chat.
- **Slot pipeline hardening.** API boundary validation, backend-switch
  completion, manager guards, and guarded stack writes; a single argv
  assembler with model-defaults wiring and provider fixes; normalizer
  bug, dead-code removal, and a11y quick wins in the slot drawers.
- **Settings polish.** Truthful apply plan, safe engine picker, secret
  descriptions, rollback behaviour, and palette ghosts.

### Docs

- README re-baselined to v0.8.4b1 + full accuracy pass (#1044, #1046):
  canonical `agent`/`utility` seeded slots, real backend-profile and
  hardware-tier tables (experimental CUDA row), removed the
  no-longer-shipped `HAL0_USER` unprivileged mode, added the **Discord**
  invite (header + Contributing).
- hal0.dev docs mirror refreshed (#1044): new `operate/services` page,
  `operate/auth` rewritten to the real ADR-0012 reverse-proxy model (the
  fictional `--auth=basic` / managed-Caddy docs from 3e056de removed
  site-wide), plus the full v0.8.x feature-doc sweep.
- Handoffs: platform reliability/config/UI review (#1010), ONNX / Strix
  Halo NPU research and integration plan (#1034), llama.cpp seed-profile
  evaluation + consolidation proposal (#1041).

## [v0.8.4b1] — 2026-07-04

A **models, logs & memory** follow-up to v0.8.3b1. Models can now carry a
preferred runtime profile that loads with them, image-gen/ComfyUI models get
their own properly-tagged surface, the slot context window finally persists
across reloads, the logs/events system is unified, and the memory subsystem
gains a destructive-op audit trail plus console shape guards. **Safe upgrade
from v0.8.3b1 — no breaking changes; all additions are additive.**

### Added

- **Model preferred profile.** A registry model can declare `defaults.profile`
  — the runtime profile it wants loaded with it. A slot adopts it on create
  (when it has no explicit profile) and on **every model swap**, gated on
  device/type compatibility (an incompatible preference is ignored and the slot
  keeps its device-default profile; slot hardware is never flipped to satisfy a
  model). Surfaced as a **Preferred profile** selector in the model recipe
  editor.
- **ComfyUI / image-gen model surface.** The Models view gains a **Models |
  Image/ComfyUI** segmented toggle; image-gen models are grouped by their
  models-tree category (checkpoints/loras/vae/upscale_models/…) and kept out of
  the dispatcher list. ComfyUI models are correctly tagged `image`/`comfyui` at
  every registration path, and `/api/models` self-heals rows an older pull
  mis-tagged by deriving the ComfyUI category from the on-disk path (no
  migration needed).
- **Memory: audit trail for destructive ops (#1024).** Every destructive
  `/api/memory/*` op — bank delete, and memories/config/document/directive/
  operation/mental-model deletes, plus the namespace `POST /api/memory/delete`
  — now records a durable audit row (actor + target + truthful outcome) via the
  shared `record_action` facility, so a memory wipe is attributable after the
  fact. Complements the bank-DELETE `?confirm=` guard shipped in #1028.
- **Memory: response-shape guard on the cognition consoles (#1026).** The
  recall/reflect/directives passthroughs now validate the load-bearing envelope
  key (`results`/`text`/`items`); upstream Hindsight shape drift surfaces as a
  loud `memory.engine_shape` 502 instead of a silently-blank console panel. The
  two colliding `recall` contracts (namespace `{items}` vs bank `{results}`) are
  now documented at both call sites.

### Changed

- **Unified logs/events.** Restores per-slot model-load logs, real source/slot
  attribution, and a channel selector across the logs/events surface.
- **Memory Overview UI.** The graph-extraction gate now sits beside a shrunk
  "memories retained" spark in the top row; card headings share one unified
  "eyebrow" style; dropped the stray `ADR-0023` label from the extraction title.

### Fixed

- **Persistent slot context.** The slot edit drawer seeds the context field
  from the persisted `[model].context_size` (not the live runtime metric or a
  hardcoded 16384) and only writes `ctx_size` when it actually changed, so an
  unrelated save no longer clobbers the stored context window with 16k on a cold
  reload or swap.
- **Don't surface invisible models.** FLM tags advertised by the composite
  upstream before their weights are pulled are dropped from the catalog (the
  dedicated probe still surfaces the genuinely installed ones); freshly-pulled
  ComfyUI checkpoints are no longer mis-filed as chat models.
- **Memory tab functional; bank delete guarded (#1028).** The Memory tab renders
  its graph status/slot UI and consolidate/list actions correctly, and a bank
  `DELETE` now requires an explicit `?confirm=` guard.
- **Stack edit drawer (#1023).** Slot cards render as labeled multi-line
  entries and the escaped toggle-knob glyph is fixed.
- **Board: Hermes kanban task-detail drawer (#1014).** The task-detail envelope
  from Hermes is unwrapped so the board drawer renders instead of showing empty.
- **Installer: cosign optional for the one-line install.** The bootstrap no
  longer hard-requires `cosign`, so the one-line installer runs on hosts without
  it (signature verification still applies where `cosign` is present).

## [v0.8.3b1] — 2026-07-04

A large **reliability and UI-completeness** release. The headline is a
72-finding platform-review remediation delivered as eight verified waves
(each finding regression-tested and gated by CI + Playwright), landing
alongside earlier staged fixes. It retires several silent-failure bugs,
adds cross-process safety and pull resumability, makes every
backend-supported value editable in the slot/model/profile drawers, and
surfaces live telemetry on the dashboard. **Safe upgrade from v0.8.2b4 —
no breaking changes; all config/UI additions are additive.**

The most user-visible behaviour changes: the dashboard now shows Power &
Thermal (live GPU clock/temp/power) and Per-Slot Throughput cards **by
default**; disabling a capability now genuinely stops it serving; and the
`bge` reranker is now classified and routed as a reranker.

### Added

- **Dashboard live telemetry, on by default.** The Power & Thermal card
  (GPU clock MHz, temp, power) and the Per-Slot Throughput card are now
  default-on, and the Utilization card shows a live clock/temp caption.
  (#1019)
- **Edit-drawer completeness.** The model editor now exposes
  `capabilities`, `backends`, `rope_freq_base`, `mmproj`, and
  `hf_repo`/`hf_filename`; slots gain a per-slot `vision` toggle and NPU
  `asr`/`embed` modality toggles — all fields the API already accepted but
  no drawer surfaced. (#1020)
- **Settings.** An opt-in anonymous telemetry toggle and the image-gen
  defaults (`default_size`, `default_steps`, `idle_restore_minutes`). (#1021)
- **Interrupted pulls resume** via HTTP `Range` (with `If-Range`) instead
  of re-downloading from zero; the on-disk prefix is re-hashed so the final
  SHA-256 stays exact. (#1017)
- **Disk-space preflight** before multi-GB pulls fails fast with a
  structured `model.insufficient_disk` error instead of filling the disk.
  (#1013)
- **Host-memory-pressure LRU eviction** of idle slots. (#1003)

### Changed

- **Retired duplicated logic** that had silently drifted: one
  device→profile derivation, one filename→capability classifier, one
  dispatchable-state predicate, and one slot-projection reconcile. (#1015)
- **Rebuilt the dash editing drawers** on a shared `FormDrawer` + `useForm`
  with an unsaved-changes dirty guard, one `compatibleModels` filter, a
  focus trap and real `<label>` wiring (a11y), plus honest, confirmed
  destructive actions (styled type-to-confirm deletes; no more "Pause"
  that silently cancels; real "fits in memory" check). (#1016, #1018)
- **Cross-process safety:** advisory file locks around registry and
  capabilities writes; parent-directory `fsync` after atomic writes. (#1017)
- **Housekeeping:** startup GC of stale pull-job snapshots and orphaned
  `.part` partials. (#1017)
- Extracted the capability-resolution heuristics out of `dispatcher/router.py`.
  (#1017)

### Fixed

- **Disabling a capability now sticks.** The disable is written through to
  the slot config, so a later request can no longer wake a "disabled" slot
  and serve from it. (#1011)
- **The NPU trio is advertised on podman-only hosts** — the picker probed
  `docker` and never offered it on the reference platform. (#1011)
- **GPU-less installs get a chat-capable primary slot** instead of one
  bound to the Kokoro TTS engine (`cpu` now defaults to `cpu-llm`). (#1011)
- **Idle-evicted embed/rerank/tts slots wake on request** instead of
  404'ing until a manual load. (#1011)
- **The `bge` reranker is classified as a reranker** (was mislabeled
  `chat`) and is routable as one. (#1015)
- **Installer/bundle pulls survive an api restart** — status/stream polls
  no longer 404 mid-install. (#1012)
- **A completed pull is no longer reported "failed"** after a restart. (#1012)
- **The GpuArbiter drain no longer unloads a slot under an in-flight
  request** (image-mode switch race). (#1012)
- **Slots are no longer advertised READY on a health-probe timeout.** (#1012)
- **Write-time validation:** a second `default=true` slot of a type is
  refused at save; `create()` no longer clobbers an existing custom slot;
  stack apply flags unresolved profile/model refs and reports **degraded**
  (not "clean") when slots fail to load. (#1013, #1018)
- **The model editor no longer silently wipes unshown launcher defaults on
  Save**, and the ComfyUI image-profile control no longer corrupts
  `device_class` on edit. (#1020)
- **Chat requests with mis-positioned or stacked `role='system'` messages
  no longer 500 the Qwen3.6-35B-A3B upstream.** The OpenAI-compat
  normaliser now collapses every system entry into one and hoists it to
  position 0 (matching the OpenAI/Anthropic convention), so a stale
  mid-array system message from the SPA/Open WebUI/LibreChat — or two
  deliberately-stacked system blocks — no longer trips the Qwen3 Jinja
  template's `System message must be at the beginning`. No-system payloads
  skip the copy. (#992)
- Additional staged fixes from the assessment sweep: dead-port guard for
  container slots (#1001), `events.gap` on subscriber-queue overflow
  (#1000), non-retryable `SlotLoadFailed` for ERROR slots (#999), bounded
  httpx pool + tighter read timeout (#998), cold-slot 404 ordering (#996),
  container slots entering SERVING and bumping `last_used_at` (#995),
  installer port reachability when Docker is co-installed (#990), updater
  rollback re-pip (#994), an in-memory PgVector write warning (#1008),
  additive seed-profile merge (#1007), durable pull-job persistence (#1006),
  canonical slot fields on npu/load + install model-update (#1009), and the
  yellow-halo favicon restore (#991).

## [v0.8.2b4] — 2026-06-30

Documentation and installer hygiene — no runtime behaviour change. This
release re-baselines the engineering docs to the current v0.8.x reality
(container runtime, Hindsight memory, agent/utility roles) and fixes a
handful of installer drift bugs surfaced by a codebase assessment sweep.
Safe upgrade from v0.8.2b3.

### Changed

- **Docs re-baselined to v0.8.x.** Swept the Lemonade→container-runtime and
  Cognee→Hindsight terminology out of `AGENTS.md`, `ARCHITECTURE.md`,
  `CONTEXT.md`, `PLAN.md`, and `README.md`; corrected the Hermes provisioner
  to its real 15-phase pipeline; refreshed version/status lines; and rewrote the
  (largely fictional) `hal0-service-management` codebase-map reference against
  the current `src/hal0/` tree. Marked the shipped Stacks and voice-stack
  superpowers plans as completed.
- **Dead ADR links fixed.** Tracked docs no longer link into the gitignored
  `docs/internal/adr/` tree (#638); surviving decisions are inlined and ADRs are
  referenced by number. (In-code citation sweep tracked in #984.)

### Fixed

- **qwen3tts migration script aligned with the `tts`-slot model.** The
  standalone-to-slot migration script's Guard 1 checked a non-existent
  `qwen3tts` slot (always 404) and Guard 3 checked a removed kokoro `:8084`
  fallback; both are corrected to the deployed design where Qwen3-TTS serves
  from the canonical `tts` slot via `voice.tts`. (#979)
- **`uninstall.sh` now removes all three sudoers grants.** It only removed
  `hal0-benchctl`, leaking `hal0-agentenv` and `hal0-comfyui` behind on uninstall.
- **Hermes private memory bank seeded under its canonical name.** `install.sh`
  seeded `private__hermes-agent`, which the server (which derives the bank from the
  agent-id via `PRIVATE_PREFIX="private:"`) never matches — corrected to
  `private:hermes` so the pre-seeded retain-mission/dispositions actually apply.
- **Dropped the obsolete Lemonade boot-contention comment** from
  `installer/comfyui/scripts/comfy-up.sh`.

## [v0.8.2b3] — 2026-06-29

GPU text-to-speech lands: Qwen3-TTS now runs as a hal0-native slot with a
one-switch swap between the Kokoro (CPU) and Qwen3-TTS (GPU) engines, plus a
GPU benchmark harness and dashboard polish. Safe upgrade from v0.8.2b2.

### Added

- **GPU Qwen3-TTS as a hal0-native slot.** New `Qwen3TTSProvider` serves
  Qwen3-TTS from the `tts` slot, with a `voice.tts` capability switch that swaps
  the engine between Kokoro (CPU) and Qwen3-TTS (GPU) without reconfiguring the
  slot. (#972, #976) The toolbox image builds + pushes to ghcr.io and its digest
  is pinned in `manifest.json`. (#975, #977) Ships a standalone-to-slot migration
  runbook + guarded script. (#974)
- **GPU benchmark harness.** A GPU benchmarking toolbox with the `hal0-benchctl`
  seam and accompanying agent skills. (#967) Dashboard gains an iGPU usage gauge
  and a prefill TTFT readout. (#968) Topbar adds Kanban / Agent-Chat launchers, a
  global agent chat, and an Archived lane. (#966)

### Fixed

- **Dispatcher injects upstream auth headers for remote providers**, so requests
  routed to authenticated remote upstreams carry their credentials. (#973)
- **UI builds land reliably on deploy** (no-cache index + install to the served
  `dist`). (#969)

### Docs

- Benchmarking toolbox UI/feature handoff. (#971)

## [v0.8.2b2] — 2026-06-24

Two fixes on the 0.8.2 beta line — profile MTP tuning now actually takes
effect, and stacks can pull their referenced models. Safe upgrade from
v0.8.2b1.

### Fixed

- **Explicit profile spec flags win over the MTP bundle.** `resolve_profile_flags`
  appended `MTP_FLAG_BUNDLE` after a profile's own flags, so the bundle's
  spec-draft defaults (`--spec-draft-type-k q8_0`, `--spec-draft-p-min 0.0`)
  silently clobbered any `--spec-draft-*` a profile pinned — there was no way to
  tune the MTP draft through a profile. The bundle is now merged as defaults that
  the profile's explicit flags override (gap-filled only). (#963)
- **Absent stack models can be pulled.** Custom GGUF builds referenced by the
  seed stacks (saber, pi-agent, qwopus coders, halostrix, gemma, …) were
  auto-scanned with empty `hf_repo`/`hf_filename`, so on stack import/apply they
  classified "unresolvable" with no download URL. Registered their public HF
  coordinates (jcbtc/ + Jackrong/ + unsloth/ repos) in the curated catalogue;
  `embed_references` falls back to curated on export; and a new
  `backfill_coordless()` repairs existing coord-less registry rows on rescan. (#964)

### Changed

- **CI**: cancel superseded PR runs (never `main`); PRs test Python 3.12 only while
  `main` runs 3.12/3.13/3.14 (3.14 non-blocking); least-privilege workflow
  permissions; Node 20 → 22. (#898)

## [v0.8.2b1] — 2026-06-23

Profiles gain the same portable export/import/sharing model stacks already have.
Safe upgrade from v0.8.1-beta.2.

### Added

- **Portable profile export/import.** A profile can now be exported to a
  self-contained, checksummed `.hal0profile.json` envelope and imported on another
  host — the same file-based sharing model stacks use. The envelope carries the
  profile template plus a sha256 content checksum and an independent
  `schema_version`; no secrets or host paths are serialized. New routes
  `GET /api/profiles/{name}`, `POST /api/profiles/{name}/export`, and
  `POST /api/profiles/import` (dry-run reports checksum validity + name collision;
  commit creates under a chosen name, `409 profiles.exists` on a duplicate). New
  MCP tools mirror the `stack_*` set: `profile_list` / `profile_status` /
  `profile_export` (autonomous read) and `profile_import` / `profile_delete`
  (gated). The dashboard adds an Export button to every profile card and an Import
  dialog (file → dry-run preview → commit). (#962)

## [v0.8.1-beta.2] — 2026-06-23

Bugfix on the 0.8.1 beta line — restores fleet auto-update. Safe upgrade from
v0.8.1-beta.1.

### Fixed

- **Updater version comparison uses PEP 440.** `hal0 update` compared versions with
  a digit-tuple parser that split on `.` and stripped non-digits per segment, so the
  pip-normalised installed beta `0.8.0b3` parsed to `(0, 8, 3)` and the tag-form
  manifest `0.8.1-beta.1` to `(0, 8, 1, 1)` — `(0,8,1,1) > (0,8,3)` is false, so
  every box on a `0.8.0bN` beta saw the new release as "not newer" and `hal0 update`
  reported nothing to apply (the installed beta number was misread as the patch
  component). The comparator now uses `packaging.version.Version` in both the updater
  and the API route, falling back to the digit-tuple only for non-PEP-440 nightly
  tags (whose timestamp ordering still relies on it). (#957)

## [v0.8.1-beta.1] — 2026-06-23

Installer/privilege simplification + Hermes durable memory on by default. The
hal0-api privilege seams (hardened unprivileged mode, the slot privilege seam)
are gone, and a fresh `hal0 agent bootstrap hermes` now provisions a working
durable-memory provider out of the box.

### Added

- **Hermes durable memory enabled by default.** Provisioning now ships a working
  `hal0-memory` provider and sets `memory.provider=hal0-memory`, so Hermes gets
  cross-session recall with no manual config. Two banks — `private:hermes`
  (default) and `shared` (cross-agent) — backed by hal0's Hindsight engine via
  the hal0-api REST front door; reads union both banks. The provider exposes
  `hal0_memory_{search,recall,add}` (with `shared=true` to write the shared
  bank) and auto-injects recalled context each turn. (#955)

### Changed

- **Hermes agent identity is `hermes`** (was `hermes-agent`), matching the
  `hal0 agent` registry. The agent-id is the single source for the
  `X-hal0-Agent` MCP headers, the persona memory namespace, and the prelude. (#955)
- **Memory plugin install path fixed.** The plugin is copied to
  `$HERMES_HOME/plugins/hal0-memory/` (a direct child of `plugins/`, which the
  Hermes loader actually scans) instead of the nested `plugins/memory/…` that
  never loaded. (#955)

### Removed

- **Hardened (unprivileged hal0-api) mode removed**; hal0-api runs as root.
  Dropped live-hello; fixed ready-summary IPs. (#953)
- **Dormant slot privilege seam removed** (`hal0-slotctl` + euid routing). (#954)

### Breaking

- **Hermes memory namespace renamed `private:hermes-agent` → `private:hermes`.**
  Existing `private:hermes-agent` data is not auto-migrated; reprovisioned agents
  start recalling from `private:hermes` + `shared`. (#955)

## [v0.8.0-beta.3] — 2026-06-23

Canonical LLM roles + Hindsight-native memory extraction
([ADR-0023](docs/internal/adr/0023-canonical-llm-roles-agent-utility.md)). The two
canonical LLM roles are now **`agent`** (the capable default + fallback anchor,
replacing `chat`) and **`utility`** (the cheap helper, now seeded on every
install). `chat` and `primary` are retired as slot/role names.

### Changed

- **Canonical roles are `agent` + `utility`.** `agent` replaces `chat` as the
  default/anchor everywhere (seeded slots, dispatch rule-9 fallback, the default
  pin set, `_configured_primary`). `utility` joins `SEEDED_SLOTS` so a fresh box
  never silently falls back to a heavy model for cheap extraction.
- **Generalized virtual addressing.** Any enabled `type=llm` slot `X` is now
  addressable as `hal0/X` (chain `(X, agent)`); the advertised canonical virtuals
  are `hal0/agent`, `hal0/utility`, `hal0/npu`.
- **Memory graph extraction is operator-selectable and actually wired.**
  `[memory.graph].extraction_slot` names the local llm slot Hindsight uses for
  graph extraction; hal0 propagates it to `hindsight-api` via a systemd drop-in
  (`HINDSIGHT_API_LLM_MODEL=hal0/<slot>`) + restart. `hal0 memory graph enable`
  takes `--slot <name>` (validated against the live enabled-llm-slot set).
- **Cognee fully removed.** The Cognee engine + wrapper are deleted; Hindsight is
  the platform engine (with a PgVector boot-degrade fallback). `MemoryRecord`
  survives as an alias of `MemoryItem`.

### Breaking

- **`hal0/chat` is no longer advertised.** Clients pinned to `hal0/chat` (Hermes,
  OpenWebUI, any custom consumer) must repoint to **`hal0/agent`**.
  Hermes `model.default` is now `hal0/agent`.
- **`memory.graph.route` / `memory.graph.upstream` removed**, replaced by
  **`memory.graph.extraction_slot`** (default `"utility"`). Old `route`/`upstream`
  keys in `hal0.toml` are silently dropped on load (no hard-fail on upgrade). The
  `hal0 memory graph enable --route/--provider/--model` options are gone — use
  `--slot`.
- **`primary` is no longer a slot alias.** `SLOT_ALIASES` is `{"agent-hermes": "agent"}`.

## [v0.8.0-beta.2] — 2026-06-22

Bugfix release on the 0.8.0 beta line. No behaviour changes beyond the two
fixes below — a safe upgrade from v0.8.0-beta.1. First release to carry #948.

### Fixed

- **Operator Board live updates restored.** The board's events-WS proxy
  (`/api/board/events`) resolved its upstream Hermes session token from the
  `HERMES_SESSION_TOKEN` env var only, while the REST path harvests the
  rotating per-process token from the dashboard HTML. With no env pin (the
  default), the WS connected upstream with no token, Hermes rejected the
  upgrade (403), and the browser socket died with 1011 — so tasks created in
  Hermes loaded on refresh but never pushed live to the board. The WS bridge
  now shares the REST client's token resolution (env-pin → HTML-harvest →
  rotation cache) and re-harvests + retries once on connect failure. (#949)
- **Hermes privileged env seam.** A privileged env-write seam lets the
  unprivileged provisioner write `root:root` `.env` files, so Hermes agent
  config provisioning works under the dropped-root `hal0-api`. (#948)

## [v0.8.0-beta.1] — 2026-06-21

First beta of the 0.8.0 line — the model-config, Hermes, and permissions
overhaul. Configuration becomes a declarative single source of truth
(**Stacks** + single-source launch argv), Hermes consolidates onto its own
config ownership, and `hal0-api` can finally drop root. Voice (TTS + STT)
lands end-to-end. One behaviour change to be aware of: the `hal0/primary`
and `hal0/flm` virtual aliases are gone — see **Changed**.

### Added

- **Stacks — declarative config SSOT.** A `StackConfig` schema plus a
  `StackApplyEngine` that `plan()`s a Stack into a ChangeSet, `apply_config()`s
  it as an atomic commit with rollback, and `converge()`s the live slot set
  (primary-slot load/swap/skip + capability-child routing through the
  orchestrator). Content-hash drift detection and an active-stack pointer,
  export/import via a checksummed `.hal0stack.json` envelope, snapshot of live
  config into a Stack, and a `StacksCatalog` CRUD with seed guards. Seed stacks
  (saber / forge / pi) derived from the roster bench.
  (#921, #923, #925, #926)
- **Voice stack — TTS + STT.** Brought up and verified end-to-end: `voice_wire`
  fixed, Open WebUI Call mode wired, and the NPU-trio facade auto-provisions
  STT. (#924, #928)
- **Single-source slot argv (overhaul stream A).** A resolver dedups the launch
  flag soup down to a last-wins canonical command; per-flag provenance is
  exposed at `GET /api/slots/{name}/resolved`, and the slot Edit drawer renders
  the resolved command with per-flag source badges (base / profile /
  extra_args). (#929, #930, #931, #932)
- **Capability-based slot fallback.** When a slot's `model.default` isn't
  locally servable (registered-but-no-file, or pulled-away), `load()` falls back
  to the best locally-registered model matching the slot's capability —
  excluding diffusion / image / video models and preferring name-similarity to
  the configured id. (#940, #942)
- **Hardened permissions — run `hal0-api` unprivileged (opt-in).** Set
  `HAL0_USER=hal0` and the installer drops the API off root: a declarative
  ownership table (audited read-only by `hal0 doctor perms`), a narrow
  privileged seam (`hal0-slotctl` + a no-wildcard sudoers grant) so the
  unprivileged API can still write per-slot units and drive `hal0-slot@*`, and
  a codified flip (run-as drop-in + recursive chown of config + state, pruning
  `agents/` + `secrets/` + the models dir). Slot containers stay rootful — the
  container remains the sandbox boundary. Default `HAL0_USER=root` is
  byte-for-byte unchanged. (#929, #943, #944, #945)
- **Hermes owns its own config.** The runtime is unpinned with a real upgrade
  path, and a config-set overlay replaces the whole-file `config.yaml` render —
  Hermes owns and self-migrates its config while hal0 layers only its keys.
  (#934, #938)
- **Chat-template render-validation.** The template catalog is render-validated
  so a broken template can no longer ship silently. (#917)

### Changed

- **Breaking — `hal0/primary` and `hal0/flm` virtual aliases removed.** Virtual
  model names now map 1:1 to their resolution chains; `hal0/primary` no longer
  resolves (use `hal0/chat`) and `hal0/flm` is gone (use `hal0/npu`). Slot-name
  back-compat is intentionally kept, and the Hermes overlay now emits
  `model.default: hal0/chat`. (#939)
- **q8_0 KV cache, universally.** Main and MTP-draft KV caches are now `q8_0`
  across slots — near-lossless and keeps fused FlashAttention on AMD HIP. (#933)
- **Profiles** lift bench-tuned MTP config into `rocm-moe` / `rocm-dnse`. (#922)
- **Open WebUI** disables PersistentConfig so the env prewire wins the chat
  connection. (#927)
- Docs mirrored from hal0-web.

### Fixed

- **Installer** — `setup --storage-dir` is passed as a separate argv token so
  fresh `--models-dir` installs seed slots correctly (#946); the hardened-perms
  flip chowns config + state recursively so a root→hal0 upgrade doesn't strand
  root-owned state subdirs (#945); `hermes gateway install` runs
  non-interactively and skips `enable` when the unit is absent (#941); a
  lemonade-team PPA is added so the FLM/NPU `.deb` resolves on a fresh Ubuntu
  box (#937); the registry scans the effective store / pull_root rather than
  only declared roots (#935).
- **Hermes gateway** marks its `EnvironmentFile` optional (`-`) so fresh
  installs don't crash-loop on a missing secrets vault. (#936)
- **Dependencies** — bump vulnerable deps flagged by Dependabot. (#919)

## [v0.7.3-beta.2] — 2026-06-19

Second beta in the 0.7.3 line. Vision lands on the chat slot, idle slots
finally give their RAM back, and the Operator Board stops crashing on
task creation.

### Added

- **Chat-slot vision** — mmproj sidecars are now associated with their parent
  model in the registry, the container provider emits `--mmproj` from that
  sidecar, and vision auto-surfaces as a capability with a per-slot toggle.
  (#899, #900, #901)
- **TTL-driven hard eviction** — idle slots are now unloaded after their timeout,
  freeing resident RAM instead of merely relabelling READY→IDLE. (#902)
- **Hermes memory authorship** — writes from Hermes are stamped with an
  `agent:hermes` author tag. (#912)

### Fixed

- **Operator Board** no longer black-screens (React #31) when adding a task;
  modal styling, drag-to-delete, and the agent-chat drawer are reworked, and
  board chat now runs on the agent slot instead of the (wedged) chat slot.
  (#905, #914, #916)
- **Slot MTP gate** hardened on the backend and NUL bytes stripped from
  model-modal inputs. (#918)
- **Memory** rejects anonymous private writes (stops `private__anonymous`
  misrouting). (#915)
- **Memory banks grid** fills its width again, unnested from the section
  title row. (#911)
- **ComfyUI** reads slot logs from journald and reworks its card layout. (#909)

### Changed

- Docs mirrored from hal0-web.

## [v0.7.3-beta.1] — 2026-06-19

First Beta. The dashboard becomes a full operations console — ComfyUI image
generation, an agent task board, NPU/FLM slots, and a unified profile-card
layout — on top of honest slot health and per-slot context derivation.

### Added

- **ComfyUI generation engine** — full platform integration (model store,
  capability picker, installer wiring, V2 Image-Gen pane). The Image-Gen tab
  collapses its queue/workflows and an Inference-tab dot tracks live state; image
  generation flips the GPU into exclusive image mode via the iGPU switchover.
  (#878, #890, #881)
- **NPU occupancy** — a living occupancy grid with per-slot accents and
  activity-driven breathing, replacing the NpuFlmStack/trio picker. (#859, #861, #860)
- **Operator Board** — a hal0-skinned kanban wired to Hermes (`/api/board/*`) with
  a live agent-chat drawer and working task creation. (#852, #858)
- **Agents page** — an agent-card library with a live Hermes card. (#848)
- **Dashboard overhaul** — inference/NPU/ComfyUI cards unified to the profile-card
  style; Profiles given an engine-style section header and moved into the Slots
  tab; inference-pane living-grid redesign; sidebar nav accordion + bottom Services
  launch zone; a live-journal footer with runtime + service health groups; the
  memory+throughput band lifted above the tabs and slot cards freed from the
  accordion. (#888, #889, #879, #894, #867, #853)
- **Editable per-slot `extra_args`** with a Regenerate overlay. (#854)
- **Qwen3.6 MTP** chat template + slot rails.
- A **generated changelog** is now included in every release (nightly + stable). (#842)

### Changed

- **Slot health-probe honesty** — a slot is marked ready only once its real
  `/health` passes, not on a systemd snapshot. (#866)
- Slot context is derived per-slot and never silently inherits llama-server's
  4096; the edit-drawer default is 16k. (#862, #850)
- Disabled-but-running slots are surfaced; the enable toggle moved into the drawer. (#856)
- The runtime indicator split into a sidebar launcher + a footer health chip. (#864)
- Durable group-shared model ownership for an editable `/opt/hal0`. (#843, #857)
- Nightly versions carry a sub-day timestamp so same-day re-cuts stay monotonic. (#841)

### Fixed

- **Hardware:** report the live GTT total instead of a stale cached probe value. (#891)
- **NPU:** probe AIE columns via a temp file, not `-o /dev/stdout`. (#893)
- **Slots:** harden container config-drift comparisons and warn on drift. (#880, #869)
- **Routing:** translate FLM `<tag>-FLM` ids to served tags in the chat-slot rewrite. (#840)
- **Hermes:** run-as-`hal0` guard + ownership handover prevents root-clobber;
  corrected env arg order and dashboard TUI argv order. (#844, #847)
- **Dashboard:** dedup the journal SSE ring; chyron/timestamp polish; grid
  alignment; empty memory-bank graph no longer locks the dashboard; stray
  dev-test slots removed from the persona UI; responsive sizing + chrome cleanup.
  (#868, #871, #870, #845, #855, #846, #851)

### Docs

- Restored `doctor perms` + `migrate model-layout` to the CLI reference; added the
  deploy + PR workflow for parallel teammate sessions. (#849, #865)

## [v0.5.1-alpha.1] — 2026-06-15

Pre-Alpha. Retires the web FirstRun picker in favour of a terminal `hal0 setup`
TUI, and adds Ubuntu 26.04 / Python 3.14 install support.

### Added

- **`hal0 setup` TUI** — replaces the web FirstRun picker with a rich two-column
  terminal setup (storage → Extensions → Main model → Agent model → NPU) over an
  always-on context pane. Hybrid apply (in-process at install time, via the API
  when it's up — roster coherence), `--auto`/`--storage-dir`/`--no-pull`/
  `--no-extensions` flags, and a tier-less `POST /api/install/apply-selections`
  endpoint (#833).
- **Extensions** — selectable, auto-wired Apps (Open WebUI) + Agents (Hermes, Pi),
  a growing registry surfaced in `hal0 setup` (#833).
- **Ubuntu 26.04 / Python 3.14 install support** — per-distro FLM `.deb` selection,
  hindsight `--ignore-requires-python`, py-version-agnostic Hermes web_dist (#829).

### Changed

- A fresh install seeds the hardware-recommended Main slot **non-destructively**
  (only slots whose config is absent) and writes the first-run sentinel via
  `hal0 setup --auto --no-pull` — so `hal0 update`/re-install never overwrites a
  customised slot. The web bundle-tier picker is retired; the bundle backend is
  kept dormant for the future *Stacks* feature (#833).

### Removed

- Web FirstRun picker (`firstrun.jsx` + hooks), the v1 `/api/bundles` surface,
  `bundles/store.py`, and the legacy `/api/install/pick-default` route (#833).

## [v0.5.0-alpha.1] — 2026-06-14

Pre-Alpha. Zero-boot install + FirstRun v2: a fresh install now stands up the
memory engine, agents, and Hermes with no manual steps, and the FirstRun wizard
orchestrates a full multi-slot bring-up from a single bundle pick.

### Added

- **FirstRun v2** — quick-path wizard + orchestrated multi-slot install from a
  single bundle/kit pick (#809), with an Advanced drawer exposing per-slot
  model/profile overrides (#812).
- **Slot config UX** — Phase 2 per-slot MTP override + capability-gated MTP pill
  (#800); Phase 3 non-manual chat templates, model-level and per-slot (#802).
- **Zero-boot installer** — stands up a local Hindsight memory engine + seed
  banks (#806), ships the hal0 agent skills + drop-in dirs (#805), and
  provisions Hermes on a fresh install (#804).
- **NPU telemetry** — live column / duty / tok-s / KV surface, repointed to
  `hal0-toolbox-flm:0.9.43` (#813).
- **Settings** — HuggingFace token field + `api.env` hint for gated pulls
  (#816); standalone `/pull` uses capability-grouped paths (#815).
- **Dashboard overhaul** — the home page becomes a customizable operator
  widget board: drag/resize/pin-to-home slot cards, live memory-map,
  throughput, utilization and power monitors, a quick-chat tester, and a
  live ComfyUI job-queue widget; layout persists per operator (#814).
- **v0.5 navigation** — Connections dissolves into Slots/Agent tabs with
  sidebar sub-links; Memory + MCP unify under a tabbed Agent shell (#817).

### Fixed

- Non-blocking slot controls + NPU/image-gen toggles; cancel mid-load (#801).
- Slot edit drawer shows profile intent in its dropdown (#811).
- Enforce device↔profile backend coherence on slot create/update (#807).
- Drop the unimplemented `memory migrate --apply` flag (#820).

### Internal

- Recolor the device palette — free red for errors/stop (#803).
- CI tests against the latest supported Python (3.12) only (#808).
- gitignore `.superpowers/` brainstorm scratch (#810).

## [v0.4.1-alpha.1] — 2026-06-14

Pre-Alpha. First release carrying the clean-install hardening proven end-to-end
on fresh Ubuntu 24.04 containers:

- Bundled-agent install converges on the hal0-managed venv — `hal0 agent
  install hermes` provisions toolchain → venv → wrapper → unit in one
  foreground command, and the API path becomes a thin register-or-hint (#766).
- Installer auto-installs the python venv stdlib on clean Debian/Ubuntu instead
  of aborting at preflight (#778); NPU host-lib prereqs (ffmpeg6/XRT) are now
  best-effort, not fatal (#779).
- `/var/lib/hal0` permissions let the `hal0` agent refresh the shared STATE.md
  the session hook reads (#777).
- Slot config UX Phase 1: grouped drawer, reasoning pill, type-default pane,
  reactive model dropdown (#796).

## [v0.3.2-alpha.1] — 2026-05-29

End-of-stream cut for v0.3. Bundles MCP-completion, memory-map redesign,
the Settings → Updates fix, the silent-eviction dispatcher recovery,
ADR-0020 OpenRouter callback skeleton, the persona spending-cap primitive,
and the docs/internal pin + dashboard-v3 walkthrough.

After this tag, active scope rolls to v0.4 (install-mode reconciliation,
UI polish, fully-implemented Agents/UI/Install bootstrapped) and v0.5
(MCP admin + memory wiring across UI and agents).

### Added

- **Per-persona spending-cap primitive** (#411 — Phase 0 OpenRouter
  prereq). `[persona.budget]` TOML sub-table + pure-Python budget
  enforcement layer landing BEFORE the V1 OpenRouter upstream provider
  and V2 `hal0-fusion` MCP server. DA review of the OpenRouter
  integration plan flagged this as P0 must-fix #3 — without a
  spending-cap envelope, fusion (4.4× cost vs single-model) plus a
  recursing Hermes loop could drain a $200/credit pool overnight.
  - `src/hal0/agents/budget.py` — `Budget` dataclass, append-only
    `BudgetLedger`, pure `check_budget` / `record_charge`, daily /
    monthly / lifetime aggregation + per-call max.
  - REST surface under `/api/agents/{id}/personas/{pid}/budget` —
    `GET` (caps + spend + headroom), `PUT` (replace; round-trip
    preserves), `POST /check` (dry-run pre-call gate), `POST /charge`
    (post-response recorder).
  - Ledger at `/var/lib/hal0/agents/{agent_id}/personas/{persona_id}/spend.jsonl`
    — append-only JSON-lines, fsync per write, `tail -f | jq` friendly.
  - `PersonaBudgetPanel` dashboard editor under Personas tab.
  - Persona seed (hermes + coder) ships with empty budget block;
    operators opt in. `hal0 agent reprovision hermes` preserves
    operator-set budgets (idempotent seed, `overwrite=False`).
  - **Scope:** per-persona only in v0.3.2; per-agent and platform-wide
    scopes deferred to v0.4. No provider charges this primitive yet —
    V1 OpenRouter wires the pre-call gate and post-response record.
- **ADR-0020 + OpenRouter callback skeleton + loopback guard** (#409,
  Phase 0 OpenRouter prereq). Documents why the future OAuth PKCE
  callback URL is constrained to `127.0.0.1` so ADR-0012's LAN-trust
  posture survives the V1 OpenRouter integration. Ships a registered
  `GET /api/openrouter/auth/callback` route returning HTTP 501 with a
  per-route loopback guard so V1 inherits a baseline that respects
  the constraint from day 1. No live behaviour change.
- **Dashboard v3 `/agent` real-backend wiring** (#364, closes #207
  #228 #227 #226). `useAgents()` hook against `/api/agents`; live
  Memory tab against `/api/memory/graph/status`; live Skills tab
  against new `GET /api/agents/skills`; PersonaEditModal hydrated
  from new `GET /api/agents/persona-enums`. Server-side TONES + TOOLS
  - skill catalog moved to `src/hal0/agents/persona.py`.
- **Embedding model pinning + rerank wiring** (#365, closes #116).
  New `[memory.embedding]` config block — `model`,
  `rerank_enabled`, `rerank_url`, `rerank_over_fetch_factor`,
  `rerank_max_candidates`, split `rerank_connect_timeout_s` /
  `rerank_read_timeout_s`. Defaults preserve v0.3.0 semantics
  (rerank off, embedding model unchanged).
- **Private namespace contract for REST + read path** (#366 + #369,
  closes #317 #367). `X-hal0-Agent` + `X-hal0-Private` header
  contract on `/api/memory/{add,search,list,delete}` — shared
  ADR-0005 §3 resolver in `src/hal0/memory/namespace.py`. Wrapper
  `add` / `search` / `list_items` / `delete` accept per-call
  `client_id`; `_allowed_read_datasets` honors it so per-agent
  reads work end-to-end. Audit rows now stamp the resolved per-call
  identity instead of the singleton wrapper's anonymous default.
  Identity hardening: regex on agent id (path-traversal blocked),
  rejection of `private:*` agent values, rejection of body
  `dataset=private:*` when the private toggle is off.
- **Dashboard v3 `/mcp` install/uninstall/config + real audit
  stream** (#368, closes #305 #224 #222). New
  `src/hal0/mcp/installed.py` registry + `src/hal0/mcp/manifest.py`
  resolver (`oci` / `npm` / `uvx` / `git` / `http` specs). 501
  stubs for install / uninstall / config replaced with real
  impls; `/api/mcp/resolve` added; `/api/mcp/servers` merges
  bundled (live FastMCP introspection) + installed (registry).
  Real audit stream consumed by `useMcpServerLogs`. SSRF guard
  on URL fetch (loopback / RFC-1918 / link-local / 169.254.169.254
  / mDNS / CGNAT / unspecified all blocked; redirects disabled).
  Registry files at `/etc/hal0/mcp-servers/<id>.toml` written
  0o600 inside a 0o700 directory.

### Fixed

- **Settings → Updates: Install update silently no-op'd** (#386).
  The dashboard's Install button hit `POST /api/updates/apply`,
  received 202 with a `job_id`, toasted "Update started", and never
  polled the job — so when the background apply hit
  `UpdateExtractError` from a leftover `/usr/lib/hal0/hal0-<v>/`
  the user saw nothing. Three fixes:
  - UI: `useUpdateApply` signature corrected (`version?`, not
    misnamed `channel`); `useUpdateCheck` GETs `/api/updates/check`
    (was POSTing to a GET-only route → silent 405); new
    `useUpdateJob(jobId)` poller surfaces `running` / `applied` /
    `failed` to inline progress + toasts.
  - Backend: `Updater._extract_tarball` now quarantines a prior
    hal0 extraction at the same path to `<dest>.stale-<unix-ts>`
    instead of refusing, so a retry after a half-failed apply
    isn't permanently wedged. Foreign non-empty dirs are still
    refused — heuristic recognises hal0 installs by `VERSION`
    file or `pyproject.toml` `name="hal0"`.
  - Deduped the non-empty check in `Updater.apply()`; the extract
    step is the single source of truth.
- **Dispatcher silent-eviction recovery** (#392). When Lemonade
  silently evicts a model mid-stream the dispatcher now catches the
  upstream 502, refreshes slot state, and retries once before
  surfacing — turning a user-visible 502 into a transparent recovery.

### Tests

- **δ-harness coverage of Hermes `delegate_task` for 3 backends**
  (Phase 0 OpenRouter prereq — DA must-fix #2). New δ-tier
  pytest suite at `tests/harness/integration/test_delegate_task_*.py`
  proves the `delegate_task → execution-backend` dispatch hop works
  end-to-end for local + docker + modal with mocked
  `BaseEnvironment` subclasses (no Modal credits, no docker pulls in
  CI). The matrix test fans out one call across all three backends
  and asserts each was invoked exactly once with a per-backend-shaped
  payload. Findings catalogued at `tests/harness/FINDINGS.md` §46
  including the upstream audit (R7's "7 backends" claim corrected
  to 6 — local/docker/singularity/modal/daytona/ssh; Vercel Sandbox
  not present in upstream pin `0554ef1a`). Gates V3a Hermes
  observability per
  `openrouter-research-2026-05-28/PLANNING.md` §3 Phase 0.

### Docs

- **Internal docs pin + ADR-0017 + release-manifest refresh** (#389).
- **Operate + dashboard + installer sweep** (#390): Lemonade reference
  page, dashboard v3 walkthrough, installer auth section gutted to
  match the ADR-0012 post-Caddy reality.
- **PLAN §9 async-job polling contract** (#387). Codifies that any
  202+job_id endpoint requires UI polling of `GET /status/{id}` until
  terminal state — the underlying pattern behind the #386 fix.

### Deferred

- MCP-installed-server supervisor: start / stop / restart still
  return 501; installed servers report `state=stopped`. Dashboard
  buttons disabled with tooltip pending the supervisor design.
- AgentInbox / AgentOverview hero strip / Recent records pane /
  Skills "calls" column / per-store DB tile breakdown — adjacent
  hardcoded surfaces in dashboard v3 (filed as #374-#380).
- Manifest fetcher streaming + size guard, patch_config R-M-W lock,
  bundled-id shadow defense, dev-host worktree disk footprint
  (filed as #381-#384).
- **Install-mode reconciliation** (#406, HITL→AFK) and **hal0-test-template
  CT 200 + clone harness** (#407, AFK) — both filed against v0.4 scope.

## [v0.3.1-alpha.1] — 2026-05-27

Hermes-and-Cognee + dashboard v3 polish release. v0.3 stream work that
landed on `main` between 2026-05-23 and 2026-05-27 — 64 PRs — packaged
into the first patch tag after the v0.3.0-alpha.1 auth/Caddy cut.

### Added

- **Hermes-Agent bootstrap pipeline** (PRs #279, #284, #286, #289, #291,
  #292, #295, #296, #298, #316). 12-phase pipeline (`preflight`,
  `install`, `home_init`, `env_probe`, `config_write`, `mcp_wire`,
  `namespace_register`, `context_link`, `model_automap`, `voice_wire`,
  `smoke_tests`, `self_report`). Plugin model (`Hal0Profile`,
  `Hal0MemoryProvider`). `hal0 agent {status,log,upgrade}` CLI.
- **MCP host: per-agent client allow-list** (ADR-0013 — PRs #278, #293,
  #295, #300, #304). `mcp_client.py`, host-introspection probe tools
  for `hal0-admin`, per-agent MCP-clients view in the dashboard, full
  read-only introspection + audit-log SSE on the MCP page.
- **Memory graph extraction** (ADR-0014 — PRs #287, #290, #294, #297,
  #303). `[memory.graph]` schema + cognify gate on Cognee. New
  `/api/memory/{add,search,list,delete}` REST shims (closes #302).
  `hal0 memory graph {status,enable,disable}` CLI. Graph-extraction
  panel in dashboard Memory tab.
- **Agents > Peers tab** (PR #299) — identity cards from agents
  dataset.
- **Models surface** (PRs #313, #319, #343, #353) — scan +
  add-by-path + model-dir setting, single `[models].store` setting
  with firstrun + migration, default scan/preview recursive with UI
  toggle, model.type derived at the `useModels` hook.
- **Chat surface in dashboard** (PRs #309, #314, #315, #356, #357,
  #358) — real chat against the primary slot, slot indicator dots +
  warming pulse, collapsible reasoning above the answer, chat moves
  to its own `/chat` route, snapshot/memmap/throughput sidebar
  mirrored onto `/slots`.
- **Footer journal + update banner** (Epic #322 — PRs #321, #328,
  #329, #330, #332). `/api/journal` + `/api/journal/stream` merged
  log surface; Settings → Updates wired to the real backend.
- **Slot UX bundle** (PRs #281, #282, #283, #342, #344, #351) — POST
  normalizes Lemonade-shape model + auto-assigns port, `hal0 slot
  create --type` derives Lemonade device, max_loaded_models 4→8,
  swap-arrow affordance, zero-red-dots bundle, swap popover reads
  live `/api/models`.
- **One-line Proxmox VE LXC installer** (PR #341).

### Fixed

- **Slot backend update now invalidates state.json** (PR #360, issue
  #359). Previously `POST /api/slots/{name}/backend` rewrote the TOML
  but `extra.backend` in state.json stuck at the boot-time adoption
  value forever, so the snapshot lied even though inference itself ran
  on the new backend.
- **Dispatcher fall-through to Lemonade proxy** (PR #277) and **drift
  to OFFLINE not ERROR when lemond evicts a model** (PR #276).
- **Hermes uninstall** — registry coherence + state-dir cleanup
  (#352), venv + context_link teardown (#354), memory teardown failure
  surfacing (#355).
- **`/v1/health.last_use`** treated as an opaque counter (PR #307);
  removes spurious "idle since the unix epoch" rendering.
- **Live sidebars + memory map + throughput** (PRs #306, #308, #328)
  finally read the real backend instead of HAL0_DATA seed fixtures.

### Changed

- **Bundle name** rendered from manifest instead of placeholder text
  across install banners + progress (#214 / #331).
- **MCP page** moved from mock to real backend introspection (#304).
- **Settings → Updates** moved from mock to real backend (#321).
- **UpdateBanner** wired to live update state (#324 / #329).
- **HAL0_DATA fixtures** further retired — multiple dash surfaces now
  read `/api/models` (#345 / #351).

### Notes

This is a patch-level tag (`0.3.0 → 0.3.1`) by SemVer convention, but
the scope is closer to a minor release — Hermes, memory graph, and the
MCP host surface are all new user-facing systems. Future patch tags

## [v0.3.0-alpha.2] — 2026-05-28 (Hermes integration sweep)

End-to-end Hermes-Agent integration lands. The 12-PR master-plan
(`docs/internal/scratch/hermes-research-2026-05-28/MASTER-PLAN.md`)
ships as one mergeable surface: provisioner overhaul, persona TOML,
hal0-cognee memory plugin, <hal0-agent@.service> template, chat WS
proxy, plugin host, SidebarAgentBlock, v3 dashboard refactor, HermesChat
composer/transcript, the missing endpoints (`restart`, `skills`,
`memory/stats`), tests + docs sweep, and the upstream pin / weekly
drift CI job.

Decision record consolidated in
[ADR-0019](docs/internal/adr/0019-v0_3-hermes-integration.md);
upstream pin process in
[ADR-0018](docs/internal/adr/0018-upstream-hermes-pin-and-upgrade.md).

### New / improved

- **`hermes_provision` overhaul (#393, #396)** — 12-phase orchestrator
  (preflight → install → env_probe → home_init → config_write →
  mcp_wire → context_link → namespace_register → model_automap →
  voice_wire → smoke_tests → self_report). Idempotent + checkpointed.
  Composite `hal0` upstream + MCP registration + system-prompt
  addendum + persona seed all happen during bootstrap.
- **hal0-cognee MemoryProvider (#394)** — `src/hal0/agents/hermes/plugins/memory_cognee/`
  wraps `/api/memory/*` so memory is part of the prompt
  (`system_prompt_block`), not a tool the agent has to remember to
  call. Locks the #317 dataset-namespace contract.
- **`hal0-agent@.service` template (#395)** — sandboxed systemd
  instance template (`NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome=yes`, `Type=notify`, `WatchdogSec=60`). Soft-link to
  lemonade (`Wants=`, not `Requires=`/`BindsTo=`) so the agent
  survives a lemonade GPU-cleanup hang. CLI shim at
  `/usr/local/bin/hal0-agent`.
- **Persona TOML store + endpoints (#399)** — `GET/POST
  /api/agents/{id}/personas[/{pid}/activate]`. Hot-reload nudge over
  JSON-RPC swaps system-prompt scope on the next turn without restart.
  Seeded personas: `hermes`, `coder`.
- **Plugin host (#397)** — manifest proxy at
  `/api/dashboard/plugins`; per-plugin static-asset surface at
  `/dashboard-plugins/{name}/...`; shadow-DOM SDK shim. Lets the v3
  dashboard mount upstream Hermes plugin bundles (kanban today)
  inside an `<AgentView>` tab.
- **Chat WS proxy + session REST shim (#398)** — `/api/agents/{id}/{events,
  submit,session/*}`. Origin allowlist + HMAC session cookie on every
  WS upgrade; embed token in `Authorization: Bearer` (never the query
  string). `tool.progress` server-side coalesced at 100ms; ordering
  invariant (progress before complete) preserved.
- **SidebarAgentBlock (#400)** — service/persona/approvals/skills/
  memory chips + `[Open chat]` button. Parameterised by `agent_id` so
  v0.4 pi-coder lights up by adding a row.
- **Dashboard v3 agents refactor (#401)** — `<AgentView>` monolith
  split into Composer, Transcript, Sidecar; Inbox tab dropped; Peers
  tab folded into Memory.
- **HermesChat composer + transcript (#404)** — React composer
  (Enter submits, Shift+Enter newline); zustand transcript with
  WebSocket reconnect (250ms → 4s jittered backoff); inline tool-call
  cards.
- **ADR-0018 upstream Hermes pin + weekly hermes-sdk-diff CI (#403)**
  — `pyproject.toml [tool.hal0.upstream-hermes]` is the
  machine-readable pin; `.github/workflows/hermes-sdk-diff.yml` opens
  a drift issue weekly when any tracked file changes between pin and
  upstream HEAD.
- **PR-11 sweep — tests + docs + final missing endpoints**:
  - `POST /api/agents/{id}/restart` — systemctl restart wrapper for
    the SidebarAgentBlock service chip. Audit-logged via
    `hal0.agents.audit`. Subprocess-level timeout + spawn-failure
    envelopes.
  - `GET /api/agents/skills` — replaces the static catalog the
    SidebarAgentBlock used during build-out. Returns the v0.3
    catalog (`hermes-core` + `hal0-admin` + `hal0-memory`). Bumps
    ride ADR-0018 drift PRs.
  - `GET /api/agents/{id}/memory/stats` — per-agent counts the
    sidebar memory chip renders; pulls from the in-process Cognee
    wrapper. Graceful `available=false` fallback when memory isn't
    configured.
  - δ-harness `tests/harness/integration/` — full chat round-trip +
    persona activate round-trip against a `FakeWsServer` mock hermes
    (no GGUF download required).
  - `AGENTS.md`, `ARCHITECTURE.md`, `CONTEXT.md` glossary refresh
    (composer, transcript, plugin host, sidecar agent block, persona
    TOML, hal0-cognee, hermes-sdk-diff, HMAC session cookie,
    X-hal0-Agent, composite hal0 upstream).
  - ADR-0019 consolidates the master-plan decisions.

### Internal contracts

- `X-hal0-Agent` (NOT Bearer) is the identity claim on hal0-api per
  ADR-0012; the chat-proxy injects it on outbound hops, the browser
  never sees it.
- `/api/agents/{id}/*` is the v0.4-ready shape — every endpoint is
  parameterised by agent id; v0.3 only resolves `"hermes"`.
- Bundled agents follow single-pick (ADR-0004): installing one
  uninstalls any other.

### Known follow-up

- hal0-web `public/CONTENT_BRIEF.md` + `src/pages/agents.astro`
  update lands in a sibling PR on the `Hal0ai/hal0-web` repo.

## [v0.3.0-alpha.1] — 2026-05-23

**Caddy and the auth surface are removed.** PLAN.md v0.3 stream 4
("Admin / auth simplification") lands as a hard cut rather than the
softer "reduce/keep simplified password auth" originally planned in
ADR-0001. Architecture in [ADR-0012](docs/internal/adr/0012-remove-auth-and-caddy.md),
which supersedes [ADR-0001](docs/internal/adr/0001-collapse-edge-auth-into-fastapi.md).

### Breaking

- **Auth is gone.** A fresh install is open on `0.0.0.0:8080`. There is
  no password, no Bearer-token store, no `/api/auth/*` router, no
  first-run claim OTP, no session cookie. If hal0 is reachable from a
  hostile network, you must front it with an upstream reverse proxy
  that owns auth (Traefik / nginx / Cloudflare Tunnel; see
  `docs/operate/auth.mdx`).
- **Caddy is gone.** The installer no longer installs Caddy or renders
  a Caddyfile. The `hal0-caddy.service` unit is no longer shipped.
  `uninstall.sh` still tears down legacy `hal0-caddy.service` and
  `/var/lib/hal0/.first-run.lock` artifacts from older installs.
- **`--no-tls` install flag is gone** (now the only path).
- **`HAL0_AUTH_ENABLED` / `HAL0_AUTH_DISABLED` env vars are no-ops.**
  Both are unread by any hal0 process post-upgrade.
- Bearer tokens minted under v0.2.x stop working — there's no token
  store to validate them against. Programmatic clients that hit
  `/v1/*` no longer need (or are even able to use) an Authorization
  header.

### New / improved

- **v3 React dashboard on `main`** (#235), with the deferred
  slot-metrics normalizer (#249) and the slot type/group inference +
  hardware shape normalizer (#253) that took the sparse Lemonade
  payloads to a rendered state.
- **`/v1/*` reverse-proxy to Lemonade** (#248, closes #212). hal0-api
  catches every un-routed `/v1/{path:path}` and forwards to
  `127.0.0.1:13305`. Sidebar `lemond` status chip now updates from
  `/v1/health` instead of permanently reading "down."
- **Footer chips honor backend null** (#252, closes #221). `queued` /
  `coresident` render as `—` when Lemonade hasn't surfaced them.
- **Settings → default landing tab is now "Secrets"** (was "Auth"; the
  panel is gone).

### Removed code

- `src/hal0/api/auth/` (4 files, 712 lines) — first-run lockfile,
  password hash/verify, OTP rate-limiter
- `src/hal0/auth/` (3 files, 646 lines) — token store, password
  helpers, `auth_enabled()`
- `src/hal0/api/middleware/auth.py` (508 lines) — `require_token`,
  `require_writer`, `require_admin` deps + `AuthIdentity` resolver
- `src/hal0/api/routes/auth.py` (33 KB) — `/api/auth/{status,login,
  logout,password,me,tokens,tokens/{id}/rotate}`
- `ui/src/api/hooks/useAuth.ts` (58 lines) — token reveal/rotate hooks
- `ui/src/dash/settings.jsx::AuthSection` (~60 lines)
- `tests/api/test_auth_*` + `tests/auth/` — ~2,500 lines of test
  coverage for moot architecture
- `packaging/caddy/Caddyfile.template` + `packaging/systemd/hal0-caddy.service`
- ~135 lines of `install_caddy_tls()` + `--no-tls` handling in `install.sh`
- ~110 lines of first-run-lockfile + OTP minting + password-claim
  banner in `install.sh`

### Upgrade notes

- An existing v0.2.x install will lose its password + tokens on the next
  install. `uninstall.sh` cleans up the legacy Caddy unit + lockfile if
  you want a clean slate first.
- If you were relying on `--no-tls`, drop the flag — the installer no
  longer accepts it (and no longer needs it).

## [v0.2.0] — 2026-05-23

**The Lemonade Server adoption release.** AMD's Lemonade Server
replaces the six per-modality toolbox containers and the
`hal0-slot@.service` template as the unified inference runtime; one
`hal0-lemonade.service` supervises a single `lemond` daemon. Architecture
recorded in [ADR-0008](docs/internal/adr/0008-lemonade-adoption.md),
[ADR-0009](docs/internal/adr/0009-flm-trio-npu-packing.md),
[ADR-0010](docs/internal/adr/0010-bundle-picker-no-default-stack.md);
locked implementation contract at
[`docs/internal/lemonade-adoption-plan-2026-05-22.md`](docs/internal/lemonade-adoption-plan-2026-05-22.md).

### Breaking

- **v0.1.x → v0.2 is a clean break — no auto-migration.** `install.sh`
  detects v0.1.x state (presence of `/etc/hal0/slots/*.toml` AND
  absence of `/var/lib/hal0/lemonade/config.json`) and refuses to
  overwrite it, printing explicit backup + wipe instructions and
  exiting non-zero. See [https://hal0.dev/docs/v0.2-upgrade](https://hal0.dev/docs/v0.2-upgrade)
  for the user-facing procedure.
- **Per-modality toolbox containers retired.** `hal0-toolbox-vulkan` /
  `rocm` / `flm` / `moonshine` / `kokoro` / `comfyui` are no longer
  built or pulled. Their dispatch responsibilities consolidate into
  Lemonade's `llamacpp` / `flm:npu` / `whisper.cpp` / `kokoro:cpu` /
  `sd-cpp` recipes.
- **`hal0-slot@.service` systemd template retired.** Per-slot units
  no longer exist. `hal0-lemonade.service` is the new daemon
  supervisor — one process serving every slot via Lemonade's per-type
  LRU.
- **Model layout reorganised** to the canonical
  `/var/lib/hal0/models/<recipe>/<capability>/` tree. PR-7's
  migration script reorganises `/mnt/ai-models/{local,flm-ubuntu,moonshine_voice,voices,comfyui}`
  into the same shape with per-leaf symlinks back to the canonical
  path. Lemonade's `extra_models_dir` points at the canonical tree.
- **`/etc/hal0/slots/*.toml` removed** as a persistence surface;
  `capabilities.toml` is now the single source of truth for slot
  selections. The slot lifecycle state machine in
  `src/hal0/slots/state.py` survives; per-slot Provider classes and
  the slot-systemd-template do not.
- **Moonshine STT retired** in favour of `whisper.cpp` via Lemonade.
  More accurate but heavier on weak CPUs; lite-tier users may notice.
- **ComfyUI workflows lost.** `sd-cpp` covers the 90% case; power
  users are directed to external ComfyUI installations for advanced
  workflow graphs.
- **`HAL0_BACKEND=lemonade` env flag** introduced in PR-8 and removed
  in PR-10 — Lemonade is now the unconditional runtime.

### Features

- **Lemonade Server unified inference runtime** (PR-3 #156 through
  PR-22). One `lemond` process per host on `127.0.0.1:13305`, cache
  - config at `/var/lib/hal0/lemonade/`, supervised by
  `hal0-lemonade.service`.
- **`LemonadeProvider`** is the only `Provider` in v0.2's dispatch
  path. Capability dispatcher reads `/v1/health` for slot state and
  routes through Lemonade's `/v1/chat/completions` / `/v1/embeddings`
  / `/v1/rerank` / `/v1/audio/*` / `/v1/images/*` endpoints.
- **FLM trio NPU packing** (PR-19 #201, PR-20 #202). Lemonade's
  `flm.args = "--asr 1 --embed 1"` packs chat + transcription +
  embedding into one `flm serve` process sharing the single AMDXDNA
  hardware context. hal0 exposes three slots (`agent`, `stt-npu`,
  `embed-npu`); the capability dispatcher reads
  `/v1/health.loaded[].backend_url` for the FLM model and routes
  `stt-npu` / `embed-npu` requests directly to the child's port
  (Lemonade only knows about the chat role). NPU exclusivity (one
  `device = "npu", type = "llm"` slot enabled at a time) is enforced
  in `capabilities.toml` validation; chat-model swap surfaces a
  "swap incoming, voice + embed paused" UX. See ADR-0009.
- **OmniRouter client-side tool-calling** (PR-16 #189). 8 tools — 5
  upstream-mirrored (`generate_image`, `edit_image`,
  `text_to_speech`, `transcribe_audio`, `analyze_image`) + 3
  hal0-custom (`embed_text`, `rerank_documents`, `route_to_chat`).
  Dynamic per-request filtering: a tool is included in the LLM
  prompt only if at least one enabled slot of its target type exists
  AND (for label-gated tools) at least one of those slots has a
  model with the required labels. LLMs without the `tool-calling`
  label receive no tools. `route_to_chat` is one-shot delegation,
  blocked at depth=1, blocked across NPU LLM slots.
- **First-run bundle picker** (PR-17 #196, PR-18 #198).
  `capabilities.toml` ships empty by design; the dashboard's first
  load renders four hardware-anchored tiers (`hal0-Lite` ≥16 GB /
  `Default` ≥32 GB / `Pro` ≥64 GB / `Max` ≥100 GB Strix Halo) plus
  the AMD-curated `LMX-Omni-52B-Halo` kit, with a "Skip — configure
  manually" path. Tiers that don't fit detected unified RAM grey out
  with a tooltip. Bundle manifests live at
  `/var/lib/hal0/models/collections/omni/`. The NPU trio is opt-in
  even at Pro and Max tiers. See ADR-0010.
- **Settings → Lemonade admin panel** (PR-13 #183). Surfaces
  `/internal/config` snapshot + `/internal/set` atomic writes for a
  curated subset of keys. Guards against overriding `llamacpp.args`
  to an unbounded value (would cause the multi-LLM CPU
  oversubscription deadlock).
- **Journal panel folded into Logs tab** (PR-14 #184). Lemonade's
  `/logs/stream` WebSocket streams into the dashboard's event ring,
  alongside hal0's own structured journal.
- **Metrics shim** (PR-12 #179). Per-slot TTFT + tok/s +
  prompt_tokens scraped from `/v1/stats`. FLM-native KV%
  (`kv_token_occupancy_rate_percentage`) on NPU slots. See known
  limitations below for the GPU-slot KV% gap.
- **`[CPU]` chip + tooltip** on the voice slot card (PR-15 #186)
  disclosing that kokoro is CPU-only in v0.2. GPU TTS deferred to
  v0.3.
- **Dashboard reads `/v1/health` for slot state** (PR-11 #163);
  surfaces NPU exclusivity, FLM trio coresident marker, and the
  nuclear-evict banner via `/logs/stream` line parsing.
- **Mandatory `llamacpp.args = "--parallel 1 --threads N"`** in the
  `lemond` config baseline (PR-5 #159). N is computed at install
  time as `(cores − 2) / 4`, min 2. Without this, two concurrent
  child llama-servers oversubscribe the CPU and freeze the Vulkan
  dispatch — a hard install-time requirement, not a tunable.
- **Per-type LRU concurrency.** Six independent type budgets
  (`llm`, `embedding`, `reranking`, `transcription`, `tts`, `image`)
  reported by `/v1/health.max_models`; default global budget set to
  4. Nuclear evict-all only fires when a `/v1/load` errors AND the
  error message does NOT substring-match "not found" / "does not
  exist" / "No such file" — common failure modes (bad path, missing
  variant, mistyped name) return graceful errors and leave the
  loaded pool intact.
- **Slot model**: bare-name identity + `type` (Lemonade vocab:
  `llm | embedding | reranking | transcription | tts | image`) +
  `device` (`gpu-rocm | gpu-vulkan | cpu | npu`) + `model` + `enabled`
  - optional `default` + `group` for dashboard rollup. User-added
  slots via `hal0 slot add NAME --type TYPE --model MODEL`. Exactly
  one `default = true` per type enforced at save / load.
- **Canonical model namespace.** `registered` (no prefix, from
  `registry.toml` → Lemonade's `server_models.json`) vs `user.*`
  (on-demand pulls via `POST /v1/pull`). `extra.*` auto-discovery
  unused. Dashboard surfaces two badges: `blessed` and `pulled`.
- **`hal0 registry sync`** (PR-6 #141 → #151) — regenerates
  `/var/lib/hal0/lemonade/resources/server_models.json` from
  `registry.toml` and restarts `lemond`. Hourly drift detector
  surfaces a dashboard banner when `registry.toml` is newer than
  `server_models.json`.
- **`hal0 registry import`** (PR-21 #203) — single command, restores
  `registry.toml` from a v0.1.x backup tarball. Slot selections must
  be redone via the bundle picker.
- **`hal0 doctor` extended** to probe `lemond` reachability + FLM
  `.deb` presence (Linux NPU path).

### Internal

- **22 implementation PRs landed across 6 sub-phases.** Foundation
  (PR-2 #137, PR-3 #156), install + registry (PR-4 #157, PR-5 #159,
  PR-6 #141 → #151, PR-7 #158), slot layer rewrite (PR-8 #161, PR-9
  #160, PR-10 #162), UI + metrics (PR-11 #163, PR-12 #179, PR-13
  #183, PR-14 #184, PR-15 #186), OmniRouter + bundles (PR-16 #189,
  PR-17 #196, PR-18 #198), NPU + close-out (PR-19 #201, PR-20 #202,
  PR-21 #203, PR-22 — this PR).
- **`SlotManager` simplified** ~358 LOC in PR-10 (#162) — provider
  ABC dispatch + per-slot systemd adoption logic deleted.
- **Legacy provider classes preserved as code** (used by image-gen /
  hardware-probe / catalog non-slot consumers) but no longer in the
  Lemonade dispatch path.
- **`SlotConfig.device` refactor + `capabilities.toml`
  `schema_version=2` migration** (#143 → #153).
- **Preload validation + idle-unload driver** (#144 → #152) shipped
  ahead of ADR-0007 supersession; preload validation removed per
  ADR-0008 §3 in `e660fa3`.
- **`src/hal0/lemonade/`** — HTTP client + `catalog_sync.py` +
  `metrics_shim.py` + `log_proxy.py`.
- **`src/hal0/omni_router/`** — client + tool definitions
  (checksum-pinned mirror of Lemonade upstream's
  `toolDefinitions.json`; CI script `scripts/check-tool-definitions.sh`
  fails on drift).
- **NPU FLM trio dispatch carve-out** documented in
  ADR-0009 — narrow exception to ADR-0008's "Lemonade owns
  inference lifecycle" thesis; scoped to the two endpoint paths
  (`/v1/audio/transcriptions`, `/v1/embeddings`) that Lemonade
  doesn't know exist on the FLM child.
- **v0.2.1 dashboard rewrite** (slice #176, PR #199) cut over on
  `main` in parallel; PR #197 carries v2 polish work and remains
  open at v0.2 ship.

### Known limitations

- **KV% for GPU slots reads `—`.** Lemonade's bundled `llama-server`
  (b9253 Vulkan, b1274 ROCm) returns `null` for `n_past` /
  `n_prompt_tokens` / `prompt` in `/slots` responses, even during
  active inference. PR #124's KV%-from-`/slots` strategy did not
  survive the migration. FLM/NPU slots get KV% native from the
  `kv_token_occupancy_rate_percentage` field in
  `/v1/chat/completions` responses. v0.2.x patch path: hal0 builds
  its own llama-server and swaps via `lemonade config set
  llamacpp.{rocm_bin,vulkan_bin}` if upstream doesn't populate the
  fields within ~6 weeks. See ADR-0008 §Costs.
- **Kokoro TTS is CPU-only in v0.2.** No upstream GPU-Kokoro on
  Linux at v0.2 ship. UI surfaces a `[CPU]` chip + tooltip on the
  voice slot card. GPU-accelerated TTS deferred to v0.3.
- **Performance: parity-to-regression vs the v0.1 hal0-Vulkan
  baseline** (-13% to -18% on tested models in spike #1;
  hermes-14b at parity). Accepted in exchange for the
  six-toolbox-to-one-runtime maintenance collapse.
- **NPU LLM swap is slow (~14s).** Changing the `agent` slot's
  chat model tears down the FLM trio (stt + embed go with it) and
  restarts `flm serve <new-chat-model> --asr 1 --embed 1`. UI
  surfaces "swap incoming, voice + embed paused".
- **FLM .deb install is manual on Linux.** Lemonade's `flm:npu`
  auto-installer is Windows-only as of v0.2. Linux install
  procedure is PPA `lemonade-team/stable` + libxrt-npu2 + ffmpeg6
  - boost1.83 + fftw3 + FastFlowLM `.deb`. The hal0 installer
  handles this end-to-end; users running off-script need the
  `hal0_lemonade_flm_npu_install` recipe.
- **Ongoing pin maintenance** for two upstream artifacts (the
  Lemonade embeddable tarball + the FastFlowLM `.deb`). Each hal0
  release manually bumps both pins, sha256-verifies, and CI-smokes
  the install + a triple-concurrency probe before tagging.

[v0.2.0]: https://github.com/Hal0ai/hal0/releases/tag/v0.2.0
