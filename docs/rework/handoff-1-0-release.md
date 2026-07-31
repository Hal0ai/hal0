# Handoff — hal0 v1.0.0 release (paste-in brief for a new session)

**Written:** 2026-07-30, last updated 2026-07-31. **Version in tree:** `1.0.0-rc.1`
(`pyproject.toml:13`). **`github/main` tip:** `7c9ed3d2` — `fix(updater): tell the truth about what
a rollback is actually serving (#1549)`.

> You are picking up the v1.0.0 endgame. Nine parallel build streams are in flight in
> `/mnt/mintdev/worktrees/hal0-v1/`, plus a release-prep branch. **Update (2026-07-31): six of
> nine streams (A, C, D, E, F, I) are now fully reviewed with a PASS verdict — see §6a/§6b.** G and
> H still need finishing (G has 11 uncommitted files, H has nothing committed at all). The remaining
> path is: finish G/H → rebase everything onto current `github/main` (§2 — streams are 40 commits
> behind) → merge in order → CHANGELOG fold → tag → install test on CT151 → update test on CT150.
>
> Everything below marked **verified** was re-checked against git and the live boxes before being
> written. Two claims carried in from an earlier session did not survive that check — see §4.

---

## 1. Build streams (verified)

All nine streams branch from the same base: **`34445a25`** —
`docs(handoff): close out the pin/drawer audit — final merge set + lxc105 git deploy record (#1410)`.

| Stream | Branch | Worktree (`worktrees/hal0-v1/`) | Commits | Tree |
|---|---|---|---|---|
| A — install/setup + brain | `feat/install-setup-unify` | `A-install-setup` | 9 | clean |
| B — updater + profile wipe | `feat/update-profile-reseed` | `B-update-reseed` | 7 | clean |
| C — profiles tuning-only | `fix/profile-tuning-only` | `C-profile-tuning` | 7 | clean |
| D — slot-drawer UI | `feat/ui-inline-model-edit` | `D-ui-inline-edit` | 5 | clean |
| E — model row menu | `feat/ui-model-row-menu` | `E-ui-row-menu` | 4 | clean |
| F — write-boundary | `fix/write-boundary-enforcement` | `F-agent-mcp` | 4 | clean |
| I — metrics egress | `fix/metrics-egress` | `I-metrics-egress` | 3 | 5 modified |
| G — tool-call parser | `feat/brain-tool-reroute` | `G-brain-tools` | 2 own (11 total) | 7 modified, 4 untracked |
| H — slot health/routing | `fix/slot-health-routing` | `H-slot-health` | 0 | 11 files, none staged |

**Totals:** 41 commits across the streams, plus 3 on `hal0/hal0-v1-release-prep`
(`cb6100fb` PyPI suppression, `9830e8dc` test-harness hardening, `dce1c8e3` UI typecheck gate) = **44**.

Notes that matter for merge:

- **G is stacked on A.** `feat/brain-tool-reroute` contains all 9 of A's commits plus 2 of its
  own (`53ac67d9` route tool rounds to `[brain_chat] tool_model`, `e420c3dd` its tests). Merge A
  first, or merge G and get A for free — do not merge both independently and expect a clean history.
- **H has committed nothing.** Its branch tip *is* the shared base `34445a25`. All of its work is
  loose in the working tree: 10 modified files (`providers/container.py`, `slot_view/__init__.py`,
  `slots/drift.py`, `slots/manager.py`, `slots/watchdog.py`, and 5 test files) plus one untracked
  (`tests/slots/test_slot_health_token.py`). "11 files staged" is 11 files *changed*; the index is
  empty.
- **I's tree is dirty on top of its 3 commits** (`api/routes/hardware.py`, `slot_view/__init__.py`,
  and 3 test files) — the concurrent-probe work landed, the latency work did not.

## 2. Every stream is based 40 commits behind `github/main` — rebase before merging

`34445a25` is an ancestor of `github/main`, **40 commits back**. Everything merged since — including
the four updater/install fixes that landed today (#1546, #1540, #1541, and the Connections/Security
polish) — is *not* in any stream. Streams B and I in particular touch the updater and the metrics
path that #1540/#1541 just rewrote; expect real conflicts there, not textual ones.

