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
5. **Telemetry honesty, per page.** On a box with no GPU, the dashboard must not present *host*
   GPU or NPU statistics as local capacity. Assert it on **every page that draws an accelerator
   tile**, against `/api/hardware` `compute_capable` — not once for "the dashboard". rc.4's fix
   covered Overview and Settings and missed the Slots page telemetry header, and a
   single-surface check scored that as fixed (#1841 item 1). This is the highest-value check in
   the lane: the UI is confidently wrong rather than visibly broken.
6. **Widget vs section agreement.** A summary widget must not contradict the full section or the
   API behind it. Concretely: the Overview "Services" widget must be non-empty whenever
   `/api/services` returns at least one entry (rc.5's was unconditionally dead, #1836), and the
   Slots page counts must be internally consistent — rendered rows == card header count ==
   footer "slots N" == tab badge.
7. **Badge agreement.** Every status badge must agree with the system: bench worker badge vs
   `systemctl is-active hal0-bench-worker`, slot badges vs slot state, and the agent card health
   badge vs BOTH `/api/agents` `unit_active` and the footer service chips — three sources that
   have disagreed before. Note that an amber "READY" dot on the Hermes card is by design
   (`known-issues.yaml: ui-agents-card-amber-ready-dot`).
8. **Accessibility floor, asserted not eyeballed.** Every interactive element in the persistent
   top bar and nav rail must have a non-empty accessible name, and the name must not be an
   internal CSS class. Check at a **rail-width viewport (721–1080 px)** as well as full width:
   the rail hides `.lbl` spans, which is where rc.5's unnamed service links were only visible.
9. Screenshot each section for the report.

If another lane is mutating the box during your window, say so. Every "does the UI show real
data" comparison is only valid if the baseline is re-read at the same instant.

Close the browser when done.

## Carry-forward

Anything found here that can be expressed against fixtures should be promoted into
`ui/tests/e2e/` as a spec in the fix PR, and then removed from this brief. This lane should
only hold checks that genuinely require a live install.
