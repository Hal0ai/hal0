## Review

### Correct

- **Canonical 1.0 catalog is unambiguous in the seed source and tests.** The intended immutable seeds are exactly:
  `chat`, `chat-long-context`, `dense`, `moe`, `embedding`, `reranking`, `cpu-chat`, `flm`, `kokoro`, `qwen3-tts`, and `comfyui`.
  - Source: `src/hal0/config/data/seed_profiles.toml:12-108`
  - Ratified contract: `docs/rework/hal0-specs/spec-hw-slot-ownership.md:115-143`
  - Exact-set assertion: `tests/config/test_seeds_parity.py:5-24`
- **Fresh-install references were moved to workload names rather than backend names.**
  - LLM seed slots use `chat`: `installer/etc-hal0/slots/agent.toml:17-24`, `installer/etc-hal0/slots/brain.toml:24-31`, `installer/etc-hal0/slots/utility.toml:15-24`
  - TTS seeds use `kokoro` and `qwen3-tts`: `installer/etc-hal0/slots/tts.toml:7-12`, `installer/etc-hal0/slots/qwen3tts.toml:12-17`
  - Reranker uses `reranking`: `installer/etc-hal0/slots/rerank.toml:9-14`
- **The implementation generally reflects the intended ownership split.**
  - Slot-owned hardware grid—`device`, `n_gpu_layers`, `threads`, `binary`, `image_pin`—is represented in `src/hal0/config/schema.py:353-405`.
  - Profiles no longer contain an `image` field: `src/hal0/config/schema.py:1101-1111`.
  - Seed profiles have no backend selector, image, or grid-owned flags; this is explicitly tested in `tests/config/test_seeds_parity.py:27-35`.
  - The partition guard covers both long and short forms of NGL/device/thread flags: `tests/slots/test_argv.py:389-435`.
- **The hardware migration correctly preserves deliberate legacy physical settings.**
  - Model NGL and preferred runner are folded to the slot.
  - Deliberate `profile.image` pins are copied to referencing slots; former-default debris is dropped.
  - Legacy `slot.image` becomes `slot.image_pin`, with slot-local image taking precedence over the profile image.
  - Old model columns are then nulled, and the operation is gated, dry-run-first, and idempotent.
  - Evidence: `src/hal0/config/migrations/hw_slot_ownership.py:1-49`, `tests/config/test_hw_slot_ownership_migration.py:46-288`.

### Intended old → new expectation map

These are **workload mappings**, not a request to retain backend-specific aliases as permanent seed profiles:

| Old profile/reference | Canonical expectation | Notes |
|---|---|---|
| `rocm` | `chat` by default | Backend moves to `slot.device="gpu-rocm"`. For an explicitly MoE or dense workload, choose `moe` or `dense` instead. The Saber seed demonstrates `rocm` → `moe`. |
| `vulkan` | `chat` by default | Backend moves to `slot.device="gpu-vulkan"`. An embedding/reranking slot previously using plain `vulkan` should instead use `embedding`/`reranking`. |
| `cuda` | `chat` | CUDA placement and runner/image are slot facts. |
| `rocm-longctx` | `chat-long-context` | Device/backend/NGL/threads are removed from the tune. |
| `rocm-dense`, `vulkan-dense` | `dense` | Backend distinction is entirely on the slot. |
| `rocm-moe`, `vulkan-moe` | `moe` | Backend distinction is entirely on the slot. |
| Historical `rocmfpx-rocm`, `vkfpx-dense`, `rocm-dnse` | `dense` | These had already passed through earlier naming generations. |
| Historical `vkfpx-moe` | `moe` | Same workload, backend now slot-owned. |
| `embed`, `vulkan-embed` | `embedding` | Workload-mode flag `--embedding` remains in the profile; backend/NGL/thread flags do not. |
| `rerank`, `vulkan-rerank` | `reranking` | Workload-mode flag `--reranking` remains in the profile. |
| `cpu-llm` | `cpu-chat` | `--threads` moves to the slot; `--threads-batch` remains a logical tune. |
| `tts` | `kokoro` | Runtime-family identity, not merely cosmetic renaming. |
| `tts-qwen3` | `qwen3-tts` | Runtime-family identity, not merely cosmetic renaming. |
| `flm` | `flm` | Unchanged canonical name. |
| `comfyui` | `comfyui` | Unchanged canonical name. |

