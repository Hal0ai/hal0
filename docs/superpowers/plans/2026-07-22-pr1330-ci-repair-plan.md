# PR #1330 CI Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore honest Python, UI, and Gamma CI signal on authoritative PR #1330 head `b91567fcff9c771b89808eadc71379174a0b259f` with a minimal, attributable repair.

**Architecture:** Work only in the isolated `fix/pr1330-ci-repair` branch. Reproduce each known failure before applying its smallest fix, verify each slice independently, then run progressively broader gates. CI hardening is not part of this plan and requires separate approval after this repair is green.

**Tech Stack:** Python 3.12, uv, Ruff, pytest, React/JSX, Node.js 22, npm, Vite, Playwright/Chromium, git, graphify.

## Global Constraints

- Base commit is exactly `b91567fcff9c771b89808eadc71379174a0b259f`; main ancestry baseline is `f07a1cb6c6eaa93e852a3a27e37d7faf63521608`.
- Worktree is `/home/mint/hal0/.worktrees/ci-pr1330-repair`; branch is `fix/pr1330-ci-repair`.
- Do not import divergent branch changes or commit `09120a9e`.
- Do not add or preserve a test skip as acceptance evidence.
- Do not touch CLI implementation, `installer/**`, updater/uninstall implementation, Hermes files, release/nightly workflows, live systems, `REWORK_BOARD.md`, or generated `graphify-out/**` files.
- A cancelled Chromium run is unknown, not passing.
- Full required GitHub CI remains the final merge gate.
- Do not implement the future lifecycle distro matrix or lifecycle CI design from `docs/rework/ci-pr1330-repair-coordination-note.md`.
- The final handoff must give the lifecycle-overhaul owner an exact reconciliation head and current CI/test inventory.
- Stop if an edit would cross an excluded ownership boundary or an unrelated failure cannot be isolated.

## File structure

- `tests/api/test_profiles_route.py`: assert the profile route contract actually seeded at the authoritative PR head and satisfy Ruff.
- `ui/src/dash/model-drawer.jsx`: retain one complete `dirty` calculation, including `thinking` and `jinja` comparisons.
- `ui/src/dash/slot-modals.jsx`: keep the hardware controls inside one correctly nested `FieldGroup`.
- Existing `ui/tests/e2e/specs/*.spec.ts`: verification surface only unless a specific current-control mismatch is reproduced and separately reviewed.

---

### Task 1: Repair the profile-route contract and Ruff failure

**Files:**
- Modify: `tests/api/test_profiles_route.py:96-124`
- Read: `src/hal0/config/data/seed_profiles.toml:70-90`

**Interfaces:**
- Consumes: `GET /api/profiles`, whose seed rows include `seed` and `device_class`.
- Produces: assertions that FLM serializes as `npu`, Kokoro as `cpu`, and Chat as `None` at this PR baseline.

- [ ] **Step 1: Confirm the worktree baseline and isolate HAL0 state**

Run:

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair
test "$(git merge-base HEAD b91567fcff9c771b89808eadc71379174a0b259f)" = b91567fcff9c771b89808eadc71379174a0b259f
export HAL0_HOME="$(mktemp -d /tmp/hal0-pr1330-profile.XXXXXX)"
printf 'HEAD=%s HAL0_HOME=%s\n' "$(git rev-parse HEAD)" "$HAL0_HOME"
```

Expected: HEAD descends from `b91567fc`; `HAL0_HOME` points under `/tmp`.

- [ ] **Step 2: Install the exact Python environment**

Run:

```bash
uv sync --frozen --extra dev --python "$(which python3.12)"
```

Expected: dependency synchronization succeeds without changing `uv.lock`.

- [ ] **Step 3: Reproduce the focused contract failure**

Run:

```bash
uv run pytest -q tests/api/test_profiles_route.py::TestListProfiles::test_device_class_values
```

Expected: FAIL because FLM is `npu` rather than `None` (and Kokoro is `cpu` rather than `None`).

- [ ] **Step 4: Reproduce the Ruff finding**

Run:

```bash
uv run ruff check tests/api/test_profiles_route.py
```

Expected: FAIL with unused `noqa` code `RUF100` for `# noqa: F631`.

- [ ] **Step 5: Apply the minimal assertion repair**

Replace:

```python
            assert "seed" in item  # noqa: F631
```

with:

```python
            assert "seed" in item
```

Replace:

```python
        assert flm["device_class"] is None
        kokoro = next(item for item in data if item["name"] == "kokoro")
        assert kokoro["device_class"] is None
```

with:

```python
        assert flm["device_class"] == "npu"
        kokoro = next(item for item in data if item["name"] == "kokoro")
        assert kokoro["device_class"] == "cpu"
```

Do not change the Chat assertion.

- [ ] **Step 6: Verify the focused repair**

Run:

```bash
uv run pytest -q tests/api/test_profiles_route.py
uv run ruff check tests/api/test_profiles_route.py
uv run ruff format --check tests/api/test_profiles_route.py
```

