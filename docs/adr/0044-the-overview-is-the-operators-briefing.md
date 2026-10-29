# ADR-0044: The Overview is the operator's briefing

## Status

**ACCEPTED (2026-10-29).** An operator decision.

It builds on ADR-0042 (the dock is the console's one status surface), which
made the dock the place for global status and raw events, and on ADR-0039
(memory figures have one owner and two budgets), whose rule that a memory
figure has one source the Overview keeps. It also relies on ADR-0043 (agent
bridge and bridge keys) for the delegated runs the page lists.

## Context

The `#dashboard` route, labelled "Overview" in the sidebar, is the console's
landing page. It was built as a fixed-band, swap-in-place board and was not
reshaped while the rest of the console grew around it: the dock (ADR-0042),
the Slots telemetry header, the agent cards, the agent bridge (ADR-0043), the
Image Gen pane, Hindsight memory and benchmarks. It reflected none of that,
and it had three problems:

- **It duplicated other surfaces.** A five-cell health strip, a throughput
  card, an activity card and a slot table repeated numbers the dock and the
  Slots page already show, and a third place that can disagree with the first
  two is what ADR-0042 set out to remove.
- **It was empty on an idle box.** Its two usage widgets read the dispatcher's
  60 s rollups, which hold nothing once requests stop. A home box that is
  mostly idle showed blanks on the page the operator sees first.
- **It carried a customization system for little value.** The operator could
  swap a widget for another in place and the choice was saved on the server.
  That cost a layout store, a REST route, a hook pair, widget-definition
  tables, swap CSS and a permissions row, to rearrange a page that has one
  right order.

## Decision

1. **The Overview is a briefing: it summarises and links.** It answers four
   questions in order, top to bottom: what is the box doing now, what needs
   the operator, who is working, how did it perform. Every section is a
   summary with a link to the page that owns the detail. It never duplicates
   the dock (ADR-0042) and never becomes a second Slots, Agents or Logs page.
   Raw journal and activity rows stay in the dock. A figure the dock or the
   Slots header already shows is repeated on the Overview only when the
   Overview adds something to it (the memory bar with per-slot tok/s stays;
   the health strip goes).
2. **Customization is removed.** The swap-in-place picker, its hooks, the
   widget-definition tables and the swap CSS go, and so does the layout
   endpoint, `GET/PUT /api/user/dashboard-layout`
   (`src/hal0/api/routes/dashboard_layout.py`), with its store and the
   `dashboard-layout.json` row in `src/hal0/install/perms.py`. The GET route
   is removed rather than kept as a stub: an older dashboard against a newer
   backend already fails soft on a 404 (`ui/src/api/hooks/useDashLayout.ts`).
   A saved layout file on an older box is simply unmanaged; no role is kept
   for it. This is an operator decision of 2026-10-29. The change that edits
   `perms.py` is reviewed and merged by the operator, not auto-merged.
3. **Usage figures come from the request-metric table.** The Usage section
   reads `GET /api/stats` (`stats_endpoint` in `src/hal0/api/routes/hardware.py`,
   backed by `stats_summary` in `src/hal0/metrics/read.py`) with a window of
   `1h`, `24h` or `7d`. The default is 24h, and the choice is remembered per
   browser. That table keeps every request, so the section has numbers on an
   idle box. The 60 s dispatcher rollups are not read by the Overview; they
   feed the dock and stay there. The response gains an additive `buckets`
   list (`ts`, `requests`, `tokens_completed`) for the tokens-per-bucket bar
   strip; the change is backward compatible.
4. **The honesty contract carries over unchanged.** A metric the probe cannot
   source renders as an em dash with a caption, never `0`. A measured 0 tok/s
   renders with its serving count, so "nothing is generating" and "nothing is
   measured" stay different words on the page. An empty usage window renders
   em dashes, not zeros. A box without GPU or NPU access shows its own truth
   (`no GPU access`), never the host's numbers.
5. **Memory figures keep one owner (ADR-0039).** The Overview's memory bar
   and the Now band's free-memory fact come from the shared memory-map model
   over `GET /api/stats/hardware`'s `memory` block; the section computes no
   budget of its own. The Memory section (bank stats) shows only when the
   memory subsystem is enabled, the same gate as the Memory nav item.
6. **`kv_used` is a fraction, not a token count.** In `GET /api/system-stats`
   (`system_stats` in `src/hal0/metrics/read.py`), a slot's `kv_used` is the
   share of its KV cache in use in parts per million: the sampler stores
   `kv_cache_usage * 1_000_000` (`src/hal0/metrics/sampler.py`). The Serving
   tile derives tokens from it: percent is `kv_used / 10000`, and the context
   bar shows that share of the slot's effective context
   (`ui/src/dash/overview/serving-pure.ts`). Treating `kv_used` as tokens
   draws a bar that is wrong by orders of magnitude.

### The sections

Each section reads, and links to, the owner of the detail:

- **Now band.** Mode (chat, image or switching), slots loaded, requests
  serving and tok/s, free memory, GPU load, contextual actions, and the
  unified memory bar. Reads the slot list, the memory-map model, the hardware
  and power stats, the arbiter and the ComfyUI switchover.
- **Needs you.** Approvals, error slots, failed downloads, an available
  update, slot drift, dev messages, doctor diagnoses of warn or worse and
  failing health checks, each with its inline actions; or one "all clear"
  line. Reads the notifications feed, the doctor feed and the system health
  check. The slot-drift banner is folded in here.
- **Serving now.** Slot tiles grouped in the Slots rail's lanes, with state,
  tok/s, in-flight count, a context bar and a per-slot throughput sparkline;
  stopped slots fold into load chips. Reads the slot list, `/api/system-stats`
  and the throughput history.
- **Agents.** The Hermes and Pi rows and the latest delegated bridge runs,
  with the board's in-progress count. Reads the agent overview, the bridge
  runs list (ADR-0043) and the board stats.
- **Usage.** The hero figures, a tokens-per-bucket bar strip and the per-model
  table for the chosen window. Reads `GET /api/stats` (decision 3).
- **Hardware.** GPU, CPU, NPU, RAM and disk with short sparklines kept in the
  browser. Reads the hardware stats, the hardware probe and the system health
  check.
- **Recent digest.** The newest five activity records, with consecutive slot
  state changes folded into one row (`ui/src/dash/overview/recent-digest-pure.ts`).
  Reads the activity trail; the link opens the dock's events tab.
- **Image engine.** ComfyUI mode, engine state, queue, render progress and the
  model inventory counts, with links to Generate and the editor. Reads the
  ComfyUI status and the arbiter. Omitted when the box has no `img` slot and
  no ComfyUI status.
- **Services.** One row per hal0-managed service with its state and, for Open
  WebUI, its wired capabilities. Reads the service list.
- **Memory.** The memory engine's state, the bank count, and nodes, links and
  observations for the chosen bank. Reads the memory engine, banks and bank
  stats. Gated as in decision 5.
- **Version strip.** The hal0 and FLM versions, the update channel, whether an
  update is available, and the host. Reads the update state and the hardware
  probe.

## Consequences

- Removed: the fixed-band page and its swap-in widgets, the throughput and
  utilization cards, the health strip, the activity card, the layout hooks
  and endpoint, and the end-to-end specs that asserted on the old page's DOM.
  One Overview spec replaces them.
- A box on an older hal0 that kept a saved layout file keeps the file; nothing
  reads it. The layout `GET` returns 404 on a newer backend, which an older
  dashboard already tolerates.
- A new global status or raw event belongs in the dock (ADR-0042). A new
  Overview section needs a question from the four above that no existing
  section answers, and it links to the page that owns the detail.
- Any new Overview figure must say what it does when its source is missing:
  an em dash with a caption. A section that renders `0` for "unknown" is a
  defect.
- The Serving tile's context bar is only as exact as the sampler's
  parts-per-million figure. A slot with no sample shows no bar, not a full or
  empty one.
- The hardware sparklines are a client-side ring of recent polls, so they
  start empty after a page load. This is a deliberate trade for not adding a
  server-side history for a summary page.

## Alternatives considered

- **Keep the customizable board and add the new sections as widgets.**
  Rejected by the operator: the page has one right order, and the layout
  store, route and swap rules were the cost of letting it be wrong.
- **Source the usage figures from the dispatcher rollups, as before.**
  Rejected: they are a 60 s live window, so an idle box shows nothing. The
  request-metric table answers "how did it perform" over hours and days.
- **Read tokens per slot from `kv_used` as a count.** Rejected: it is a
  fraction. Only a ratio against the slot's effective context gives tokens.
- **Show raw events on the page.** Rejected: ADR-0042 gives the dock that
  job. The Overview folds recent activity into a five-row digest and links to
  the dock.
- **Keep the layout route as a stub returning an empty layout.** Rejected:
  a stub is a compatibility shim for a client that already handles the 404.

## References

- ADR-0042 (the dock is the one status surface); ADR-0039 (memory figures
  have one owner); ADR-0043 (agent bridge and bridge keys).
- `ui/src/dash/overview/` (`OverviewView.jsx`, `serving-pure.ts`,
  `usage-pure.ts`, `recent-digest-pure.ts`, `now-band.jsx`, `needs-you.jsx`,
  `serving-now.jsx`, `agents-panel.jsx`, `usage.jsx`, `hardware.jsx`,
  `recent.jsx`, `image-engine.jsx`, `services.jsx`, `memory.jsx`,
  `version-strip.jsx`, `overview.css`).
- `src/hal0/api/routes/hardware.py` (`stats_endpoint`),
  `src/hal0/metrics/read.py` (`stats_summary`, `system_stats`),
  `src/hal0/metrics/sampler.py` (`kv_used`).
- `src/hal0/api/routes/dashboard_layout.py`, `src/hal0/install/perms.py`
  (`dashboard-layout.json`): removed by this change.
