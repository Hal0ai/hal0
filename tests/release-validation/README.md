# hal0 release-candidate validation kit

A versioned, replicable, agent-driven validation suite for release candidates. It is the
successor to the three ad-hoc workflows used for `v1.0.0-rc.4` (`rc4-readonly-sweep`,
`rc4-stateful-lane`, `rc4-verify-majors`), generalised so every release runs the same lanes,
carries forward what the previous release learned, and gets sharper rather than starting over.

This kit is **not** a substitute for `pytest` or `scripts/release-test.sh`. Those verify what we
already knew to check. This kit is exploratory: agents drive the real product on real boxes the
way a user would, and the things they find are folded back into the kit — and, when they are
mechanical enough, into `pytest`/`release-test.sh` so they stop needing an agent at all.

## Layout

| Path | What it is |
|---|---|
| `kit.toml` | Kit version, lane registry, model tier per lane, phase configuration |
| `boxes.toml` | The fleet: box profiles, access details, capabilities, reset procedure |
| `known-issues.yaml` | Do-not-re-report list — open dupes and adjudicated by-design behaviour |
| `regressions.yaml` | Every previously filed finding, re-probed on every subsequent release |
| `lanes/readonly/*.md` | Read-only surface briefs (safe to run in parallel, no mutations) |
| `lanes/stateful/*.md` | Stateful briefs (serialised on one box, mutations expected) |
| `lanes/update/*.md` | In-place upgrade briefs (run on the update box, parallel to the fresh box) |
| `templates/report.md` | Report skeleton the synthesis phase fills in |
| `reports/*.md` | One committed report per validated release |

The orchestration lives at `.claude/workflows/rc-validate.js` (and `rc-fix-fleet.js` for the
optional fix wave). Workflow scripts have no filesystem access by design, so they carry **paths**,
not content: each agent reads its own brief, `CONTEXT.md`, `known-issues.yaml`, and
`regressions.yaml` itself.

## Running a validation

```sh
# 1. Reset and install the RC on the target boxes (operator, out of band):
#    ct151-cpu-fresh  -> pct rollback 151 pristine, fix resolv.conf, fresh install
#    ct150-update     -> stage the new preview manifest, leave it on the PREVIOUS release
#
# 2. Then, from a Claude Code session in the hal0 repo:
```

Invoke the `rc-validate` workflow with args:

```json
{
  "version": "1.0.0-rc.5",
  "boxes": ["ct151-cpu-fresh", "ct150-update"],
  "mode": "file",
  "repo": "/abs/path/to/hal0/worktree"
}
```

* `mode: "report"` — findings, verification, and a written report. Nothing is filed.
* `mode: "file"` — the above, plus a GitHub issue per confirmed finding. **Default.**
* `lanes: ["api","cli",…]` — restrict the run (smoke re-check after a hotfix).

The fix wave is deliberately a **separate** workflow (`rc-fix-fleet`) and only runs when the
operator asks for it. It takes the filed issue numbers, works ~6 agents wide (see *Process
lessons*), and monitors each PR to a conclusion.

## Phases

| # | Phase | Agents | Tier | Barrier? |
|---|---|---|---|---|
| 0 | Preflight | 1 per box | sonnet | yes — a version/reachability mismatch aborts the run |
| 1 | Read-only sweep | 1 per read-only lane | sonnet | no (pipelines into triage) |
| 2 | Stateful lane | 1 per stateful lane, **serialised** | sonnet, effort high | serialised by construction |
| 2b | Update lane | 1–2 on the update box | sonnet, effort high | runs concurrently with 2 (different box) |
| 3 | Regression probes | 1 per `regressions.yaml` batch | fable (mechanical) / sonnet (judgment) | no |
| 4 | Triage + dedup | 1 | opus | yes — needs every finding at once |
| 5 | Adversarial verify | 1 skeptic per candidate | opus, effort high | no |
| 6 | Synthesis + report | 1 | opus | yes |
| 7 | File issues (`mode: file`) | 1 | sonnet | — |
| 8 | Kit curation | 1 | sonnet | — |

Phase 8 is what makes the kit compound: it writes back the new known-issues, promotes every
filed finding into `regressions.yaml`, and appends any new check an agent invented to the lane
brief that should have contained it. Its output is a diff for the operator to review, not a
silent commit.

