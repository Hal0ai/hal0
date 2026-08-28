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
#    ct151-cpu-fresh  -> pct rollback 151 pristine, then the POST-ROLLBACK CHECKLIST in
#                        boxes.toml (rollback DELETES the dev0/dev3 passthrough entries —
#                        re-add + confirm gid=991; `pct set 151 --nameserver`, never an
#                        in-container resolv.conf edit; `apt install -y curl jq`), then
#                        fresh install
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

* **10** (2026-08-25) — ct151 reset facts relearned the hard way during the rc.9 fresh lane
  (#2064). `boxes.toml` `[boxes.ct151-cpu-fresh]`: gotcha 4 upgraded from a gid-drift check to
  a hard re-add — `pct rollback 151 pristine` DELETES the dev0/dev3 passthrough entries from
  the CT config outright (the pristine snapshot predates them); gotcha 1's DNS fix corrected to
  `pct set 151 --nameserver` on the hypervisor (an in-container resolv.conf edit is regenerated
  away by PVE on every boot); new gotcha 6 — the pristine minimal Ubuntu 26.04 image ships with
  neither curl nor jq, so `apt install -y curl jq` precedes the bootstrap. The notes now carry
  an explicit POST-ROLLBACK CHECKLIST (re-add passthrough + confirm gid=991, `--nameserver`,
  curl+jq, then install) and the README reset step points at it. Pinned by
  `tests/release/test_kit_ct151_reset_gotchas.py` so a curation pass cannot drop them.

* **9** (2026-08-22) — Curation from the v1.0.0-rc.7 skeptic/verify pass (10 candidate findings
  adversarially re-checked, 10 ended up split between real-new-defect and adjudicated-by-design).
  `regressions.yaml`: 10 new entries filed as `issue: null` (mode=report, nothing filed to
  GitHub yet) — `slot-drift-ignores-image-and-device` (major: slot edit+load on an already-ready
  slot never re-renders the container's image/device), `seeded-anchor-slots-rocm-on-kfd-less-box`
  (major: fresh-install LLM slot seeds hardcode `gpu-rocm` on ANY host, refusing loudly on every
  kfd-less box including the fleet's only zero-GPU box), `brain-steward-fabricates-live-platform-
  state` (ga-blocker: a brevity instruction alone flips the steward from tool-grounded to
  confidently-wrong on the shipped small brain-sft model; a second wrong-tool-selection mode
  reproduces without any brevity hint), `hermes-venv-missing-mcp-extra` (ga-blocker: the hermes
  venv ships with NO `mcp` package at all — `hermes-agent[web]` needs `[web,mcp]` — so both
  bundled MCP servers are silently dead behind every green self-check surface, reproduced on
  every fresh AND upgraded box), `capability-apply-skips-model-pull` (major: applying an
  undownloaded catalog row never creates a pull job and returns 200 after a ~3min crash-loop
  block), `update-rollback-cli-silent-on-restart-need` (minor: a real regression against closed
  #1541 — the CLI's own rollback banner never names the pending hal0-api restart, though the
  dashboard already does), `hindsight-response-format-400-via-thinking-policy` (ga-blocker: the
  single highest-value find this round — hal0-api's `/v1` unconditionally injects
  `chat_template_kwargs.enable_thinking=false`, which fatally collides with `response_format:
  json_object` on any Qwen3-templated model, taking down every hindsight fact-extraction call
  AND any client's structured-output request; this REFUTES two separate candidate findings that
  blamed hindsight and hermes respectively for what turned out to be one proxy-layer defect),
  `mcp-status-tools-iserror-on-error-state-resource` (major: the MCP admin wrapper's error
  sentinel collides with the slot object's own `status` field, so reading an error-state slot
  through MCP reports `isError: true` for a fully successful call), `services-health-omits-
  hindsight` (minor: `/api/services/health` hardcodes three ids and can never include hindsight,
  so the dashboard footer can never reflect a hindsight outage), and
  `capabilities-catalog-advertises-unavailable-gpu-rocm-for-registry-models` (minor: a locally
  `hal0 model add`-registered GGUF advertises `gpu-rocm` unconditionally, unlike curated rows,
  which correctly gate on host capability). Also updated three existing regression entries with
  `rc7_note`s: `crash-loop-lifecycle` (#1791, REFUTED as a regression — #2012's self-heal policy
  intentionally extended the crash-loop-to-`failed` window from ~21s to ~20min, `stopped` during
  that ramp is now correct, `expect` rewritten), `slot-capacity-vram-attribution` (#1839, the old
  ">5% divergence" reading self-triggered on a documented max(cgroup,estimate) floor for
  GTT-resident weights, `expect` rewritten around the floor).
  `known-issues.yaml`: 5 new by-design entries (`hermes-renders-upstream-400-as-context-
  exceeded`, `update-rollback-defers-hal0-api-restart`, `unversioned-openai-compat-paths-405-
  are-generic-not-rerank-specific`, `doctor-perms-split-brain-root-hermes-is-correct-detection`,
  `tts-catalog-lists-qwen3-on-gpu-rocm-regardless-of-host`) capture five candidate findings the
  verify pass refuted or adjudicated by-design, each with a `still_report_if` so the same
  ground isn't relitigated blind next release. Updated `still_report_if` on three existing
  entries where the old clause was proven over-broad or stale: `crash-loop-warming-180s-window`
  (the "container_status never reaches crashed while failed" clause self-triggered on #2012's
  new ~20min ramp), `slot-capacity-vram-on-vulkan-is-declared` (the ">5%" clause replaced with
  the max(cgroup,estimate)-floor-aware version), `memory-extraction-quality-is-anchor-dependent`
  (extended to cover the zero-yield case as the same axis as fabrication, with a discrimination
  procedure so "0 facts extracted" is not filed as a broken retain path without first ruling out
  duplicate-content dedup and anchor yield variance) and `load-restart-error-surfacing` (#1424,
  confirmed it covers the Vulkan-preflight-refusal bare-500 shape too, provided
  metadata.message is populated).
  `lanes/**.md`: folded in every check the run's agents invented that survived review — api.md
  gained the capabilities local-vs-catalog backend diff and a direct `/api/services` vs
  `/api/services/health` cross-check; cli.md strengthened its concurrent-lane attribution
  guidance and added a doctor-perms-vs-#1896 standing cross-check; mcp.md gained the isError/REST
  parity check as a standing (not one-off) item; hermes.md gained an explicit `hermes mcp test`
  connectivity check (batched with the smoke_tests live-state check) and named the exact
  hindsight-operations-list baseline endpoint; ui.md cross-references the new api.md services
  check so a backend omission is never misdiagnosed as a frontend defect; slots.md gained the
  podman-CreatedAt drift-detection recipe for the already-ready edit+load path, a zz*-prefixed
  contention pre-scan, and a seeded-anchor-device check in the baseline step; routing.md gained
  the response_format/json_object probe, a dispatch.decision cross-check standing instruction,
  and clarified the /rerank 405 as generic-not-rerank-specific; brain.md's fabrication test now
  requires a matched brevity/plain phrasing pair per question plus a tool-RESULT-semantics check,
  not just tool-frame presence; memory.md gained a hindsight-port-vs-hal0-wrapper cross-check, a
  same-marker retry through the newly-routed target, and a plain-chat-vs-extraction-slot A/B;
  capabilities.md gained the pull-before-load check and the local-vs-catalog backend check;
  upgrade.md gained the `.hal0.previous` snapshot-before-testing warning, the post-rollback
  `/api/health`-vs-CLI-version check, and a running-image-vs-resolved-image diff; post-upgrade.md
  gained the same running-image cross-check plus a memory ctx-preflight fail-fast-with-no-
  dangling-op probe.
  `boxes.toml`: `[boxes.ct151-cpu-fresh]` — the documented `rc6-installed` convenience
  post-install snapshot NO LONGER EXISTS (`pct listsnapshot 151` shows only `pristine` and
  `current`); `snapshot` and every reset instruction corrected to the single remaining path
  (`pct rollback 151 pristine`, then run the installer fresh). `[boxes.ct163-cpu-fresh]` gained
  its previously-missing `cores`/`ram_gb`/`api`/`auth_required` keys for consistency with the
  other three box entries, and its notes now state plainly that it is the fleet's only
  zero-GPU-passthrough box and reproduces `seeded-anchor-slots-rocm-on-kfd-less-box`.

* **8** (2026-08-21) — Vulkan-restoration coverage for the rc.7 fold (#1948), FINALIZED against
  the landed fold chain: #1954 (kfd identity by runner uid + device gid, `e5e5925a`), #1973 (the
  `gpu-vulkan` lane re-enable behind an explicit image allowlist, `3474ec03`), #1959 (repin to
  `ghcr.io/hal0ai/hal0-combined:0822`, `8f23afba`). Composed with v6/v7's `image_status`
  tri-state work rather than duplicated — the api.md/slots.md tri-state checks stay exactly as
  v6/v7 wrote them; this version only adds the Vulkan-specific checks and resolves the five
  `FINALIZE-AFTER-PHASE-D` markers a v6 draft (written in parallel with the lane re-enable PR)
  had left open, against the actual merged mechanics rather than a guess:
    1. **The gate is an explicit allowlist, not a version comparison.**
       `VULKAN_CAPABLE_IMAGE_REFS` (`config/schema.py`) is a `frozenset` earned per-image by the
       #1948 §3-C matrix — deliberately NOT "the pin or later", because the tags have no total
       order (git shortrefs, date stamps, names) and Vulkan correctness was empirically NOT
       monotonic in build recency (the bisect found an OLDER tree correct and a NEWER one
       broken). `image_serves_vulkan_lane()` checks membership; `effective_runner_image()` looks
       THROUGH a pending retag so a migration decision and a live-load decision can never
       disagree; `vulkan_lane_serves()` composes the two and is the one predicate the
       derivation ladders, the bench harness, the installer's shell-mirrored preflight, and the
       updater's migration all share.
    2. **Preflight refusal is `require_kfd_for_gpu_slot`'s `gpu-vulkan` branch**
       (`providers/_gpu.py`), which on the `llama` runtime lane on an AMD host now delegates to
       `_require_vulkan_lane_prerequisites`: gate 1 is the image allowlist above (refuses by
       name, cites #1888, names `VULKAN_FIXED_IMAGE` as the fix), gate 2 is
       `render_node_present()` for the runner identity (uid 0, the rootful slot container — not
       whoever calls the function). `/dev/kfd` is NOT consulted on this path at all — Vulkan
       needs no compute node, which is the whole point of the kfd-less-box restoration.
    3. **The capabilities catalog OFFERS the lane without gating on image** — confirmed
       `capabilities/catalog.py`'s llama.cpp fan-out: `gpu-vulkan` is offered whenever the host
       advertises that backend (a render node exists), independent of which image is installed.
       "The picker is an OFFER, not a promise" is the source's own comment; a slot created on
       that row still has to clear the load-time image gate and then #1922's readiness probe.
       api.md 6c rewritten to state this as fact, not a maybe.
    4. **`ENV_ALLOW_VULKAN_FALLBACK` survives unchanged** and now downgrades TWO correctness
       refusals to a warning — missing `/dev/kfd` on the ROCm path, and an unvalidated Vulkan
       image on the restored path — never the render-node check, which is a passthrough fact no
       env var can change. `boxes.toml` wording finalized accordingly.
    5. **Seeds stay `gpu-rocm` by operator ruling.** `install/profile_derive.derive_device`
       still derives ROCm first whenever `/dev/kfd` is usable, even though the §3-C matrix shows
       Vulkan is FASTER on the reference hardware (+13.96% prefill, +20.45% decode — the
       corrected direction from the old ade07ba-era "Vulkan ~-10%" expectation kit v5 carried;
       drop that stale expectation everywhere it appears). ROCm is offered first in the picker
       because it has the longer validation history and is what MTP/speculative decode is tuned
       on. The documented way to run Vulkan deliberately is `hal0 slot edit <slot> --hardware
       vulkan` — a manual opt-in, not a new default. Kit checks must not flag "seeded slot is
       still gpu-rocm on a box that could run Vulkan" as a defect.

  `boxes.toml`: `[boxes.ct152-cpu-fresh]` REMOVED — the box was destroyed 2026-08-21. There is
  now no CPU-only fresh box in the fleet at all; the fleet-coverage note says so plainly instead
  of the old "do not claim no CPU-only box remains" hedge, since that claim is now simply true.
  ct151's note and the fleet-coverage note both drop their `FINALIZE-AFTER-PHASE-D` HTML
  comments and state the real gate: an explicit `VULKAN_CAPABLE_IMAGE_REFS` allowlist, not a
  version/tag comparison.
  `lanes/stateful/slots.md` 8c/8d finalized: 8c names `_require_vulkan_lane_prerequisites`'s two
  gates explicitly instead of hedging on the wording; 8d's repro now greps
  `_require_vulkan_lane_prerequisites` and `image_serves_vulkan_lane` by name and expects the
  `GpuPreflightError` naming both #1888 and `VULKAN_FIXED_IMAGE`.
  `lanes/readonly/api.md` 6c finalized per point 3 above; the speculative 6b from the v6 draft
  (written before #1968 merged, hedging on whether it would) is DROPPED — v6/v7's check 6 and
  the `image-status-wrong-podman-store` expect clause already cover the tri-state fully and a
  second check saying the same thing would duplicate rather than compose.
  `lanes/update/upgrade.md` gains a check verifying the rc.6 -> rc.7 retag on ct150 actually
  lands `image_pin = ghcr.io/hal0ai/hal0-combined:0822` (both the legacy `image` key and the
  newer `image_pin` key are checked, with `image_pin` taking precedence per #1959's final
  commit, and the perf-row numbers are recorded as the new reference envelope — Vulkan ahead of
  ROCm, not behind).
  `regressions.yaml`'s two new entries keep their `from: v1.0.0-rc.7` (that is when the fold
  landed) even though this kit version is 8; `updater-image-pin-retag-blind-spot` gets its issue
  number confirmed against #1959's merged commit, and `vulkan-lane-stale-image-preflight-refusal`
  gets `issue: 1973` (the lane re-enable PR that actually implemented the check it describes).
  `.claude/workflows/rc-validate.js`'s verify-prompt fleet note is unchanged from the v6 draft —
  it was written to be correct once the fold landed and still is.

* **7** (2026-08-21) — follow-up to 6, landed before its first run. `readonly/api.md` check 6
  now names both `reason=` values on `slot_view.image_probe_failed` (`probe-error` when the
  probe raised, `probe-timeout` when the slot probe blew its deadline first). v6 promised that
  every `unknown` has a reason-bearing journal line behind it while the timeout path emitted
  only the generic `container_probe_timeout`; the product side of that gap is fixed in the
  same PR, so the promise now holds as written. No check added or removed.

* **6** (2026-08-20) — `image_status` gains an `unknown` member (#1939), so the three briefs
  that adjudicate that field learn to tell its two failures apart. `missing` on a running slot
  is still regression `image-status-wrong-podman-store` — a confident wrong answer. `unknown`
  is the API declining to answer because the rootful `hal0-podman-ro` seam was unusable
  (wrapper rc 66, absent sudoers grant, probe timeout); it is a **seam finding**, not a
  self-contradiction, and it must be followed up rather than passed over — a fleet reading
  `unknown` everywhere means the grant did not install, and `unknown` on a box whose seam is
  demonstrably healthy is a defect of its own. `readonly/api.md` check 6, `stateful/slots.md`
  check 1, and the `image-status-wrong-podman-store` expect clause all now say so, and all
  three point at the `reason=` field on the `podman_ro.image_present_unanswered` journal line
  (`grant-denied` / `podman-failed` / `podman-absent` / `invalid-argument` / `seam-error`),
  which is where the wrapper's exit-code contract now surfaces, or at
  `slot_view.image_probe_failed` (`reason=probe-error` / `probe-timeout`) for an unknown that
  never reached the seam. Every `unknown` has one of those two lines behind it; one with
  neither is a finding. No lane, tier, or phase change.

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