Expected: all commands exit 0.

- [ ] **Step 7: Commit the Python repair**

```bash
git add tests/api/test_profiles_route.py
git commit -m "test: align profile route seeds with baseline"
```

Expected: one-file commit; no source behavior change.

---

### Task 2: Remove the duplicate model-drawer declaration

**Files:**
- Modify: `ui/src/dash/model-drawer.jsx:644-675`

**Interfaces:**
- Consumes: local model-edit state (`name`, `types`, `caps`, `backends`, model metadata, profile, context, template, MTP, thinking, and Jinja values).
- Produces: exactly one boolean `dirty` binding used by the existing drawer save controls.

- [ ] **Step 1: Install the exact UI environment**

Run:

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair/ui
npm ci
```

Expected: installation succeeds without changing `package-lock.json`.

- [ ] **Step 2: Reproduce the parser failure**

Run:

```bash
npm run lint
```

Expected: FAIL at `src/dash/model-drawer.jsx` because `dirty` is declared twice. The command may also report the independent `slot-modals.jsx` parser failure.

- [ ] **Step 3: Remove only the second declaration**

Delete the second block beginning with the space-indented line:

```jsx
  const dirty =
```

and ending with:

```jsx
    jinja !== triFromDefault(init.jinja);
```

Retain the first tab-indented declaration in full. It must continue to include both:

```jsx
		thinking !== triFromDefault(init.enable_thinking) ||
		jinja !== triFromDefault(init.jinja);
```

- [ ] **Step 4: Verify this parser defect is gone**

Run:

```bash
npm run lint 2>&1 | tee /tmp/hal0-pr1330-ui-lint-after-drawer.log
! rg -n 'dirty.*already been declared|Identifier .dirty. has already been declared|model-drawer\.jsx' /tmp/hal0-pr1330-ui-lint-after-drawer.log
```

Expected: the duplicate-`dirty` diagnostic is absent. Overall lint may still fail only on the independent `slot-modals.jsx` parser defect.

- [ ] **Step 5: Commit the drawer repair**

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair
git add ui/src/dash/model-drawer.jsx
git commit -m "fix(ui): remove duplicate drawer dirty state"
```

Expected: one-file commit.

---

### Task 3: Repair the slot hardware field-group nesting

**Files:**
- Modify: `ui/src/dash/slot-modals.jsx:832-981`

**Interfaces:**
- Consumes: the existing slot editor form and `FieldGroup` component.
- Produces: one syntactically valid Hardware group containing device, NGL, threads, binary, and image-pin controls.

- [ ] **Step 1: Reproduce the remaining parser failure**

Run:

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair/ui
npm run lint
```

Expected: FAIL in `slot-modals.jsx` around the Hardware group due to a closing `</FieldGroup>` appearing before the Hardware group begins while surrounding JSX remains nested incorrectly.

- [ ] **Step 2: Remove the premature close only**

Delete this standalone line immediately before the Hardware-grid comment:

```jsx
      </FieldGroup>
```

Keep the Hardware group’s own balanced opening and closing tags:

```jsx
      <FieldGroup label="Hardware" hint="device · placement · runner">
```

and:

```jsx
      </FieldGroup>
```

Do not reformat or refactor adjacent hardware controls.

- [ ] **Step 3: Verify lint and production parsing**

Run:

```bash
npm run lint
npm run build
```

Expected: both commands exit 0; neither file emits a parser error.

- [ ] **Step 4: Run diagnostic UI gates without broadening scope**

Run:

```bash
npm run typecheck
npm run test:unit
```

Expected: record exact results. If either fails for an unrelated baseline reason, do not edit additional files; preserve logs and return for scope review.

- [ ] **Step 5: Commit the slot-modal repair**

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair
git add ui/src/dash/slot-modals.jsx
git commit -m "fix(ui): restore slot hardware field nesting"
```

Expected: one-file commit.

---

### Task 4: Validate Gamma coverage honestly

**Files:**
- Verify: `ui/tests/e2e/specs/slot-drawer-profile-v3.spec.ts`
- Verify: `ui/tests/e2e/specs/slot-edit-controls-v3.spec.ts`
- Verify: `ui/tests/e2e/specs/slots-wireup-v3.spec.ts`
- Modify: none by default

**Interfaces:**
- Consumes: repaired production JSX and existing production-shaped Playwright fixtures.
- Produces: executable Chromium evidence without introducing unconditional skips.

- [ ] **Step 1: Prove this branch did not import the prohibited skip commit**

