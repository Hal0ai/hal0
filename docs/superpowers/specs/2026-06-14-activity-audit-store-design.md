# Activity / Audit Store — Design

**Date:** 2026-06-14
**Branch:** `feat/activity-audit-store`
**Status:** Approved (AFK execution, autonomy granted)

## Problem

As hal0's configuration surface grows (slots, models, profiles, capabilities,
backends, providers), there is no durable, trustworthy record of *what changed,
who changed it, and whether the change actually took effect*. Today:

- The only event surface is `EventBus` — an **in-memory ring of 500 events**
  (`src/hal0/events/__init__.py:44,90`). It is lost on every restart/deploy,
  has no before/after state, and records no success/failure outcome.
- **~25 mutation routes emit nothing at all** (slot delete/edit/swap, every
  profile CRUD op, capability apply, config writes, approvals deny, updater
  switchover/rollback, comfyui switchover, backend load/unload, mcp/agent
  install). Actions happen silently.
- The footer/journal and `/api/logs` + `/api/health` surfaces are **partially
  broken** (13 catalogued defects; the health endpoint actively *lies* — a
  systemd-FAILED slot reports `status: ok`; `/api/health` 404s in production;
  slot logs are doubled in journald; the footer blanks after restart because
  the event-id counter resets to 1).

The user wants a **source of truth**: a clean, readable, colorized, filterable
Activity Log on the slots page that confirms edits *actually took place* — and
the broken log/health plumbing diagnosed and fixed.

## Goals

1. **Durable audit store** (SQLite) that is the single source of truth for
   user actions and system state changes — survives restarts, queryable,
   exportable.
2. **Confirmation guarantee**: every tracked action records its *outcome*
   (`ok` / `error`) captured **after** the operation, with before/after state
   for config edits. If an edit failed, the record says so.
3. **Comprehensive coverage**: every CRUD/lifecycle action across slots,
   models, profiles, capabilities, backends, providers, mcp, agents, approvals,
   updater, comfyui — plus state transitions, model pulls, health/drift
   transitions, and **denied/failed actions**.
4. **Readable UI**: a slots-page ActivityLog that is colorized, severity/kind
   filterable, **bounded** (fixed-height contained scroll, not an infinite
   spinning firehose), with export. Replaces the now-redundant sidebar widgets.
5. **Fix the broken plumbing** that is in-scope (health lie, event epoch,
   stream filters, duplicate logs, UI honest-health), and **file every
   defect** as a GitHub issue.

## Non-goals

- Not replacing journald / `/api/logs` (raw unit logs stay as-is; we fix bugs).
- Not a distributed/remote audit sink. Single-node SQLite on `/var/lib/hal0`.
- Not auth/RBAC on the audit surface (read-only, LAN-open like the rest).
- Not migrating the footer journal off EventBus this round (it stays live +
  gets its bugs fixed; durability is delivered via the new Activity surface).

---

## Architecture

Two complementary layers, one durable source of truth:

```
                       ┌─────────────────────────────────────────┐
   mutation routes ───▶│  audit_action(...) context manager       │
   (slots, models,     │  • capture before-state                  │
    profiles, caps,    │  • run the operation                     │
    backends, mcp,     │  • on success → record outcome=ok + after│
    agents, approvals, │  • on exception → record outcome=error   │──┐
    updater, comfyui)  │    + message, then re-raise              │  │
                       └─────────────────────────────────────────┘  │
                                                                     ▼
   EventBus.emit() ──────────(mirror, durable forward)──────▶ ┌──────────────┐
   (slot.state, pull.*,                                       │  AuditStore  │
    system.*)                                                 │  (SQLite,    │
                                                              │  stdlib)     │
                                                              │  app.state   │
                                                              │  .audit      │
                                                              └──────┬───────┘
                                                                     │
   ActivityLog (slots page) ◀── GET /api/activity (filter+SSE+export)┘
```

- **EventBus stays** as the fast, in-RAM live stream for the footer + SSE. Its
  `emit()` already documents itself as the hook for "forwarding to journald"
  (`events/__init__.py:80`). We add a durable forward there: every emitted
  event is also written to `AuditStore` as a `kind="event"` record. This
  captures structural state changes (`slot.state`, `pull.*`, `system.*`) for
  free, durably.