A blind `rocm → chat` or `vulkan → chat` rewrite is therefore insufficient: the slot type/model workload must be considered. The changed seed stacks illustrate this distinction in `src/hal0/config/data/seed_stacks.toml:30-86`.

### Ownership boundary reconstructed

- **Model owns logical/model-intrinsic facts:** model defaults and family-specific logical tuning, MTP capability, Jinja capability, chat template, modality, architecture/quant metadata. It must not own ordinary device, NGL, thread count, runner, or image selection.
- **Profile owns a reusable logical workload template:** flags such as batch/ubatch, KV policy, flash attention, embedding/reranking mode, reasoning policy, and workload context policy. `mtp` remains only as one-release informational API/on-disk compatibility metadata.
- **Slot owns physical placement:** device/backend, NGL, CPU threads, binary/runner key, image pin, port, state, and lifecycle identity.
- **Runner registry owns default container images and fit metadata:** the slot’s `binary` selects a runner registry entry; `image_pin` is the explicit override.
- **Legacy model/profile hardware flags must not be accepted on new writes.** The deliberate compatibility boundary is read/migrate support, not continued creation of contradictory ownership.

### Legacy compatibility that should remain

1. **Do not recreate backend variants as immutable seeds.** Existing old names may remain as operator-owned custom data, as specified in `docs/rework/hal0-specs/spec-hw-slot-ownership.md:140-143`.
2. **Preserve old custom profile rows on load**, while ignoring/removing their obsolete `image` field so strict image-less schema validation remains possible.
3. **Preserve deliberate old image pins through the one-shot migration**, with slot-local pin precedence and stale-default debris removal.
4. **Preserve old model NGL/preferred-runner values only long enough to fold them onto slots**, then clear the legacy columns.
5. **Keep `ProfileConfig.mtp` for the promised one-release API/TOML compatibility window**, but never let it drive launch.
6. **Keep warning fallback to `chat` for a missing ordinary llama-server chat/tune profile.** This is suitable for retired backend/dense/MoE names when degraded-but-runnable chat is preferable to a hard failure.
7. **Do not use `chat` fallback for runtime-family or endpoint-mode profiles.** Old `tts`, `tts-qwen3`, `embed`, and `rerank` names require either a temporary semantic alias or an explicit slot-reference migration because falling back to chat changes the server process or endpoint mode.

### Blocker

- **Blocker / high — legacy TTS slots can silently launch the wrong runtime after upgrade.**
  - `src/hal0/providers/container.py:104-132` catches every missing profile and falls back to `chat`, regardless of slot type or former runtime family.
  - `src/hal0/profiles/__init__.py:102-126` recognizes only the exact canonical names `kokoro` and `qwen3-tts`; an operator-preserved profile named `tts` or `tts-qwen3` is classified as `llama-server` once its old image is removed.
  - `src/hal0/config/migrations/hw_slot_ownership.py:1-49` migrates hardware/image ownership but performs no profile-reference rename.
  - The fresh seed TOMLs were changed from `tts` → `kokoro` and `tts-qwen3` → `qwen3-tts`, but seed TOMLs do not overwrite existing operator slot files.
  - Consequently, both possible legacy states are unsafe:
    1. no materialized old profile → missing-name fallback selects `chat`;
    2. materialized old profile survives → exact-name runtime classifier still selects `llama-server`.
  - A one-release semantic alias/rebind for `tts → kokoro` and `tts-qwen3 → qwen3-tts`, or an explicit slot profile migration, is required before this can be considered upgrade-safe.

### Notes and contradictions

