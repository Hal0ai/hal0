# Task for delegate

You are implementing the first task of the official prerelease release publication plan: `Task 1: Deep release-policy module`

Read your task brief FIRST: /home/mint/hal0/.superpowers/sdd/task-1-brief.md
It contains the full task text, exact types, interfaces, tests, commands, and commit message.

Your working directory is: /home/mint/hal0/.worktrees/prerelease-channel

Context: this is a stdlib-only module whose one interface — `ReleasePolicy.from_tag(tag)` — powers tag classification, channel routing, and GitHub/PyPI publication decisions.

## Global Constraints
- Stdlib-only: `re`, `dataclasses`, `argparse`, `json`, `Literal` from `typing`
- Tag forms: `v1.0.0-alpha.N`, `v1.0.0-beta.N`, `v1.0.0-rc.N`, `v1.0.0`, `v1.0.0-nightly.14digit`
- `to_github_outputs()` returns lowercase string booleans

## Your Job
1. Read the brief, implement via TDD, run tests with `HAL0_HOME=$(mktemp -d)`
2. `uv sync` first to get the editable install
3. `uv run ruff check` and `uv run mypy` before committing
4. Write a concise report to: /home/mint/hal0/.superpowers/sdd/task-1-report.md

Return ONLY: status, commits, test line-count, and concerns.

---
**Output:**
Write your findings to exactly this path: /home/mint/hal0/.superpowers/sdd/task-1-report.md
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