import { describe, expect, it } from 'vitest'
import {
  SLOT_STATE_COPY,
  SERVICE_HEALTH_COPY,
  statusCopyForSlotState,
  statusCopyForServiceState,
} from './status-copy'

// The 9 wire values of hal0.slots.state.SlotState — pinned again (in Python)
// by tests/ui_contracts/test_status_copy_mirror.py so this list can't drift
// from the backend enum silently.
const SLOT_STATES = [
  'offline',
  'pulling',
  'starting',
  'warming',
  'ready',
  'serving',
  'idle',
  'unloading',
  'error',
]

describe('SLOT_STATE_COPY', () => {
  it('covers every SlotState wire value exactly once, non-empty', () => {
    expect(Object.keys(SLOT_STATE_COPY).sort()).toEqual([...SLOT_STATES].sort())
    for (const state of SLOT_STATES) {
      expect(SLOT_STATE_COPY[state as keyof typeof SLOT_STATE_COPY].length).toBeGreaterThan(10)
    }
  })

  it('keeps the precise word out of its own sentence (consequence-first, not a restatement)', () => {
    // Loose smoke check: the sentence should read as "what it means", not
    // literally start by repeating the enum word.
    for (const [state, copy] of Object.entries(SLOT_STATE_COPY)) {
      expect(copy.toLowerCase().startsWith(state)).toBe(false)
    }
  })
})

describe('SERVICE_HEALTH_COPY', () => {
  it('covers up | stopped | down exactly', () => {
    expect(Object.keys(SERVICE_HEALTH_COPY).sort()).toEqual(['down', 'stopped', 'up'])
  })
})

describe('statusCopyForSlotState / statusCopyForServiceState', () => {
  it('resolves every known word', () => {
    for (const state of SLOT_STATES) {
      expect(statusCopyForSlotState(state)).toBe(SLOT_STATE_COPY[state as keyof typeof SLOT_STATE_COPY])
    }
    expect(statusCopyForServiceState('up')).toBe(SERVICE_HEALTH_COPY.up)
  })

  it('falls back honestly on an unknown or missing word instead of throwing', () => {
    expect(statusCopyForSlotState('some-future-state')).toMatch(/unrecognised/i)
    expect(statusCopyForSlotState(null)).toMatch(/unrecognised/i)
    expect(statusCopyForServiceState(undefined)).toMatch(/unrecognised/i)
  })
})
