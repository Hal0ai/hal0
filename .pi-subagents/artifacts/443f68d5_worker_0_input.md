# Task for worker

You are a delegated subagent running from a fork of the parent session. Treat the inherited conversation as reference-only context, not a live thread to continue. Do not continue or answer prior messages as if they are waiting for a reply. Your sole job is to execute the task below and return a focused result for that task using your tools.

Task:
In /home/mint/hal0 on branch rework/descar at ea3e4661, fix ONLY the listed tests below. Do not touch src/hal0/. Each fix must assert the 1.0 contract: workload profile names (chat/chat-long-context/dense/moe/embedding/reranking/cpu-chat/flm/kokoro/qwen3-tts/comfyui), no profile.image field, slot-owned image/device.

Files and exact fixes:

1. tests/api/test_install_apply.py::test_apply_seeds_jobs_and_creates_slots
   Current: `assert chat["profile"] in ("rocm", "vulkan")`
   New:     `assert chat["profile"] in ("chat", "dense", "moe", "chat-long-context")`
   (the workload-oriented 1.0 default for an LLM GPU slot is one of the workload profiles; verify the actual produced value with the live test, but assert membership of the canonical workload set, not backend names)

2. tests/api/test_meta_enums.py::test_payload_matches_canonical_taxonomy
   Current: `body["device_default_profiles"]["cpu"] == "cpu-llm"` and similar
   New:     rename expected literal to `"cpu-chat"` and any other expected literals to their canonical workload names. Find all literals in this test that use retired names and rewrite them. The canonical mapping is in the report; verify against the live test's actual output before finalizing.

3. tests/cli/test_doctor_profiles.py::test_images_warn_when_in_use_image_not_pulled and test_images_ok_when_repo_present_regardless_of_tag
   Current: p = _profile("rocm", image="...", used_by=("primary",)); assert rows[0]["status"] == ...
   These test profile-image warnings, which are obsolete because ProfileConfig no longer carries image. Rewrite both tests to instead exercise the slot-image check that replaced this (look in src/hal0/cli/doctor_commands.py for the slot-side equivalent; if none, delete these two tests as obsolete — but prefer rewrite, not removal). Keep the imports clean.

4. tests/config/test_schema_npu.py::test_seed_tts_toml_validates
   Current: `assert slot.profile == "tts"`
   New:     `assert slot.profile == "kokoro"` (the actual seeded slot TOML is now workload-oriented — verify the file under tests/config/_seeded_slots/ or wherever it points is consistent with the renamed profile).

5. tests/golden_paths/test_gp05_stamped_launch_layering.py::test_launch_builder_emits_no_profile_or_slot_flag_segment
   Current: `assert labels == {"base", "model_extra_args", "model_defaults", "chat_template", "mmproj"}`
   New:     the actual set the test sees is `{"base", "chat_template", "mmproj", "model_extra_args", "slot_hardware"}` (slot_hardware is the new segment from spec-hw-slot-ownership; model_defaults is no longer a separate segment because it was merged into model_extra_args). Update the assertion to `{"base", "chat_template", "mmproj", "model_extra_args", "slot_hardware"}` and update the test docstring/comment to reflect the merged segment.

6. tests/install/test_orchestrate.py::test_apply_setup_creates_chat_slot_and_plans_pull and test_apply_setup_scaffolds_modelless_slot_without_pull
   Both assert the seeded slot's profile name. Update:
   - chat slot: `("chat", "dense", "moe")` instead of `("rocm", "vulkan")`
   - embed slot: `"embedding"` instead of `"embed"`
   Run the tests to discover the actual current value, then fix the literals accordingly.

7. tests/registry/test_duplicate_refcount.py::test_duplicate_with_profile_stamps_flags
   Uses `ProfileCatalog().resolve("cpu-llm")`. Replace with `resolve("cpu-chat")` and adapt the expected flag behavior to whatever cpu-chat stamps (run the test to see current actual output, then update expectations).

8. tests/slots/test_device_profile_coherence.py — all 4 failures
   These tests assert pre-1.0 backend-named profiles (`rocm`, `vulkan`) interact with backend-named devices (`gpu-rocm`, `gpu-vulkan`) to detect contradictions. The 1.0 contract says profiles are device-agnostic — the contradiction check should be against `profile.backend` (still in ProfileConfig). Read the existing tests, find the contradiction-detection path, and rewrite the assertions to use:
     - workload names: "chat", "kokoro", "embedding", "reranking" (instead of "rocm"/"vulkan")
     - still detect contradictions via `profile.backend` if it is set
     - drop tests that no longer apply because profiles are device-agnostic (e.g. test_device_change_reconciles_conflicting_profile should now be a no-op OR document that device flip does NOT change workload profile).
   Be conservative: keep as many tests as possible, just update the profile-name literals.

