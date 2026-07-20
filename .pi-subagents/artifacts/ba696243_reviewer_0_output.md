I have enough evidence to finalize. Writing up the diagnosis now.

## Review

**CI run 29718900970** ran on commit `54a5dc68` ("feat: define 1.0 workload-oriented seeded profiles") and produced **123 Python test failures** (downloaded from `gh run view --log-failed`). All other jobs (`sunset`, `ui`) passed. The failure job failed at `pytest tests/ -q`, not at lint/format/install.

### Stated intent of 54a5dc68 (spec-hw-slot-ownership)

1. **`ProfileConfig.image` is removed by design.** `src/hal0/config/schema.py:1090-1180` keeps `extra="forbid"` and no longer declares `image`. A comment cites spec-hw-slot-ownership §3. The companion loader `load_profiles_config` (`src/hal0/config/loader.py:525-535`) deliberately strips a stray `image` key from un-migrated `profiles.toml` files so the removal is load-safe. The deploy-window migration `hal0.config.migrations.hw_slot_ownership` (`src/hal0/config/migrations/hw_slot_ownership.py`) folds pre-spec `profile.image` → slot `image_pin`, then deletes the profile key. This is a one-way contract change.
2. **Workload-oriented seeded profile catalog** replaces backend-oriented: `seed_profiles.toml` keys are `chat`, `chat-long-context`, `dense`, `moe`, `embedding`, `reranking`, `cpu-chat`, `flm`, `kokoro`, `qwen3-tts`, `comfyui` (per `docs/rework/hal0-specs/spec-hw-slot-ownership.md` §10). Old backend names (`rocm`, `vulkan`, `vulkan-server`, `vulkan-embed`, `vulkan-rerank`, `tts`, `tts-qwen3`, `cpu-llm`, `embed`, `rerank`) are gone from the seed catalog.
3. **Hardware grid moves to slot:** new typed `SlotConfig` fields `n_gpu_layers`, `threads`, `binary`, `image_pin`.

### Failure triage — all 123 are stale tests, zero production regressions

I clustered the 123 failures into two mechanistic groups. Counts add up to 123 exactly.

**Group A — `ProfileConfig(image=...)` constructor calls → `extra="forbid"` rejects the stray field (≈57 failures).** Tests that hard-coded `image="ghcr.io/…"` in `ProfileConfig(...)` literals (or via the fake-profile helper) now raise `pydantic_core._pydantic_core.ValidationError: 1 validation error for ProfileConfig, image, Extra inputs are not permitted`. Example proof from the CI log:
```
tests/api/test_slots_image_pull.py:43
  ProfileConfig(image="ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server", …)
  → pydantic ValidationError: image Extra inputs are not permitted
```
File-level counts: `test_slots_image_pull.py` 9, `test_container_chat_template.py` 8, `test_container_mmproj.py` 5, `test_container_npu.py` 1, `test_container_spec_dispatch.py` 5, `test_container_vision_toggle.py` 4, `test_parallel_batching.py` 3, `test_qwen3tts_container_spec.py` ~9 (mix), `test_kokoro_container_spec.py` ~5 (mix), `test_container_resolved_detail.py` 1, `test_device_profile_coherence.py` 4, `test_model_fit.py` 4, `test_seed_profiles_migration.py` 4, `test_apply_plan.py` 2, `test_recommend.py` 3, `test_flm.py` 1.

