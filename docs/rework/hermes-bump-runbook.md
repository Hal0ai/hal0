# Hermes-bump runbook — moving the pinned upstream commit

Operator steps for bumping the reviewed, human-gated `hermes-agent` upstream
pin (`[tool.hal0.upstream-hermes]` in `pyproject.toml`, ADR-0018 §4) to a new
commit. hal0 tracks upstream Hermes at a single **reviewed commit**, never a
moving branch or open version range — the mechanics for that (SHA pin, the
weekly `hermes-sdk-diff` drift job, the frozen contract-fixture suite,
`scripts/hermes-sdk-diff.sh --bump`) already exist and are exercised weekly.
What did not exist before this doc: a single place that lists the **full**
procedure end to end. `--bump` only rewrites `pyproject.toml`; three other
hardcoded copies of the pin, the contract fixtures, and two live-validation
phases all need a conscious human pass. This is that pass.

**Effort:** minor/S per the R5 sync assessment §7 item 5, but do not rush
steps 5–8 — they are the actual point of a human-gated bump.

---

## 0. When to run this

- The weekly `hermes-sdk-diff` GitHub Action (`.github/workflows/hermes-sdk-diff.yml`)
  opened/updated an `upstream-drift` issue and the tracked surfaces' diff has
  been reviewed and judged safe (or safe-with-adapter-fixes) to adopt.
- An operator deliberately wants to move to a specific newer upstream
  commit/tag ahead of the weekly cadence (e.g. a published security fix).

Never bump because CI is red on something unrelated, and never let the
weekly job auto-merge a bump — the workflow only opens an issue; a human
runs this procedure and opens the PR by hand.

## 1. Preconditions

- [ ] The drift issue (or your own diff) has been read in full — not just
      skimmed. `scripts/hermes-sdk-diff.sh` (no args) against the current pin
      shows the same diff locally if you want a second look:
      `bash scripts/hermes-sdk-diff.sh`.
- [ ] The candidate commit is an **annotated tag or a commit you can point to
      a specific reviewed release** — hal0 refuses unreviewed VCS pins (see
      `hal0.agents.hermes_provision.VETTED_HERMES_REFS` / `#1247`, the
      hermes-agent 0.15.2 broken-wheel floor guard). Resolve the tag to its
      commit SHA (`git ls-remote https://github.com/NousResearch/hermes-agent.git <tag>`)
      before starting — every pin location below wants the 40-char commit
      SHA, not the tag name.
- [ ] You have a way to run the new Hermes build somewhere before both-boxes
      validation (a scratch LXC, a container, or at minimum a local
      `pip install` of the pinned ref into a throwaway venv) — steps 5–6 need
      to read real source, not guess from the diff.
- [ ] Working tree is clean on a feature branch off `rework/descar` (or
      current integration branch) — this touches 4+ files across 3
      subsystems (config, installer, tests) and should land as one reviewable
      commit, not mixed with unrelated work.

## 2. The four pin locations (read this before touching anything)

`--bump` only rewrites the first row. The other three are separate hardcoded
copies that exist for good, load-bearing reasons (see "why" column) and
**will not raise an error if left stale** — nothing currently cross-checks
row 3/4 against row 1 at import time, only at test time (§5).

| # | Location | Rewritten by `--bump`? | Why it exists |
|---|----------|:---:|----------------|
| 1 | `pyproject.toml` → `[tool.hal0.upstream-hermes].commit` (+ `.date`) | **Yes** | Single source of truth the weekly diff job and `hermes-sdk-diff.sh` read. |
| 2 | `installer/agents/hermes/requirements.txt` — the `hermes-agent[web] @ git+...@<sha>` line | No — manual | What actually gets installed on a box. `tests/agents/hermes/test_contract_compatibility.py::test_installer_pins_reviewed_official_commit` fails if this drifts from row 1. |
| 3 | `tests/agents/hermes/test_contract_compatibility.py` — module-level `HERMES_COMMIT` constant | No — manual | Frozen expected value the contract suite asserts rows 1 and 2 against (`test_sdk_diff_pin_matches_frozen_commit`, `test_installer_pins_reviewed_official_commit`). |
| 4 | `src/hal0/agents/hermes_provision.py` → `VETTED_HERMES_REFS` frozenset | No — manual | The broken-build-floor allowlist (`#1247`): an unreviewed git-ref pin is rejected by `hermes_requirement_is_vetted()` exactly like a known-broken published version. If you don't add the new SHA here, `install_hermes` refuses the new pin outright — this is often the step that gets forgotten because nothing about it looks version-related at a glance. |