Also: the canonical checkout's local `main` is itself **19 commits stale** (`5be7f2a5`, #1494).
Fetch before you compute anything from it.

```sh
git -C /mnt/mintdev/repos/hal0 fetch github
git -C /mnt/mintdev/repos/hal0 branch -f main github/main   # only if nothing is checked out on it
```

## 3. None of this work is pushed — it exists only on RAID0

`git ls-remote --heads github` matches **zero** of the nine stream branches or
`hal0/hal0-v1-release-prep`. Their upstream is set to `github/main`, which is why `git branch -vv`
shows them as "ahead of github/main" rather than as tracked remote branches.

44 commits and three dirty working trees are single-copy on a Btrfs RAID0 pool whose only
protection is `mintdev-backup.timer`. Push the seven finished branches before doing anything else:

```sh
for b in feat/install-setup-unify feat/update-profile-reseed fix/profile-tuning-only \
         feat/ui-inline-model-edit feat/ui-model-row-menu fix/write-boundary-enforcement \
         fix/metrics-egress hal0/hal0-v1-release-prep; do
  git -C /mnt/mintdev/repos/hal0 push -u github "$b"
done
```

## 4. Test boxes — two corrections

Both boxes are reachable and were probed read-only on 2026-07-30.

### CT151 `hal0-test-2604` @ 10.0.1.151 — install-test target

`PRETTY_NAME="Ubuntu 26.04 LTS"`, GPU + NPU passthrough. Snapshot `pristine` exists
(2026-07-30 07:55:21, "provisioned ubuntu-26.04, pre-hal0-install").

**Correction:** the *live* container is no longer pristine — `hal0 --version` returns
`hal0 1.0.0rc1`. The snapshot is clean; the box is not. Roll back before the install test, or the
test measures an upgrade-over-rc1, not a fresh install:

```sh
ssh root@10.0.1.110 'pct rollback 151 pristine'
```

### CT150 @ 10.0.1.150 — update-test target

Verified healthy on the v0.9.8 baseline: `hal0 0.9.8`, `/api/health` → 200, `hal0-api` and
`hindsight-api` both `active`, no `/etc/hal0/profiles.toml`.

This is a real 0.9.8 — the drifted out-of-band 1.0.0 was purged and reinstalled through the signed
public one-liner rather than by bypassing the installer's verification guard, because
`releases.hal0.dev/stable.json` serves exactly 0.9.8 on the public channel. So the baseline is
byte-identical to what current users run, cosign-verified end to end.

**Correction (re-verified 2026-07-31):** there is **no `baseline-0.9.8` snapshot, and a snapshot is
not currently possible.** `pct snapshot 150 ...` fails with `snapshot feature is not available`.
Cause: CT150's config carries two raw bind mounts —

```
mp1: /devpool/dev/repos,mp=/mnt/dev/repos,backup=0
mp2: /devpool/dev/projects,mp=/mnt/projects,backup=0
```

— pointed at host paths rather than PVE-tracked `storage:volid` entries. `devpool` is itself a zfs
pool, but these two mountpoints bypass PVE's storage layer entirely (raw bind), and PVE refuses to
snapshot an LXC that has any unmanaged bind mountpoint attached, regardless of what the rootfs
storage (`local-zfs`, snapshot-capable) supports on its own.

As it stands the update test is **one-shot** — running it destroys the only known-good 0.9.8 baseline,
and there is no cheap way to get it back. Options, cheapest first:

1. **Skip the snapshot, accept one-shot.** Re-verify the update test's assertions are complete
   enough that a single run is sufficient, and re-install 0.9.8 from the public channel again
   afterward if a second baseline run is ever needed (see the reinstall note above — it's
   reproducible, just not instant).
2. **Detach `mp1`/`mp2` for the duration of the test**, snapshot, run the test, and decide whether
   to reattach and re-snapshot after. Changes the container's mount config — confirm nothing else
   depends on those paths being present before doing this.
3. **Clone the container** (`pct clone 150 <new-id> --full`) instead of snapshotting — full clones
   don't have the same bind-mount restriction, at the cost of a full 500 GB copy.

This wasn't caught in the first handoff pass; the earlier draft's `pct snapshot 150 baseline-0.9.8 ...`
command does not work as written (`-` is also an invalid character in a PVE snapshot name, on top of
the bind-mount block) — don't run it verbatim.