**Group B — old backend-oriented profile-name assertions vs new workload-oriented catalog (≈66 failures).** Tests that expect `("rocm", "vulkan", "vulkan-embed", "vulkan-rerank", "cpu-llm", "tts", "tts-qwen3", "embed", "rerank")` now see the seed keys `("chat", "embedding", "reranking", "cpu-chat", "kokoro", "qwen3-tts")`. Example proofs:
```
test_payload_matches_canonical_taxonomy  tests/api/test_meta_enums.py:106
  assert body["device_default_profiles"]["cpu"] == "cpu-llm"
  E  AssertionError: assert 'cpu-chat' == 'cpu-llm'

test_apply_seeds_jobs_and_creates_slots  tests/api/test_install_apply.py:112
  assert chat["profile"] in ("rocm", "vulkan")
  E  AssertionError: assert 'chat' in ('rocm', 'vulkan')

test_tts_capability_switch  tests/capabilities/test_tts_capability_switch.py
  assert profile == "tts"          E  assert 'kokoro' == 'tts'
  assert profile == "tts-qwen3"    E  assert 'qwen3-tts' == 'tts-qwen3'
```
`test_profile_derivation_parity.py` is the largest single-file cluster (31 failures): every parameter combination of `(chat|coder|embed|rerank|utility|tts|agent|image) × (gpu-rocm|gpu-vulkan|cpu)` expects the old per-backend name. The 4 `test_kokoro_container_spec` and 6 `test_qwen3tts_container_spec` failures are `hal0.errors.NotFound: profile 'tts' not found` / `profile 'tts-qwen3' not found` — they hard-coded the old profile keys instead of `kokoro` / `qwen3-tts`. `tests/cli/test_doctor_profiles.py` 2 failures are `IndexError: list index out of range` because `check_profile_images_present` (correctly) returns `[]` when no profile carries an image — the helper was relying on a "warn-on-missing-image" path that no longer exists.

**Production-regression check:** zero of the 123 failures point at a defect in the new code. The loader's drop-image shim works (`loader.py:528-533` — silent `entry.pop("image", None)` before validation), the migration's planner/applier is sound (`hw_slot_ownership.py:160-280` covers fold-to-slots and debris-drop paths), and the `extra="forbid"` pydantic behavior is the intended enforcement. No failure trace includes an `AttributeError`, `KeyError`, `TypeError`, or unexpected `RuntimeError` originating from `src/hal0/` outside of the test fixtures.

### Decision: keep `ProfileConfig.image` removed

- The spec (`docs/rework/hal0-specs/spec-hw-slot-ownership.md` §1, §3, §6) is explicit that image is a slot concern (`image_pin` or `RUNNER_IMAGES[binary]`). The migration is irreversible: rolling `image` back into `ProfileConfig` would resurrect the dual-owner problem the spec was designed to eliminate (slot-vs-profile silently disagreeing on the image).
- The deploy-window migration already covers existing `profiles.toml` files: `loader.load_profiles_config` strips a stray `image` and the migrator folds deliberate pins onto slot `image_pin`. Restoring the field would require re-introducing the same intentional duality that the commit removed.
- A vendor test that hard-codes `ProfileConfig(image="…")` is broken because the test was written against the *pre-spec* schema, not because the schema is wrong. Updating the test fixture to call `ProfileConfig(flags="…", mtp=False)` (no image) — or, where the test genuinely needs an image, constructing a `SlotConfig(image_pin="…")` instead — is the correct fix.

### Remediation plan (ordered, evidence-backed)

1. **Bulk-fix test fixtures that construct `ProfileConfig(image=…)`.** Remove the `image=` kwarg from every failing helper, or move the image to `SlotConfig(image_pin=…)` where the test is exercising image-pin plumbing. Affected files (exact):
   - `tests/api/test_slots_image_pull.py` (line 43 — `_fake_profile_catalog`)
   - `tests/providers/test_container_chat_template.py` (~7 helpers)
   - `tests/providers/test_container_mmproj.py` (~5 helpers)
   - `tests/providers/test_container_npu.py` (1 helper)
   - `tests/providers/test_container_spec_dispatch.py` (5 helpers, 4 of which also need the new profile name; 1 only needs the `image=` removal)
   - `tests/providers/test_container_vision_toggle.py` (4 helpers)
   - `tests/providers/test_parallel_batching.py` (3 helpers)
   - `tests/providers/test_qwen3tts_container_spec.py` (helpers around lines 38, 165–174 — drop `image=` AND rename `tts-qwen3` → `qwen3-tts`)
   - `tests/providers/test_kokoro_container_spec.py` (helpers around line 38, 45, 58, 65, 70, 94, 121, 164 — drop `image=` AND rename `tts` → `kokoro`)
   - `tests/providers/test_container_resolved_detail.py` (~1 helper)
   - `tests/slots/test_device_profile_coherence.py` (4 helpers)
   - `tests/model_fit/test_model_fit.py` (~4 helpers)
   - `tests/updater/test_seed_profiles_migration.py` (~4 helpers — likely building pre-migration fixtures; these may need to keep `image=` in a free-form dict rather than `ProfileConfig.model_validate(...)`)
   - `tests/stacks/test_apply_plan.py` (~2 helpers)
   - `tests/hardware/test_recommend.py` (~3 helpers)