- **High — public reference documentation still advertises the removed backend/image-owned profile model.**
  - `docs/reference/providers-profiles-devices.mdx:86-107` lists `rocm`, `rocm-dense`, `rocm-moe`, `vulkan-*`, `cuda`, `embed`, `rerank`, `tts`, `tts-qwen3`, and `cpu-llm`, including an image column and MTP ownership.
  - `docs/reference/cli.mdx:248-252` lists the retired names as current profiles.
  - `docs/getting-started/index.mdx:40-43` says profiles are per-backend and provide image/MTP behavior.
  - `docs/getting-started/setup.mdx:89,99-101` and `docs/getting-started/wsl.mdx:27,76-78` still direct users to `cpu-llm`.
  - These are production-facing documents, not merely historical changelog entries.
- **Medium — production schema comments contradict the actual schema and ratified ownership.**
  - `src/hal0/config/schema.py:1090-1101` still describes a profile as a backend template supplying “image + bench-tuned flag bundle” and everything except model path/context/port.
  - `src/hal0/config/schema.py:1170-1177` still uses `[profile.rocm]` with an `image` key as the canonical example, even though `extra="forbid"` and the field has been removed.
  - The `backend` field description in `src/hal0/config/schema.py:1128-1140` calls profile backend authoritative, while canonical seeds deliberately set it to `None` and launch derives backend from `slot.device`.
  - The `flags` description says profiles contain no context arguments, but canonical `dense` and `moe` include `-c`: `src/hal0/config/data/seed_profiles.toml:29-46`.
- **Medium — production source comments still encode the old naming/ownership contract.**
  - `src/hal0/providers/container.py:1-10` says profiles supply image and MTP while slots supply only model/context/port.
  - `src/hal0/providers/container.py:104-118` documents fallback as backend profiles `rocm`/`vulkan`, although the implementation now prefers `chat`.
  - `src/hal0/install/profile_derive.py:1-11,111-160` repeatedly documents `rocm`, `vulkan`, `embed`, `vulkan-embed`, `rerank`, `vulkan-rerank`, `tts`, and `cpu-llm` even though the return values are canonical workload names.
  - `src/hal0/capabilities/profile_fit.py:1-47` similarly describes the retired backend-specific lanes.
  - `src/hal0/config/seeds.py:89-96` still calls the result “image sentinels resolved,” despite images and sentinel resolution having been removed.
- **Medium — endpoint-mode legacy names need the same scrutiny as TTS.**
  - Missing `embed`/`vulkan-embed` and `rerank`/`vulkan-rerank` references also fall back to `chat`, losing `--embedding` or `--reranking`.
  - The canonical fresh-install derivation is correct in `src/hal0/install/profile_derive.py:141-164`, but there is no test attesting old-name upgrade behavior.
  - These should be temporary semantic aliases or explicit reference migrations, not generic chat fallback.
- **Medium — compatibility coverage is incomplete and some UI E2E fixtures still attest the obsolete profile shape.**
  - `tests/config/test_hw_slot_ownership_migration.py` thoroughly tests physical-field migration but does not test profile-name migration/alias behavior.
  - `tests/install/test_profile_derive.py` verifies only fresh canonical derivation.
  - `ui/tests/e2e/specs/profiles-crud-v3.spec.ts:22-35,152-206` still uses `image`, `backend="rocm"`, `cloned_from="vulkan"`, and expects a `vulkan` seed card.
  - `ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts:25-38` retains a slot fixture with `profile: "rocm"`.
- **Note — historical changelog/archive references should remain historical.** Old names in `CHANGELOG.md` and `docs/archive/**` describe prior releases and should not be treated as current canonical guidance; unlike current reference/getting-started pages, they should not be mechanically rewritten without historical context.
- **Note — requested `plan.md` and `progress.md` were absent.** Reads of `/home/mint/hal0/plan.md` and `/home/mint/hal0/progress.md` returned `ENOENT`, so this review was reconstructed directly from commit `54a5dc68`, current source, migration, docs, and tests.
- **Fixed:** none; review was read-only.