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
* every slot: name, assigned model, state, port, profile — AND each slot TOML's `device`
  value, read from the config TOMLs under `/etc/hal0/slots/` directly (`hal0 slot list` has
  no device column, and `/var/lib/hal0/slots/` is per-slot STATE, not config; the
  post-upgrade migration check diffs against exactly this)
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
   * **2b. Channel-URL path (#1883).** Before the main upgrade run, separately verify the
     documented channel-URL path end-to-end — do not rely on this box's pinned GitHub-asset
     `HAL0_RELEASES_URL` for it (the pin bypasses exactly the path #1883 fixed). Two halves,
     and the mechanics matter:
     - Proxy correctness: `curl -sS -o /dev/null -w '%{http_code}\n'` against
       `https://releases.hal0.dev/<channel>.json` AND the sibling
       `https://releases.hal0.dev/<channel>.json.bundle` — both 200, and the bundle
       byte-matches the GitHub release asset. (Note the daemon's effective URL is not
       byte-identical to your curl target for non-stable channels on an http override —
       `releases_url()` appends `?channel=<ch>` — so compare content, not URLs.)
     - Verified-fetch path: `HAL0_RELEASES_URL` is read by the DAEMON from
       `/etc/hal0/api.env` — exporting it in your shell does nothing, and `hal0 update
       --check` uses the unverified manifest fetch, so neither exercises cosign. Instead:
       copy `/etc/hal0/api.env` aside, point `HAL0_RELEASES_URL` at the channel URL in it,
       restart `hal0-api`, then `PUT /api/updates/channel` (re-setting the current channel is
       enough — that route is the one path into the cosign-verified manifest fetch outside a
       full prepare). Confirm from the journal that verification ran and succeeded against
       the proxied bundle. Restore api.env byte-for-byte and restart again; say so in the
       report, and record both the pinned and channel URL values so the distinction doesn't
       get lost.
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
   (`known-issues: update-restart-error-breadcrumb-on-success`). While in the journal, verify
   the audit trail #1935 restored: parent-side `updater.*` lines must carry a populated
   `job_id` (not `None`) and a successful prepare must leave prepare/verify breadcrumbs —
   their absence is regression `update-audit-trail-gaps` re-opening, not designed behavior.
   Also confirm the verification evidence that persists on disk:
   `/var/lib/hal0/cache/<ver>/manifest.json` must exist, post-date the sha256/cosign checks, and
   record the digest and signer identity.
5. **Migrations.** Which migrations ran? Do they log what they changed? Re-run the upgrade (or
   the activation step) and confirm migrations are idempotent.
   * **5b. The rc.7 image-pin retag (#1959 B1).** Before upgrading, record every slot's image
     pin under BOTH the legacy `image` key AND the newer `image_pin` key
     (`grep -n 'image_pin\|^image\b' /var/lib/hal0/slots/*.toml`, plus any custom entry in
     `profiles.toml`) — `hal0 slot migrate-hw --apply` folds an old `image` pin into `image_pin`
     and drops the bare key, so a box that ran that migration carries `image_pin` only. After
     the upgrade, every SERVED pin (`image_pin` if present, else `image`) that exactly equalled
     a known former default must read `ghcr.io/hal0ai/hal0-combined:0822`, with a matching
     `updater.slot_image_retagged` journal line. On any slot carrying BOTH keys, confirm
     `image_pin` — not `image` — is the one actually judged and rewritten (`_resolve_image_ref`
     reads `image_pin` first and no longer reads the bare key at all; the retag sweep binds in
     the same order after the B1 review's precedence fix). A pin that was a deliberate operator
     override (not an exact former default, under either key) must be byte-identical before and
     after. See `regressions.yaml: updater-image-pin-retag-blind-spot`.
   * **5c. Perf-row reference envelope, now inverted.** If bench numbers are captured for this
     upgrade (`hal0 bench` or the #1948 §3-C matrix commands), record decode/prefill for BOTH
     `gpu-rocm` and `gpu-vulkan` on this box (kfd present, so both lanes are reachable — Vulkan
     via `hal0 slot edit <slot> --hardware vulkan`). The reference envelope as of `:0822` is
     Vulkan AHEAD of ROCm on both metrics (+13.96% prefill, +20.45% decode per #1948's matrix on
     this box) — this REPLACES the old ade07ba-era expectation ("Vulkan ~-10% decode vs ROCm")
     carried by earlier kit versions. A >20% variance from the new envelope, in either
     direction, needs explanation; do not flag "Vulkan is faster than ROCm" as a regression.
6. **Rollback.** `hal0 update --rollback`. Does it return the box to the previous version with
   its state intact? Then roll forward again. If rollback is not exercised here it is not
   exercised anywhere. (`updater.*` journal lines from the ROLLBACK path legitimately carry
   `job_id=None` — the rollback route constructs its Updater without a job — so do not
   re-open `update-audit-trail-gaps` from rollback-window lines; check 4's job_id assertion
   applies to the upgrade itself.)

## Leave behind

The box on the new version, healthy, with the "before" snapshot recorded in your report for the
`post-upgrade` stage to diff against.