A full two-box run is roughly 25–30 agents. Restrict `lanes` for smaller passes.

## Process lessons (carried forward, do not relearn)

* **Serialise stateful work per box.** Two agents mutating one hal0 install produce findings
  neither can reproduce.
* **Run fix agents ~6 at a time.** A 12-wide fleet was killed twice by host restarts during the
  rc.4 wave, losing uncommitted work each time.
* **No per-PR CHANGELOG hunks during a fix wave.** Every merge re-conflicts every remaining
  branch (tracked as #1545). Consolidate in one CHANGELOG PR at the end.
* **The merge train catches what per-branch CI cannot.** rc.4's #1808 × #1799 integration break
  was invisible on both branches. Re-run the suite on the merged tip, not on the branches.
* **Adversarial verification pays for itself.** Four of rc.4's would-be issues were refuted or
  reclassified as by-design before filing.
* **Skeptics need the source.** Verifiers read the installed tree (`/usr/lib/hal0/venv/...`),
  not just the black-box behaviour.
* **The idle reaper (300 s, `idle_timeout_s`) evicts slots between stages.** A stage that finds
  a slot offline should reload it and say so, not report it as a regression.
* **`/mnt/ai-models` ggufs on the workstation are often symlinks into `huggingface/hub`** —
  staging them to a box needs `rsync -L`. And `/mnt/ai-models` is not in hal0's model roots, so
  a staged gguf is invisible until `hal0 model add` runs.
* **A fix that names one surface usually fixed one surface.** rc.5's three half-fixes (#1802
  fixed two of four ctx surfaces, #1807 fixed the seeded profile path but not `slot create`,
  #1797's telemetry gate covered two of three pages) all passed a narrow re-probe. When a
  regression entry names a surface, probe the whole class.
* **`promote_ready: true` in `regressions.yaml` is a to-do list.** An entry that is mechanical
  and stable belongs in `pytest` or `scripts/release-test.sh`, and should be deleted from the kit
  in the PR that writes the test.

## Versioning policy

`kit.toml` carries `kit_version`. Bump it in the same PR as any change to lane briefs,
known-issues, or regressions, and add a line to the changelog below. A report records the
`kit_version` it ran under, so an old report can always be read against the rules it ran with.

### Kit changelog

* **2** (2026-08-11) — curation after the `v1.0.0-rc.5` run. `regressions.yaml` grows from 11 to
  26 entries: the 15 findings filed as #1827–#1841, plus an `rc5_note` and `last_result` on every
  rc.4 entry recording what actually held on a real box. Two rc.4 repros were rewritten because
  they were unrunnable as written — `brain-tools-image-gate` no longer asks a probe agent to
  repin the shared brain slot, and `fact-extraction-strips-literals` is now explicitly gated on a
  retain reaching a terminal state. `known-issues.yaml` grows from 8 to 29: eighteen candidates
  the adversarial pass refuted or ruled by-design are banked with their rationale and a
  `still_report_if` clause, so the next run does not spend budget re-deriving them; six entries
  are flagged `review_due` against v1.0.0. Lane briefs absorbed the checks agents invented this
  run — the ones that paid were **generalisations of a previous release's narrow check**: sweep
  every bank sub-resource rather than `/stats`; compare context windows on all four surfaces
  rather than two; assert provisioning honesty on every phase and on warnings and skips, not just
  recorded failures; gate GPU telemetry per page rather than per dashboard. Also: the brain lane
  gets `budget_min = 20` (a single CPU tool turn exceeds the 12-minute default, which is why its
  last three checks went untested twice running), the memory lane's dependency on an untouched
  `utility` slot is recorded as an ordering constraint in `kit.toml`, and `boxes.toml` records
  that ct151 is wedged, that ct152 has no snapshot and carries residue, and precisely why
  ct105-prod cannot cross-check the two hermes fresh-install defects.
* **1** (2026-08-10) — initial extraction from the rc.4 validation workflows. Five read-only
  lanes, six stateful lanes, one update lane, 11 regression entries seeded from rc.4 issues
  #1787–#1797, four adjudicated by-design entries in known-issues.
