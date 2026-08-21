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
| `provenance/*.md` | Where a pinned artefact came from — the tree, refs and digests behind an image a release cites (#1970) |
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
| 3 | Regression probes | 1 per `regressions.yaml` batch | fable (mechanical) / sonnet (judgment) | no — except the `serialize: true` subset, which runs sequentially after the whole sweep (its repros mutate box-wide state) |
| 4 | Triage + dedup | 1 | opus | yes — needs every finding at once |
| 5 | Adversarial verify | 1 skeptic per candidate | opus, effort high | no |
| 6 | Synthesis + report | 1 | opus | yes |
| 7 | File issues (`mode: file`) | 1 | inherits session tier | — |
| 8 | Kit curation | 1 | sonnet | — |

Phase 8 is what makes the kit compound: it writes back the new known-issues, promotes every
filed finding into `regressions.yaml`, and appends any new check an agent invented to the lane
brief that should have contained it. Its output is a diff for the operator to review, not a
silent commit.

A full two-box run is roughly 25–30 agents. Restrict `lanes` for smaller passes.

Tier labels are enforced in the workflow script via explicit `model:` pins (kit v5 — before
that, only mechanical/triage/verify/report were pinned and every other phase silently inherited
the invoking session's tier). The one deliberate exception: File issues (phase 7) still
inherits the session tier.

## Process lessons (carried forward, do not relearn)

* **Serialise stateful work per box.** Two agents mutating one hal0 install produce findings
  neither can reproduce.
* **Run fix agents ~6 at a time.** A 12-wide fleet was killed twice by host restarts during the
  rc.4 wave, losing uncommitted work each time.
* **Fix agents must never merge their own PR.** Green CI is not review. During the rc.5 wave four
  agents self-merged between 03:44 and 05:22 UTC while the Review phase had not started, so those
  reviews were written against code already on `main`. The round then returned **7 of 10 PRs
  changes-requested**, two of which did not fix the reported defect at all and one of which
  introduced a fresh cache-poisoning regression — all of it live on `main` and needing a
  fix-forward wave. Opening the PR and getting it green is the fix agent's terminal state; the
  Land phase merges, and only after an independent reviewer has read the diff.
* **Commit and push early inside an agent worktree.** Every mid-flight kill so far (three across
  rc.4 and rc.5) cost exactly the work that was sitting uncommitted. Squash-merge makes noisy
  intermediate commits free. See the rescue recipe when a wave dies with dirty agent trees.
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

* **5** (2026-08-20) — pre-rc.7 brief hardening from the GA-plan handoff, applied before the
  kit's first v-4-era full run. Four new checks close verification gaps for fixes that would
  otherwise ship CI-verified only: `post-upgrade.md` 2b (the #1934 gpu-vulkan→gpu-rocm slot
  relabel on the update box, device-key-only, non-llama untouched, journal breadcrumbs,
  idempotency), `upgrade.md` 2b (the #1883 channel-URL + `.json.bundle` proxy path, which the
  update box's pinned GitHub-asset `HAL0_RELEASES_URL` otherwise bypasses entirely),
  `slots.md` 8b (#1922's output-sanity readiness gate must be verified as a product gate, not
  merely worked around by the coherence canary), and `hermes-e2e.md` 9 (doctor-perms
  convergence re-probed after all mutation, plus a manual owner/group `stat` — #1942 proved
  green-after-mutation on the mode axis only). `upgrade.md` check 4 inverted to expect the
  #1935-restored audit trail (populated `job_id`, prepare/verify breadcrumbs) instead of
  documenting their absence as designed. Model tiers are now enforced: every phase's `agent()`
  call carries an explicit `model:` pin matching `[defaults]` (previously preflight, all lanes,
  judgment regressions, and curation silently inherited the invoking session's tier). The
  verify prompt's fleet note no longer claims kfd-less fresh boxes "run the Vulkan lane"
  (stale since #1923) — and neither do `boxes.toml`'s ct151/fleet-coverage notes: post-#1923,
  gpu-rocm LLM slots REFUSE to start on kfd-less boxes (no CPU fallback); LLM coverage there
  means device=cpu slots. ct151 gains gotcha 5: post-rollback services need
  `systemctl start hal0.target`. Adversarial review of this version's PR (#1956) then
  corrected four of its own checks before first use: the post-mutation stat targets
  `/var/lib/hal0/STATE.md` (not a `hermes/` subdir); the upgrade "before" snapshot now
  explicitly captures each slot TOML's `device` value so the migration diff is executable;
  the #1934 check is gated on **#1960** (filed from that review: the updater runs
  post-activation migrations pre-swap in the OUTGOING tree, so a release's new migrations
  never fire on the upgrade that ships them); and the channel-URL check drives the
  cosign-verified fetch the only way it can be reached — `HAL0_RELEASES_URL` in
  `/etc/hal0/api.env` plus `PUT /api/updates/channel` — instead of a shell env var and
  `update --check`, which never verify. `tests/release/test_rc_validate_kit_contract.py`
  now pins `kit.toml [defaults]` against the script's model pins so the tier contract cannot
  silently rot.

* **4** (2026-08-15) — curation after the `v1.0.0-rc.6` run. Landed as mode=report, so the 17
  new entries first carried `issue: null`; the filing PR then filed all of them (plus one more
  found during Codex review of that PR, `chat-to-embed-slot-500-passthrough`, #1894) and filled
  in every issue number — `regressions.yaml` now carries no unfiled entries. `regressions.yaml`
  grows from 26 to 44 entries: every previously registered entry gets an rc.6 `last_result` (11
  fixed, 2 regressed-but-known, the rest partial/blocked with the exact residue named), and 18
  new entries land — headlined by
  `brain-vulkan-backend-garbage-output`, the run's most severe finding (the pinned runner's
  Vulkan lane computes wrong values for every model while every health surface stays green).
  Its lesson is promoted into `_shared.md` as a global **coherence canary**: no lane may trust
  generated text before a temp-0 "capital of France" probe passes, because two lanes green-lit a
  box that never produced language. Two register entries were REWRITTEN because their expect
  clauses had gone wrong rather than stale: `slot-capacity-vram-attribution`'s "no
  compute-capable GPU => no VRAM" clause was refiring as a false positive on every vulkan-only
  box (the correct predicate is `vulkan_capable OR compute_capable`), and
  `hermes-polish-rollup` demanded a shipped `web_dist` the rc.4 report itself had ruled
  infeasible. `known-issues.yaml` grows to 47: sixteen rc.6 adjudications banked with rationale
  and `still_report_if` (notably: the update job's restart_error breadcrumb on success, the
  chat-to-embed 500 pass-through, capability-apply persisting intent on lifecycle failure, the
  model-add detection surfacing residue of #1855, and the hermes wheel/web_dist ruling), plus
  rc6_notes on four existing entries whose clauses fired — including deleting the
  `spawn_context_refresh` misdiagnosis from `hermes-state-md-as-of-is-change-marker` (a wrong
  guess in a still_report_if clause sent a verifier chasing it; clauses must state symptoms, not
  theories). Lane briefs absorbed the run's invented checks: name-set diffs instead of counts
  (MCP docs tables, footer service chips), grounding probes for memory extraction and the
  steward, hang triangulation for hermes turns, service-user writability sweeps, the
  args-wrapper and no-session MCP probes, and mid-lane contention re-diffs (three rc.6 slot
  checks were invalidated by foreign zz* slots). `boxes.toml` corrected: ct151 has iGPU+NPU
  passthrough and an `rc6-installed` snapshot (gpu=false, HAL0_ALLOW_CPU_ONLY and the sysfs
  gotchas were all stale — preflight caught every one); ct150 is privileged/GPU, now on rc.6 via
  the GitHub asset URL; the "no GPU fresh box" standing-gap note replaced with the real
  remaining gap (no fresh-install box that also has `/dev/kfd` — ct151 exercises Vulkan-only,
  ct150/ct105 have `/dev/kfd` but are not fresh installs; ct152-cpu-fresh still exists and is
  CPU-only, gpu=false, no snapshot; host ROCm only on prod; record each box's actual
  ROCm-vs-Vulkan lane at preflight).
* **3** (2026-08-12) — `smoke-preflight-skips-chat-probes` (#1831) rewritten. As filed it probed
  only hermes' default live-resolve config — the half of the defect rc.5 fixed — so it would have
  reported `fixed` while the `HAL0_HERMES_LIVE_RESOLVE=0` mode the issue actually named stayed
  broken. It now probes both resolution modes, names the pinned mode as the one to look for, and
  requires any recorded skip reason to be checked against the endpoint in `model.base_url` rather
  than the gateway catalog. Retiered `judgment`, `promote_ready: false`: the unit-level half moved
  into `tests/agents/test_hermes_provision.py`, and what remains needs a live box. The lesson
  generalises — **a register entry that cannot fail is worse than none**, and an entry probing
  only the configuration a fix was written against is exactly that.
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
