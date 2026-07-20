# hal0 rework — board tracking protocol (paste-in prompt)

Paste this at the start of any orchestrator or teammate session working the hal0 rework. It defines
how to read, update, and drive work through the single canonical board. Terse by design.

## Source-of-truth files (read in this order at session start)
1. **`REWORK.md`** — the spec: finish line, non-goals, invariants, checkpoints R1–R5, golden-paths,
   per-lane definition-of-done. This is *what* and *why*. Do not restate it elsewhere.
2. **`REWORK_BOARD.md`** — the board: every lane's live status, mapped to R1–R5. This is the ONLY
   status source. The old `hal0-rework-tracker*.md` are retired (banner points here) — never update them.
3. **`hal0-rework-plan.md`** — history/derivations only. Read for background, never as current authority.
4. Latest handoff(s) named in the board header (e.g. `/tmp/hal0-rework-handoff-*.md`).
Then run `bash /home/mint/hal0-status.sh` for a live snapshot (sessions, agents, lanes, CI, checkpoints).

## Single-writer rule
The **orchestrator (main session) owns `REWORK_BOARD.md`.** Teammates/agents NEVER edit it. Agents
report status in their handback message; the orchestrator writes the row. This is the anti-duplicate-
truth discipline the rework itself enforces — one owner per fact.

## Board row = the machine-readable record
Every lane is one row with these fields (keep them filled):
`id | lane | status | owner_class | deps | commit/branch | verify | deploy_state` (+ its R1–R5 checkpoint).
- **status:** `✔` done+CI-green · `▶` in-flight · `☐` todo · `⏸` deferred(safe) · `⛔` blocked
- **owner_class** (collision class — serialize lanes within a class): SEC · MODEL · RUNNER · SLOT ·
  INSTALL · HERMES · API · UI · OBS · DOCS · DEPLOY
- **commit/branch:** the hash on descar (or the held branch name if not merged)
- **verify:** what proved it (test counts, halo validation)
- **deploy_state:** halo143 status / "held for post-Rn" / "MOOT for fresh install"

## Lifecycle of a lane (how a row moves)
1. **Dispatch** — orchestrator spawns a build agent in an ISOLATED worktree off
   `origin/rework/descar`, sets the row `▶` + `owner_class` + owner. One file-mutating lane per
   collision class at a time; disjoint classes run in parallel.
2. **Build** — agent works ONLY in its worktree, `PYTHONPATH=$PWD/src`, commits there, NEVER
   pushes/merges. Its final message IS the handback record.
3. **Verify (orchestrator)** — independently: `ruff check` **and** `ruff format --check`, import-smoke
   (`create_app()`), `scripts/check_sunset.py`, targeted pytest `<90s`, and a **trial-merge vs current
   origin** (`git merge --no-commit --no-ff origin/rework/descar; git merge --abort`). Never trust the
   agent's self-report alone; never run full pytest locally (hangs on podman/systemd).
4. **Merge (orchestrator)** — `git merge --no-ff <branch>` into descar, re-verify combined, push,
   watch real CI **via an open PR** (a closed PR = pushes get no CI; open `gh pr create` if needed).
   CI (full GitHub pytest) is the real gate — a green capped local run is NOT a merge signal.
5. **Accept** — CI green → flip row `✔` with commit hash, remove the worktree + delete the branch,
   release the agent (`shutdown_request`). Log any discovered follow-up as its own row.

## Checkpoint (R1–R5) → main
Collapse descar → `main` at a checkpoint when: CI green, no BROKEN half-lane (deferred-but-inert
partials are fine), and halo install rerun passes. Tag `rework-R<n>`. Keep in-flight/next-layer lanes
OUT of the checkpoint (they belong to the next one). Don't let descar diverge from main indefinitely —
collapse at each meaningful, deployable checkpoint.

## Migration-window lanes
`P2-config`, `P2-memory/honcho`, `P3-runtime-db`, perms live-migration are **orchestrator-run live
steps, NOT agents** (they touch live state / need halo). Follow the procedure in `REWORK.md`.

## Monitor
`/home/mint/hal0-board.sh` — tmux board (sessions · live agents · lane commits · CI · checkpoints).
`/home/mint/hal0-board.sh kill` to tear down. Backed by `hal0-status.sh` (also runs standalone).

## Reusable agent-dispatch prompt skeleton
```
Build agent — <lane> [<checkpoint>].
WORKTREE (work ONLY here): <path> (branch rework/<name>, off origin/rework/descar @ <sha>).
Run python: cd <wt> && PYTHONPATH=$PWD/src /home/mint/hal0/.venv/bin/python ...
SPEC: read <spec path> IN FULL (line refs may be stale — re-verify before editing).
SCOPE: <exact deliverables>. COLLISION FENCE: you OWN <files>; do NOT touch <other lanes' files>.
VERIFY (capped, from worktree, PYTHONPATH=$PWD/src): ruff check + ruff format --check; import smoke;
  scripts/check_sunset.py (deletions LOWER scar → lower scar_baseline.txt + say old→new); targeted
  pytest <90s (name files — tests/api broadly HANGS, never run whole).
GIT: commit in worktree, conventional commits, body ends with
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>. Do NOT push/merge.
REPORT BACK (final msg = handback record): commits (hash+subject), files changed, every verify
  cmd+result, scar old→new, any shared-file edits (for merge coordination), deviations, anything
  blocked/left. Precise.
```

## Hard rules (never violate)
- Never push to `hal0`/`hal0lxc` (live lxc105). Deploy target = **halo143** side-by-side.
- Scar ratchet monotonic-down; every merge keeps `check_sunset` green.
- New route ⇒ `security/exposure.py` classification (deny-by-default) or CI fails.
- One migration number per db lane (`001` registry · `002` metrics · `003` store · `004` slots/ports;
  next lane = `005`). Two files at one version = broken migrate.
- Liveness/preflight probes hit OPEN endpoints (`/api/health`), never admin-gated (`/api/status`).
