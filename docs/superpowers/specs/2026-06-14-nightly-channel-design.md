# hal0 nightly channel — design

**Date:** 2026-06-14
**Status:** Approved (brainstorming) — ready for implementation plan
**Author:** Claude (Opus 4.8) with @alexander

## Problem

Co-developers and beta testers need the **easiest possible path to update** to
the latest hal0 development build. Today the only published channel is `stable`,
cut manually by pushing a `vX.Y.Z` tag. There is no automated, frequently-refreshed
build that testers can follow without manual tag cuts or building from source.

## Goal

A nightly build, published automatically each night from green `main`, that
existing hal0 clients can follow with a one-time channel switch:

```
hal0 update --channel nightly   # once
hal0 update                     # every night thereafter
```

## Key finding — the client is already built

The entire client + manifest side of a `nightly` channel already exists; only the
*publishing* side is missing:

- `src/hal0/cli/update_commands.py` — `UpdateChannel = {stable, nightly}`;
  `hal0 update --channel nightly` persists via `PUT /api/updates/channel`.
- `src/hal0/updater/updater.py:241` `releases_url(channel)` →
  `https://releases.hal0.dev/{channel}.json`; `ReleaseManifest.channel` is
  documented `"stable | nightly"`.
- `.github/workflows/release.yml` already accepts a `channel` workflow_dispatch
  input and writes the manifest as `<channel>.json`.

**No client-side code changes are required.**

### Version ordering works as-is

`_version_tuple` (updater.py:313) splits on `.` and extracts digits per segment.
A nightly version `0.5.0-nightly.20260614` parses to `[0, 5, 0, 20260614]`, so the
date becomes the 4th sort component — nightlies order correctly by date and each
new nightly reads as "newer" than the previously installed one. `_is_pre_release`
(updater.py:459) returns True for anything containing `-`, so nightly builds keep
the pre-stable cosign-skip hatch, consistent with current alpha behavior.

## Decision

**Approach A — scheduled nightly build on the `nightly` channel.** Reuse the
existing signed-build pipeline in `release.yml` rather than duplicating it; add a
thin scheduler workflow that gates on green `main` + new commits, tags a dated
nightly, and lets `release.yml` build/sign/publish it on the `nightly` channel.

Rejected alternatives:
- **B (moving `nightly` git branch only):** a *source* update path, not an
  *install* path — testers would re-run `install.sh` from source, which is heavier,
  not easier. Does not meet the goal.
- **C (both A and B):** more surface to maintain; the branch is redundant (see
  "Out of scope").

## Design

### 1. Audience update path
- Beta testers / co-developers: one-time `hal0 update --channel nightly`, then
  plain `hal0 update`. They follow `releases.hal0.dev/nightly.json`.
- No client code changes.

### 2. New workflow — `.github/workflows/nightly.yml` (scheduler / guard / tagger)

Triggers: `schedule: cron: "0 6 * * *"` (06:00 UTC = 02:00 America/New_York during
EDT; drifts to 01:00 during EST — acceptable for a nightly; documented in a
comment) **+** `workflow_dispatch` for manual runs.

Steps:
1. Checkout `main` with full history.
2. **Greenness gate** — query the latest `ci.yml` run on `main` via
   `gh api`/`gh run list`; exit neutrally (do not fail the workflow) if the
   latest run's conclusion is not `success`.
3. **Change gate** — resolve the commit of the most recent existing `*-nightly.*`
   tag; if it equals `main` HEAD, skip (no empty nightlies).
4. **Compute version + tag** — `BASE` = pyproject version with any pre-release
   suffix stripped (`0.5.0-alpha.1` → `0.5.0`); nightly version =
   `${BASE}-nightly.$(date -u +%Y%m%d)`; tag = `v${BASE}-nightly.${DATE}`.
5. **Tag + push** — create an annotated tag at `main` HEAD and push it. The
   `v*` tag push triggers `release.yml`.