There is a **fifth** stale-prone spot that is documentation, not a pin:
`ARCHITECTURE.md`'s `hermes-sdk-diff` glossary entry hand-lists the tracked
files (currently stale — it still names the dropped `agent/events.py` and is
missing several files `tracked_files` already carries). Not load-bearing for
correctness, but fix it in the same PR if you're touching this area — see
step 9.

## 3. Procedure

### Step 1 — Rewrite the primary pin (row 1)

```bash
bash scripts/hermes-sdk-diff.sh --bump <new-sha>
```

This rewrites `commit = "..."` and `date = "..."` inside
`[tool.hal0.upstream-hermes]` in `pyproject.toml` only. Diff it:

```bash
git diff pyproject.toml
```

Optional (in scope for this bump, not required): teach `--bump` to also
rewrite rows 2 and 4 below so future bumps need one command instead of three
manual edits — tracked as a follow-up, not blocking this procedure.

### Step 2 — Rewrite the installer pin (row 2)

Edit `installer/agents/hermes/requirements.txt`, the
`hermes-agent[web] @ git+https://github.com/NousResearch/hermes-agent.git@<old-sha>`
line — replace `<old-sha>` with `<new-sha>`. Keep the `[web]` extra and the
`git+https://` VCS form; do not switch to a version pin (`==x.y.z`) — the
project's whole posture is "reviewed commit, not a numeric release" (§1
preconditions, `#1247`).

### Step 3 — Add the new ref to the vetted allowlist (row 4)

In `src/hal0/agents/hermes_provision.py`, add the new SHA to
`VETTED_HERMES_REFS`. Decide whether to also **remove** the old SHA:
- Keep both (old + new) for one release if you want a fast revert path
  (row 4 is an allow-list, not an active pin — harmless to carry an extra
  entry).
- Drop the old SHA once the bump is validated and shipped, to keep the
  allowlist from silently growing forever.

### Step 4 — Update the frozen test constant (row 3)

In `tests/agents/hermes/test_contract_compatibility.py`, update the
module-level `HERMES_COMMIT = "..."` constant to `<new-sha>`. Leave
`HERMES_REPO` alone unless the upstream org/repo itself changed.

At this point rows 1–4 agree. Run the lockstep tests to confirm before
touching any fixture content:

```bash
uv run pytest tests/agents/hermes/test_contract_compatibility.py -k "pin or installer_pins" -v
```

`test_sdk_diff_pin_matches_frozen_commit` and
`test_installer_pins_reviewed_official_commit` must pass. Everything else in
that file will now start failing until step 5 re-vendors the fixtures — that
is expected and is the whole point (the tripwire firing).

### Step 5 — Re-vendor the contract fixtures

For every file listed in `[tool.hal0.upstream-hermes].tracked_files`
(pyproject.toml), diff the pinned-old vs pinned-new source and update the
corresponding frozen fixture under `tests/fixtures/hermes/contracts/`:

```bash
api_surface.py       ← gateway/platforms/api_server.py
config_defaults.py   ← hermes_cli/config.py, hermes_cli/auth.py
memory_provider.py   ← agent/memory_provider.py
memory_loader.py     ← plugins/memory/__init__.py
plugin_context.py    ← hermes_cli/plugins.py
provider_profile.py  ← providers/base.py, providers/__init__.py
voice.py             ← agent/tts_registry.py, agent/transcription_registry.py, hermes_cli/voice.py
kanban_runs.py        ← hermes_cli/kanban_db.py, plugins/kanban/dashboard/plugin_api.py
```

