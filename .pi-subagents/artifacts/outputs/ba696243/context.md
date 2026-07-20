# Code Context — PR #1324 Test-Failure Archaeology (Workload-Profile Renames + Slot-Owned Image/Device Changes)

## Files Retrieved

### Directly Reusable Test-Fix Commits

1. **`54a5dc68` feat: define 1.0 workload-oriented seeded profiles** (HEAD, Jul 20 2026)
   - **Severity**: HIGH — this is the `workload-profile rename` commit itself
   - **Renames seed profile slugs**: `rocm` → `chat`, `rocm-dense` → `dense`, `rocm-moe` → `moe`, `vulkan-dense` → (removed), `vulkan-moe` → (removed), `rocm-longctx` → `chat-long-context`, `vulkan` → (removed), `cuda` → (removed), `embed` → `embedding`, `rerank` → `reranking`, `vulkan-embed` → (removed), `vulkan-rerank` → (removed), `tts` → `kokoro`, `tts-qwen3` → `qwen3-tts`, `cpu-llm` → `cpu-chat`
   - **Deletes old profile slugs entirely**: `vulkan`, `vulkan-dense`, `vulkan-moe`, `cuda`, `vulkan-embed`, `vulkan-rerank` — reorganized into the 2x2 backend x {dense,moe} grid
   - **Fixes 39 test files** (1300 insertions/1027 deletions) adapting all old-slug/`image`/`n_gpu_layers`/`preferred_runner` references
   - **Key test files**: `tests/api/test_models_crud.py` (133 lines), `tests/api/test_profiles_crud.py` (91 lines), `tests/config/test_profiles.py` (143 lines), `tests/config/test_seeds_parity.py` (365 lines), `tests/providers/test_container.py` (140 lines), `tests/providers/test_image_resolution.py` (171 lines), `tests/slots/test_model_preferred_runner.py` (DELETED, 141 lines)

2. **`d4253f8f` feat(flags): flags own by models — strip slot flag/device/template surface + copy-on-stamp profiles + migrator** (Jul 19 2026)
   - **Severity**: HIGH — FLAGS-own centerpiece
   - **14 test files** (999 insertions, 305 deletions)
   - New: `src/hal0/config/migrations/slot_flags_fold.py` (462 lines)
   - Changed: `src/hal0/providers/container.py` (133 lines) — removes profile-flag injection
   - Tests: `test_gp05_stamped_launch_layering.py`, `test_container*.py`, `test_capability_injection.py`, `test_parallel_batching.py`, `test_runtime_launch_plan.py`, `test_slot_flags_fold.py` (new, 190 lines)

3. **`471c365a` fix(tests): migrate unit-rerender test to FLAGS-own semantics** (Jul 19 2026)
   - **Severity**: HIGH — directly fixes CI failure on `test_unit_rerender.py`
   - Fix: registers model with `ModelDefaults(extra_args="-fa on -b 1024")` before `_mk_slot()` calls
   - Single file: `tests/updater/test_unit_rerender.py` (17 lines added)

4. **`9e842528` fix(slots): per-slot image status reads root's store with tag precision** (Jul 19 2026)
   - **Severity**: MEDIUM — slot-owned image (rootful store) fix
   - `src/hal0/providers/podman_introspect.py` (31 lines), `src/hal0/api/routes/slots.py` (31 lines)
   - New test: `tests/api/test_slots_image_pull.py` (44 lines)
   - Changed: `tests/providers/test_podman_introspect.py` (41 lines)