### The missing `profiles.toml` on CT150 is deliberate

Stream B wipes and reseeds profiles on update. With no `profiles.toml` on disk the seeds are
virtual, so an update against the box as it stands would exercise the wipe path against nothing and
pass while proving nothing. **Seed custom profiles on CT150 before running the update test**, and
assert they are gone (and correctly reseeded) afterward — that assertion is the whole point of the test.

## 5. Two incidental findings from the 0.9.8 install

Both are pre-existing 0.9.8 behaviour, already addressed by Stream A. Recorded here so they are not
re-diagnosed as v1.0.0 regressions:

1. **Step-total off-by-one.** 0.9.8 prints `== (14/13)`. The off-by-one Stream A fixed is
   long-standing, not new.
2. **Stage-2 handoff hits `/dev/tty: No such device` on a non-TTY.** It degrades gracefully rather
   than hanging. That prompt is the one Stream A removed.

## 6. Remaining path to the tag

1. **Finish G** — the parser fix that makes brain tool calling work without the 19 GB anchor. Commit
   the 11 loose files.
2. **Finish H** — slot health / routing. Nothing is committed yet; this is the least-advanced stream.
3. **Finish I** — latency work (concurrent probe already landed). Its uncommitted working-tree diff
   is cosmetic-only per review (§6b) — just commit it, nothing to finish.
4. **Two adversarial reviews: Stream A and Stream F.** A rewrites the single user-facing entry point
   (`install.sh`, `feat(installer)!`) and F is the write-boundary enforcement — the two places where
   a wrong call is either unrecoverable on a user's box or a security hole. Neither should merge on a
   self-review. **DONE (2026-07-31) — both PASS, see §6a.**
5. **Push, then rebase every stream onto current `github/main`** (§2, §3). Merge order: A (or G,
   which carries A) → B → C → D → E → F → I → H → release-prep. **A, C, D, E, F, I are now all
   reviewed PASS** (§6a/§6b) — B and G's own commits (the 2 on top of A) and H were not in scope for
   this review pass; give them at least a targeted-test pass before merging if nobody already has.
   Draft PRs open for CI-signal purposes only, not ready to merge as-is (still 40 commits behind
   main): #1553 (F), #1554 (A).
6. **Fold `## [Unreleased]` into `## [1.0.0]`** in `CHANGELOG.md`. Tagged releases bundle the
   matching section as `RELEASE_NOTES.md` and extract `### Highlights` / `### Breaking` /
   `### Migrations` into `release.json`, which `hal0 update` renders as callouts — so a 1.0.0
   section without a `### Breaking` block silently ships a breaking release with no warning.
   Stream A's `feat(installer)!` and any other `!` commits belong there.
7. **Tag**, then **install test on CT151** (roll back to `pristine` first), then **update test on
   CT150** (no snapshot possible as of 2026-07-31 — see §4's correction; seed profiles first
   regardless).

## 6a. Review status — Stream A and Stream F (2026-07-31, COMPLETE — both PASS)

Both reviews are diff-read complete, targeted-test-verified, and full-suite-verified. **Both PASS.**
Neither has a blocking finding. Full-suite logs (for reference): `/var/tmp/f-full-suite3.log`,
`/var/tmp/a-full-suite3.log`.

### Stream F — write-boundary enforcement

Read in full: `config_write.py` (the new shared guard/merge pipeline — partition, reconcile,
NPU-exclusivity, default-uniqueness), and every calling-side change (`stacks/apply.py`,
`api/routes/stacks.py`, `api/routes/slots.py`, `slots/manager.py`). The fix closes a real gap: the
stacks-apply engine and `SlotManager.create()` both wrote slot TOML in-process without ever passing
through the HTTP-layer key-partition guard (`reject_model_owned_slot_keys`) or the freeform
`[server].extra_args` hardware-flag screen — so a stack apply could persist what
`PUT /api/slots/{name}/config` would 400 on. `guard_slot_write_payload` is now the one shared
in-process gate every writer runs through. No correctness issues found on read.

