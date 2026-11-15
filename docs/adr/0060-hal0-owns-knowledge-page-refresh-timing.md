# ADR-0060: hal0 owns when knowledge pages refresh and when the engine consolidates

## Status

**ACCEPTED (2026-11-15).** Adds to ADR-0058 (memory v3: one surface, the
two-bank layout). ADR-0058 decision 4b (the compat endpoint passes a page
writer's refresh settings through) is amended for the three timing fields
below; the scope fields hal0 already owned stay as they are.

## Context

Hindsight 1.2 refreshes a mental model, and so a knowledge page, on the
model's own `trigger` (`hindsight_api/engine/response_models.py`,
`MentalModelTrigger`):

- `refresh_after_consolidation`: refresh after every consolidation round
  that touched the model's scope;
- `refresh_cron`: a five-field UTC cron, run by the engine's maintenance loop
  (`engine/maintenance.py`, `_run_scheduled_mm_refresh`); a tick is skipped
  when the model is not stale. The engine refuses it together with
  `refresh_after_consolidation`;
- `min_refresh_interval_seconds`: the floor between two automatic refreshes;
  `null` falls back to the bank's `mental_model_min_refresh_interval_seconds`.
  A refresh asked for through the API ignores it.

Every writer chose these for itself. The packaged spec seeded
`refresh_after_consolidation: true` with a 30-minute floor, the coding-agents
plugin sends `refresh_after_consolidation: true` for every page it seeds, and
compat passed both through. On a live box the engine queued a refresh of
about a hundred pages after each consolidation round. Each refresh is a
multi-call reflect run, minutes of the memory model's time, and with two LLM
permits the refreshes starved retains. What the box needed was rare,
scheduled refreshes.

## Decision

1. **`[memory.refresh]` is the one source of the three timing fields.**
   `pages` (`scheduled` | `after_consolidation` | `manual`, default
   `scheduled`), `schedule` (UTC cron, default `0 3 * * *`) and
   `min_interval_s` (default 86400) map to `refresh_after_consolidation`,
   `refresh_cron` and `min_refresh_interval_seconds`
   (`src/hal0/memory/refresh_policy.py`, `desired_trigger`). `full_refresh_cron`
   is left to the page: it is an independent schedule and no writer hal0
   knows sets it.

2. **Every hal0 write path stamps them, whatever the writer sent.** Compat
   page create and node update, the admin passthrough's mental-model create
   and update and page create, the provider's `create_mental_model` and
   `update_mental_model` (the MCP tools), and `hal0 memory spec apply`. The
   writer keeps `budget`, `mode`, `fact_types`, `tags_match`. Compat and the
   admin passthrough name a replaced field in `X-hal0-Ignored-Fields`
   (`trigger.<field>`), only when the writer's value differed.

3. **A reconciler brings the engine to the policy.** It lists every mental
   model of this box's banks (the platform bank, and the private bank on a
   two-bank store) and PATCHes only those whose timing fields differ,
   sending only those fields: the engine merges a PATCHed trigger over the
   stored one, so scope and everything else stay. Then it sets each bank's
   floor and the timing fields of its `knowledge_page_default_trigger`. It
   is idempotent, pages the list, reports per page, never creates a bank,
   writes nothing while the memory write gate is set, and never raises into
   hal0-api. hal0-api runs it once the engine answers after boot, every six
   hours, and when a `memory.refresh.*` setting is saved;
   `hal0 memory refresh-policy apply` runs it by hand.

4. **Consolidation can be scheduled.** `consolidation` (`continuous` |
   `scheduled`, default `continuous`) and `consolidation_schedule` (default
   `0 2 * * *`). Scheduled sets `enable_auto_consolidation = false` on the
   banks and hal0-api starts one `POST .../consolidate` per bank when the
   cron comes due (`src/hal0/memory/refresh_runner.py`). Continuous clears
   the bank override with JSON null, the engine's "server default"
   tombstone, rather than setting `true`: a bank override of `true` would
   beat the engine-wide `false` that the maintenance isolation drop-in
   (`hal0.memory.engine_maintenance`) sets during a fold-in or an embedder
   switch. The last run is kept on disk, so a run missed while hal0-api was
   down runs once at the next start; switching to scheduled starts the clock
   then and runs nothing at once.

5. **hal0 parses cron itself.** hal0 has no cron dependency; a small parser
   (`src/hal0/config/cron.py`) reads the subset hal0 writes and croniter
   reads the same way: numbers, `*`, ranges, steps and lists, no names and no
   `@` shorthands. The settings schema validates both schedules with it.

## Consequences

- The default changes: pages refresh nightly instead of after every
  consolidation. A box that wants the old behaviour sets
  `pages = "after_consolidation"` and a smaller `min_interval_s`.
- The first reconcile on a box whose pages were stale schedules them for the
  next cron tick, so the first night after upgrading refreshes every stale
  page once.
- The bank-level floor the Knowledge tab and `hal0 memory bank profile set
  --min-refresh-interval` set no longer decides anything for a page hal0
  wrote (each page carries its own floor), and the reconciler resets it to
  `min_interval_s`.
- A page written straight to the engine, past hal0, keeps its own timing
  until the next reconcile, at most six hours.
- Which slot runs refreshes stays `[memory.lanes.mental_model_refresh]
  virtual_model`.
