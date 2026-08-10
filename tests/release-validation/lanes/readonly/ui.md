# Lane: ui (read-only, Playwright)

Live smoke of the dashboard against the real box. This complements — does not replace — the 108
mocked specs in `ui/tests/e2e/`. Those prove the components behave against fixtures; this proves
the shipped build renders **real data from a real install**, which is where rc.4's UI findings
all lived.

Load the Playwright MCP tools via ToolSearch: `browser_navigate`, `browser_snapshot`,
`browser_console_messages`, `browser_take_screenshot`, `browser_click`, `browser_tabs`,
`browser_close`.

## Rules

**Click navigation links only.** Do not toggle switches, submit forms, or press action buttons
(no load/unload/save/delete). If a page needs an interaction to reveal its state, record it as
`skipped` and note that the stateful UI path is untested.

## Checks

1. Navigate to `$API` (from `CONTEXT.md`), wait for render, take an accessibility snapshot.
2. Visit every top-level nav section. On each: does it render **real** data (do the slots shown
   match `hal0 slot list`?), error toasts, blank panes, spinners still going after 15 s.
3. `browser_console_messages` on each page — report every error, and warnings that indicate a
   real problem (failed fetches, unhandled rejections, hydration mismatches).
4. **Version and interpolation.** The version string must be shown and correct (rc.4 Settings
   showed "hal0 version —"). Look for empty template interpolations — rc.4's benchmarks header
   rendered "· GB · hal0 v". Check pluralisation ("1 banks").
5. **Telemetry honesty.** On a box with no GPU, the dashboard must not present *host* GPU
   statistics as local capacity (rc.4 showed igpu 100% / 83 °C / 116 GB GTT on a GPU-less
   16 GB container). This is the highest-value check in the lane: it is the class of defect
   where the UI is confidently wrong rather than visibly broken.
6. **Badge agreement.** Every status badge must agree with the system: bench worker badge vs
   `systemctl is-active hal0-bench-worker`, slot badges vs slot state, memory/agent health.
7. **Accessibility floor.** Nav rail icons and icon-only buttons have accessible names; the
   snapshot is navigable.
8. Screenshot each section for the report.

Close the browser when done.

## Carry-forward

Anything found here that can be expressed against fixtures should be promoted into
`ui/tests/e2e/` as a spec in the fix PR, and then removed from this brief. This lane should
only hold checks that genuinely require a live install.