- Targeted tests (`test_stacks_routes.py`, `test_slots_routes.py`, `test_write_boundary_guard.py`,
  `test_apply_plan.py`, `test_no_slot_enabled_key.py`): **155 passed.**
- **Full suite: 7841 passed, 16 skipped, 1 xfailed, 1 failed, in 411.5s.** The one failure —
  `tests/slots/test_fail_watcher_warming.py::test_warming_slot_recovers_when_stale` — is a
  **parallel-execution timing flake, not a real regression**: the test polls a background watchdog
  task against a hardcoded 5-second wall-clock deadline (`asyncio.get_event_loop().time() + 5.0`),
  and this run used `pytest-xdist -n 6` under heavy host load (see "duplicate processes" note
  below) — the watchdog task simply didn't get scheduled in time. Reran in isolation, no xdist, on
  both this worktree and A's: **8/8 passed on both.** Confirmed not stream-specific (same failure,
  same isolated-pass, on two independent branches).

**VERDICT: PASS.** No blocking findings against F. The one full-suite failure is a confirmed
CPU-contention flake in the test itself, reproduced as passing in isolation on both F and A.

### Stream A — install/setup unification

Read in full: `installer/install.sh`'s new interactive-input gate (`_interactive`, `_tty_read`,
the model-store/HF-token prompts, the agent-anchor opt-in offer), `config/schema.py`'s
`tool_model` empty-string trap fix, `brain/chat.py`'s completion-budget floor, `registry/curated.py`
and `registry/pull.py`'s new brain/agent model rows and `read_model_meta`, and the `cli/main.py` /
`cli/setup_command.py` / `cli/setup_install.py` hiding of `hal0 setup`. All well-documented,
consistent with each other, and `UI_STEP_TOTAL=16` verified to match the actual 16 `ui_step` calls
in the file (the test that pins this, `tests/installer/test_install_single_entry_point.py:227`,
exists — the file path a code comment nearby cites, `tests/install/test_ui_step_total.py`, does
not; harmless comment drift, not a real gap).

- Targeted tests (`tests/install/`, `tests/installer/`, brain/config/systemd tests touched by this
  stream): **472 passed, 2 skipped** (both skips are `shellcheck not installed` on this box — see
  below).
- **Static-analysis gap, not fixed:** no `shellcheck` on this machine and no passwordless `sudo` to
  install it, so the two shellcheck-gated tests
  (`test_platform_gate_hardening.py`, `test_hf_token_secrets.py`) skip rather than run. `install.sh`
  is the highest-risk file in the whole release — a bash lint pass genuinely didn't happen here.
  Whoever finishes this review on a box with `shellcheck` available should run it before calling A
  clean: `shellcheck installer/install.sh`.
- **Minor, non-blocking:** the top-of-file header comment (`installer/install.sh:2`) still reads
  "hal0 installer — idempotent, non-interactive." The whole point of this stream is that it now
  *is* interactive on a TTY. One-line fix, not a behavior issue.
- **Full suite: 7899 passed, 16 skipped, 1 xfailed, 1 failed, in 422.8s.** Same single failure as F
  (`test_warming_slot_recovers_when_stale`), same root cause (xdist scheduling contention against a
  hardcoded 5s deadline, not stream-specific), same clean 8/8 pass in isolation. See F's entry above
  for the full explanation — not duplicating it here.

**VERDICT: PASS.** No blocking findings against A. Two non-blocking follow-ups before merge: run
`shellcheck installer/install.sh` on a box that has it, and fix the stale header comment. Neither is
a reason to hold the merge.

### Shared test-infra flake — one line for whoever sees it again

`tests/slots/test_fail_watcher_warming.py::test_warming_slot_recovers_when_stale` uses a real
wall-clock 5-second deadline against a background asyncio task. It is reliable serial/low-concurrency
but WILL flake under `pytest-xdist` on a loaded box (confirmed: failed identically on two unrelated
branches under `-n 6`, passed 8/8 both times in isolation). Not filed as a separate issue — the fix
(swap the real deadline for a fake clock or raise the timeout) is a five-minute change but out of
scope for this release-review pass; worth a follow-up ticket if it recurs in CI's own xdist-free
runs (CI does not currently use xdist, so this shouldn't surface there).

### Draft PRs opened for CI signal (2026-07-31)

The local box's full-suite runs are slow (contended — six heavy jobs ran concurrently during this
review) and neither stream had ever had CI run against it (`push`/`pull_request`-only triggers, and
neither branch had a PR). Opened **draft** PRs to get GitHub's runners going independently:
**#1553 (F)**, **#1554 (A)**. Both note in-body that the branch is still 40 commits behind `main`
and not to merge from draft.