For each: read the new upstream source at `<new-sha>` (clone it, or use the
working copy `hermes-sdk-diff.sh` already checked out under its temp workdir
if you kept it with `HAL0_HERMES_DIFF_KEEPDIR=1`), and for every value the
fixture freezes (a signature string, a route tuple, a config key, a table
name, a security default) either:
- confirm it is byte-identical → no change, or
- update it to the new value **consciously** — this is the "adapter lane
  builds against a verified surface" gate the fixtures exist for. Do not
  bulk-copy; read the diff for each touchpoint.

If a fixture's `KNOWN GAP` canary (see `kanban_runs.py`'s
`test_kanban_runs_creation_and_cancel_routes_are_a_known_gap`) starts
failing, that means upstream added a route hal0 was missing — do not delete
the test; extend the fixture to include the new route and hand the
"executor bridge can now use it properly" follow-up to the HP-executor lane
(board), not to this bump.

Run the full contract suite:

```bash
uv run pytest tests/agents/hermes/test_contract_compatibility.py -v
```

All non-`xfail` tests pass, or every failure has been consciously resolved by
updating a fixture to match verified new upstream source (never by loosening
an assertion to "make it pass").

### Step 6 — Run the parity suites

The Hermes plugin **seeds** (hal0-memory, hal0-provider) ship as two
byte-identical copies — the canonical source
(`src/hal0/agents/hermes/plugins/memory_hindsight/`) and the installer seed
(`installer/agents/hermes/plugins/hal0-memory/`) the provisioner writes into
`$HERMES_HOME/plugins/hal0-memory/`. A Hermes bump can change the plugin SDK
shape underneath both copies at once:

```bash
uv run pytest tests/agents/hermes_plugins/test_seed_parity.py \
              tests/agents/hermes/plugins/test_hal0_provider_parity.py -v
```

Then the broader δ-harness (Hermes `delegate_task` integration coverage
across the LOCAL/DOCKER/MODAL backends) and the installer convergence unit
tests:

```bash
uv run pytest tests/harness/integration/ -v
uv run pytest tests/agents/test_hermes_provision*.py -v
```

### Step 7 — Full test + smoke gate

```bash
uv run pytest tests/ -q                       # full suite
uv run python3 scripts/check_sunset.py         # scar/shim baseline unchanged
uv run python3 -c "import hal0.api"            # import smoke
```

### Step 8 — Re-run the two live-validation phases (deploy window, both boxes)

These are **not** unit tests — they need a real box with the new Hermes
build installed. Follow `docs/rework/both-boxes-runbook-r4-stage.md`:

- **Phase 5 — Hermes plugin liveness** (§ same file): provider model
  discovery through the gateway, memory write/recall via live chat, no
  plugin import errors in `journalctl -u hermes-gateway`.
- **Phase 6 — HP-executor first live contact**: with
  `HERMES_DASHBOARD_BASE_URL` set, dispatch one board card at Hermes and
  confirm `board.hermes_executor_registered`, a run/event gets appended, and
  hal0's own board state (lane/deps/approval) stays untouched. **If Hermes
  404s the runs path** (a real, currently-open risk — see
  `tests/fixtures/hermes/contracts/kanban_runs.py`'s documented gap: the
  pinned kanban plugin has no bare run-creation route and calls the
  terminal-state route `terminate`, not `cancel`), capture the exact path
  Hermes actually serves and fix `WORKER_BASE_PATH` /
  `HermesBoardExecutor` accordingly — that is expected in-scope work for a
  bump that changes this surface, not a separate bug.

Run both phases on **both** boxes (150 privileged, 143 unprivileged-in-LXC)
per the standing both-boxes policy; record results in that runbook's
checklist. Never validate against lxc105 (untouched live reference).

### Step 9 — Documentation sweep (same PR)

