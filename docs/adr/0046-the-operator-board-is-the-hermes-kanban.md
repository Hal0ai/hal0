# ADR-0046: The Operator Board is the Hermes kanban; hal0 does not keep its own

## Status

**ACCEPTED (2026-11-01).** Operator decision of 2026-11-01: the hal0
dashboard's Operator Board reads and writes the Hermes kanban through
hal0-api, and hal0's own board store is removed. This record amends the
KB-4 rework decision (commits `0f09043e` and `e18c25ab`, which have no ADR
of their own) and extends ADR-0033 to the board. Landed as #3264 (facade and
token-file auth), #3266 (brain tools), #3265 (shim write-through and doctor
rows), #3272 (routes, events socket, store removal, converter), #3269 (UI
empty states) and the docs PR carrying this record.

## Context

KB-4 made hal0 authoritative for the Operator Board: `/api/board/*` answers
from SQLite tables in `hal0.db` (`src/hal0/db/migrations/005_board.sql`,
`src/hal0/board/store.py`). The Hermes kanban was demoted to an optional
executor behind the KB-5 seam (`src/hal0/board/dispatch.py`,
`src/hal0/board/hermes_executor.py`) and imported once, at the first
request of a process whose store was empty.

On a box running hal0 1.7.1 with Hermes v0.22.4 the board showed zero
tasks while the Hermes web UI, reading the same machine, showed the
operator's live kanban. Three causes stacked:

1. The one-shot import is the only path from Hermes to hal0. Cards created
   in Hermes afterwards never reach the hal0 board, and cards created in
   hal0 never reach Hermes.
2. The import had failed. `/etc/hal0/api.env` pinned `HERMES_SESSION_TOKEN`
   to a stale value; Hermes rotates its dashboard token at every start
   unless `HERMES_DASHBOARD_SESSION_TOKEN` is set, and a pinned value
   bypasses both the token harvest and the 401 retry
   (`src/hal0/board/__init__.py`). Every call answered 401, the store was
   seeded empty, and the non-empty store blocked every later retry.
3. The executor never registered: it requires `HERMES_DASHBOARD_BASE_URL`
   in the environment and nothing in hal0 sets it. Fourteen days of journal
   carried no `board.*` line.

The Hermes kanban at the pinned v0.22.4 is a full surface: workflow
templates, swarm and decompose, specify, PR acceptance, board transfer,
diagnostics, a worker ledger, per-profile lanes. hal0's store reproduced a
CRUD subset and would have to track every Hermes release to keep up. The
pre-KB-4 proxy already mapped the frozen FE↔BE contract (`ui/CONTRACTS.md`
"Operator Board") 1:1 onto `/api/plugins/kanban/*`, and the dashboard still
accepts the Hermes board shape.

hal0 already mints a stable per-install dashboard token for Hermes
(`hal0-agent` shim, #2899, `/var/lib/hal0/state/agents/hermes/dashboard-session-token`).
hal0-api never read it.

## Decision

**The Operator Board is the Hermes kanban.** hal0-api proxies
`/api/board/*` to the kanban plugin on the loopback Hermes dashboard, audits
mutations as before, and keeps no board state of its own.

- One client facade in `hal0.board` is the only board surface for the REST
  routes, the events WebSocket, the hal0-brain chat tools and the bench
  regression writeback.
- The dashboard token is read from the file the `hal0-agent` shim mints.
  The shim writes the effective token there even when a unit drop-in
  supplies it. On a 401 the facade re-reads the file once. The
  `HERMES_SESSION_TOKEN` environment pin and the HTML token harvest are
  removed; `hal0 doctor` warns when the pin is still present.
- Unavailability is explicit: `503 board.not_installed` (no Hermes agent
  provisioned), `503 board.unreachable` (installed, loopback connect
  failed), `503 board.no_token` (token file missing), and
  `board.upstream_error` for Hermes's own errors. Each is logged, and the
  first two raise one status-dock event per transition.
- Boxes without Hermes have no board. The page shows an install prompt.

Removed: the board store and its migration tables, the KB-5 dispatch seam
and the Hermes executor, the ETag/If-Match layer (Hermes has no per-card
revision and no client sent If-Match), and their tests.

Migrated once: cards that exist in a box's hal0 store are created in the
Hermes board of the same slug (in `triage`, never where the dispatcher can
claim them) at the first board request of the new version. If Hermes is not
installed they are exported to a JSON file under the hal0 state directory
and the journal says where. If Hermes is merely unreachable the conversion
defers and retries on later requests; only after 24 hours of deferral are
the cards exported. The legacy tables are dropped only after every card was
converted or exported.

## Consequences

- The board is live and complete: whatever Hermes holds, the hal0 page
  shows, including per-profile lanes and worker state.
- hal0 depends on the Hermes dashboard being up to show a board. That is
  the same dependency the agent chat (ADR-0033) and the dashboard plugin
  proxy already have, and it is now reported rather than hidden.
- New board features are additive proxy rows plus UI work, never a schema
  change in hal0.
- `/api/board/*` paths, methods, payload shapes and status codes are
  unchanged for 1.x. The `ETag` response header and `If-Match` request
  header are no longer honoured; they were additive and unused.
- `HERMES_SESSION_TOKEN` is ignored. `HERMES_DASHBOARD_BASE_URL` remains
  the single base-URL override.

## References

- ADR-0033 (agent chat is Hermes's own dashboard), ADR-0034 (Hermes pin
  and install posture), ADR-0042 (status dock).
- KB-4 commits `0f09043e`, `e18c25ab`; KB-5 commits `4efddf69`, `d41e1f8a`.
- Stable dashboard token: #2899, `src/hal0/cli/agent_shim.py`.
- Hermes v0.22.4 kanban plugin API: `plugins/kanban/dashboard/plugin_api.py`.
