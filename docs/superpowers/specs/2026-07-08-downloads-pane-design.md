# Downloads pane — footer tab for model pull tracking

**Date**: 2026-07-08
**Status**: draft

## Scope

Add a "downloads" tab to the hal0 dashboard footer, giving the user a single
glanceable pane to monitor all model downloads (active + recent history) with
per-download controls: cancel, retry, and clear.

## Architecture

Two changes, one backend, one frontend.

### Backend — `GET /api/models/pulls`

Returns a merged list of all pull jobs:

- **In-memory active jobs** from `app.state.model_pull_jobs` (queued, running)
- **Persisted terminal jobs** from `/var/lib/hal0/model-pull-jobs/*.json` (completed, failed, cancelled)

Dedup rule: if a model_id appears in both, the in-memory copy wins.
Persisted jobs are filtered to terminal states only (non-terminal on disk
with no matching in-memory copy is an edge case from a crash — skip it).

Each entry shape:

```jsonc
{
  "model_id": "org/repo",
  "job_id": "abc123",
  "state": "running",        // queued | running | completed | failed | cancelled
  "bytes_downloaded": 104857600,
  "bytes_total": 4194304000,
  "speed_bps": 3200000,
  "eta_s": 72,
  "hf_repo": "org/repo",     // from model registry (or null if registry row missing)
  "dest_path": "/var/lib/hal0/models/org/repo.gguf",
  "error": null,             // {code, message} when state == "failed"
  "started_at": 1720400000.0,
  "finished_at": null
}
```

Polling interval on the frontend: 2s while the footer is expanded and the
downloads tab is active.

### Backend — `DELETE /api/models/pulls/{model_id}`

Clears a terminal pull job from both in-memory (`app.state.model_pull_jobs`
pop) and disk (delete the snapshot `.json`). Returns 409 Conflict if the job
is still active (queued or running). Returns 204 on success.

### Frontend — `usePullsList` hook

Wraps `GET /api/models/pulls` with React Query. Polls every 2s only when the
footer is expanded AND the downloads tab is active (`enabled` gate). Returns
`jobs`, `hasActive` (boolean — true if any job is queued or running).

Also wraps `DELETE /api/models/pulls/{id}` as a mutation with query
invalidation on success.

Listens for `hal0:pull-started` and `hal0:pull-ended` custom events to
invalidate the query immediately (the existing `usePullJob` hook already
dispatches `hal0:pull-ended` on terminal state; we add `hal0:pull-started`
to match).

### Frontend — Footer tab structure

The footer gains a tab bar above the expanded pane:

```
[journal] [downloads]
─────────────────────
  pane content
─────────────────────
  foot-chips (runtimes, services, update chip, toggle)
```

- "journal" tab → existing `foot-pane` with source chips, search, log lines
- "downloads" tab → new `foot-downloads` pane with download rows
- The tab state is local React state in the Footer component (reset to
  "journal" when the pane is collapsed).

### Download row design

Each row:

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ●  hf.co/org/repo  →  /var/lib/hal0/models/name.gguf                     │
│ ████████████░░░░░░░░  42%    3.2 MB/s · 1m 12s left          [× cancel] │
└──────────────────────────────────────────────────────────────────────────┘
```

**State indicator** (left-most column):
- Running: green animated spinner
- Queued: clock icon, muted
- Completed: green check ✓
- Failed: red ✗
- Cancelled: grey dash —

**Progress bar**: only for queued and running. Width = pct, amber fill.

**Speed + ETA**: only for running. Format: `{fmtBytes}/s · {fmtEta}` or
"calculating…" when speed is still 0.

**Actions column** (right-aligned):
- Queued/Running: `[× cancel]` button, calls `POST /api/models/{id}/pull/cancel`
- Failed: `[↻ retry]` button, calls `POST /api/models/{id}/pull`
- Completed/Cancelled/Failed: `[clear]` button, calls `DELETE /api/models/pulls/{id}`

### Empty state

When no downloads exist (no active, no recent history): a centered message
"No downloads yet" with a link to the Models catalogue (`#models`).

### Auto-expand

When a download starts (the `hal0:pull-started` event fires before any SSE
frame lands), the footer auto-expands and switches to the downloads tab.
Collapse is always manual — the user clicks the toggle.

The existing `usePullJob.start()` method dispatches:
```js
window.dispatchEvent(new CustomEvent('hal0:pull-started', { detail: { modelId } }))
```

The Footer listens for this event and calls `onToggle` + switches to
downloads tab.

### Interaction with existing per-row pull UI

The existing `usePullJob` hook is used per-model-row in `models.jsx` for
in-place progress within the models table. That stays unchanged. The
downloads pane is a separate consumer of the same backend pull state via
the new `/pulls` endpoint — it is read-only display plus cancel/retry/clear,
not a replacement for the per-row flow.

## Data flow

```
┌─────────────┐   POST /pull     ┌──────────────┐
│ models.jsx  │ ────────────────> │  hal0-api    │
│ per-row UI  │ <── SSE stream ── │  pull engine │
└─────────────┘                   └──────┬───────┘
                                         │ writes snapshots
┌─────────────┐  GET /pulls (2s)  ┌──────▼───────┐
│ Footer      │ ────────────────> │ model-pull-  │
│ downloads   │ <──────────────── │ jobs/*.json  │
│ pane        │                   │ + in-memory  │
└─────────────┘                   └──────────────┘
```

## CSS

New CSS class: `.foot-downloads` — a scrollable vertical list of download
rows. Each row uses a 2-line grid:

```
.foot-dl-row {
  display: grid;
  grid-template-columns: 18px 1fr auto;
  grid-template-rows: auto auto;
  padding: 10px 16px;
  border-bottom: 1px solid var(--line);
}
```

Row 1: state indicator | repo link → dest path | actions (cancel/retry/clear)
Row 2: empty (spans col 1) | progress bar + stats | empty

Hover: subtle bg shift (`var(--bg-2)`).

Vertical scroll with `max-height` matching the journal pane height
(`~280px`). Newest first (active jobs first, then terminal by `started_at`).

## Files changed

| File | Change |
|------|--------|
| `src/hal0/api/routes/models.py` | Add `GET /api/models/pulls` and `DELETE /api/models/pulls/{model_id}` |
| `src/hal0/registry/pull.py` | Expose `list_persisted_jobs()` helper (or inline in route) |
| `ui/src/api/endpoints.ts` | Add `pulls`, `pullsDelete` endpoints |
| `ui/src/api/hooks/useModels.ts` | Add `usePullsList` hook, add pull-started event dispatch |
| `ui/src/dash/chrome.jsx` | Add tab bar, downloads pane, auto-expand listener |
| `ui/src/dashboard.css` | Add `.foot-downloads` and `.foot-dl-row` styles |
| `ui/src/dash/models.jsx` | No changes needed (existing per-row pull UI unchanged) |