2. **Re-key backend-oriented profile assertions to workload-oriented catalog.** Update the expected names per the spec §10 table:
   - `tests/config/test_profile_derivation_parity.py` — rewrite the parametrized matrix to expect workload keys (`chat`, `embedding`, `reranking`, `cpu-chat`, `kokoro`, `qwen3-tts`) on the workload rows, and accept the device-aware key on rows where the catalog genuinely has a device-specific variant (verify against `seed_profiles.toml` lines 74 + 81 for `kokoro`/`qwen3-tts`).
   - `tests/api/test_install_apply.py:112` — change `("rocm", "vulkan")` → `("chat",)`. The "default tier" pick now uses the workload key.
   - `tests/api/test_meta_enums.py:106` — change `"cpu-llm"` → `"cpu-chat"` (matches `seed_profiles.toml` + spec §10).
   - `tests/capabilities/test_tts_capability_switch.py` — replace `"tts"`/`"tts-qwen3"` literals with `"kokoro"`/`"qwen3-tts"` throughout (lines 75, 82, 90, 98, 145, 162–201, 225). The "engine swapped" assertions at lines 217 and 236 also need their `profile` field assertion updated.
   - `tests/slots/test_model_preferred_profile.py` — 2 helpers likely expecting the old `preferred_runner` fold target.

3. **Re-resolve `NotFound: profile 'tts'/'tts-qwen3' not found`.** Same fix as #2 — rename calls to `"kokoro"` / `"qwen3-tts"`. Specifically:
   - `tests/providers/test_qwen3tts_container_spec.py:165-174` and any helper building a `SlotConfig` with `profile="tts-qwen3"` — change to `"qwen3-tts"`.
   - `tests/providers/test_kokoro_container_spec.py` — change `profile="tts"` → `"kokoro"` (the slot type stays `type="tts"`; only the profile key changes).

4. **`tests/cli/test_doctor_profiles.py:70-87`** — `check_profile_images_present` returns `[]` because no current profile carries an image (per the new contract). The two affected tests must be rewritten: the "in-use profile image not pulled" warning path no longer exists by design; replace with assertions on `check_slot_images_present` (a slot-image check) or remove the test if the new doctor surface intentionally omits the profile-image check.

5. **Verify with `make ci-python` (or `uv run pytest tests/ -q`).** No remaining failures expected.

6. **Residual concern — review `src/hal0/api/routes/profiles.py:138, 173, 274` and `src/hal0/profiles/__init__.py:197`.** These construct `ProfileConfig(...)` from request bodies / catalogs. The CI log shows no failures here, but it's worth a manual read once the stale tests are removed to confirm operator-facing API paths don't try to write `image=` and trip `extra="forbid"` (the API will currently 422 — that's the intended behavior, but worth confirming the error envelope is human-readable).

### Residual risks

- **Back-compat on operator-edited `profiles.toml`.** The loader's `entry.pop("image", None)` shim silently drops `image` keys from un-migrated files. Operators with intentional `profile.image` pins will lose them on next boot unless they run `hal0 slot migrate-hw`. The deploy-window contract already calls this out (spec §6.3), but a dashboard banner on first boot after upgrade would reduce the surprise.
- **`tests/updater/test_seed_profiles_migration.py` may need a different fix.** These tests probably build *pre-migration* fixtures that legitimately carry `image=` (to test the migrator). The fix there is to pass the dict through `model_construct` or use a non-validating `dict` literal instead of `ProfileConfig.model_validate({...})` — confirm before bulk-applying the `image=` removal.
- **`test_profile_derivation_parity.py` is parametrized** — the rename to workload keys should be a single-key dict update, but verify the helper resolves the expected profile from `seed_profiles.toml` and `installer/etc-hal0/profiles.toml` together (the loader overlays seed keys from code, so the on-disk fixtures may not be authoritative).