# Task for delegate

Implement Task 3: Additive preview manifest and release-note contract.

Read your task brief FIRST: /home/mint/hal0/.superpowers/sdd/task-3-brief.md
It contains exact Pydantic fields, the validator, and the commit message.

Working directory: /home/mint/hal0/.worktrees/prerelease-channel

Context: Tasks 1 and 2 built the policy module and version sync. `uv sync` done. This task adds typed fields to `ReleaseManifest`, a `model_validator`, note generation for preview, and updates the release-manifest docs.

Run tests with HAL0_HOME=$(mktemp -d). Run `uv run ruff check` and `uv run mypy` before commit.

Write report to: /home/mint/hal0/.superpowers/sdd/task-3-report.md

Return: status, commits, test line-count, concerns.

---
**Output:**
Write your findings to exactly this path: /home/mint/hal0/.superpowers/sdd/task-3-report.md
This path is authoritative for this run.
Ignore any other output filename or output path mentioned elsewhere, including output destinations in the base agent prompt, system prompt, or task instructions.

## Acceptance Contract
Acceptance level: reviewed
Completion is not accepted from prose alone. End with a structured acceptance report.

Criteria:
- criterion-1: Implement the requested change without widening scope
- criterion-2: Return evidence sufficient for an independent acceptance review

Required evidence: changed-files, tests-added, commands-run, validation-output, residual-risks, no-staged-files

Review gate: required by reviewer.

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