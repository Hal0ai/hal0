# Platform-wide cron/automation management — design

Status: draft
Date: 2026-07-11
Branch context: drafts against `feat/pi-coder-provider-memory-subagents` (clean tree)

## Problem

Today scheduling on hal0 is fragmented:

- **Hermes** owns a working cron subsystem: `~/.hermes/cron/jobs.json`, a ticker that runs inside the hermes-gateway process every 60s, a CLI (`hermes cron {list,create,edit,pause,resume,run,remove,status,tick}`), and one active job (`hal0-memory-rollup`, hourly). It is hermes-internal.
- **pi-coder** has no scheduler. Any recurring work it does goes through `hermes cron` or through the shared kanban board, which it accesses via the SQLite DB.
- **OpenClaw** is currently out of scope for this design (deferred per the design discussion 2026-07-11 — to be revisited when OpenClaw is bundled into hal0).

The kanban (`~/.hermes/kanban.db`) is durable, agent-shared, has parent/child links, an event log, a runs table with idempotency keys, and a `scheduled` lane — but it is missing the cron-side fields (recurrence, dispatch target, template reference). Every Hermes cron job today lives outside that durable audit trail.

Goal: a single, agent-agnostic, kanban-native scheduler for hal0. Hermes and pi-coder become dispatch targets inside one lifecycle. The dashboard unifies view.

## Non-goals

