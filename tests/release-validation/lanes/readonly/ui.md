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
5. **Telemetry honesty, per page — and per PREDICATE.** Assert on **every page that draws an
   accelerator tile** that what it claims matches `/api/hardware`. The correct capability
   predicate is `compute_capable || vulkan_capable`: `compute_capable` only means host rocm-smi
   answered, and a vulkan-serving box (both current fresh boxes) genuinely uses the iGPU. Two
   failure directions, both reportable: a tile rendering accelerator numbers on a box where BOTH
   flags are false (the original #1841 item 1 — no such box remains in the fleet, so this half
   is code-read only), and the INVERSE — a page claiming "No GPU compute access" while slots
   generate tokens on that GPU (`known-issues: slots-gpu-telemetry-on-vulkan-box-is-correct`).
   rc.4's fix covered two pages and missed a third; a single-surface check scored it fixed.
   This is the highest-value check in the lane: the UI is confidently wrong rather than visibly
   broken. Also compare `/api/stats/hardware` gpu_util/power directly against the box's own
   request and token counters — that flags shared/foreign telemetry without needing a second
   tenant to be busy.
6. **Widget vs section agreement.** A summary widget must not contradict the full section or the
   API behind it. Concretely: the Overview "Services" widget must be non-empty whenever
   `/api/services` returns at least one entry (rc.5's was unconditionally dead, #1836), and the
   Slots page counts must be internally consistent — rendered rows == card header count ==
   footer "slots N" == tab badge.
7. **Badge agreement — including set membership.** Every status badge must agree with the
   system: bench worker badge vs `systemctl is-active hal0-bench-worker` (it lives on the
   Benchmarks page's Run Queue tab, and "stopped" beside a running unit is the armed state —
   `known-issues: ui-bench-worker-badge-on-benchmarks-page`), slot badges vs slot state, and the
   agent card health badge vs BOTH `/api/agents` `unit_active` and the footer service chips.
   For the footer: diff the service-chip ID SET against `/api/services/health` keys, not just
   each chip's colour — a service omitted from the group can never move the count, so an outage
   reads "3 / 3 ready" (regression `ui-footer-services-omits-comfyui`). Before diffing the
   footer chips, curl `/api/services` vs `/api/services/health` directly (this is now check 13
   in `lanes/readonly/api.md` — cross-reference it rather than re-deriving) — if a service id is
   missing from `/api/services/health` itself, the gap is the API's, not the footer's, and a
   UI-only fix would not close it (regression `services-health-omits-hindsight`: hindsight is
   never in the health payload under any configuration). An amber "READY" dot on
   the Hermes card is by design (`known-issues.yaml: ui-agents-card-amber-ready-dot`).
   And where one screen shows the same quantity twice (the Slots page memory ruler vs the
   Inference Engine card footer's "N GB free"), assert they reconcile — rc.6's footer read the
   entire pool as free beside a ruler showing 6.3 GB (regression
   `ui-inference-card-free-memory-wrong`).
8. **Accessibility floor, asserted not eyeballed.** Every interactive element in the persistent
   top bar and nav rail must have a non-empty accessible name, and the name must not be an
   internal CSS class. Do it as one viewport-parameterised `page.evaluate` that enumerates
   every rail/top-bar interactive element and computes `aria-label || innerText || title`
   (NOT `textContent` — it ignores display:none and reads a hidden label as a name, which is
   how an rc.6 probe scored the unnamed anchors as fixed). Run it at full width AND at a
   **rail-width viewport (721–1080 px)**: the rail hides `.lbl` spans there, and the whole
   sidebar is display:none at <=720 px, so 721–1080 is the only band where the defect exists.
9. Screenshot each section for the report.

If another lane is mutating the box during your window, say so. Every "does the UI show real
data" comparison is only valid if the baseline is re-read at the same instant.

Close the browser when done.

10. **GPU tiles on a vulkan-only box** — regression `ui-no-gpu-on-vulkan-only-box` (#2674). If
   `/api/hardware` shows `compute_capable=false`, `vulkan_capable=true`, no `/dev/kfd` while a
   vulkan slot serves, the Overview igpu row and health-strip cell and the Settings Live-hardware
   and Power & thermal GPU rows must show values. Read the rendered text with Playwright, not the
   bundle.
11. **Classification agreement across surfaces.** (a) Settings Overview 'Services' shows ComfyUI as
   warning 'down' while the footer counts 4/4 ready: both must classify 'stopped - starts on
   demand' the same way. (b) Doctor panel cards named like HAL0-API-UNREACHABLE /
   HAL0-RUNNERS-NONE-HEALTHY render with healthy evidence and '14/14 slot(s) healthy' while most
   slots are offline: healthy counts must exclude offline slots or be labelled. (c) Footer 'N / M
   ready' slots denominator changes between pages (3/3, 1/2, 2/7): assert it is stable or labelled.
   Record each as `warn` unless it contradicts a status the user acts on.
12. **Known, record as `known`:** `svc-openwebui` rail link has no accessible name at 721-1080 px
   (`ui-rail-openwebui-link-unnamed`, #1841 wontfix). Probe that band with the accessibility tree
   (`ariaSnapshot`), not textContent; report only the `still_report_if` conditions.

13. **Footer SLOTS chip, all pages in one pass.** Record `[data-testid=foot-health-runtimes]` on every nav page, with a fetch of `/api/slots` inside each page. The value is page-independent and moves only when slots change (`footer-slots-chip-counts-configured-slots`); a disagreement with `/api/slots` unchanged is a finding.
14. **Services panel vs footer.** For any service with `state: "stopped"` in `/api/services/health` (ComfyUI on a fresh box) compare the Settings > Overview Services chip with the footer pip; the panel showing a warn "down" is #2705.
15. **Tooling fallback.** If Playwright MCP tools are absent use the container: `docker run --rm --network host mcr.microsoft.com/playwright:v1.64.0-noble` with a script. Nav rail rows are `div.sb-row` hash routes, not links: take an `ariaSnapshot` and check each row exposes a role and is keyboard-focusable.

## Carry-forward

Anything found here that can be expressed against fixtures should be promoted into
`ui/tests/e2e/` as a spec in the fix PR, and then removed from this brief. This lane should
only hold checks that genuinely require a live install.
