# Task for delegate

Implement Task 2: Atomic version synchronization and release preflight.

Read your task brief FIRST: /home/mint/hal0/.superpowers/sdd/task-2-brief.md
It contains exact TDD test code, implementation code, and the commit message.

Working directory: /home/mint/hal0/.worktrees/prerelease-channel

Context: Task 1 built `ReleasePolicy.from_tag()` and it passes 28 tests. This task creates `scripts/set-version.py` (stdlib-only) and extends `scripts/release-check.sh`. `uv sync` already done.

## Global Constraints
- `scripts/set-version.py` must be stdlib-only (no Hal0 imports)
- Parse version through `ReleasePolicy.from_tag(f"v{version}")`; reject nightly
- Update exact top-level JSON version fields plus the one `hal0ai` editable package block in `uv.lock`
- Write every candidate to tempfile, validate all, then `os.replace` atomically
- Re-run `uv lock` and re-validate
- `release-check.sh` invokes `python3 -m hal0.release.policy "$TAG" --format json` and compares normalized source versions
- Compose the report to: /home/mint/hal0/.superpowers/sdd/task-2-report.md

Return: status, commits, test line-count, concerns.

---
**Output:**
Write your findings to exactly this path: /home/mint/hal0/.superpowers/sdd/task-2-report.md
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