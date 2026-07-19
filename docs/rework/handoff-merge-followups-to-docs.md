# Handoff — fold rework→main merge/meld/fix follow-ups into the R5 docs

> **Paste-in prompt for the follow-up session.** You are holding a list of
> merge / meld / fix suggestions produced while landing `rework/descar` onto
> GitHub `main`. This brief tells you exactly WHERE each kind of finding
> goes, in WHAT format, without clobbering the conventions these docs
> already enforce. Read `docs/rework/REWORK_BOARD_PROTOCOL.md` first
> (single-writer rule, lane lifecycle, verify/merge discipline).

## Base facts (verify before editing)
- `rework/descar` is in sync with `origin/rework/descar` (tip moves; re-check).
- GitHub `main` has advanced past descar's branch base: `462b28f` + #1315
  (merged) + #1316 (UI-API-1 managed-arg gap CLOSED — launch + create +
  `/validate`). Any descar→main landing rebases onto **current** `origin/main`.
- The R5 docs lanes P4-docs / P4-rules / §21.11 golden-paths are ✔ on descar.

## The doc surfaces you may write to (and their ONE job each)

| # | file | its single job | how to add |
|---|------|----------------|------------|
| 1 | `docs/rework/REWORK_BOARD.md` | **status** layer: lane rows + open-adds | **single-writer** — see below |
| 2 | `ARCHITECTURE.md` | the one authoritative internal doc: architecture, glossary, standing decisions **inline** (no ADR tree) | edit the relevant section inline |
| 3 | `CONTRIBUTING.md` → *Anti-scar rules* | the durable rules that stop scar regrowth (currently 1–11, some **gated**) | append rule 12+ only for a genuinely new, earned lesson |
| 4 | `tests/golden_paths/__init__.py` | authoritative 15-scenario coverage map (CI-here / existing-suite / deploy-runbook) | update the map row + cite the owner |
| 5 | `docs/rework/golden-paths-halo143-runbook.md` | deploy-only golden-path steps, both-boxes policy | add/adjust the scripted step |

## Routing table — finding type → target

Classify each of your suggestions, then apply the row:

- **A cutover / migration / deploy step** (something that must happen when
  descar lands on main, or on a live box) → **board** Migration-window lane
  row *or* a `## Open review-driven adds` bullet, **and** if it's a real-box
  action, a step in the **halo143 runbook** (#5). Don't bury a deploy action
  only in prose — it needs a checkable step.

- **A code meld / reconciliation** (two implementations to unify when the
  branches merge; a conflict resolution like the AGENTS.md case below) →
  **board** row for the owning lane (status ▶/☐, note the meld), *and* if it
  changes a standing decision, update **ARCHITECTURE.md** inline.

- **A fix follow-up** (a bug or hardening the merge surfaced) → **board** row
  under the owning lane or `## Open review-driven adds`, with file:line
  evidence and the fix sketch. If it's already a PR on main, point the row
  AT the PR number (like UI-API-1 → #1316) — do NOT re-spec a shipped fix.

- **A new anti-scar lesson** (a class of mistake the meld revealed, ideally
  one that bit more than once) → **CONTRIBUTING.md** rule 12+. One line rule
  + one line why. Mark **(gated)** only if CI actually enforces it. Do not
  add aspirational rules with no teeth.

- **An architecture / decision change** → **ARCHITECTURE.md** inline, next to
  the code it governs. hal0 keeps **no ADR tree** (rule 9: no ghost-doc
  citations) — do not create `docs/design/adr-NNN`; the rationale lives with
  the statement.

- **A golden-path gap** (a scenario the merge shows is unverified) → update
  the map in **`tests/golden_paths/__init__.py`**: assign it a mechanism
  (CI-here / existing-suite / deploy-runbook) and cite the owner. If
  CI-automatable, add an interface-level test (public routes only, survives
  the SLOT-B rewrite) — but FIRST grep for an existing owner (rule 11); a
  duplicate is a scar.

## Hard conventions — do not violate

1. **Board single-writer.** The board has one writer (the orchestrator
   session). If you are NOT it: put your board changes as **row deltas** in
   your report (lane id → new status/note) and let the writer apply them, or
   request the writer token. If you ARE it: edit directly, ride the change on
   a merge push.
2. **One owner per fact** (anti-scar rule 1). Before adding a test/row/section
   for a fact, grep for an existing owner and extend it instead of forking.
3. **No ghost-doc citations** (rule 9). Every path/PR/file you cite must exist
   in the tree or on the remote. The docs-reference ratchet will fail on a
   dangling link.
4. **Status legend** (board): `✔ done+CI-green · ▶ in-flight · ☐ todo ·
   ⏸ deferred · ⛔ blocked`. A row is ✔ only with merge SHA + verify evidence.
5. **Deploy-affecting = both boxes.** 150 (4.9.3/privileged) + 143
   (5.7/unprivileged). Record per box.

## Known open item to fold in (already surfaced)
- ⚠ **Root `AGENTS.md` resurrection.** The P4-docs collapse deleted
  `CONTEXT.md` + `AGENTS.md` (content already inline in `ARCHITECTURE.md`).
  Post-reconciliation, `CONTEXT.md` stayed gone but **`AGENTS.md` reappeared**
  — likely a rebase kept the remote side. Decide: re-delete it (finish the
  collapse) or keep it as a thin pointer stub. Record the decision on the
  board P4-docs row and, if kept, make it a pointer to
  `ARCHITECTURE.md#bundled-agents-v03` — not a second copy of the content.

## Output
For each suggestion: `finding → target file → exact edit (or board row delta)`.
Apply the file edits directly; hand board deltas to the writer if you don't
hold the token. Run the capped gate (ruff + format-check + import smoke +
sunset + named tests) on any code/test touch; docs-only touches just need the
dangling-link grep. Report merge SHAs + board deltas back.
