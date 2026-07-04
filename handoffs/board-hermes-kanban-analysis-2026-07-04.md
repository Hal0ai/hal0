# Operator Board ↔ Hermes kanban plugin — analysis & recommended hal0-side fixes

> Scope: the hal0 Operator Board surface (`src/hal0/board/`, `src/hal0/api/routes/board*.py`,
> `ui/src/dash/board/`, `ui/src/api/hooks/useBoard.ts`) versus the connection to the external
> hermes-agent dashboard's kanban plugin (`{HERMES_DASHBOARD_BASE_URL}/api/plugins/kanban/*`).
> All recommendations are hal0-side only — Hermes is upstream (`NousResearch/hermes-agent`,
> pip-installed, loopback-only :9119) and cannot be patched from here.

## 1. Architecture summary (what works well)

The proxy design is sound and consistently executed:

- **Single boundary.** The browser only ever talks to hal0-api `:8080` under `/api/board/*`;
  `HermesKanbanClient` (`src/hal0/board/__init__.py`) is the one place auth, base-url, and
  error mapping live. Reads are a table-driven allowlist passthrough; every mutation is an
  explicit handler wrapped in `record_action` (`board.py`), so the audit trail is complete.
- **Token model.** Hermes rotates an ephemeral per-process bearer on every restart; hal0
  harvests `window.__HERMES_SESSION_TOKEN__` from the dashboard HTML, caches it, and
  re-harvests once on 401 (REST, `__init__.py:231-236`) / on failed WS upgrade
  (`board_ws.py:132-146`). Browser credentials are stripped inbound. The shared
  `app.state.hermes_kanban` client means a refresh on either surface benefits both — the fix
  for the earlier "board loads but never updates live" regression.