- **`audit_action`** is the richer path for user-initiated mutations. It writes
  `kind="action"` records carrying `before`/`after` JSON and a truthful
  `outcome`. This is the "confirmation it actually took place" mechanism: the
  outcome is recorded *after* the operation returns or raises.
- **`AuditStore`** is the single durable sink and the only thing `/api/activity`
  reads from. ActivityLog → `/api/activity` → durable truth.

### Why this split

`audit_action` gives the confirmation + before/after that EventBus events
structurally lack, while the EventBus mirror guarantees we never *miss* a state
change that doesn't flow through an instrumented handler (e.g. a background
dispatcher preempt, a health transition). Together they are comprehensive.
De-dup is by design: state transitions arrive as `kind=event`; user actions as
`kind=action`. The UI can show both, filtered.

---

## Component 1 — `AuditStore` (`src/hal0/activity/__init__.py`)

New single-file package mirroring `events/__init__.py`. Uses **stdlib
`sqlite3`** (per house pattern `memory/cognee_wrapper.py:352-390`; aiosqlite /
sqlalchemy are in `uv.lock` but **not installed** — stdlib is the only safe
choice). Per-call connection, `row_factory=sqlite3.Row`, WAL mode, schema via
idempotent `CREATE TABLE IF NOT EXISTS`. Writes wrapped in `asyncio.to_thread`
so the event loop never blocks on disk.

### Schema (`audit` table)

| column        | type    | notes |
|---------------|---------|-------|
| `id`          | INTEGER PK AUTOINCREMENT | monotonic, durable cursor |
| `ts`          | TEXT    | ISO-8601 UTC |
| `kind`        | TEXT    | `action` \| `event` |
| `category`    | TEXT    | `slot`\|`model`\|`profile`\|`capability`\|`backend`\|`provider`\|`mcp`\|`agent`\|`approval`\|`updater`\|`comfyui`\|`system` |
| `action`      | TEXT    | dotted verb, e.g. `slot.delete`, `profile.update`, `capability.apply` |
| `target`      | TEXT    | the entity acted on, e.g. slot name / model id |
| `actor`       | TEXT    | `dashboard` \| `cli` \| `mcp:<agent>` \| `system` (from request header / context) |
| `severity`    | TEXT    | `info` \| `warn` \| `error` \| `ok` (success) |
| `outcome`     | TEXT    | `ok` \| `error` \| `pending` (events: NULL) |
| `message`     | TEXT    | human-readable one-liner |
| `before`      | TEXT    | JSON snapshot (config edits) or NULL |
| `after`       | TEXT    | JSON snapshot or NULL |
| `error`       | TEXT    | error message when outcome=error |
| `duration_ms` | INTEGER | wall time of the action |
| `request_id`  | TEXT    | correlate with logs / multi-step ops |

Indexes on `ts`, `category`, `action`, `outcome`, `severity` for filterable
queries. `PRAGMA user_version` gate in `init_schema()` for future migrations
(we are first; keep it simple).

### Public API

```python
class AuditStore:
    def __init__(self, db_path: Path, *, retention_days: int, max_rows: int | None): ...
    def init_schema(self) -> None: ...                       # idempotent, first-boot
    async def record(self, *, kind, category, action, target, actor,
                     severity, outcome, message, before=None, after=None,
                     error=None, duration_ms=None, request_id=None) -> int: ...
    async def record_event(self, event: dict) -> int: ...     # EventBus mirror adapter
    def query(self, *, since=0, category=None, action=None, severity=None,
              outcome=None, actor=None, kind=None, search=None, limit=200) -> list[Row]: ...
    def export(self, *, fmt: "csv"|"json", **filters) -> str | bytes: ...
    async def prune(self) -> int: ...                         # retention enforcement
```

`audit_action` helper (async context manager) lives here:

```python
@asynccontextmanager
async def audit_action(store, *, category, action, target, actor,
                       before=None, message=None):
    rec = ActionInProgress(...)   # holds after/outcome to be filled
    t0 = monotonic()
    try:
        yield rec                  # handler runs, may set rec.after
        await store.record(..., outcome="ok", severity="ok",
                           after=rec.after, duration_ms=elapsed)
    except Exception as exc:
        await store.record(..., outcome="error", severity="error",
                           error=str(exc), duration_ms=elapsed)
        raise
```

### Retention

