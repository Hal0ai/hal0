# Slot pin + drawer field-wiring audit — session handoff (2026-07-30)

## Scope

Two threads, run back to back:

1. **Pin lifecycle** — promote the §21.10 operator pin to a real per-slot,
   operator-writable control with a UI surface, and retire the `enabled` toggle
   it had been standing in for.
2. **Drawer field-wiring audit** — prove that every editable field in the slot
   drawer (`#slots/:name`) and the model drawer (`#models` → *Edit options*)
   reaches the wire with the right key, on the right route, and close the gaps.

Thread 2 was scoped as test-writing. It became a bug hunt: the specs passed, but
writing them surfaced eight defects, two of which were silent data loss.

Specs written during the session: `docs/rework/hal0-specs/spec-slot-pin-lifecycle.md`.

## Decisions

- **`pinned` is the single residency control.** Tri-state on disk — explicit key
  wins in both directions, the `agent`/`utility`/`npu` anchor set applies only
  when the key is absent. Key *presence* (not truthiness) is the discriminator,
  so an authored `false` un-pins an anchor. Full semantics in the spec above.
- **Activation is derived from model presence, not a flag.** A `type: llm` slot
  with a model bound is active. This generalizes the NPU trio's existing rule and
  kills a field that could contradict observable reality. `enabled` removal is
  staged on `feat/remove-slot-enabled` (no PR at time of writing).
- **The removal wire value for a cleared slot override is `null`, never `""`.**
  `reconcile_slot_updates` implements None-means-delete; `""` persists an
  empty-string override, which is a different and still-broken state. Settled by
  probing the merge directly rather than by preference.
- **Drawer save gates unify rather than multiply.** #1372's root cause was two
  copies of one predicate drifting apart; the fix collapses them into a single
  derived value. The same shape recurs in #1390/#1391 and is filed as #1398.

## Pull requests