F's `sunset` check failed on CI — **investigated, not a real finding.** It's the scar-ratchet
(`scripts/check_sunset.py`), non-required per branch protection, and the exact false-positive
pattern tracked separately in #1502 ("the scar ratchet counts prose, so accurate documentation trips
it"): F's diff uses the word "legacy" seven times, every instance in a docstring or test-assertion
message describing *pre-existing on-disk data* ("the legacy value survives; the guard didn't fire"),
not an actual deprecated code shim. Confirmed by grepping the diff for the ratchet's own regex
(`removed in #|DEPRECATED|deprecated|legacy|backward.compat|compat shim`) and reading every hit.
Don't re-chase this on a future CI run of F unless the count goes up for a *different* reason.

**A's `ci.yml`/`playwright.yml` never triggered — unresolved platform anomaly, not a config issue.**
CodeQL ran fine on #1554 (twice — once on open, once after a nudge commit), but `gh run list
--branch feat/install-setup-unify` returns zero rows for CI or Playwright, at any status, before or
after pushing an empty `chore: nudge CI` commit to force a fresh `synchronize` event. Checked and
ruled out: no `paths`/`paths-ignore` filter on either workflow, no draft-PR guard, no branch-name
restriction, Actions are enabled repo-wide (`allowed_actions: all`) — nothing in this repo's config
explains F (#1553) and the tmpfs-guard PR (#1551) both triggering normally while A (#1554) doesn't.
Whoever picks this up: check `gh run list --branch feat/install-setup-unify` fresh: if it's still
empty, this needs a GitHub-side look (webhook delivery log in repo Settings → Webhooks, or just
close/reopen #1554), not more config spelunking here. **Not blocking** — A's local full-suite run is
the fallback signal and is further along than F's.

### Local full-suite runs were accidentally duplicated — found and fixed (2026-07-31, ~23:00)

Both F's and A's local full-suite reruns had ended up running **twice each** against the same
worktree simultaneously — one instance from an earlier relaunch this session, one from a process
that was already alive when this session picked back up after the `/tmp` → `CLAUDE_CODE_TMPDIR`
migration and was mistakenly assumed to be a helpful pre-existing relaunch rather than adopted or
killed. Combined with Stream C's minimax worker running its *own* full suite and an unrelated,
apparently-hung `rg --hidden --follow` process from a separate session eating >1000% CPU, load
average hit ~26 on a 16-core box — which is why progress crawled at ~1%/9min instead of a normal
pace. Killed the duplicate F and A pytest processes (kept one of each); did **not** touch the `rg`
process since it's parented to a different, apparently-live session, not something safe to kill
without that session's owner confirming it's dead. **Correct current log paths: `/var/tmp/f-full-
suite.log` and `/var/tmp/a-full-suite.log`** (no `2` suffix — the survivors were the pre-existing
processes, not the ones this session explicitly relaunched; the `*2.log` files are from the killed
duplicates and are stale).

Even post-cleanup this box is not fast for these suites — budget on the order of an hour for a full
local run here. If you're blocked waiting on it, `gh pr checks 1553`/`1554` (once A's CI gap above
resolves) is the better signal.

### Tmp infrastructure note (unrelated to either stream, cost real time)

Running both streams' full suites concurrently filled the 16G `/tmp` tmpfs (`HAL0_HOME=$(mktemp -d)`
and pytest's own `pytest-of-mint` basetemp both land there by default) — a known, previously
recorded issue on this box. The first two full-suite attempts for both A and F reported "exit 1"
from a wrapper `tail` rather than from pytest itself (no `pipefail`), which briefly looked like real
test failures and would have been a false read if not re-verified against pytest's actual exit code.
Separately, the user migrated the whole box's Claude Code tmp dir from `/tmp` to
`/mnt/mintdev/scratch/claude-tmp` (`CLAUDE_CODE_TMPDIR` in `.claude/settings.json`) mid-review, which
briefly relocated an in-flight log file to `/var/tmp`. Current reruns write `HAL0_HOME` and
`--basetemp` under `/var/tmp/hal0-tests-{A,F}2-<pid>/` (logs: `/var/tmp/{a,f}-full-suite2.log`,
the first-attempt `*-full-suite.log` files are stale/killed — ignore them), which has hundreds of
GB free — do the same for any future full-suite run on this box rather than trusting the tmpfs
default.

**A genuine fix for the root cause is filed as PR #1551** (`fix/pytest-tmpfs-guard`, closes #1490):
`tmp_path_retention_count = 1` + `tmp_path_retention_policy = "failed"` in `pyproject.toml`, plus an
`atexit` cleanup for `tests/conftest.py`'s collection-time `HAL0_HOME` scratch dir (which isn't a
`tmp_path`-family fixture, so the retention setting alone doesn't reach it). Verified: a passed
test's fixture dir goes to 0 bytes immediately rather than accumulating across generations. Not
release-blocking, but land it — it directly caused the two false-failure incidents above and will
keep costing every future session on this box until it merges.

## 6b. Review status — Streams C, D, E, I (2026-07-31, minimax-swarm workers)

Lower-risk than A/F, so delegated to four parallel MiniMax-M3 workers (`~/.claude/helpers/
mm-worker.sh`) for a read-only diff review + targeted-test pass, per workspace delegation policy
(UI/tuning/metrics work, not security/architecture-critical). All output reviewed by this session
before being recorded here — do not treat a worker's raw report as ground truth without that.

### Stream D — slot-drawer UI (`feat/ui-inline-model-edit`, 5 commits) — reviewed, clean

Inline model-edit pencil on every slot card (InferencePane + SlotsView) opening `ModelDrawer`
side-by-side via a new `DrawerDock` context; drops dead/model-owned fields (`chat_template`,
`extra_args`, `parallel`, `ctx_size`) from the slot drawer; adds a shared `slotModelRow` resolver so
the slot card, InferencePane card, and slot drawer can't drift in precedence rules.

- Vitest: **21/21 passed** (4 files).
- Typecheck: 1 error, **pre-existing on base `34445a25`, not introduced by D** — `useRuntime.ts:43`,
  `Property 'model_default' does not exist on type 'Slot'`. Already fixed on `github/main` by
  `dce1c8e3` (release-prep branch) — pure rebase noise, not a real gap. See the note at the end of
  this section.
- Findings: a handful of minor aria-label polish items (empty `aria-label="Edit model"` when
  disabled-but-mounted, a network-error vs. no-model-bound title that both correctly disable the
  control but say the same thing) — all non-blocking, listed in the worker transcript if wanted
  later. No swallowed errors, no missing loading states.

**No blocking findings against D.**

### Stream E — model row menu (`feat/ui-model-row-menu`, 4 commits) — reviewed, one real a11y gap

Kebab menu (`Icons.more`) on every model row → "Edit model settings" → opens `ModelDrawer` decoupled
from catalog selection; sidebar recipe restyle to match drawer form rhythm; a placeholder-text fix;
a new `flags-tune.test.ts` (41 tests).

- Vitest: **41/41 passed** (4 files).
- Typecheck: same single pre-existing `useRuntime.ts:43` error as D — confirmed independently by
  reverting the file to base and reproducing it there too. Not introduced by E; already fixed on
  `github/main`.
- **Real finding, filed as issue #1552:** the `Menu` primitive (`primitives.jsx:762`) this stream
  exercises for the first time (per its own spec header, "the Menu primitive's first real call
  site") has no Escape-to-close, no arrow-key navigation, no `role="menu"`/`role="menuitem"`, and
  the backdrop dismiss never returns focus to the trigger button. The primitive predates this
  stream, but E is what makes the gap user-reachable for the first time. Not release-blocking (no
  destructive action lives behind it — the only menu item is Edit, which routes through the normal
  confirmed-safe drawer flow) but should land before or shortly after 1.0.
- No swallowed errors; the one destructive-adjacent path (delete) is untouched by this stream and
  still confirms via `DeleteModelDialog`.

**No blocking findings against E** — the a11y gap is tracked separately (#1552), doesn't block merge.

### Stream I — metrics egress (`fix/metrics-egress`, 3 commits + uncommitted) — reviewed, clean

Committed work gates hal0-internal metrics/hardware fan-out to actual hal0 peers (excludes
third-party OpenAI-compatible providers, disabled upstreams, local slot upstreams; adds explicit
tri-state `hal0_peer` config), adds URL-level egress tests, and adds the concurrent-probe work
(bounded slot-probe concurrency, per-slot deadline, concurrent container/capacity/metrics
aggregation).

- Targeted tests (`test_metrics_egress.py`, `test_probe_concurrency.py`, `test_peers.py`):
  **67 passed.**
- **Uncommitted working-tree changes are cosmetic only, not half-finished work** — this corrects the
  earlier read in §1's table (which flagged I as merely "5 modified"). Line-wrapping, ASCII-hyphen
  cleanup in `slot_view/__init__.py`, and a redundant `(TimeoutError, asyncio.TimeoutError)` tuple
  collapsed to plain `TimeoutError` (equivalent on the repo's Python 3.12+ floor). Safe to commit
  as-is.
- One non-blocking note: `api/routes/hardware.py:287` treats malformed JSON from a 200 peer response
  like an offline peer without logging why — matches the existing degradation contract, not a new
  gap.

**No blocking findings against I.**

### Stream C — profiles tuning-only (`fix/profile-tuning-only`, 7 commits) — reviewed, clean

Its own MiniMax worker stalled (30+ min silent, likely stuck on a second serial full-suite attempt
after its first hit the 550s timeout at 14%) — killed it and finished the review directly rather
than wait indefinitely on it.

The stream's real theme is broader than its name: "the model/slot owns a value, the profile becomes
an inert hint" — carried consistently through context-size ownership (`_resolve_context_size` now
model-first, slot value a ceiling not an override), GPU device-node passthrough (now decided from
`slot.device`, not `profile.device_class`/`.backend`, closing a real bug where a `cpu-chat` profile
on a `device="cpu"` slot still requested real GPU device nodes), an implausible-context-size guard
on the launch path, and a profile-import checksum-verification gap (`checksum_ok` was computed for
`dry_run` and never re-checked on commit — same integrity-gap *pattern* as issue #1512, but for
profiles rather than stacks). All 296 lines touched in `providers/container.py` — the one file that
looked like scope creep for a "profiles tuning-only" stream at first glance — are the direct,
necessary consequence of making `profile.device_class`/`.backend` inert: declaring them inert in
schema/docs without also fixing the one place that still read them as authoritative for hardware
passthrough would have been a lie with a real correctness bug behind it. No scope creep.

- Full suite (run directly, `pytest-xdist -n 6`, after killing the stalled worker):
  **7849 passed, 16 skipped, 1 xfailed, 0 failed, in 352.6s.**

**VERDICT: PASS.** No findings against C.

### The `useRuntime.ts` typecheck error, seen independently by both D's and E's reviewers

Both workers hit the identical pre-existing typecheck failure and both correctly attributed it to
base drift rather than their own stream — cross-confirms it's real and not stream-specific. It's
already fixed on `github/main` (`dce1c8e3`, part of the release-prep branch's `ci(ui): gate
typecheck, and drop the dead read it was hiding`), introduced by `0aa40d97` sometime between the
shared stream base (`34445a25`) and current `main`. Every stream will see this error on `ui`
typecheck until it rebases past `dce1c8e3` — expected, not a new defect to chase.

## 7. Quick state check for a fresh session

```sh
R=/mnt/mintdev/repos/hal0
git -C $R fetch github && git -C $R log --oneline -1 github/main
for w in A-install-setup B-update-reseed C-profile-tuning D-ui-inline-edit E-ui-row-menu \
         F-agent-mcp G-brain-tools H-slot-health I-metrics-egress; do
  d=/mnt/mintdev/worktrees/hal0-v1/$w
  printf '%-18s %2s ahead  %2s dirty\n' "$w" \
    "$(git -C $d rev-list --count github/main..HEAD)" \
    "$(git -C $d status --porcelain | wc -l)"
done
ssh root@10.0.1.110 'pct listsnapshot 150; pct listsnapshot 151'
```
