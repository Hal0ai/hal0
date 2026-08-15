# Lane: upgrade (update box, order 1)

The in-place upgrade path — the one every existing user takes, and the one rc.4 never validated.
This lane runs on the update box concurrently with the fresh box's stateful lanes; the two boxes
never interact.

The box arrives on the **previous** release with accumulated state. That accumulated state is
the asset: it is the only place migrations, seed reconciliation, and config carry-forward get
exercised against something other than a clean slate.

## Before you touch anything

Capture a complete "before" snapshot and write it into your report — you cannot compare after
the fact otherwise:

* `hal0 --version`, the release channel, and `HAL0_RELEASES_URL`
* every slot: name, assigned model, state, port, profile
* `hal0 config` dump, capability bindings, model registry listing
* memory bank inventory and counts
* which units are enabled and running
* file inventory and ownership of `/etc/hal0` and `/var/lib/hal0`

## Checks

1. **Discovery.** `hal0 update --check`. Does it see the new version, with a real version string
   and a real digest? rc.4's check rendered a placeholder manifest as "1.0.0rc4 → 0.0.0 (stable)
   up to date" with a zeroed digest — a user on that build would never be told an update exists.
2. **Ranking.** Does it correctly rank the RC against what is installed, including the rc-vs-GA
   ordering? A wrong ranking silently withholds the update. `hal0 update --target <ver>`
   bypasses the gate and is the documented recovery — confirm it still works, and note that
   needing it *is* the finding.
3. **The upgrade itself.** Run it. Capture the full output. Watch for: staged-tree permission
   gates (a box with `UMASK 002` produces a 0775 staged tree that the activate security gate
   refuses — the gate's own hint is the remediation), signature/digest verification, service
   restarts, and how long the API is unavailable.
4. **Failure honesty — in both directions.** If any phase fails or warns, does the command say
   so and exit non-zero? A successful-looking upgrade that half-applied is the worst outcome in
   this lane. Then the inverse: audit the applied job JSON for error-shaped fields on the
   SUCCESS path (`restart_error`, `error`, `*_skipped`) and cross-check each against the journal
   outcome before reading any as a failure — `restarted: null` + `restart_error: "systemctl
   exited -15"` on a successful upgrade is the designed ambiguous-self-restart representation
   (`known-issues: update-restart-error-breadcrumb-on-success`). While in the journal, note that
   parent-side `updater.*` lines log `job_id=None` and a successful prepare leaves NO
   verification breadcrumbs (regression `update-audit-trail-gaps`) — correlate by request_id and
   timestamps instead, and confirm the verification evidence that DOES persist:
   `/var/lib/hal0/cache/<ver>/manifest.json` must exist, post-date the sha256/cosign checks, and
   record the digest and signer identity.
5. **Migrations.** Which migrations ran? Do they log what they changed? Re-run the upgrade (or
   the activation step) and confirm migrations are idempotent.
6. **Rollback.** `hal0 update --rollback`. Does it return the box to the previous version with
   its state intact? Then roll forward again. If rollback is not exercised here it is not
   exercised anywhere.

## Leave behind

The box on the new version, healthy, with the "before" snapshot recorded in your report for the
`post-upgrade` stage to diff against.