| PR | State | What |
|---|---|---|
| [#1368](https://github.com/Hal0ai/hal0/pull/1368) | merged | Drawer header toggle becomes Pinned/Unpinned; explicit `pinned=false` un-pins anchors; effective pin lifted onto `GET /api/slots` (#1367) |
| [#1373](https://github.com/Hal0ai/hal0/pull/1373) | open | Field-wiring contract specs + the #1372 fix + rejected-write specs (carries #1377 and #1382 after they merged into its branch) |
| [#1377](https://github.com/Hal0ai/hal0/pull/1377) | merged | "Clear override" actually removes a persisted `chat_template` (#1372) |
| [#1382](https://github.com/Hal0ai/hal0/pull/1382) | merged | Rejected-write contracts for both drawers |
| [#1397](https://github.com/Hal0ai/hal0/pull/1397) | merged | `hal0 slot migrate-flags` CLI **+ the fold data-loss fix** (#1396) |
| [#1400](https://github.com/Hal0ai/hal0/pull/1400) | open | Per-worktree e2e dev-server port (#1399) |

Parallel session (separate agent, same repo, unreachable by mailbox):

| PR | State | What |
|---|---|---|
| [#1386](https://github.com/Hal0ai/hal0/pull/1386) | open | Model drawer Context size validation (#1378) |
| [#1392](https://github.com/Hal0ai/hal0/pull/1392) | open | Vision/mmproj save gate + emptied Display name clears (#1380, #1381) |
| [#1401](https://github.com/Hal0ai/hal0/pull/1401) | open | Slot drawer UX — inline model edit, profile selector, binary de-dup, template override state |

> **⚠️ #1386 and #1392 conflict with each other.** Both add a save gate to the
> same region of `model-drawer.jsx`; cherry-picking one onto the other produces
> three conflicts (derivation block, `onSave` early-return, button `disabled`).
> Merged blind, **the second silently drops the first's guard.** Verified
> resolution — keep both derivations and unify:
>
> ```js
> const saveBlocked = !!flagsError || !!mmprojError || !!ctxError;
> ```
>
> With that, the combined branches give 34 passed across the model-drawer specs.
> Posted to both issues. Whoever merges second needs it.

## Issues

| Issue | State | One-line status |
|---|---|---|
| [#1367](https://github.com/Hal0ai/hal0/issues/1367) | closed | Pin toggle + anchor override — shipped in #1368 |
| [#1371](https://github.com/Hal0ai/hal0/issues/1371) | open | Field-wiring specs tracker — work delivered in #1373; close when it merges |
| [#1372](https://github.com/Hal0ai/hal0/issues/1372) | open | Clear-override wrote nothing — **fixed** in #1377; close when #1373 merges |
| [#1378](https://github.com/Hal0ai/hal0/issues/1378) | open | Context size `parseInt` corruption — fix in flight (#1386, parallel session) |
| [#1379](https://github.com/Hal0ai/hal0/issues/1379) | open | **HELD** — Template/Parallel/Extra Args inert at launch; removal blocked, see below |
| [#1380](https://github.com/Hal0ai/hal0/issues/1380) | open | Decorative vision/mmproj error — fix in flight (#1392) |
| [#1381](https://github.com/Hal0ai/hal0/issues/1381) | open | Display name cannot be cleared — fix in flight (#1392) |
| [#1388](https://github.com/Hal0ai/hal0/issues/1388) | open | **Unfixed** — NPU toggles clobber the configured FLM tag with a stale `model_id` |
| [#1389](https://github.com/Hal0ai/hal0/issues/1389) | open | **Unfixed** — Save silently dead on an NPU slot with malformed persisted `extra_args` |
| [#1390](https://github.com/Hal0ai/hal0/issues/1390) | open | Phantom `ctx_size` persist from a live-metric baseline — instance of #1398 |
| [#1391](https://github.com/Hal0ai/hal0/issues/1391) | open | Degraded poll ⇒ rewrite-everything + cold restart — instance of #1398 |
| [#1396](https://github.com/Hal0ai/hal0/issues/1396) | closed | Migrator unreachable — shipped in #1397 |
| [#1398](https://github.com/Hal0ai/hal0/issues/1398) | open | Structural: dirty-tracking uses a live-polled prop as baseline (3 instances) — filed only |
| [#1399](https://github.com/Hal0ai/hal0/issues/1399) | open | E2E port hazard — fix in #1400 |

## Test evidence

New specs, all red-first:

| Spec | Cases | Red-first evidence |
|---|---|---|
| `slot-drawer-field-wiring-v3.spec.ts` | 14 | W7 captured `[]` PUTs against an expected 1 before the #1372 fix |
| `model-drawer-duplicate-v3.spec.ts` | 5 | new surface — no prior e2e for the duplicate dialog |
| `drawer-save-errors-v3.spec.ts` | 7 | new surface — a grep for `status: 4`/`5` across every `slot-*` spec returned **nothing** |
| `tests/cli/test_slot_migrate_flags.py` | 7 | file failed to **import** — no such CLI command |
| `tests/config/test_slot_flags_fold.py` | +2 | pinned the re-parked `extra["server"]` shape and divergence through it |
| `tests/api/test_slot_config_validation.py` | +2 | first coverage of None-deletion for any slot key |
| `ui/tests/e2e/port.test.ts` | 7 | file failed to import — no `port.ts` |

Suite state at handoff: full e2e **458 passed / 18 skipped** (476 collected),
`pytest tests/config tests/cli tests/slots` **1919 passed / 9 skipped**, vitest
**11 passed**, eslint + tsc + ruff clean.

## Two findings worth carrying forward

### The migrator would have destroyed the data it existed to rescue

`slot_flags_fold` had no operator entry point at all — no CLI, no installer hook,
only test references — while the launch-side readers were already deleted. Boxes
upgraded with a bench-tuned slot had silently stopped applying that tune with no
supported recovery path.

Building the CLI exposed a second, worse problem: **the fold itself dropped every
slot's `[server].extra_args`.** `collect_inputs` feeds the planner
`SlotConfig.model_dump(by_alias=True)`, where the `_tuck_server_into_extra`
model_serializer re-parks the table under `extra["server"]`; `_slot_flag_tokens`
read only a top-level `server` key. Proven against the real loader:

```
top-level server key present?  False
extra: {'server': {'extra_args': '-b 2048'}}
tokens the planner sees: (['--parallel', '4', '--kv-unified'], None, None)
```

The same gap defeated the divergent-share guard — two slots differing *only* in
`extra_args` folded to an identical tune, so the planner saw no conflict and
would have silently picked a winner. Both fixed in #1397. Shipping the CLI
without it would have made an unreachable-but-broken migrator
reachable-and-broken.

### Local e2e results across parallel worktrees were fiction

`playwright.config.ts` pinned port 5173 with `reuseExistingServer: !CI`, so a run
in one worktree attached to another's Vite server and tested a branch it never
checked out. Not flaky — confidently wrong in both directions:

```
npx playwright test ...                     →   7 failed, 27 passed
HAL0_E2E_PORT=5199 npx playwright test ...  →  34 passed     # identical commits
```

Those 7 failures were nearly filed as a regression against another agent's
branch. CI was never affected (`CI=1` disables reuse). #1400 derives a stable
per-worktree port; `HAL0_E2E_PORT` still wins.

**Two process lessons, both learned the hard way:**

- **A filtered Playwright run cannot prove suite health.** The first #1400 commit
  put a vitest file under `tests/e2e/`, which Playwright's default `testMatch`
  collected; its `vitest` import aborted collection for the *entire* suite
  (`Total: 0 tests in 0 files`) and broke γ-suite. A filtered run still passed
  and hid it. Fixed by pinning `testMatch: '**/*.spec.ts'`.
- **Re-fetch before rebasing a branch that has had a stacked PR merged into it.**
  A local `test/field-wiring-specs` sat behind its remote after #1382 landed;
  `checkout` + `rebase` silently produced a branch missing that merged work, and
  a force-push would have deleted it from the PR. Caught by diffing file
  presence before pushing.

## Open items

1. **#1379 is HELD** pending direction. Investigation concluded the inert
   Template/Parallel/Extra Args controls are inert *by design*
   (`spec-flags-ownership` §1–§6 — slots have no flag surface), so the fix is
   **removal**, not rewiring. Sequencing matters: the migrator (#1397, now
   merged) had to land first, or removing the controls would strand unmigrated
   values behind a UI that no longer shows them. Also overlaps #1401
   (`fix/slot-drawer-ux`), which must be understood first.
2. **#1398 structural fix not started** — collides with everything drawer-shaped
   in flight. Sequence after #1386/#1392/#1401 land. Three candidate directions
   are in the issue; #1372's fix (collapse duplicated predicates into one derived
   value) is the working template.
3. **#1388 and #1389 are unfixed and unowned.** Both are real, both reproduced.
   #1388 is the more serious — an ASR/Embed toggle rewriting the configured FLM
   chat tag is silent config corruption on NPU boxes.
4. **`enabled` removal** — staged on `feat/remove-slot-enabled`, no PR yet.
5. **Doctor/preflight warning** for slots still carrying an unfolded flag surface
   — noted as a stretch goal in #1396, deliberately not bundled into #1397.

---

## Addendum — final state + lxc105 deploy (2026-07-30, session close)

Everything queued in this handoff has landed. Final merge set beyond the tables
above: **#1408** (`SlotConfig.enabled` removed — model-presence is the
activation signal, one-shot boot migration, 400 `slot.removed_key_denied` on
the retired key), **#1406** (#1389 NPU dead-Save fix), **#1373** (field-wiring
+ rejected-write spec set, #1372 CHANGELOG), **#1400** (per-worktree e2e
ports), **#1402/#1403** (docs), plus the parallel session's **#1386/#1392**
(ctx validation, vision/mmproj gate + Display-name clear — the predicted
`saveBlocked` collision resolved as the unified three-term gate),
**#1385/#1387/#1374/#1375/#1384/#1404/#1405/#1407/#1409**.

Queue mechanics worth remembering: branch protection is `strict: true`, and
GitHub auto-merge never self-updates a branch — a 13-PR armed queue sat
indefinitely until update-branch rounds drained it (CHANGELOG unions were the
recurring conflict; every one resolved by keeping both sides).

**lxc105 (10.0.1.142) deployed from git main @ `a0487038`.** The installed
1.0.0 CLI predates `hal0 update --source git`, so the deploy replicated the
updater by hand: UI built locally (`ui/dist` is served from the `current`
source tree, not the wheel), tree rsynced to
`/usr/lib/hal0/hal0-git-a0487038/`, `pip install` into the shared venv,
`hal0.previous` recorded, atomic `current` symlink swap, `hal0-api` restart.

Live verification on the box:
- `/api/health` → `{"status":"ok","version":"1.0.0rc1"}`
- boot migration ran: **zero** `/etc/hal0/slots/*.toml` carry an `enabled` key
- `GET /api/slots` lifts effective `pinned`; `agent` + `utility` report
  `pinned: true` from the anchor set with no authored key
- `POST /api/slots/utility/unload` → 409 `slot.pinned`,
  `"slot 'utility' is pinned — pass ?force=true to unload it anyway"` — the
  exact contract this effort was asked to surface in the UI

Still open, unchanged priorities: #1379 (inert drawer controls removal — now
unblocked), #1383 (identity-table `enabled` column), #1390/#1391/#1398
(dirty-baseline family), #1393/#1394 (vision↔mmproj backstop).

Note for future sessions: the working repo moved to `/mnt/mintdev/repos/hal0`;
the `hal0` git remote (`hal0:/opt/hal0-dev`) is the live box's git source —
fetch-sync it on the box, never push to it.
