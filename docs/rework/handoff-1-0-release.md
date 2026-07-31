# Handoff — hal0 v1.0.0 release (paste-in brief for a new session)

**Written:** 2026-07-30. **Version in tree:** `1.0.0-rc.1` (`pyproject.toml:13`).
**`github/main` tip:** `7c9ed3d2` — `fix(updater): tell the truth about what a rollback is actually serving (#1549)`.

> You are picking up the v1.0.0 endgame. Nine parallel build streams are in flight in
> `/mnt/mintdev/worktrees/hal0-v1/`, plus a release-prep branch. Seven streams are complete
> with clean trees; three still have uncommitted work. The remaining path is: finish G/H/I →
> two adversarial reviews → rebase → merge → CHANGELOG fold → tag → install test on CT151 →
> update test on CT150.
>
> Everything below marked **verified** was re-checked against git and the live boxes on
> 2026-07-30 before this file was written. Two claims carried in from the previous session did
> not survive that check — see §4.

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

**Correction:** there is **no `baseline-0.9.8` snapshot.** `pct listsnapshot 150` reports only
`current`. As it stands the update test is **one-shot** — running it destroys the baseline. Take the
snapshot first:

```sh
ssh root@10.0.1.110 'pct snapshot 150 baseline-0.9.8 --description "hal0 0.9.8 public-channel baseline for the 1.0.0 update test"'
```

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
3. **Finish I** — latency work (concurrent probe already landed).
4. **Two adversarial reviews: Stream A and Stream F.** A rewrites the single user-facing entry point
   (`install.sh`, `feat(installer)!`) and F is the write-boundary enforcement — the two places where
   a wrong call is either unrecoverable on a user's box or a security hole. Neither should merge on a
   self-review.
5. **Push, then rebase every stream onto current `github/main`** (§2, §3). Merge order: A (or G,
   which carries A) → B → C → D → E → F → I → H → release-prep.
6. **Fold `## [Unreleased]` into `## [1.0.0]`** in `CHANGELOG.md`. Tagged releases bundle the
   matching section as `RELEASE_NOTES.md` and extract `### Highlights` / `### Breaking` /
   `### Migrations` into `release.json`, which `hal0 update` renders as callouts — so a 1.0.0
   section without a `### Breaking` block silently ships a breaking release with no warning.
   Stream A's `feat(installer)!` and any other `!` commits belong there.
7. **Tag**, then **install test on CT151** (roll back to `pristine` first), then **update test on
   CT150** (snapshot first, seed profiles first).

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