- Fix `ARCHITECTURE.md`'s `hermes-sdk-diff` glossary entry (`### hermes-sdk-diff`)
  if its hand-listed tracked-files example drifted further from
  `pyproject.toml`'s actual `tracked_files` (it already lists a dropped file,
  `agent/events.py`, as of this writing — worth fixing opportunistically).
- Update `docs/rework/hermes-official-integration-research.md`'s pinned-commit
  references if that doc is still actively read (it is currently a point-in-time
  research record — check its own header before editing).

## 4. Verification checklist

- [ ] `pyproject.toml`, `requirements.txt`, `HERMES_COMMIT` constant, and
      `VETTED_HERMES_REFS` all reference `<new-sha>` (or, for row 4, at least
      contain it).
- [ ] `uv run pytest tests/agents/hermes/ tests/agents/hermes_plugins/ tests/harness/integration/ -v` — all green (xfail rows unchanged from before the bump, or consciously re-justified if the deviation upstream fixed itself).
- [ ] `uv run pytest tests/ -q` — full suite green.
- [ ] `scripts/check_sunset.py` — no regression against the scar baseline.
- [ ] `bash scripts/hermes-sdk-diff.sh` — reports **no drift** against the new pin (proves rows 1/2/3 and `tracked_files` all agree with the new upstream HEAD-of-pin).
- [ ] Phase 5 + Phase 6 (§3 step 8) passed on both boxes, results recorded in `both-boxes-runbook-r4-stage.md`.
- [ ] `hal0 agent upgrade hermes` exercised at least once against a box already on the old pin, end to end (pulls the newly-vetted ref within the requirements floor/cap, runs `hermes config migrate`, reprovisions) — this is the operator upgrade path real users hit; a bump that only worked via a from-scratch install is not done.

## 5. Rollback

The bump is a single commit touching config + tests, never auto-merged and
never mutating live boxes by itself — rollback is cheap:

1. `git revert <bump-commit-sha>` restores all four pin locations and every
   fixture to the previous reviewed state atomically (they were one commit).
2. If a box was already upgraded live via `hal0 agent upgrade hermes` before
   the regression was found: re-run `hal0 agent upgrade hermes --to <old-version-or-ref>`
   pinned back to the previous vetted ref (still in `VETTED_HERMES_REFS` if
   you chose to keep it in step 3), then re-run Phase 5/6 to confirm the box
   is back to a known-good state.
3. Re-open (or comment on) the `upstream-drift` issue explaining what broke,
   so the next bump attempt doesn't repeat it.

## 6. PR conventions

- Title: `chore(hermes): bump upstream pin to <short-sha>`.
- Body: link the drift issue, summarize what changed in the tracked surfaces
  (the `hermes-sdk-diff.sh` markdown output is a good starting point), list
  any fixture value that changed and why, and call out explicitly if step 8
  (both-boxes live validation) has not landed yet — do not silently merge a
  bump whose live-validation phases are still pending; say so in the PR body
  and track it as a follow-up task.
- Never skip CI, never auto-merge — the entire point of `VETTED_HERMES_REFS`
  + the contract suite is that a pin change is human-reviewed, every time.

## References

- `pyproject.toml` → `[tool.hal0.upstream-hermes]` (ADR-0018 §4, cited inline).
- `scripts/hermes-sdk-diff.sh` — the diff/bump tool this runbook wraps.
- `.github/workflows/hermes-sdk-diff.yml` — weekly drift job + issue automation.
- `tests/agents/hermes/test_contract_compatibility.py` — the lockstep + frozen-surface suite.
- `tests/fixtures/hermes/contracts/` — the frozen fixtures re-vendored in step 5.
- `src/hal0/agents/hermes_provision.py` → `VETTED_HERMES_REFS`, `hermes_requirement_is_vetted()`.
- `docs/rework/both-boxes-runbook-r4-stage.md` — Phase 5/6 live-validation steps.
- `docs/rework/r5-sync-assessment-2026-07-19.md` §7 item 5 — the ask this runbook fulfills.
