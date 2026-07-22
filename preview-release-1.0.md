# Preview Release 1.0

## Integration status

- Rebased `work/v1-rc-critical-path` onto current `main`.
- Dropped obsolete slot/model drawer repair commits.
- Confirmed `ui/src/dash/model-drawer.jsx` and `ui/src/dash/slot-modals.jsx` have no diff from `main`.
- Preserved lifecycle, package, and profile work.

## UI coverage and fix

- Updated stale UI tests to match the current controls.
- Fixed a shared-row binding bug that rendered the Kokoro info popover as literal `{sub}`.
- Targeted Chromium UI suite: **42 passed, 3 skipped**.

## Delivery

- Force-pushed the rebased branch to [PR #1341](https://github.com/Hal0ai/hal0/pull/1341).
- Generated Shepherd and graph outputs remain intentionally unstaged.
