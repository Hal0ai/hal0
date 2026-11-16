# 0062. Re-admit a slot the admission gate refused once capacity frees

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
   For each slot marked `admission_refused`, it calls `load()` only when all
   of these hold:
   - no lifecycle operation holds the slot's lock;
   - the crash-loop breaker permits a trial (`_crash_loop_remaining_s == 0`),
     so the existing i4 backoff and park cool-downs still bound the retry
     rate;
   - `admission_would_fit()`, a read-only dry run of the admission plan
     (free memory less other reservations, plus what the eviction planner
     could free from idle, non-pinned slots), says the slot's configured
     model fits.

   A box that is still short pays nothing: no eviction, no load attempt, no
   breaker count. When the dry run and the real admission disagree, the real
   refusal counts on the breaker as before, and retries back off to the park
   ceiling.

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
- The dry run uses the same footprint estimate as the gate, so it can only be
  as accurate as that estimate. When the estimate can't be made, the gate
  fails open and so does the dry run.
