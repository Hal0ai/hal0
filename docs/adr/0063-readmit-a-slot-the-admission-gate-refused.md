# 0063. Re-admit a slot the admission gate refused once capacity frees

## Status

Accepted

## Context

The pre-load admission gate (`hal0.slots.preload_evict.admit`) refuses a load
with `PreloadEvictionFailed` when the incoming model still doesn't fit after
evicting every idle, non-pinned resident slot. `SlotManager.load` stamps the
slot ERROR and counts a crash-loop breaker failure, and then nothing retries
it:

- no unit was ever started, so the fail-watcher (which owns a wedged WARMING
  slot) never watches it;
- the breaker half-opens after its backoff, but a half-open breaker only
  *permits* a trial, it doesn't start one;
- the only automatic trigger is a request that names the slot's bound model
  id (`_ensure_backend_for_model`). Capability routing's rule-9 default lands
  on the `agent` anchor, finds no registered upstream and answers a bare
  `404 dispatch.no_route` that names the caller's model.

On a real box (#3502) the anchor stayed in ERROR for 11 hours after one
transient shortfall, and every agent-role request 404ed with an error that
read like a caller typo.

A request-driven wake (the #2666 approach for an offline anchor) does not fix
this. A wake re-runs the same admission while memory is still short, gets
refused again, and evicts an idle slot each time for nothing. The retry has
to follow a capacity change, not request arrival.

## Decision

1. **Mark the refusal.** A load that fails with `PreloadEvictionFailed`
   stamps `error_reason = "admission_refused"`
   (`hal0.slots.preload_evict.ADMISSION_REFUSED`) on the ERROR record. As
   with the other `error_reason` values, the mark belongs to that one ERROR:
   any later transition retires it, so an operator unload (ERROR to OFFLINE)
   or a successful load ends the re-admission.

2. **Re-admit on the reaper tick.** The idle monitor's loop
   (`SlotReaper._loop`, every `_idle_monitor_interval_s`) runs
   `SlotManager.readmit_refused_once()` after its idle and pressure sweeps.
   The sweep only schedules: each refused slot gets its own tracked task
   (`SlotManager._readmit_one`), at most one per slot, so a load that takes
   minutes to come up never holds the reaper's sweeps while memory pressure
   is at its highest. Stopping the idle monitor cancels those tasks. A slot
   is scheduled only when:
   - the slot is meant to be up without a request: the type's default slot
     (`default = true`, the anchor), a pinned slot (explicit `pinned = true`
     or a default-pinned anchor name) or a slot that boot-starts
     (`autoload = true`, or the absent-key default derived from a bound
     model). A plain on-demand slot keeps its mark but is left for the next
     request that names it: re-admitting it in the background would evict
     idle slots for a model nobody has asked for since the refusal;
   - no lifecycle operation holds the slot's lock, and no earlier retry of it
     is still running;
   - the crash-loop breaker permits a trial (`_crash_loop_remaining_s == 0`),
     so the existing i4 backoff and park cool-downs still bound the retry
     rate.

   The task then loads only when:
   - the GPU arbiter permits the load (`GpuArbiter.permits_background_load`):
     no switch is running, and the current mode would not refuse the slot's
     group, so an llm-group slot is never put on the GPU in image mode,
     pinned or not. The request path enforces the same contract before its
     lazy-load (`Dispatcher._guard_gpu_image_mode`); `_load` itself does
     not, so the sweep has to ask;
   - `admission_would_fit()`, a read-only dry run of the admission plan
     (free memory less other reservations, plus what the eviction planner
     could free from idle, non-pinned slots), says the model fits. The model
     probed is the one the load would admit: the configured default after
     `_resolve_servable_model`, the same substitution `_load` makes.

   The load itself re-checks, under the slot lock and before anything starts,
   that the slot is still ERROR with the `admission_refused` mark and that the
   arbiter still permits it. An operator unload anywhere in the window between
   the sweep's snapshot and the load therefore cancels the retry instead of
   starting the slot again.

   The arbiter closes the window after that check on its own side. Once the
   check passes, `_load` spends its first steps (teardown of the ERROR unit,
   `_check_load`, the admission evictions) in OFFLINE with the slot lock held.
   A switch into image mode counts any llm-group slot whose lock is held as
   on its way onto the GPU, whatever its state: it waits for it
   (`_await_loads`, bounded by `_LOAD_WAIT_S` with the existing rollback),
   then unloads it if it landed and drops it from the saved set if it did
   not. This covers an operator load in the same window too. Withdrawing
   inside the admission gate instead would not work: by then the slot has
   gone OFFLINE and lost its mark, so it would never be retried.

   A box that is still short pays nothing: no eviction, no load attempt, no
   breaker count. When the dry run and the real admission disagree, the real
   refusal counts on the breaker as before, and retries back off to the park
   ceiling. The dry run plans with estimated candidate footprints while the
   real gate re-reads free memory after each unload, so they can differ; the
   retry logs `slot.admission_readmit_plan_mismatch` with the planned
   evictions and free memory next to the gate's actual figures.

3. **Name the broken slot in the 404.** When capability routing raises
   `LegacyResolutionFailed` for a slot that is in ERROR, the
   `dispatch.no_route` response keeps its status, code and existing details
   (`model`, `path`, `legacy_error`), and also carries `slot`, `state`,
   `load_error` and, when set, `error_reason`. These are the detail keys
   `SlotLoadFailed` already uses. The message names the slot and its load
   error instead of "model not found in registry". An offline or never-loaded
   slot keeps the plain body, since #2666 owns waking those.

## Consequences

- A transient shortfall now heals by itself within one reaper tick of
  capacity freeing: an idle slot evicted by the reaper, an operator unpin or
  unload, or a smaller ctx.
- Only an admission refusal is retried this way. A crashed spawn, a failed
  output-sanity gate or a broken model keeps the current contract: an
  operator or request-driven retry, under the breaker.
- The status stays 404 rather than becoming `SlotLoadFailed`'s 502, so
  clients and the release-validation contract that key on
  `dispatch.no_route` keep working; the new keys are additive.
- Re-admission is a background action that may evict idle, non-pinned slots
  on the reaper's own initiative, exactly as the admission gate would for an
  operator load. That includes the `agent` anchor when another slot was the
  refused one. An anchor evicted this way goes OFFLINE, not ERROR, so this
  sweep does not re-admit it; waking it stays with #2666.
- The dry run uses the same footprint estimate as the gate, so it can only be
  as accurate as that estimate. When the estimate can't be made, the gate
  fails open and so does the dry run.