5. **`aff99e5e` feat(images): rocmfpx as universal default + hal0 update slot migration (#1297)** (Jul 13 2026)
   - **Severity**: MEDIUM — slot-owned image resolution
   - `src/hal0/providers/container.py` (30 lines), `src/hal0/updater/updater.py` (46 lines)
   - New test: `tests/config/test_default_image_gate.py` (38 lines)
   - Changed: `tests/providers/test_image_resolution.py` (24 lines), `tests/updater/test_image_retag.py` (47 lines)

### Unrelated (Excluded)

1. `9bde4b76` feat(slots): §7 slot-purity — fold chat_template into model (#1325) — UNRELATED (slot tier, not profile/device)
2. `e5c99919` / `f7e498ad` feat(slots): id-keying naming-seam flip — UNRELATED (slot ID migration, not profile/device)
3. `28f497c5` feat(api): typed request bodies (#1322) — UNRELATED (API typing surface)
4. `nfs/fix/static-slot-seed-test-pollution` — test infra, not profile/image/device
5. `nfs/feat/slot-publish-host` — infrastructure, not profile/image/device
6. `nfs/feat/1108-safe-slot-activation` — slot activation, not profile/image/device
7. `nfs/feat/1111-post-update-drift` — post-update drift, not profile/image/device
8. `nfs/feat/image-cli` — CLI image commands, separate scope

## Key Code

### Seed Profile Renames (54a5dc68 — seed_profiles.toml diff)

```
[profile.rocm]         → [profile.chat]
[profile.rocm-dense]   → [profile.dense]
[profile.rocm-moe]     → [profile.moe]
[profile.rocm-longctx] → [profile.chat-long-context]
[profile.embed]        → [profile.embedding]
[profile.rerank]       → [profile.reranking]
[profile.tts]          → [profile.kokoro]
[profile.tts-qwen3]    → [profile.qwen3-tts]
[profile.cpu-llm]      → [profile.cpu-chat]
DELETED: vulkan, vulkan-dense, vulkan-moe, cuda, vulkan-embed, vulkan-rerank
```

Key semantic shifts in the new profile model:

- Profiles are device-agnostic workload templates (no `image`, no `backend`)
- `image` field removed — resolved at slot level via HW-gated default
- `flags` simplified: no `-ngl`, no image pin — launch flags come from model.defaults.extra_args
- `preferred_runner` moved from Model to SlotConfig.binary

### FLAGS-own architecture (d4253f8f)

```
BEFORE: profile.flags + slot_overrides + model_extra_args → container argv
AFTER:  model.defaults.extra_args + model_defaults + chat_template → container argv
        (profile.flags is copy-on-stamp only, never read at launch)
        (slot flag surface inert, kept for backward compat – HAL0-SUNSET)
```

### Unit rerender test fix pattern (471c365a)

```python
# BEFORE: _mk_slot() used unregistered slots → assertion failed
# AFTER:
reg = ModelRegistry()
try:
    reg.get("some-model")
except Exception:
    reg.add(Model(
        id="some-model", path="/tmp/some-model.gguf",
        capabilities=["chat"],
        defaults=ModelDefaults(extra_args="-fa on -b 1024"),
    ))
```

## Architecture

Commit chain (newest → oldest), with relevance annotated:

```
54a5dc68  (HEAD)  workload profile renames         ← PR #1324 context
44a25835          sync with #1326 (board sync)
3ddd9f58          #1326 board sync
34905e44          board sync
9bde4b76          §7 slot-purity (#1325)            ← UNRELATED
5edac8a8          sync with #1323
a894188c          MCP autogen (#1323)               ← UNRELATED
1c455cd0          sync with #1322
28f497c5          typed bodies (#1322)              ← UNRELATED
ea658abc          sync post-#1321
471c365a          fix test (FLAGS-own)               ← REUSABLE
d4253f8f          FLAGS-own backend                  ← REUSABLE
9e842528          slot image status                  ← REUSABLE
aff99e5e          rocmfpx default                    ← REUSABLE
```

PR #1324 has NO exact git-ref'd commit in this repo. The workload-profile rename landing is commit `54a5dc68` at HEAD. Its parent `44a25835` is a sync with #1326 (board). If PR #1324 = workload-profile renames, then `54a5dc68` IS that PR's content.

## Start Here

Open **`tests/config/test_profiles.py`** — the most concentrated delta of profile-slug renames (143 lines changed). Then **`tests/providers/test_image_resolution.py`** (171 lines) for the image ownership changes. Cross-reference with `tests/api/test_models_crud.py` (133 lines) for the `preferred_runner`/`n_gpu_layers` removal pattern and new `slot.hardware_flag_denied` tests.

## Reusability Summary

| Commit | Reusable? | What it provides |
| -------- | ----------- | ------------------ |
| `54a5dc68` | YES | Blueprint: exact slug mapping + field removals across 39 test files |
| `471c365a` | YES | Pattern: register Model with ModelDefaults before slot tests |
| `d4253f8f` | YES | Pattern: FLAGS-own assertions, slot_flags_fold test template |
| `9e842528` | YES | Pattern: tagged-ref matching for slot-owned image status |
| `aff99e5e` | YES | Pattern: HW-gated default image resolution tests |
| `9bde4b76` | NO | Slot-purity fold, different concern |
| `e5c99919`/`f7e498ad` | NO | Slot ID migration, different concern |

## Constraints & Risks

1. **`54a5dc68` is at HEAD** — any PR #1324 review must rebase or verify 39 test adaptations match expectations.
2. **Seed profile DELETIONS** — `vulkan`, `cuda`, `vulkan-embed`, `vulkan-rerank`, `vulkan-dense`, `vulkan-moe` slugs removed. Any consumer referencing them gets 404.
3. **`image` field banned from profile bodies** — POST/PUT with `"image": "..."` gets 4xx. Replace with `"flags": "-fa on"`.
4. **`preferred_runner` tests fully removed** — three model CRUD tests deleted; field now on SlotConfig.binary.
5. **`n_gpu_layers` dropped from model defaults** — `test_scan_with_rows_persists_user_overrides` no longer asserts it.

---

```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "Searched all local and remote refs (142 branches across local, nfs remotes, and nfs/feat/fix/lane namespaces). Found 5 directly reusable commits (54a5dc68, 471c365a, d4253f8f, 9e842528, aff99e5e) with exact file paths, line ranges, diff content, and severity ratings. Identified 6 unrelated branches/commits excluded from scope. Output written to /home/mint/hal0/.pi-subagents/artifacts/outputs/ba696243/context.md."
    }
  ],
  "changedFiles": [
    "tests/updater/test_unit_rerender.py",
    "tests/api/test_profiles_crud.py",
    "tests/api/test_profiles_route.py",
    "tests/api/test_activity_routes_instrumented.py",
    "tests/api/test_models_crud.py",
    "tests/api/test_slots_routes.py",
    "tests/api/test_slots_container_state.py",
    "tests/config/test_profiles.py",
    "tests/config/test_seeds_parity.py",
    "tests/config/test_seeds_data.py",
    "tests/config/test_hw_slot_ownership_migration.py",
    "tests/config/test_schema.py",
    "tests/config/test_loader_profiles_save.py",
    "tests/providers/test_container.py",
    "tests/providers/test_image_resolution.py",
    "tests/providers/test_capability_injection.py",
    "tests/providers/test_container_assembler.py",
    "tests/providers/test_podman_introspect.py",
    "tests/api/test_slots_image_pull.py",
    "tests/config/test_default_image_gate.py",
    "tests/updater/test_image_retag.py",
    "tests/slots/test_model_preferred_runner.py (DELETED)",
    "tests/config/test_slot_flags_fold.py (NEW)"
  ],
  "testsAddedOrUpdated": [
    "tests/config/test_slot_flags_fold.py",
    "tests/api/test_slots_image_pull.py",
    "tests/config/test_default_image_gate.py",
    "tests/config/test_hw_slot_ownership_migration.py",
    "tests/cli/test_slot_migrate_hw.py",
    "tests/runners/test_registry.py"
  ],
  "commandsRun": [
    {
      "command": "git log --oneline --all --grep=\"1324\"",
      "result": "passed",
      "summary": "No commit references PR #1324; PR not yet merged or not git-referenced"
    },
    {
      "command": "git log --oneline --all --grep=\"workload-profile\" -i",
      "result": "passed",
      "summary": "Found 1 match: 54a5dc68 feat: define 1.0 workload-oriented seeded profiles"
    },
    {
      "command": "git log --oneline --all -50",
      "result": "passed",
      "summary": "Mapped commit chain: HEAD (54a5dc68) through descar→main R5 collapse"
    },
    {
      "command": "git show 54a5dc68 --stat",
      "result": "passed",
      "summary": "102 files changed, 8051 insertions, 6471 deletions. 39 test files modified."
    },
    {
      "command": "git branch -a",
      "result": "passed",
      "summary": "Enumeration of 142 branches: local (21), nfs remote (121)"
    }
  ],
  "validationOutput": [
    "Verified all 5 reusable commits are in the local commit graph and have full diff content available",
    "Verified 6 unrelated commits/branches excluded from reuse",
    "Test file changes confirmed by examining --stat and -- tests/ diff output for each commit"
  ],
  "residualRisks": [
    "PR #1324 does not appear as a Git ref or commit message in this repo; workload-profile landing (54a5dc68) is at HEAD without an explicit PR merge commit — confirm PR number mapping",
    "39 test files were adapted in 54a5dc68; any test file NOT in that set still referencing old slugs will fail",
    "vulkan/cuda/vulkan-embed/vulkan-rerank/vulkan-dense/vulkan-moe slugs deleted completely (no redirect/substitute) — CI scripts or external consumers may break"
  ],
  "noStagedFiles": true,
  "diffSummary": "Read-only archaeology: no edits performed. Report written to output path.",
  "reviewFindings": [
    "Blocker: seed profile slugs renamed in 54a5dc68 — any test referencing [profile.rocm], [profile.vulkan], [profile.embed], [profile.rerank], [profile.tts], [profile.cpu-llm], [profile.rocm-longctx], [profile.rocm-dense], [profile.rocm-moe] will fail with 404 or field-validity errors",
    "Blocker: image field removed from profile bodies — tests POSTing/PUTting profiles with image field will get 4xx schema errors",
    "Blocker: preferred_runner removed from model CRUD — three tests deleted; any CI test still asserting model.preferred_runner will fail",
    "Blocker: n_gpu_layers dropped from model defaults assertions — test_scan_with_rows_persists_user_overrides will fail if still asserting n_gpu_layers: -1",
    "Non-blocker: 471c365a test_unit_rerender.py fix pattern should be applied to any slot-creation tests in the PR branch that lack ModelRegistry pre-registration"
  ],
  "manualNotes": "The workload-profile rename (54a5dc68) is already at HEAD of this repo. If PR #1324 is an OPEN review branch (not yet pushed), its tests need the same 39-file adaptation. The exact slug-to-slug mapping, image/flag/device field removals, and preferred_runner deletion pattern are fully captured in 54a
