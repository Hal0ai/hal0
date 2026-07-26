# Task for worker

You are a delegated subagent running from a fork of the parent session. Treat the inherited conversation as reference-only context, not a live thread to continue. Do not continue or answer prior messages as if they are waiting for a reply. Your sole job is to execute the task below and return a focused result for that task using your tools.

Task:
In /home/mint/hal0 on branch rework/descar at ea3e4661, fix the 123 failing tests in PR #1324 by updating ONLY the test files and fixtures. Do NOT modify any source under src/hal0/, do NOT add a legacy alias layer, do NOT re-add `ProfileConfig.image`. The 1.0 contract is: image is slot-owned, profile is workload-oriented (chat/dense/moe/cpu-chat/embedding/reranking/kokoro/qwen3-tts/flm/comfyui), backend lives on SlotConfig.device.

Inputs:
- Full CI failure log: /tmp/pi-bash-7057d38c6a881d3e.log
- Reference docs: docs/rework/hal0-specs/spec-hw-slot-ownership.md
- Canonical seeded profile names: chat, chat-long-context, dense, moe, embedding, reranking, cpu-chat, flm, kokoro, qwen3-tts, comfyui

Method:
1. Run pytest on each failing file once to capture remaining failure shapes and to detect interactions between test classes (use -p no:cacheprovider --no-header -q --tb=line). DO NOT skip on first failure — read the actual current assertion.
2. For every failing test, fix the TEST file or its fixtures so it asserts the canonical 1.0 contract. Do not touch src/. Tests should reflect that images come from the slot (via SlotConfig.image_pin / runner default / runner key 'binary') and that profile names are workload-oriented.
3. Apply targeted edits with the edit tool; do not use sed/awk. Show file:line before/after in the report.
4. After every batch, re-run only the tests you just fixed. When the entire failing set passes locally with `PATH="$PWD/.venv/bin:$PATH" pytest -q tests/ --tb=line` (full suite — should land at 123 → 0 failures with the full count above 7000), record the exact command and the final tail line.
5. NEVER mark the task complete without running the full Python suite end-to-end. Report the exact `X failed, Y passed, Z skipped` final line.
6. Do not commit, do not push. Leave the working tree dirty for review. Report all modified files.

Specific already-known issues you must handle:
- `tests/config/test_profiles.py::test_image_is_not_a_profile_field` — KEEP as-is; it's a guard. Do not delete.
- `tests/updater/test_seed_profiles_migration.py` tests build pre-migration dict fixtures; construct via raw dict then route through the migrator's input path (not `ProfileConfig(...)`).
- `tests/cli/test_doctor_profiles.py` IndexError failures — these hit `check_profile_images_present` which now returns []; convert to asserting the slot-image check path that replaced it, OR remove the obsolete assertions if the doctor surface intentionally omits the profile-image warning.

Output a final report containing:
- Exact list of files modified (path only)
- Final pytest summary line (e.g. '0 failed, 6901 passed, 13 skipped, 1 xfailed, ...')
- The two green-run commands you executed
- Any test that could not be fixed without touching src/, with the exact reason

## Acceptance Contract
Acceptance level: reviewed
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Implement the requested change without widening scope
- criterion-2: Return evidence sufficient for an independent acceptance review

Required evidence: changed-files, tests-added, commands-run, validation-output, residual-risks, no-staged-files

Review gate: optional by reviewer.

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
    },
    {
      "id": "criterion-2",
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