`ActivityConfig` (pydantic `BaseModel` in `config/schema.py`, registered on
`Hal0Config`): `enabled: bool = True`, `retention_days: int = 30`,
`max_rows: int | None = 50_000`. Env override `HAL0_ACTIVITY_RETENTION_DAYS`.
`prune()` runs on startup and after every N writes (cheap `DELETE WHERE ts <`).
This keeps the DB bounded without ever losing recent history.

---

## Component 2 — `/api/activity` route (`src/hal0/api/routes/activity.py`)

Mirrors `routes/events.py`. Read-only, no auth dep.

```
GET /api/activity?since=<id>&category=<c>&action=<glob>&severity=<s>
                 &outcome=<o>&actor=<a>&kind=<k>&search=<q>&limit=<n=200>
    → {"records": [...], "next_since": int, "epoch": "<boot-id>"}

GET /api/activity/stream?since=<id>&<same filters>
    → SSE: durable backfill (id > since) then live tail. Filters honored
      server-side (fixes the B10 class of bug for this surface from day one).

GET /api/activity/export?fmt=csv|json&<same filters>
    → file download (Content-Disposition attachment).
```

Inline `Hal0Error` subclasses: `ActivityUnavailable` (503),
`ActivityInvalidQuery` (400). Mounted near `__init__.py:1155`.

Includes an `epoch` (process boot-id) in every response so the client can
detect a restart and reset its cursor — closing the **B8** footer-blanking
class of bug for this surface.

---

## Component 3 — Instrumentation (the coverage)

`audit_action` (or `store.record`) added to every mutation site. Full list
(file:line from the backend-map agent):

| Category | Routes instrumented |
|----------|---------------------|
| **slot** | create `slots.py:292`, **delete :816**, **edit-config :837**, **defaults :873**, **set-backend :937**, load :1074, unload :1110, restart :1117, **swap :1124**, pull :1410 |
| **model** | pull/add/update/delete/swap (`models.py` — already emit events; add action records w/ outcome for delete/swap) |
| **profile** | **create :134, update :160, delete :186** (`profiles.py`) |
| **capability** | **apply/set :39** (`capabilities.py`) |
| **backend** | npu load :327 / unload :399 (`backends.py`) |
| **provider** | credential write `providers.py:207` (**redacted** — never store secret values) |
| **config** | `PUT /models` `config.py:228`, `settings.py` config_save |
| **mcp** | install :771, delete :831, config :849, action :884 (`mcp.py`) |
| **agent** | install :97, delete :298 (`agents.py`) |
| **approval** | **approve :100, deny :125** (`approvals.py`) — denied actions captured |
| **updater** | apply :415, rollback :500, channel :533 (`updater.py`) — switchover-class |
| **comfyui** | switchover :415, pin :510 (`comfyui.py`) |
| **system** | EventBus mirror: `slot.state`, `pull.*`, `system.*`, health/drift transitions |

**Bold** = currently emits *nothing*. Provider credential writes record the
*fact* and *actor* only — never the secret (redaction enforced in the helper).

State transitions, model-pull lifecycle, health/drift, and dispatch-preempt
arrive via the EventBus durable mirror, so they are covered without touching
every internal call site.

---

## Component 4 — ActivityLog UI (`ui/src/dash/`)

Replace the three sidebar widgets (`SnapshotStrip`, `MemoryMap`,
`ThroughputCard`) inside `.dash-side` at all four `slots.jsx` render branches
(`1078-1082`, `1114-1116`, `1144-1146`, `1274-1278`) with a single
`<ActivityLog/>`. Keep the `.dash-side` wrapper (the 320px grid column) and the
`.side-card` shell. Drop the now-unused imports.

Clone the footer journal pane (`chrome.jsx:416-545`) for structure, but back it
with the **durable** `/api/activity` (new `useActivity` hook in
`ui/src/api/hooks/`, following the `useLogs.ts` SSE + `useQuery` backfill
convention). Differences from the footer:

- **Severity filter chips** (the footer lacks these): `All · ok · info · warn ·
  error`, mirroring `.foot-pane-chip`/`.on` toggle pattern. Plus a compact
  **kind/category** dropdown (slot/model/profile/…).
- **Colorized lines** using existing CSS vars: `--ok #6fcf97` (success),
  `--warn #e8b94e` (amber), `--err #ef6b6b` (red), `--accent #feaf00` (brand),
  neutral for info. Left border + level badge per `.foot-line.{warn,error}`.
  Each row shows: time · actor · action · target · ✓/✗ outcome glyph · message.
  A failed edit renders red with ✗ — instant "did it actually take?" read.
