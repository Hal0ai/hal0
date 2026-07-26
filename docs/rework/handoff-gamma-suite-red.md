# Handoff — γ-suite (chromium) persistently red on main (#1333)

> **You are picking up investigation of the γ-suite Playwright CI that has been
> failing with the same 12 slot-drawer tests on every run since 2026-07-20.**
> The failure is on `main` itself (not a flaky test, not branch-specific) and is
> blocking PRs that touch the release pipeline from merging via the γ gate.
>
> Issue: **<https://github.com/Hal0ai/hal0/issues/1333>**
>
> Read in this order: (1) this handoff, (2) issue #1333 (the evidence), (3)
> `ui/tests/e2e/specs/slot-drawer-profile-v3.spec.ts`,
> `ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts`,
> `ui/tests/e2e/specs/slots-wireup-v3.spec.ts` (the actual failing tests), (4)
> `ui/src/` drawer components to understand current DOM structure.

---

## 1. Where things stand (verify before editing)

- **Merged recently:**
  - `b6dc1247` — fix(setup): raise apply-selections HTTP timeout to 300s + env override (#1332)
  - `8e73ab19` — fix(ci): include release_kind and prerelease_stage in release manifest (#1331)
- **γ-suite has been failing with the same 12 tests since 2026-07-20T18:23:03Z**
  (run `29767516143`). All subsequent runs on `main` and on unrelated PR
  branches fail identically.
- **No fix attempted yet.** This handoff is the first scoped response.
- **Strong signal that this is a real UI regression, not flakiness:** the same
  12 tests fail deterministically on every rerun; failure mode is
  `element(s) not found` / `toBeVisible` timeout after waiting for a specific
  DOM selector (drawer form row, ctx_size input, MTP control, NGL editor,
  reasoning pill, profile select, extra_args field). CI timing variance does
  not produce this pattern.
- **Suspect window:** the commits between the last green γ-suite run and
  `29767516143`. Likely culprits: drawer rework, MTP control addition, the
  `--no-profiles` migration, or any `ui/src/` change that restructured the
  slot drawer DOM.

---

## 2. Reproduction (verify the issue is still live before touching code)

```bash
# 1. Confirm γ-suite is currently red on main
gh run list --workflow="Playwright (γ)" --branch main --limit 3 \
  --json databaseId,conclusion,createdAt

# 2. Get the failing tests from the most recent main run
LATEST=$(gh run list --workflow="Playwright (γ)" --branch main --limit 1 \
        --json databaseId -q '.[0].databaseId')
gh run view "$LATEST" --log-failed 2>&1 \
  | grep -oE "tests/e2e/specs/[a-z0-9-]+\.spec\.ts:[0-9]+:[0-9]+ › [^›]+ › [^›]+$" \
  | sort -u
# Expected output: 12 lines from slot-drawer-profile-v3, slot-edit-controls-v3,
# slots-wireup-v3 — see issue #1333 for the canonical list.
```

If the failure list doesn't match issue #1333, **stop and update the issue**
before proceeding — a different failure mode is a different bug.

---

## 3. Strategy: bisect first, then decide (UI fix vs test fix)

The right fix depends on **whether the new UI is the intended design or the
tests are stale**:

| If bisect reveals… | Fix path |
| --- | --- |
| A drawer redesign that intentionally changed the DOM (e.g. fields moved, MTP got its own pane, profile selector was removed) | **Update the tests** to match the new drawer structure. Tests are documenting obsolete behaviour. |
| A UI change that broke drawer field visibility (regression — fields should still be there but aren't) | **Fix the UI** to restore field visibility. Tests are the source of truth. |
| An ambiguous middle (e.g. partial drawer rework, some fields kept, some moved) | **Investigate with the slot steward** — is the new shape intentional? If yes, update tests; if no, fix UI. |

Don't pick a side before bisecting. Don't pick "update tests" as the default
just because it's faster — the slot steward and operators rely on the
drawer fields the tests cover (ctx_size, NGL, extra_args, MTP, reasoning
pill), so if those fields are genuinely missing from the drawer, that's a
functional regression users are hitting.

---

## 4. Phases

### Phase 0 — Preflight (10 min)

1. `git pull origin main` and confirm HEAD matches the latest `gh run list --branch main` red run.
2. Open issue #1333 in another tab; keep the canonical failing-test list handy.
3. Confirm the repo's UI build is clean:

   ```bash
   cd ui && npm ci && npm run build && npm run typecheck
   ```

4. Find the last green γ-suite run on main (search backwards in
   `gh run list --workflow="Playwright (γ)" --branch main --json databaseId,conclusion,createdAt`
   for the first one with `conclusion: "success"`).
5. Find the first red run with the 12-test signature (run `29767516143`
   per issue #1333 — confirm by inspecting its log).
6. Confirm the suspect window is small enough to bisect:

   ```bash
   git log --oneline <last-green-sha>..29767516143 -- ui/
   ```

   If more than ~30 UI commits, prefer a direct read of recent drawer-touching
   commits over full bisect.

### Phase 1 — Bisect the regressing commit (1–2 h)

Pick whichever is faster:

**Option A — automated bisect** (if ≥5 candidate commits in window):

```bash
git bisect start <last-green-sha> 29767516143
git bisect run bash -c '
  cd ui && npm ci --silent &&
  npx playwright test --grep "C7a — GPU slot" --reporter=line 2>&1 | tail -5
'
```

**Option B — manual diff read** (if window is small):

```bash
git log --oneline <last-green-sha>..29767516143 -- ui/src/
# For each candidate, eyeball the diff for drawer / form-row / MTP /
# profile / NGL / extra_args / reasoning changes.
```

When you find the first commit where "C7a — GPU slot" fails (or any other
test in the canonical 12), stop and read the commit message + diff in full.
The commit message should explain the intent (redesign, fix, refactor) —
that tells you which fix path to take (UI vs tests).

### Phase 2 — Decide and implement (1–3 h depending on path)

**If the tests need updating** (intentional UI redesign):

- Update each of the 12 failing selectors in
  `slot-drawer-profile-v3.spec.ts`, `slot-edit-controls-v3.spec.ts`,
  `slots-wireup-v3.spec.ts` to match the new DOM.
- Preserve test intent (what behaviour is being asserted). Don't just make
  the assertion pass — write a comment on each updated line explaining
  *why* the selector changed (link the redesign commit).
- Add a CHANGELOG.md bullet under "Changed" if the new drawer behaviour is
  user-visible (operators reading the docs need to know fields moved).

**If the UI needs fixing** (regression):

- Restore the expected DOM structure (drawer form-row, adv-disclosure,
  ctx_size input, MTP control, NGL editor, reasoning pill, extra_args field).
- The simplest fix is often reverting just the breaking part of the
  offending commit (`git revert -n <sha>` then drop the specific hunk that
  broke the drawer).
- Verify with the 12 failing tests that they now pass.
- Add a CHANGELOG.md bullet under "Fixed".

**Branch + commit conventions:**

- Branch off latest `main`: `fix/gamma-suite-slot-drawer-<short-name>`
- One commit per fix path; squash only if multiple commits clutter history
- Sign off: `git commit -s` (DCO required per CONTRIBUTING.md)

### Phase 3 — Verify (30 min)

1. Run the 12 failing tests locally against your fix:

   ```bash
   cd ui && npx playwright test \
     --grep "C7a — GPU slot|C7d|C7e|C7i|C7j|C4 — drawer reasoning pill|C5 — NGL|C5 — editing ctx_size|drawer fields are grouped|extra_args is editable|Edit slot — drawer PATCHes" \
     --reporter=line
   ```

   All 12 must pass.
2. Run the full slot-related γ slice (regression check that your fix
   didn't break adjacent tests):

   ```bash
   npx playwright test --grep "Slots|Slot|SlotCard" --reporter=line
   ```

3. Push the branch; trigger the full γ workflow:

   ```bash
   git push -u origin fix/gamma-suite-slot-drawer-<short-name>
   gh workflow run "Playwright (γ)" --ref fix/gamma-suite-slot-drawer-<short-name>
   ```

   Wait for completion (~9 min). Must pass.
4. Open PR; reference issue #1333 in the description.
5. After PR lands, watch the next 3 main γ-suite runs to confirm no
   recurrence:

   ```bash
   gh run list --workflow="Playwright (γ)" --branch main --limit 5 \
     --json databaseId,conclusion,createdAt
   ```

   Update issue #1333's acceptance criteria as you complete each.

---

## 5. Acceptance criteria (mirrors issue #1333)

- [ ] Identified the regressing commit (Phase 1 output)
- [ ] Fix path chosen and justified (UI regression vs intentional redesign)
- [ ] γ-suite passing on the fix branch for one full run
- [ ] γ-suite passing on `main` for 3 consecutive runs after merge
- [ ] Issue #1333 closed with a one-paragraph summary of root cause + fix
- [ ] CHANGELOG.md bullet added if user-visible

---

## 6. Rollback

If the fix path turns out wrong (e.g. updating tests for an intentional
redesign, but the slot steward says the redesign was a mistake):

- Revert the fix PR (`git revert <merge-sha>` on main)
- Re-open issue #1333 with new findings
- Don't try to second-guess in-flight — revert cleanly, document, move on

---

## 7. Risks / things to watch for

- **Don't bisect on a long window.** If the suspect commit range has >30 UI
  commits, the bisect will take longer than a focused read. Eyeball first,
  bisect second.
- **Don't conflate test flakiness with regression.** If after a fix the
  γ-suite passes once but fails on the next run with a *different* test
  set, the underlying issue is flakiness or test-isolation, not the drawer
  DOM. That's a different bug — file a new issue, don't reopen #1333.
- **The tests are testing real drawer behaviour operators depend on.** If
  the fix path is "update tests to match new UI", make sure the new UI
  actually exposes the same functionality. A drawer that's prettier but
  lost the ability to edit ctx_size is a functional regression, not a
  test issue.
- **Don't merge without a green main γ-suite.** The blocker is "main is
  red" — even a correct PR can't land while the gate stays red. Coordinate
  with whoever is reviewing to make sure the fix lands first.
- **Check the slot steward / hal0-brain context.** The slot steward reads
  `/api/slots` and uses the same field names the drawer uses. If the
  drawer renamed a field, the steward probably needs updating too.

---

## 8. Quick reference

| Resource | Path / URL |
| --- | --- |
| Issue | <https://github.com/Hal0ai/hal0/issues/1333> |
| Failing tests | `ui/tests/e2e/specs/{slot-drawer-profile-v3,slot-edit-controls-v3,slots-wireup-v3}.spec.ts` |
| Drawer components | `ui/src/` (grep for `drawer`, `SlotDrawer`, `SlotEdit`, `form-row`) |
| γ workflow | `.github/workflows/gamma.yml` (or whatever the file is named — check `gh workflow list`) |
| Last green run | find via `gh run list --workflow="Playwright (γ)" --branch main --json databaseId,conclusion` |
| First red run | `29767516143` (2026-07-20T18:23:03Z) — verify still matches |

---

## 9. After this lands

- The `bug` label on #1333 stays until the 3-consecutive-green requirement
  is met, then the issue closes.
- Add a brief note to the rework board (`docs/rework/REWORK.md`) under
  "Resolved this week" pointing at the merged commit + issue #1333.
- Consider filing a follow-up: "γ-suite: add `--grep`-based smoke subset
  that runs on every PR so a 12-test drawer regression doesn't sit on main
  for hours unnoticed". The Playwright `γ` workflow is a release-gate —
  it shouldn't be the *first* signal of a UI regression.