9. tests/slots/test_model_preferred_profile.py — 2 failures
   - test_create_adopts_compatible_preferred_profile: uses `profile="vulkan"`. Update to workload name. The slot.profile comes from _register(...) — verify the actual value and assert against the canonical workload name.
   - test_profile_fits_slot_matrix: `fits("vulkan", gpu_vulkan)` — update to `fits("chat", gpu_vulkan)` (workload profile is device-agnostic, so "chat" fits both gpu-rocm and gpu-vulkan slots).

10. tests/stacks/test_apply_plan.py::TestGuardedReconcile — 2 failures
    - test_conflicting_device_profile_is_flagged_not_applied: passes a stack with `device="gpu-vulkan"` + `profile="rocm"`. Since profiles are device-agnostic in 1.0, the contradiction check is via `profile.backend`. Rewrite the test to set `profile="chat", backend="rocm"` and assert the stack still detects a backend-vs-device mismatch. Or, if the stack engine no longer detects this, drop the test.
    - test_device_flip_repoints_stale_profile: expects `after["profile"] == "rocm"` after a device flip from gpu-vulkan → gpu-rocm. In 1.0, profile is workload-oriented and does NOT change on device flip. Rewrite to assert `after["profile"]` is unchanged (whatever was there before).

11. tests/updater/test_seed_profiles_migration.py — 4 failures
    Tests build profiles TOML using `_seed_table("rocm")` / `_seed_table("vulkan")` (removed seeds) and a `profile.embed` with `image=` (removed field). Fixes:
    - Replace `_seed_table("rocm")` with `_seed_table("chat")` (or whatever canonical chat seed exists).
    - Replace `_seed_table("vulkan")` with `_seed_table("dense")` or another canonical GPU workload seed.
    - The `profile.embed` block in test_divergent_seed_named_entry_is_rescued_not_deleted uses `image = "..."`. Rewrite the block to drop the `image = ...` line (or route through `_seed_table("embedding") + custom flags`).
    - The `_seed_table("rocm")` KeyError means SEED_PROFILES no longer has "rocm". Verify the actual canonical chat seed key (likely "chat") and substitute.
    Update the resulting expected `on_disk` snapshots to match the new keys.

12. tests/providers/test_flm.py::test_image_ref_honors_slot_override
    Current: `assert provider.image_ref({"image": "ghcr.io/dev/flm-pin:test"}) == "ghcr.io/dev/flm-pin:test"`
    The production FLM provider now reads `image_pin` (per its source comment), not `image`. Update the test to pass `image_pin=` instead of `image=`.

Constraints:
- Do not touch src/hal0/.
- Do not add a legacy alias layer.
- For every fixed file, re-run the targeted test (single file) to verify it passes locally with `PATH="$PWD/.venv/bin:$PATH" pytest -q tests/<path>/test_file.py`.
- After all 12 groups fixed, run the full Python suite: `PATH="$PWD/.venv/bin:$PATH" pytest -q tests/ --tb=line` and record the final tail line (e.g. "0 failed, 6901 passed, 13 skipped, 1 xfailed"). Do not commit or push.

Output:
- List of files modified.
- Final pytest summary line.
- One-line description per file of the change.
- Any test that could not be fixed without modifying src/ (state the reason).

## Acceptance Contract
Acceptance level: checked
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Implement the requested change without widening scope

Required evidence: changed-files, tests-added, commands-run, residual-risks, no-staged-files

Finish with a fenced JSON block tagged `acceptance-report` in this shape:
Use empty arrays when no items apply; array fields contain strings unless object entries are shown.
`criteriaSatisfied[].status` must be exactly one of: satisfied, not-satisfied, not-applicable.
`commandsRun[].result` must be exactly one of: passed, failed, not-run.
`manualNotes` and `notes` are optional strings; an empty string means no note and does not satisfy `manual-notes` evidence.
```acceptance-report
{
  "criteriaSatisfied": [
    {
      "id": "criterion-1",
      "status": "satisfied",
      "evidence": "specific proof"
    }
  ],
  "changedFiles": [
    "src/file.ts"
  ],
  "testsAddedOrUpdated": [
    "test/file.test.ts"
  ],
  "commandsRun": [
    {
      "command": "command",
      "result": "passed",
      "summary": "short result"
    }
  ],
  "validationOutput": [
    "validation output or concise summary"
  ],
  "residualRisks": [
    "none"
  ],
  "noStagedFiles": true,
  "diffSummary": "short description of the diff",
  "reviewFindings": [
    "blocker: file.ts:12 - issue found, or no blockers"
  ],
  "manualNotes": "anything else the parent should know"
}
```