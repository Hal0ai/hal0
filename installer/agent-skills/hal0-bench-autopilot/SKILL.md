---
name: hal0-bench-autopilot
category: homelab-ops
description: The weekly judgment pass over benchlab's benchmark dataset.
---

# hal0-bench-autopilot

The weekly judgment pass, run by Hermes after the timer session (or invoked
manually). Procedure encoded in SKILL.md:

1. `hal0 bench status` — confirm last session outcome; for failed cells read
   the artifact log, classify (OOM → mark model/depth combination as
   excluded in the suite override; hang → retry once; else file board task).
2. Regression review (§11 output): for each flagged cell, check the trend
   against provenance markers. Drift explained by an image/flag change →
   annotate; unexplained >10% drop → re-run the cell once, and if it
   reproduces, open a board task with the two run_ids.
3. `hal0 bench publish --check` → if changed, regenerate, update the "what
   the data shows" prose ONLY if a headline fact changed (pre-registered
   claims live in the mdx; the skill edits them with the same
   accept/reject discipline as the runbook §2), open the site-repo PR.
4. Never publish contended/non-exclusive numbers; never lower `reps` to make
   budget — drop whole cells instead (planner already orders by value).

## Commands

Exclusivity is per-sweep: `hal0 bench run` on an `exclusive = true` suite stops
and restarts the active GPU slots around each Tier-A sweep itself — there is no
session-level quiesce verb.

```bash
# Run the autopilot pass
hal0 bench plan --suite roster --json > /tmp/bench-worklist.json
hal0 bench run --suite roster --dry-run  # dry-run first
hal0 bench run --suite roster            # actual run (prints regression flags at the end)
hal0 bench status
hal0 bench publish
```

## Output

A short report:
- Session outcome (ok/failed/skipped counts)
- Any regressions detected (with delta % and run_ids)
- Any cells that need re-running
- Whether the roster was updated
- Any board tasks created