6. **Retention** — keep the last **7** nightly releases + tags; delete older ones
   with `gh release delete --cleanup-tag`. Never touch stable releases.

If two nightlies somehow land on the same UTC date (manual re-run), the tag is
reused/clobbered — the dated tag is idempotent per day.

### 3. Surgical edits to `release.yml` (channel-aware)

All edits are conditional on channel; the stable path is byte-for-byte unchanged.

- **Channel derivation (tag-push path):** if the resolved `TAG` matches
  `*-nightly.*`, set `CHANNEL=nightly`; otherwise `stable`. The existing
  `workflow_dispatch` `channel` input still wins when provided.
- **Version gate (lines 84–96):** for nightly, strip the `-nightly.DATE` suffix
  from the tag and compare only the **base** against the pyproject version, instead
  of requiring exact equality. Stable keeps exact-match.
- **Publish flags (lines 312–317):** for nightly, `gh release create
  --prerelease=true` and **omit `--latest`**, protecting the stable
  `/releases/latest` path that `install.sh` and stable `hal0 update` depend on.
  Stable keeps `--prerelease=false --latest`.
- **Unchanged and reused:** UI build, tarball stage, cosign sign-blob +
  self-verify, manifest generation (named `nightly.json`), and schema
  self-validation. Keeping the build in `release.yml` means the cosign signer
  identity stays pinned to a single workflow file — no second identity to teach
  the updater about.

### 4. Manifest delivery

`release.yml` already uploads `<channel>.json` as a release asset. The
`releases.hal0.dev/<channel>.json` URL is served by the Cloudflare Pages
middleware in the **private hal0-web** repo (`functions/_middleware.ts`).

**External dependency (verify, fix if needed):** confirm the middleware resolves
an arbitrary channel (`nightly.json`) and is not hardcoded to `stable`. The
`release.yml` header comment states it serves `/<channel>.json` generically; this
must be verified before relying on it. Until confirmed, the fallback works:
`HAL0_RELEASES_URL=https://github.com/Hal0ai/hal0/releases/download/<tag>/nightly.json hal0 update --check`.

## Out of scope (deliberately deferred)

- **Tracking `nightly` git branch.** Redundant with dated tags (immutable
  per-nightly source ref) + `main` (rolling source); the target audience updates
  via the installer channel, not a checkout; and an auto-force-pushed moving ref
  adds collision surface in a repo with many concurrent worktree sessions. If a
  co-developer later asks for a named rolling ref, it is a two-line follow-up
  (`git push -f origin <sha>:refs/heads/nightly` in `nightly.yml`).
- **Toolbox image rebuilds.** Same as stable: the nightly manifest mirrors the
  `toolbox_images` digests pinned in `manifest.json` at tag time (unchanged
  behavior).
- **Updater / CLI changes.** None needed.

## Testing / verification

- **Manual dry run:** `workflow_dispatch` on `nightly.yml`, confirm it produces a
  `v…-nightly.YYYYMMDD` tag, a prerelease (not latest) GitHub Release with
  tarball + `.sig` + `.crt` + `nightly.json`, and that the existing
  "Self-validate manifest against ReleaseManifest schema" step passes.
- **Round-trip:** `HAL0_RELEASES_URL=<nightly.json asset URL> hal0 update --check`
  reports the nightly version as available.
- **Stable regression:** push/dispatch a normal `vX.Y.Z` and confirm it still
  publishes with `--prerelease=false --latest` and `stable.json` (no behavior
  change).
- **Gates:** verify greenness gate skips when `main` CI is red, and change gate
  skips when HEAD already nightly-tagged.
- **CF delivery:** once hal0-web is confirmed, `curl https://releases.hal0.dev/nightly.json`.

## Success criteria

1. A green `main` produces a signed nightly prerelease automatically at ~02:00 ET.
2. `hal0 update --channel nightly` then `hal0 update` upgrades a tester to it.
3. The stable channel and `/releases/latest` are demonstrably unaffected.
4. No more than 7 nightly releases/tags persist.