- **Bounded, not a firehose**: fixed `max-height` contained scroll
  (`overflow-y:auto`), newest-first, default shows the most recent ~200, with a
  "load older" affordance and a per-`pull.progress`-style throttle so progress
  spam can't drown lifecycle rows. Pause-on-hover so it doesn't scroll out from
  under you.
- **Export button** → `/api/activity/export?fmt=csv` (and JSON) honoring the
  active filters, so you never have to keep an overflowing pane open to keep
  history.
- **Live tail** via `/api/activity/stream`, with the `epoch` reset guard.

Styling in a new `ui/src/dash/activity-log.css` (imported like the other
per-pane css files), reusing `dashboard.css` severity vars + `.side-card`.

### Tests
Update specs that assert the removed widgets (`memory-map-v3.spec.ts`,
`sidebar-runtime-widget.spec.ts`, `footer-journal-pane-v3.spec.ts`) and add
`activity-log.spec.ts` (filter chips, colorization, bounded scroll, export).

---

## Component 5 — In-scope logs/health fixes

Diagnosed defects (all filed via `/to-issues`; these fixed this round):

- **B1** — `/api/health` 404 in prod. Verify deploy-lag (route exists at
  `health.py:124` in source); add smoke test asserting 200 JSON.
- **B2/B4** — health endpoint lies. `health/system` (`health.py:177`) reports
  `ok` ignoring `s.state`; slot `status()` (`manager.py:1037`) trusts
  `systemctl is-active` without probing the model server. Fix: count
  ERROR/failed slots → `degraded`; gate ready-set states on
  `container_readiness_check`/`ContainerProvider.health`.
- **B8** — event-id resets to 1 on restart → footer blanks. Add per-process
  `epoch`/boot-id to `/api/events` + `/api/activity` responses; client resets
  cursor on epoch change.
- **B10** — `/api/events/stream` ignores `type`/`severity`. Accept + apply
  server-side. (`/api/activity/stream` is built correct from the start.)
- **B12** — UI ignores honest `/api/health/system`. Add `useHealthSystem`;
  drive the runtime chip color + tooltip from real `degraded`/`checks`.
- **B13** — slot log drawer drops the named `event: error` SSE frame
  (`slot-modals.jsx:807`) → spins forever. Rename backend frame to
  `event: degraded` and `addEventListener` for it.
- **B3** — slot logs doubled in journald (podman `journald` log-driver *and*
  unit `StandardOutput=journal`). Pick one path (`--log-driver=none`,
  conmon→unit owns stdout).

Deferred to follow-up issues (filed, not fixed this round): B5, B6, B7, B9,
B11, and the LOW/cosmetic items (stale docstrings, journal sort-by-ts, dead
source chip).

---

## Data flow — a worked example

User edits slot `chat` context_size via the dashboard:

1. `PUT /api/slots/chat/config` handler enters
   `audit_action(category="slot", action="slot.edit_config", target="chat",
   actor="dashboard", before=<current toml>)`.
2. Handler writes the new TOML, reloads.
3. On success: `AuditStore.record(outcome="ok", severity="ok",
   after=<new toml>, duration_ms=…)`. On failure: `outcome="error",
   error="…"`, and the exception propagates to the normal error envelope.
4. The slot reload triggers `slot.state` transitions → EventBus emits → durable
   mirror writes `kind="event"` rows.
5. ActivityLog's SSE tail shows a green `✓ slot.edit_config chat` row, followed
   by the state-transition rows — the user *sees the edit landed*. If it
   failed, a red `✗` row with the reason.

---

## Risks / mitigations

- **Write amplification** (every event hits disk): writes are tiny, WAL +
  `asyncio.to_thread`, and `pull.progress` is the only high-frequency emitter —
  we down-sample progress rows in the mirror (keep start/end + every Nth).
- **Schema drift over time**: `PRAGMA user_version` gate from day one.
- **Another session holds the repo branch**: all work in the
  `feat/activity-audit-store` worktree off `origin/main`; `wip claim` the files.
- **Deploy parity**: CT105 deploy via `scripts/deploy.sh` (rebuilds gitignored
  `ui/dist`), then live-validate `/api/activity`, health fixes, and the UI.
```
