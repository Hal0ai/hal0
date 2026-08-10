# Shared rules — every lane agent reads this first

You are validating a hal0 release candidate on a real box. Read, in this order:

1. `CONTEXT.md` in your run directory — the live facts for this run: version under test, box,
   IP, API base, SSH invocation, which models are staged, current slot state, and (for stateful
   lanes) the state the previous stage left behind. **`CONTEXT.md` beats anything written here
   or in your brief.** If they disagree, trust `CONTEXT.md` and say so in your report.
2. `known-issues.yaml` — do not re-report anything listed there. Record it as `known` and move
   on. Each by-design entry has a `still_report_if:` clause; that clause is the only thing that
   turns it back into a finding.
3. Your own lane brief.

## Report discipline

* Every check gets a result: `pass`, `fail`, `warn`, `known`, or `skipped`. A check you ran out
  of time for is `skipped`. **Never report a check you did not actually run as `pass`.**
* Every finding needs the **exact command** and the **shortest decisive output line**. Not a
  full dump, not a paraphrase. If you cannot produce a repro another agent can run blind, you
  do not have a finding yet — you have a suspicion, and it should be reported as one.
* Severity:
  * `critical` — data loss, or a core promised feature is completely broken
  * `major` — a feature is unusable or silently returns the wrong result; workaround may exist
  * `minor` — rough edge, friction, recoverable
  * `cosmetic` — presentation only
* **Silent wrongness outranks loud brokenness.** A feature that reports success while doing
  nothing is worse than one that errors. Weight severity accordingly.
* Report what a *user* would experience, not what an expert who knows the workaround would.
  "Works once you load the utility slot" is a finding, not a pass, on a fresh install.

## Discipline about the box

* Respect your lane's mutation budget. Read-only lanes: GET requests and read-only CLI verbs
  only — no loads, unloads, restarts, writes, or POST/PUT/DELETE except any explicitly listed
  in your brief. If a command might change state, skip it and record it as `skipped`.
* Stateful lanes: mutate freely within your brief, but never uninstall hal0, never permanently
  stop or mask hal0-api or the hermes units, never delete files from the shared model store,
  never reboot, and never touch anything off-box.
* The idle reaper (`idle_timeout_s`, 300 s by default) evicts slots between stages. Finding a
  slot offline that a previous stage left loaded is **expected** — reload it, note it, do not
  file it.
* Model loads on a CPU box take 30–90 s. Poll health; be patient; use long curl timeouts. A
  CLI `ReadTimeout` does not mean the server abandoned the operation — always verify the
  server-side outcome before concluding.
* Stay inside your time budget (in `CONTEXT.md`). Stop and report rather than running long;
  a partial report delivered is worth more than a complete one that never lands.

## Leaving the box

Stateful lanes end by stating `box_state_on_exit` precisely — which slots are loaded with which
models, what config you changed, what you created and did not clean up. The next stage is
handed that string verbatim and will trust it.

## What a good lane report looks like

Findings are the deliverable, but so is the negative space: a lane that reports "28 checks, 26
pass, 2 fail" is far more useful than one that reports only the two failures, because next
release we can see what stopped being checked.
