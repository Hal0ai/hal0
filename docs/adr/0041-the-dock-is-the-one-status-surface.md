# ADR-0041: The dock is the console's one status surface

## Status

**ACCEPTED (2026-10-26).** An operator decision.

It builds on ADR-0038 (image mode lasts only while an image renders), which
made the GPU's state something an operator has to read at a glance, and on
ADR-0040 (the gate's own credential), whose "blocked" state is the one error
that keeps a banner.

## Context

The console said what the GPU was doing in four places, each carrying part of
the controls, and kept its event feeds in two unrelated surfaces:

- `ui/src/dash/image/ImageStatusLine.jsx`, the status line at the head of the
  Image Gen pane: the 11 kinds of `statusLine()` with their buttons.
- `GpuImageModeBanner` (`ui/src/dash/primitives.jsx`), a banner on every other
  page while the GPU was in image mode, with `gpu-image-mode-pure.ts` behind it.
- `DeviceCard` (`ui/src/dash/image/EngineStatCards.jsx`) on the Engine tab:
  GPU temperature, clock and utilisation, which the telemetry bar at the top
  of the Slots page already shows.
- The Engine tab's `Stop image mode` button, one more place to leave image
  mode.

The feeds were the footer (`Footer` in `ui/src/dash/chrome.jsx`): slot and
service pips, an update chip, the journal and the downloads, and
`ActivityLog` (`ui/src/dash/activity-log.jsx`), the durable audit records, in
a 320 px column at the right of the Slots page. Someone watching a model load
read the journal in one place and the action that started it in another.

That 320 px column also held the Image Gen pane to a narrow main area, and it
only existed on the Slots page.

## Decision

1. **The dock is the only global status and event surface.** The footer
   becomes the dock (`ui/src/dash/dock/`): one always-visible row with the
   GPU segment, the slot and service pip groups, the update chip and the
   toggle, a two-row ticker of the newest events under it, and an expanded
   pane with three tabs, **events**, **gpu** and **downloads**. Expanded, it
   sits in the page's flow (`min(300px, 45vh)`) and pushes the page up. It
   never covers it. It is part of the phone layout.
2. **A banner at the top of a page is reserved for an error that blocks a
   feature.** Today that is one: ComfyUI's GPU gate has no credential for
   hal0's sign-in (`gate.state === 'no_credential'`), so image generation is
   refused until someone fixes it (`GpuGateBlockedBanner`). It has no dismiss
   and no button. Every other GPU state, including a refusal or a stale gate,
   lives in the dock.
3. **GPU-mode actions live only in the dock.** Hold, keep, unpin, stop and
   cancel are the segment's buttons (at most two) and the GPU tab's (all of
   them). The words, the precedence and the choice of buttons stay
   `statusLine()`'s (`status-line-pure.ts`): the dock is a new renderer for
   the same view model. Engine actions, **Restart engine** and **Logs**, are
   also on the Engine tab, beside the engine's facts. The GPU tab offers
   **Stop image mode** whenever image mode is on, which the line itself
   offers only while pinned, because grace, hand-off and rendering have no
   other way out now that the Engine tab's button is gone.
4. **The events feed is the journal plus the activity trail's `action`
   records.** Every journal line is a `J` row. An activity record joins as an
   `A` row only when `kind === 'action'`: the `event` records are a durable
   copy of the same EventBus events the journal already shows, and are kept
   for export and the API. The dock asks the activity stream for `kind=action`
   and `limit=200`, so the ring never holds the rows it would not show. The
   filters (stream, severity, source, slot, search) apply on the browser, so
   toggling one never reopens a stream. **Export** downloads the activity
   trail with the current filters; journal lines are not exported and are on
   the Logs page.
5. **The Inference tab's side column is the slot rail, and the Image Gen tab
   has none.** The rail lists every slot grouped by device, counts the
   configured ones (so its `N / M ready` equals the dock's slots fraction),
   and flashes a row when the dock's feed gets an event for that slot. The
   Image Gen pane takes the main area's whole width, and its Generate tab
   becomes two columns at a viewport of 1360 px and up.

## Consequences

- Removed: the Image Gen pane's status line, `GpuImageModeBanner` with
  `imageModeBanner()`, the Engine tab's `DeviceCard`, `deviceCells()` and
  `Stop image mode` button, and the Slots page's `ActivityLog`. Their tests go
  with them. The pure tests for `statusLine()` stay.
- Both streams, the journal's and the activity trail's, are open for as long
  as the dock is mounted, which is every page: the ticker and the `+N` unseen
  badge on the toggle need live rows while the dock is collapsed. The
  journal's stream used to open only when the pane did. Each browser tab
  holds two server-sent-event connections, and the activity backfill is
  capped by `limit`.
- The dock's `/status` poll runs at its active cadence only on the Image Gen
  route or while a Generate run of this browser is in flight; elsewhere it
  idles. The arbiter poll that fed the old banner is the same one.
- One action instance serves the segment and the GPU tab, so there is one
  confirm dialog and one error. The segment holds the dock's one `aria-live`
  region; the GPU tab adds none, or a screen reader would hear the state
  twice.
- A page deep-linked to `#slots` keeps working. The agent card's "open
  activity" link now opens the dock on the events tab (`hal0:open-events`)
  instead of navigating to the Slots page.
- A reader who used to find activity under the Slots page finds it under the
  dock on every page, merged with the journal. The docs say so
  (`docs/guides/dashboard-and-settings.mdx`, `docs/guides/logs-and-activity.mdx`).
- A new global status, or a new kind of event row, goes into the dock. A new
  top banner needs the same bar as `GpuGateBlockedBanner`: a feature is
  blocked until someone acts.

## Alternatives considered

- **Keep the pane's status line and the banner, and add the dock.** Rejected:
  two places that say the GPU's state can disagree, which is what the
  operator was reading around.
- **Two tabs, one for the journal and one for the activity trail.**
  Rejected by the operator: the interesting case is a line of one beside a
  line of the other, in time order. The stream filter gives the separate
  view when it is wanted.
- **Every activity record in the merged feed, with the duplicates matched
  away.** Rejected: the `event` records duplicate journal lines, and the
  dock does no fuzzy matching between the two. It takes the one kind that
  records a user action, with its actor and before/after.
- **An overlay dock.** Rejected: an expanded pane over the page hides the
  controls it describes. The dock sits in the page's flow and pushes it up.
- **Moving Restart engine and Logs to the dock only.** Rejected: they are
  engine actions and belong beside the engine's facts too. Only the
  GPU-mode actions are dock-only.

## References

- PR (this change); ADR-0038 (image mode lasts only while an image renders);
  ADR-0040 (the gate's own credential).
- `ui/src/dash/dock/Dock.jsx` (`dock`, `dock-toggle`, `dock-tab-events`,
  `dock-tab-gpu`, `dock-tab-downloads`, the streams and the `hal0:open-events`
  listener), `GpuSegment.jsx` and `GpuPane.jsx` (`dock-gpu-segment`,
  `dock-gpu`, `gpu-facts`), `EventsPane.jsx` (the filters and `dock-export`),
  `dock-pure.ts` (`mergeFeeds`, `segmentActions`, `TICKER_ROWS`),
  `GpuGateBlockedBanner.jsx`.
- `ui/src/dash/slot-rail.jsx`, `ui/src/dash/slot-rail-pure.ts` (the rail's
  groups, counts and footer).
- `docs/guides/dashboard-and-settings.mdx` ("The dock", "Slot rail"),
  `docs/guides/generate-images.mdx` ("What a render does to chat"),
  `docs/guides/logs-and-activity.mdx`.