Run:

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair
git merge-base --is-ancestor 09120a9e HEAD && exit 1 || true
git diff --unified=0 b91567fcff9c771b89808eadc71379174a0b259f -- ui/tests/e2e | rg '^\+.*test\.skip' && exit 1 || true
```

Expected: both guards exit 0 and no newly added unconditional skip is printed.

- [ ] **Step 2: Install Chromium when necessary**

Run:

```bash
cd ui
npx playwright install --with-deps chromium
```

Expected: Chromium and required system dependencies are available.

- [ ] **Step 3: Run the focused drawer/control specifications first**

Run:

```bash
CI=true npx playwright test \
  tests/e2e/specs/slot-drawer-profile-v3.spec.ts \
  tests/e2e/specs/slot-edit-controls-v3.spec.ts \
  tests/e2e/specs/slots-wireup-v3.spec.ts \
  --project=chromium
```

Expected: tests execute rather than becoming newly skipped. Record pass/fail/skip counts exactly. If a test fails because its control has genuinely been replaced, stop and present the failure, replacement control, and proposed rewrite before editing the test.

- [ ] **Step 4: Run the workflow-equivalent Gamma command**

Run:

```bash
CI=true npm run test:e2e
```

Expected: complete Playwright result with exact pass/fail/skip counts. A cancellation or timeout is reported as unknown.

- [ ] **Step 5: Confirm no Gamma files changed accidentally**

Run:

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair
test -z "$(git diff --name-only b91567fcff9c771b89808eadc71379174a0b259f -- ui/tests/e2e)"
```

Expected: exit 0. Any required test rewrite needs separate review before commit.

---

### Task 5: Run final repair gates and audit the changeset

**Files:**
- Verify: `tests/api/test_profiles_route.py`
- Verify: `ui/src/dash/model-drawer.jsx`
- Verify: `ui/src/dash/slot-modals.jsx`
- Verify: repository-wide Python and UI gates
- Modify: generated graph files only transiently; do not commit them

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: final local evidence and a narrowly scoped branch ready for independent review and required GitHub CI.

- [ ] **Step 1: Run focused Python gates with isolated state**

Run:

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair
export HAL0_HOME="$(mktemp -d /tmp/hal0-pr1330-final.XXXXXX)"
uv run pytest -q tests/api/test_profiles_route.py
uv run ruff check tests/api/test_profiles_route.py
uv run ruff format --check tests/api/test_profiles_route.py
```

Expected: all commands exit 0.

- [ ] **Step 2: Run required repository Python gates**

Run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest -q tests/
```

Expected: all commands exit 0. If an unrelated baseline failure occurs, capture the exact failing node and stop rather than expanding scope.

- [ ] **Step 3: Run required UI gates**

Run:

```bash
cd ui
npm run lint
npm run build
```

Expected: both commands exit 0.

- [ ] **Step 4: Refresh the project graph after source changes**

Run:

```bash
cd /home/mint/hal0/.worktrees/ci-pr1330-repair
graphify update .
```

Expected: graph update completes. Do not stage `graphify-out/**`.

- [ ] **Step 5: Audit ownership, skips, whitespace, and lockfiles**

Run:

```bash
allowed='^(tests/api/test_profiles_route\.py|ui/src/dash/model-drawer\.jsx|ui/src/dash/slot-modals\.jsx|docs/superpowers/(specs/2026-07-22-pr1330-ci-repair-design\.md|plans/2026-07-22-pr1330-ci-repair-plan\.md))$'
git diff --check b91567fcff9c771b89808eadc71379174a0b259f
unexpected="$(git diff --name-only b91567fcff9c771b89808eadc71379174a0b259f | grep -Ev "$allowed" || true)"
test -z "$unexpected" || { printf 'Unexpected paths:\n%s\n' "$unexpected"; exit 1; }
git diff --unified=0 b91567fcff9c771b89808eadc71379174a0b259f -- ui/tests/e2e | rg '^\+.*test\.skip' && exit 1 || true
git status --short
```

Expected: no unexpected paths, no added skips, no lockfile changes, and only known generated graph dirt remains unstaged if hooks changed it.

- [ ] **Step 6: Record the verification result without merging or pushing**

Run:

```bash
git log --oneline --decorate b91567fcff9c771b89808eadc71379174a0b259f..HEAD
git diff --stat b91567fcff9c771b89808eadc71379174a0b259f..HEAD
```

Expected: design/plan commits plus three narrowly scoped repair commits. Do not merge or push.

Write the handoff with these explicit fields:

Use these headings and populate them directly from the preceding commands and logs:

```markdown
## CI repair handoff
- Ready-to-integrate head
- Baseline (`b91567fcff9c771b89808eadc71379174a0b259f`)
- Workflows and job names changed
- Tests or fixtures removed, consolidated, or replaced, with replacement coverage
- Required jobs: Python lint/format/pytest; UI lint/build; Gamma Chromium workflow
- Optional/diagnostic checks: UI typecheck and unit tests until separately promoted
- Measured or approximate required-PR duration
- Remaining flaky, skipped, duplicated, or disproportionately expensive suites
- Lifecycle assumptions to preserve, including rebasing onto this repair head before CI consolidation
- Exact verification commands, exit statuses, and concise results
- Exact GitHub CI state, or `pending` when it has not run
```

Do not invent timings or results. Use `unavailable` when no defensible measurement exists.