- Sub-minute cron granularity (1m is the floor; sub-second isn't a real use case here).
- Distributed or HA scheduling. hal0-api is single-instance.
- DAG / workflow orchestration — kanban parent/child already covers that surface.
- OpenClaw integration (revisit when OpenClaw is bundled).
- Replacing the existing kanban task lifecycle.
- Auto-migration of `jobs.json` into kanban. Manual command only.

## Decisions taken during brainstorming

1. **Kanban absorbs the schedule.** `tasks` grows cron fields; kanban.db is the single source of truth.
2. **Agent routing per job + reusable template.** Each scheduled task has `agent_target` (Hermes or pi-coder) and an optional `template_id` referencing a `job_templates` row. Template is a reusable bundle (prompt + skills + model + toolsets); schedule is the timing.
3. **Runs become child tasks.** Parent = the schedule. Every fire inserts a child `tasks` row (`status='ready'` → claim → run → done/failed). Reuses existing `task_links` parent/child machinery, `task_runs`, and `task_events`.
4. **`hermes cron` CLI becomes a shim over kanban.db.** Same command surface; new storage backend. Backward compatible.
5. **`pi cron` CLI is added** — parallel surface for pi-coder operators. Same DB.
6. **`hal0 cron` CLI is added** — the canonical platform surface. Agent CLIs delegate to it.
7. **Migration is manual, opt-in.** `hal0 cron migrate-import [--dry-run]` reads `jobs.json`, creates scheduled task rows, and (without `--dry-run`) renames the source file to `jobs.json.imported-<ts>`. No auto-migration on startup.
8. **No destructive-action guard.** Per design-discussion decision 2026-07-11. Cron-fired runs are trusted; operator takes responsibility for what they schedule. The known unguarded-deletes incident (2026-07-04, ~632 records lost including the `private__hermes-agent` bank) is recorded here as a known risk that this decision knowingly accepts.

## Architecture

Two distinct concerns kept separate by an interface boundary:

```
  hal0-api
    ├── kanban cron ticker (new)         ← only touches kanban.db
    │     scans tasks WHERE cron_expr IS NOT NULL AND next_fire_at <= now
    │     for each due row: idempotency_key insert into task_runs
    │     advances next_fire_at via croniter
    │     creates child tasks (parent_id=<schedule>, status='ready')
    │     does NOT call into any agent
    │
    └── dispatcher workers               ← separate component
          claims ready children
          routes by child.agent_target
          updates child.status via lifecycle events
          knows about Hermes + pi-coder entry points
```

The invariant the design enforces: **hal0-api's cron ticker writes only to kanban.db.** It never invokes an agent. Agent dispatch is a separate consumer of the kanban queue.

### Why split ticker from dispatcher

A combined ticker (cron + dispatch in one loop) couples scheduling to agent availability. If Hermes is down, a combined ticker either loses fires or queues indefinitely. Splitting them gives:

- The ticker is durable and crash-safe regardless of agent state. It only ever needs kanban.db.
- The dispatcher is agent-aware and can be restarted, scaled, or replaced without touching the scheduler.
- A schedule with `agent_target='hermes'` can still fire while Hermes is down — its child task lands `ready`, sits there until Hermes comes back, then gets claimed. The schedule never misses a beat.

### Component ownership

| Component | Lives in | Reads | Writes |
|-----------|----------|-------|--------|
| Cron ticker | `hal0-api` | `kanban.db` | `kanban.db` |
| Cron UI / REST | `hal0-api` | `kanban.db` | `kanban.db` |
| Dispatcher (Hermes target) | new `hal0-dispatcher` worker (or inside `hal0-api`) | kanban via notify sub | kanban via REST |
| Dispatcher (pi-coder target) | same | kanban via notify sub | kanban via REST |
| `hermes cron` CLI | hermes venv | kanban.db (replaces jobs.json read) | kanban.db |
| `pi cron` CLI | pi-coder venv | kanban.db | kanban.db |
| `hal0 cron` CLI | hal0 venv | kanban.db | kanban.db |

The `hal0-agent@hermes.service` unit already runs Hermes; the existing ticker in `cron/scheduler.py` is removed (the gateway no longer runs cron ticks). The ticker is replaced by the hal0-api ticker, which is fully decoupled from any agent process.

## Storage — kanban schema additions

### `tasks` table additions

```sql
ALTER TABLE tasks ADD COLUMN cron_expr          TEXT;     -- 5-field cron; NULL = one-shot / no recurrence
ALTER TABLE tasks ADD COLUMN agent_target       TEXT;     -- 'hermes' | 'pi-coder'; required when cron_expr IS NOT NULL
ALTER TABLE tasks ADD COLUMN template_id        TEXT;     -- FK -> job_templates.id; NULL = use body as raw prompt
ALTER TABLE tasks ADD COLUMN next_fire_at       TEXT;     -- ISO timestamp, indexed
ALTER TABLE tasks ADD COLUMN deliver_target     TEXT;     -- 'origin' | 'local' | 'telegram' | ...
ALTER TABLE tasks ADD COLUMN last_fired_at      TEXT;
ALTER TABLE tasks ADD COLUMN last_fire_status   TEXT;     -- 'ok' | 'failed' | 'timeout' | 'skipped'
ALTER TABLE tasks ADD COLUMN last_fire_error    TEXT;     -- truncated error message
CREATE INDEX idx_tasks_cron_due ON tasks(next_fire_at) WHERE cron_expr IS NOT NULL AND status = 'scheduled';
```

### New `job_templates` table

```sql
CREATE TABLE job_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    agent_target    TEXT NOT NULL,            -- default agent when no schedule override
    prompt          TEXT NOT NULL,
    skills_json     TEXT,                     -- JSON array of skill names
    model_id        TEXT,                     -- slot/model id (nullable -> agent default)
    toolsets_json   TEXT,                     -- JSON array of toolset names
    deliver_target  TEXT,                     -- default delivery target
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
```

### Existing machinery reused as-is

- `task_links` (parent → child) — schedule is parent; child per fire.
- `task_runs` (idempotency_key UNIQUE) — keyed by `f"{schedule_id}|{scheduled_fire_at}"`. Stops double-fires on crash-recovery.
- `task_events` — automatic fan-out for UI (status changes, comments, attachments).
- `kanban_notify_subs` — dispatcher subscribes to ready children of schedules it owns.
- `task_attachments`, `task_comments` — free for runs.

## Ticker — single service in hal0-api

Lives inside hal0-api as one asyncio task.

```
async def cron_ticker_loop(stop: asyncio.Event):
    interval = 30  # seconds; configurable via [cron] in hal0.toml
    while not stop.is_set():
        try:
            await _tick_once()
        except Exception:
            logger.exception("cron tick failed; will retry next interval")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass

async def _tick_once():
    now = utcnow()
    due = await db.fetch_all(
        "SELECT id, cron_expr, agent_target, template_id, deliver_target "
        "FROM tasks "
        "WHERE cron_expr IS NOT NULL "
        "  AND status = 'scheduled' "
        "  AND next_fire_at <= ? "
        "ORDER BY next_fire_at LIMIT 50",  # backpressure cap
        (now.isoformat(),),
    )
    for row in due:
        scheduled_at = _compute_scheduled_fire_at(row, now)
        idem = f"{row.id}|{scheduled_at.isoformat()}"
        try:
            await db.execute(
                "INSERT INTO task_runs(task_id, idempotency_key, status, started_at) "
                "VALUES (?, ?, 'claimed', ?)",
                (row.id, idem, now.isoformat()),
            )
        except IntegrityError:
            continue  # already ran; skip
        await _create_child_task(row, scheduled_at)
        await _advance_schedule(row, scheduled_at)
```

### Crash safety

- `task_runs.idempotency_key` is UNIQUE. On restart, `_tick_once` re-scans due rows; rows already inserted get a conflict and skip cleanly. No duplicate children.
- The ticker is a single asyncio task inside the hal0-api process, supervised by the same lifecycle that supervises the FastAPI app. On crash → systemd restart (`Restart=on-failure`, `RestartSec=3` per the existing hal0-api.service unit) → ticker re-runs.
- `_tick_once` runs the scan, idempotency insert, child creation, and `next_fire_at` update in a single SQLite transaction per row. Half-completed rows are impossible.

### Backpressure

Per-agent ready-child cap (default 50, configurable). If exceeded for an agent, the ticker still creates the `task_runs` row (idempotency preserved) but does NOT create the child task — instead, it writes a `task_events` row of kind `backpressure_skip` and advances `next_fire_at`. The dashboard surfaces an alert.

The dispatcher drains the backlog as it makes progress; once below cap, the next tick fires normally.

### Concurrency

- Across schedules: parallel (`asyncio.gather` over the due batch).
- Within a schedule: serialized by the UNIQUE constraint on `idempotency_key`.

## Dispatcher — agent routing

The dispatcher is a separate component running as its own asyncio task inside the hal0-api process (gated by `[cron] dispatcher_enabled`). It claims ready children whose `parent_id` references a schedule, and routes by `agent_target`.

```
async def dispatch_loop(stop):
    while not stop.is_set():
        for target in ('hermes', 'pi-coder'):
            child = await claim_next_child(target)
            if child is None: continue
            asyncio.create_task(_dispatch_one(child, target))
        await asyncio.sleep(1)

async def claim_next_child(target):
    # atomic claim: UPDATE tasks SET status='running' WHERE ... RETURNING ...
    ...

async def _dispatch_one(child, target):
    if target == 'hermes':
        result = await hermes_client.run_session(
            prompt=child.body, skills=child.skills_json,
            model=child.model_id, toolsets=child.toolsets_json,
        )
    elif target == 'pi-coder':
        result = await pi_coder_client.run(
            prompt=child.body, skills=child.skills_json,
            model=child.model_id, toolsets=child.toolsets_json,
        )
    else:
        result = DispatcherError(f"unknown agent_target {target!r}")
    await _finalize_child(child, result)
```

`hermes_client` and `pi_coder_client` are thin HTTP wrappers over each agent's existing entry point (hermes-gateway's session API; pi-coder's CLI/MCP). They return success/failure + output text + cost. Output text gets attached to the child as `task_attachments`; status transitions land in `task_events`.

### Per-run timeout

Default 1 hour, overridable per schedule via a `task_meta` field (`timeout_seconds`). Dispatcher enforces with `asyncio.wait_for`. On timeout: child → `failed`, reason `timeout`. Schedule itself unaffected — next fire still happens.

## Migration — manual command

No auto-migration on startup. Operator runs:

```
hal0 cron migrate-import [--dry-run] [--from PATH]
```

Default `--from` is `~/.hermes/cron/jobs.json`. Steps:

1. Read each row. Required fields: `name`, `schedule.expr`, `deliver`. Optional: `prompt` (default empty), `script`, `no_agent`.
2. For each row, INSERT a `tasks` row with `status='scheduled'`, `cron_expr=schedule.expr`, `agent_target='hermes'` (legacy default), `deliver_target=deliver`, `body=prompt`.
3. If `--dry-run`: print the rows that would be created; do not write.
4. Without `--dry-run`: write the rows; on success, rename the source file to `jobs.json.imported-<unix-ts>` (NOT delete).
5. Idempotency: skip any row whose `name` already exists as a scheduled task in kanban.

After migration, the operator can:

- Verify schedules appear in `/automations`.
- Run `hal0 cron list` and compare against the legacy `hermes cron list`.
- Delete the renamed `jobs.json.imported-<ts>` file when satisfied.

The legacy `cron/scheduler.py` ticker in the Hermes venv is removed in a follow-up release after the kanban ticker has proven itself. During the transition window, the gateway no longer starts its ticker (no `cron` feature flag in hermes config).

## CLI surfaces

### `hermes cron …` (rewired)

Same command surface as today. Internals: read/write kanban.db instead of jobs.json. Existing scripts keep working.

### `pi cron …` (new)

```
pi cron list [--agent hermes|pi-coder] [--status scheduled|paused|...]
pi cron create <schedule> [prompt] [--agent hermes|pi-coder] [--template NAME] [--deliver TARGET] [--name NAME]
pi cron edit <task-id> [--schedule EXPR] [--agent TARGET] [--pause] [--resume]
pi cron pause <task-id>
pi cron resume <task-id>
pi cron run <task-id>            # fires immediately (creates one child)
pi cron remove <task-id>
pi cron status
```

Thin wrapper over the same kanban DB and REST endpoints that `hal0 cron` uses.

### `hal0 cron …` (new, canonical)

Same surface as `pi cron`. Both delegate to `hal0-api`'s `/api/cron/*` endpoints. `hal0 cron list` shows every scheduled task across the platform; `hal0 cron list --agent hermes` filters.

### `hal0 templates …` (new)

```
hal0 templates list
hal0 templates show <name>
hal0 templates create <name> [--agent TARGET] [--prompt-file PATH] [--skill SKILLS] [--model ID] [--toolset TOOLSETS] [--deliver TARGET]
hal0 templates edit <name> ...
hal0 templates remove <name>
```

### `hal0 cron migrate-import` (new, one-shot)

As described in Migration.

## REST endpoints

`hal0-api` exposes:

- `GET    /api/cron/jobs` — list schedules with `next_fire_at`, `last_fire_status`, agent target, template ref. Filterable by `agent`, `status`.
- `POST   /api/cron/jobs` — create schedule (body: `{cron_expr, agent_target, template_id?, deliver_target?, body?, name?, skills?, model_id?, toolsets?, parent_id?}`).
- `GET    /api/cron/jobs/{id}` — show schedule + last N child runs inline.
- `PATCH  /api/cron/jobs/{id}` — edit any field; recompute `next_fire_at` if `cron_expr` changes.
- `POST   /api/cron/jobs/{id}/pause` — `status='blocked'`.
- `POST   /api/cron/jobs/{id}/resume` — `status='scheduled'`; recompute `next_fire_at`.
- `POST   /api/cron/jobs/{id}/run` — force immediate child creation (idempotency_key uses `now()`, not the scheduled time).
- `DELETE /api/cron/jobs/{id}` — blocked when active children exist; 409 otherwise.
- `GET    /api/cron/jobs/{id}/runs` — paginated child task list.
- `GET    /api/cron/templates` — list templates.
- `POST   /api/cron/templates` — create.
- `PATCH  /api/cron/templates/{id}` — edit.
- `DELETE /api/cron/templates/{id}` — blocked when in use; 409 otherwise.
- `POST   /api/cron/migrate-import` — body `{from_path?, dry_run?}`. Reads jobs.json, creates scheduled tasks.

`hermes cron` and `pi cron` CLIs are thin HTTP clients over these.

## Dashboard UX

- Existing operator-board: no lane changes. Scheduled tasks (where `cron_expr IS NOT NULL`) appear in the Scheduled lane; existing filters continue to work.
- New **Automations** view at `/automations` (or a top-nav tab): grid of schedules. Columns: name, agent, cron expression, next fire, last fire status (colored dot), last 5 child runs (mini-list with timestamps + status pills). Click → opens the schedule task detail; child runs render inline below the schedule body via existing parent/child drill-down.
- A new filter chip `Type: cron | once | all` on the operator board.
- A new badge on the scheduled lane rows showing `agent_target` (color-coded chip).
- The dashboard's existing `/api/board/*` endpoints get new optional query params: `?has_cron=true` and `?agent_target=hermes|pi-coder`.

## Failure modes

- **Ticker crash mid-fire.** Idempotency_key conflict skips the row cleanly on restart. No duplicate children.
- **Agent never responds.** Dispatcher applies timeout (default 1h). Child → `failed` with reason `timeout`. Schedule unaffected; next fire still happens.
- **Schedule disabled while a child is running.** Allowed: child finishes normally; no new children spawn until re-enabled.
- **Agent permanently down.** Children accumulate in `ready`. Dispatcher's per-agent backpressure cap (50) trips; ticker stops creating new children and writes `backpressure_skip` events. Dashboard alerts.
- **Destructive actions in scheduled prompts.** Not guarded in this design (per decision 8). Documented in the README as a known risk.
- **Concurrent ticker instances.** Single-instance assumption. If two hal0-api processes run, the UNIQUE constraint on `task_runs.idempotency_key` stops double-firing. Documented.
- **jobs.json edited out-of-band after migration.** Out of scope. Renamed file is read-only history.

## Configuration (hal0.toml)

```toml
[cron]
enabled = true                  # ticker + dispatcher both gated by this
tick_interval_seconds = 30
backpressure_cap_per_agent = 50
default_run_timeout_seconds = 3600
dispatcher_enabled = true       # if false, only the ticker runs; children pile up in 'ready' until a human claims them
```

If `enabled=false`, the ticker and dispatcher asyncio tasks are not started; everything else (CLI, REST, dashboard) still works. If `dispatcher_enabled=false` but `enabled=true`, only the ticker runs; children land in `ready` and wait for human claim via the existing operator-board actions.

## Testing plan

- **Unit**
  - 5-field cron parser produces correct `next_fire_at` for DST boundaries, leap seconds, and end-of-month.
  - Idempotency conflict handling (`IntegrityError` → skip).
  - Template resolution (template_id present, missing, NULL).
  - Backpressure cap trips at the boundary.
- **Integration**
  - Ticker fires a schedule → child task created with correct parent link → dispatcher claims → status transitions through `running` → `done`/`failed` → events fan out via `task_events`.
  - Repeat with both Hermes and pi-coder mock adapters.
  - Pause/resume recomputes `next_fire_at` correctly.
- **Migration**
  - jobs.json with N rows → `hal0 cron migrate-import --dry-run` → no DB writes; printed rows match expected.
  - jobs.json with N rows → `hal0 cron migrate-import` → DB has N scheduled task rows; jobs.json renamed.
  - Re-running migrate-import is a no-op (idempotent).
- **Failure**
  - Kill ticker mid-fire → restart → no duplicate children, no missed fires (idempotency keys dedupe).
  - Dispatcher times out → child → `failed` with reason `timeout`.
  - Backpressure cap trips when dispatcher is down.
- **Dashboard**
  - `/automations` renders schedule grid with last 5 runs inline.
  - Operator-board filter chip `Type: cron | once` narrows the Scheduled lane.
  - Per-row `agent_target` badge renders correctly.

## Open questions / risks

1. **Where the dispatcher lives.** This spec assumes it's inside hal0-api. Alternative: separate `hal0-dispatcher` systemd unit. The trade-off is process isolation vs. operational simplicity. Default: inside hal0-api, gated by `[cron] dispatcher_enabled = true`. Revisit if a dispatcher crash should not bring down the cron ticker.
2. **Agent entry points not yet verified.** The Hermes entry point is "session via hermes-gateway" (TBD at implementation time — verify the actual API). pi-coder's entry point is via the MCP adapter shim (verify).
3. **Cron expression parsing.** `croniter` is already in the hermes venv (`/var/lib/hal0/venvs/hermes/lib/python3.12/site-packages/croniter`). hal0-api's venv will need it too — bundle as a dep.
4. **Concurrent hal0-api.** The single-instance assumption is explicit. Document in the installer README. If HA is ever required, the design supports it via the idempotency_key UNIQUE constraint, but no leader election is built.
5. **Migration of existing per-profile cron dirs.** Each Hermes profile has its own `cron/` directory (`~/.hermes/profiles/{coder,homelab,research,hal0-brain}/cron/`). The default `migrate-import` only reads the active profile's cron. A `--all-profiles` flag is a follow-up.

## Rollout

1. Schema migration (additive columns + new `job_templates` table) lands behind `[cron] enabled = false` default.
2. CLI surfaces (`hal0 cron`, `pi cron`, `hermes cron` shim) ship but read from jobs.json unchanged until the operator flips the flag.
3. `hal0 cron migrate-import --dry-run` ships.
4. Operator runs migrate-import; verifies schedules via `hal0 cron list`; flips `[cron] enabled = true`.
5. Ticker starts. Old Hermes ticker (`cron/scheduler.py` in hermes venv) is removed in a follow-up release after one full month of stable kanban-driven cron.