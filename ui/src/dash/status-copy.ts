// hal0 dashboard — consequence-first status copy (H4 / study 3.5-partial).
//
// Slot phase words and service health words are precise-but-internal
// vocabulary ("warming", "idle", "stopped") — accurate for an operator who
// already knows the state machine, opaque to one who doesn't. This module is
// the single owner of a one-sentence "what this means for you" line per
// word, kept SEPARATE from the vocabulary itself: callers render the precise
// word first (unchanged) and this sentence alongside it (tooltip / drawer),
// never as a replacement — ODS's STATUS_DESCRIPTIONS pattern
// (ods/extensions/services/dashboard/src/pages/Extensions.jsx:60-84) without
// its icon/color duplication, since hal0 already owns those via
// slot-status.js / services.jsx.
//
// `SLOT_STATE_COPY`'s keys are `src/hal0/slots/state.py`'s `SlotState` wire
// values exactly — pinned by `tests/ui_contracts/test_status_copy_mirror.py`
// so a new lifecycle state can't ship without also getting a sentence here.
// `SERVICE_HEALTH_COPY`'s keys are the three words
// `src/hal0/api/routes/services_health.py`'s `_owui_state`/`_hermes_state`
// helpers emit (`up` | `stopped` | `down`) — no Python enum backs those (they
// are literal strings), so there is nothing to mirror-test against; the
// vitest completeness test below is the whole contract.

export type SlotStateWord =
  | 'offline'
  | 'pulling'
  | 'starting'
  | 'warming'
  | 'ready'
  | 'serving'
  | 'idle'
  | 'unloading'
  | 'error'

export type ServiceHealthWord = 'up' | 'stopped' | 'down'

export const SLOT_STATE_COPY: Readonly<Record<SlotStateWord, string>> = {
  offline: 'Not running — no requests reach it until something starts it or a request wakes it.',
  pulling: 'Downloading its model files — not ready to serve yet.',
  starting: 'Container is booting — requests queue until it reports healthy.',
  warming: 'Loading the model into memory — the first request through this may wait longer than usual.',
  ready: 'Loaded and healthy, waiting for a request — nothing to do here.',
  serving: 'Answering a request right now.',
  idle: 'Loaded but unused recently — a candidate to unload and free its memory.',
  unloading: 'Shutting down to free its memory — requests will not reach it until it restarts.',
  error: 'Failed to load or crashed — nothing is routed here until it is fixed and restarted.',
}

export const SERVICE_HEALTH_COPY: Readonly<Record<ServiceHealthWord, string>> = {
  up: 'Reachable and answering normally.',
  stopped: 'Not running on purpose — it starts on demand when something needs it.',
  down: 'Crashed or failed — it will not come back on its own; restart it.',
}

const SLOT_STATE_FALLBACK = 'Unrecognised lifecycle state — treat as unavailable until confirmed otherwise.'
const SERVICE_HEALTH_FALLBACK = 'Unrecognised health word — treat as unavailable until confirmed otherwise.'

export function statusCopyForSlotState(state: string | null | undefined): string {
  if (state && Object.prototype.hasOwnProperty.call(SLOT_STATE_COPY, state)) {
    return SLOT_STATE_COPY[state as SlotStateWord]
  }
  return SLOT_STATE_FALLBACK
}

export function statusCopyForServiceState(state: string | null | undefined): string {
  if (state && Object.prototype.hasOwnProperty.call(SERVICE_HEALTH_COPY, state)) {
    return SERVICE_HEALTH_COPY[state as ServiceHealthWord]
  }
  return SERVICE_HEALTH_FALLBACK
}