- **Wire-shape tolerance.** `useBoard.ts` normalises the four board shapes Hermes may emit
  (`{columns:[…]}`, `{lanes}`, `{tasks}`, bare array), the `/tasks/{id}` envelope
  (`{task, comments, events, attachments, links, runs}`), epoch-second timestamps, and the
  `{name, on_disk, counts}` actor shape (`boardActors.js`) that previously black-screened the
  dashboard (React #31).

The gaps below are almost all on the **hal0 UI layer** — the FE diverges from its own frozen
contract (`ui/CONTRACTS.md` §"Operator Board") and from what the backend proxy actually
forwards.

## 2. Bugs (correctness — ordered by impact)

### 2.1 `DELETE /links` from the drawer never removes a dependency  — HIGH
- Backend + upstream contract: `parent_id`/`child_id` ride as **query params**
  (`board.py:218-229`; asserted in `tests/board/test_board_routes.py:227-238` — `fwd["body"] == ""`).
- UI: `useRemoveLink` (`useBoard.ts:697-714`) sends them as a **JSON body**
  (`api(…, {method:'DELETE', body: linkBody})`), query string carries only `?board=`.
- Result: the proxy forwards `DELETE /api/plugins/kanban/links` with no ids → upstream 4xx
  (surfaced as `board.upstream_error`) or no-op. The drawer's dep-chip "×"
  (`task-drawer.jsx:87-91`) toasts "dep removed" regardless.
- **Fix:** build the query string in `useRemoveLink`:
  `${ENDPOINTS.boardLinks}?parent_id=…&child_id=…[&board=…]`, drop the body.

### 2.2 Events WS never reconnects after a clean close → live updates die silently — HIGH
- `board_ws.py` closes the browser socket with **1011** when the upstream connect fails
  (`:143-146`) and closes normally when the pumps end (Hermes restart drops the upstream,
  `:169-174`), explicitly expecting "the client's reconnect logic" to engage.
- But `useBoardEventsStream` only schedules a reconnect in **`ws.onerror`**
  (`useBoard.ts:972-984`); `onclose` just sets `connected=false` (`:986-988`). A server-sent
  close frame (1011 or normal) fires only `close` in browsers — so after any Hermes restart
  the board stops updating live until a full page reload, precisely the regression class the
  backend WS fix was shipped for.
- **Fix:** move the backoff/reconnect scheduling into `onclose` (guarded by the effect's
  `cancelled` flag so unmount/`follow=false` doesn't loop), keep `onerror` as a
  `ws.close()` trigger. Optionally track the last received `cursor` and pass it as `since`
  on reconnect so the gap is replayed (the frame already carries it:
  `{"events":[…], "cursor": N}`, `board_ws.py:1-8`).

### 2.3 "Show archived" shows an empty lane — MEDIUM
- The archived lane is revealed by `showArchived` (`board-view.jsx:288-291`) but the data
  query is always `useBoardView({})` (`board-view.jsx:177`) — `include_archived=true` is
  never sent, even though the hook supports it (`useBoard.ts:443`). Unless Hermes returns
  archived tasks by default (it hides them, which is why the param exists), the lane is
  permanently empty.
- **Fix:** `useBoardView({ includeArchived: showArchived })` (the option is already in the
  query key, so toggling refetches correctly).

### 2.4 Selected board is not threaded into queries, mutations, or the WS — MEDIUM
- `board-view.jsx` keeps `board` state and calls `switchBoard.mutate(slug)` (server-side
  "current board"), but then calls `useBoardView({})`, `useUpdateTask()`,
  `useCreateTask()`, `useBoardEventsStream()` etc. with **no board argument** — every hook
  supports one. The frozen contract says "`?board=<slug>` threads through every
  task/board-scoped call".
- Consequences: two dashboards (or the MCP/agent surface) racing on the server-side current
  board mutate each other's view; the WS subscribes to all boards' events; the local
  `setBoard(slug)` happens before the switch mutation settles with no rollback on error.
- **Fix:** pass `board` through `useBoardView({board})`, the mutation hooks
  (`useUpdateTask(board)` …), `useBoardAssignees(board)`, and
  `useBoardEventsStream({board})`. Keep the `/switch` POST for Hermes-side default-board
  semantics, but stop depending on it for correctness.

### 2.5 Orchestration `mode` is a phantom field — MEDIUM
- The contract's PUT knobs are `orchestrator_profile, default_assignee, auto_decompose,
  auto_promote_children` (`CONTRACTS.md:214`, `useBoard.ts:139-145`). There is no `mode`.
- Yet the pill renders `orchData.mode ?? "auto"` (`board-view.jsx:461-467`) — always "auto",
  since GET `/orchestration` never returns `mode` — and `OrchPopover.handleSave` PUTs
  `mode` upstream (`orchestration-popover.jsx:55-63`), which Hermes will ignore or reject.
  The popover header's "dispatching/paused" state is therefore cosmetic fiction, violating
  the board's own "NO STUB DATA" hard rule.
- **Fix:** drop `mode` from the PUT payload and the pill, or derive a real signal (e.g.
  from `/config`/`/diagnostics`) and render "source pending" until one exists.

### 2.6 Dropping a card anywhere outside a lane hard-DELETEs the task — MEDIUM (UX/data-loss)
- `board-view.jsx:598-621`: any drop on the board background calls `delTasks([dragId])` →
  `DELETE /api/board/tasks/{id}` upstream. One missed lane target = task (and its comments,
  runs, links — Hermes owns the row) destroyed with no confirmation and no undo.
- **Fix:** make drop-outside archive instead of delete, or require the existing
  `delArmed` veil to be a distinct explicit drop target with a confirm step. At minimum
  offer an undo toast (re-`POST /tasks` is lossy, so prefer archive).

### 2.7 Small UI truth bugs — LOW
- **Refresh is a no-op:** `doRefresh` only toasts "board refreshed" (`board-view.jsx:341-343`).
  Wire it to `queryClient.invalidateQueries({queryKey: boardKey(board)})` (expose via the
  bridge) or `boardViewQ.refetch()`.
- **Attention "Show" clears filters instead of filtering:** it resets tenant/profile/search
  and toasts "filtered to attention" (`board-view.jsx:495`) — it does not filter to
  blocked/review. Add an attention filter state the lane filter respects.
- **`window.LANE` is never defined** — `stName` (`task-drawer.jsx:16-19`) always falls back
  to the raw status id, and the contract's "`running` → 'in-progress'" display label is
  implemented nowhere (`BOARD_LANES` says "Running"). Publish the lane map from
  board-view (`window.LANE = Object.fromEntries(BOARD_LANES.map(l => [l.id, l]))`) and fix
  the label.
- **Chat tool frames render as "operator":** `roleLabel` in `agent-chat.jsx:76-77` maps
  everything non-assistant to "operator", so the `role:'tool'` frames from `useBoardChat`
  show as operator messages. Add a `tool` branch (and consider rendering `tool_result`
  frames, which are currently invisible — only used for invalidation).
- **`depCount` badge char-indexing:** `task.depCount[0] !== task.depCount[2]`
  (`kcard.jsx:47`) breaks for counts ≥10 ("10/12"). Split on "/" and compare parts.
- **`create_task` warning dropped:** contract says surface the "no dispatcher running"
  `warning` as a toast/banner; `board-view.jsx:683-688` toasts a generic success. Inspect
  the mutation result for `warning` and toast it.
- **Unknown statuses vanish:** `normaliseBoardResponse` drops tasks whose status isn't in
  the 9-value enum from every lane (`useBoard.ts:408-414`) while keeping them in `tasks` —
  invisible on the board, still counted in "N tasks". Bucket unknowns into `triage` (or an
  "unknown" catch-all) so upstream enum drift degrades visibly.

## 3. Functional gaps vs the Hermes connection

### 3.1 Board chat can mutate but cannot see the board — HIGH (functionality)
`board_chat.py` gives the LLM ten **write** tools mapping 1:1 onto the audited mutations,
but:
- **No read tools** (`get_board`, `get_task`, `list_assignees`) and **no system prompt** —
  the model receives only the raw user/assistant turns (`_chat_stream`, `board_chat.py:357-374`).
  "What's blocked?" or "move the auth task to review" (the UI's own suggestion chips,
  `agent-chat.jsx:13-18`) cannot be answered/executed unless the user pastes task ids.
- **Recommended:** (a) prepend a system message describing the surface + lane semantics;
  (b) add read tools backed by the same allowlisted GET paths (`/board`, `/tasks/{id}`,
  `/assignees`) — they need no audit rows, matching the REST split; or cheaper, inject a
  compact board snapshot (ids/titles/status/assignee) into the system message per turn.
- **No token streaming:** the loop posts `stream: False` and emits each round's full text as
  one `token` frame — the UI's streaming renderer is real but receives one blob per round.
  Streaming the final round would materially improve perceived latency; the SSE contract
  already supports it.
- The FE also omits `X-hal0-Agent` on `/chat` (`useBoard.ts:1075-1083`); audit actor falls
  back to "dashboard", which is acceptable but a `chat` marker would make
  `board.chat.turn` rows self-describing without relying on `message="chat:<tool>"`.

### 3.2 Bulk actions bypass the bulk endpoint — MEDIUM
`moveTo`/`delTasks` fan out N single-task mutations (`board-view.jsx:307-319`) even though
`POST /tasks/bulk` exists, is audited as one row (`board.py:147-157`), and `useBulkTasks`
is already exported on the bridge. N PATCHes = N audit rows, N upstream round-trips, N
invalidations, and partial-failure states the UI doesn't report. Use `useBulkTasks` for the
bulkbar (and drag-multi-select later); keep single-card ops on PATCH.

### 3.3 WS invalidation storm — LOW/MEDIUM
Every WS frame invalidates the board query (`useBoard.ts:961-970`). Hermes polls
`task_events` at 300ms and pushes batches; a busy run can trigger several refetches per
second, each a full `GET /board` proxied to Hermes. Debounce invalidation (e.g. trailing
250–500ms), or eventually apply `events` payloads to the cache directly. Also type
`BoardEvent` to the real frame (`{events:[…], cursor}`) — the current `{kind, task_id}`
interface describes an element, not the frame, and `lastEvent` is misleading to consumers.

### 3.4 Unused Hermes surface — LOW
`/stats`, `/diagnostics`, `/workers/active`, `/runs/{id}`, `/tasks/bulk`, `/reclaim`,
`/profiles/{name}` PATCH are proxied, audited, hook-wrapped and bridge-published — and no
board component calls them. Either surface them (stats strip in the board top bar;
workers-active badge on `running`; reclaim button on stuck `running` cards — reclaim is
exactly the recovery path for a claim-TTL expiry) or note them as future in CONTRACTS to
keep the FROZEN table honest.

## 4. Design / UI polish

- **Reduced motion:** `board.css` animations (`board-pulse` on `.kdot.live`, typing dots,
  `data-accent="dot"` pulse) have no `prefers-reduced-motion: reduce` guard; the
  dashboard-wide rule only covers `.dot.*`. The board's own acceptance gate requires the
  loops to die under reduced motion. Add one `@media` block in `board.css`.
- **Stub replies still shipped:** `agent-chat.jsx:46-53` contains canned fake responses
  (used only when the hook bridge is absent, but "NO STUB DATA" is a hard rule and the
  fallback can mask a broken bridge in prod). Replace the stub path with a gated
  "chat backend unavailable" notice.
- **Conditional hook calls:** `board-view.jsx`/`task-drawer.jsx` call hooks behind
  `window.__hal0Use* ?` guards. Because the bridge loads before mount this works, but if a
  bridge global ever appears/disappears between renders React will throw (hook-order).
  Worth a comment-level invariant at minimum; longer-term, the window-globals prototype
  files should migrate to real imports.
- **Optimistic move only:** `useUpdateTask` patches the cache optimistically for `status`
  changes only; reassign/priority edits wait for the round-trip. Fine, but drag-and-drop
  between lanes plus the 300ms event echo can cause a brief snap-back when the refetch
  lands before Hermes commits; the debounce in §3.3 also mitigates this.

## 5. Suggested execution order

1. §2.1 remove-link query params (one-line hook fix + unit test).
2. §2.2 WS reconnect-on-close (+ cursor resume) — restores the "live board" guarantee.
3. §2.3/§2.4 thread `includeArchived` + `board` through the hooks.
4. §2.6 drag-delete → archive/confirm.
5. §3.1 board-chat read tools + system prompt.
6. §2.5, §2.7 batch of small truth fixes.
7. §3.2 bulkbar → `/tasks/bulk`; §3.3 invalidation debounce; §4 polish.
