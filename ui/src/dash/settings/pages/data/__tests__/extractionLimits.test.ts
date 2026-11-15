// #1834 — the pure half of the extraction-limits panel.

import { describe, expect, it } from 'vitest'
import {
  EXTRACTION_LIMIT_FIELDS,
  limitsFormAllValid,
  limitsFormDirty,
  limitsFormFromStatus,
  limitsFormValidity,
  limitsPutBody,
  limitsSupported,
  reservationsSupported,
  workerSlotPool,
} from '../extractionLimits.js'

const STATUS = {
  extraction_limits: { max_concurrent: 1, max_tokens: 4096, llm_retries: 1, task_retries: 2, retry_backoff_s: 120, worker_slots: 4 },
}

describe('extraction limits form', () => {
  it('mirrors the schema bounds and defaults', () => {
    const byKey = Object.fromEntries(EXTRACTION_LIMIT_FIELDS.map((f: any) => [f.key, f]))
    expect(byKey.max_concurrent).toMatchObject({ min: 1, max: 8, fallback: 1, putKey: 'extraction_max_concurrent' })
    expect(byKey.max_tokens).toMatchObject({ min: 3072, max: 32768, fallback: 4096 })
    expect(byKey.llm_retries).toMatchObject({ min: 0, max: 5, fallback: 1 })
    expect(byKey.task_retries).toMatchObject({ min: 0, max: 10, fallback: 2 })
    expect(byKey.retry_backoff_s).toMatchObject({ min: 10, max: 3600, fallback: 120 })
    expect(byKey.worker_slots).toMatchObject({ min: 3, max: 32, fallback: 4, putKey: 'extraction_worker_slots' })
  })

  it('seeds the form from status, falling back to defaults for an older API', () => {
    expect(limitsFormFromStatus(STATUS)).toEqual({
      max_concurrent: '1', max_tokens: '4096', llm_retries: '1', task_retries: '2', retry_backoff_s: '120', worker_slots: '4',
    })
    expect(limitsFormFromStatus({})).toEqual({
      max_concurrent: '1', max_tokens: '4096', llm_retries: '1', task_retries: '2', retry_backoff_s: '120', worker_slots: '4',
    })
    expect(limitsFormFromStatus(undefined).max_tokens).toBe('4096')
  })

  it('validates each field against its bounds', () => {
    const form = { ...limitsFormFromStatus(STATUS), max_tokens: '1000', llm_retries: 'x', max_concurrent: '9' }
    expect(limitsFormValidity(form)).toMatchObject({ max_tokens: false, llm_retries: false, max_concurrent: false, task_retries: true })
    expect(limitsFormAllValid(form)).toBe(false)
    expect(limitsFormAllValid(limitsFormFromStatus(STATUS))).toBe(true)
  })

  it('sends only the limits that changed, under their PUT keys', () => {
    const form = { ...limitsFormFromStatus(STATUS), max_concurrent: '2', retry_backoff_s: '120' }
    expect(limitsPutBody(form, STATUS)).toEqual({ extraction_max_concurrent: 2 })
    expect(limitsFormDirty(form, STATUS)).toBe(true)
    expect(limitsFormDirty(limitsFormFromStatus(STATUS), STATUS)).toBe(false)
  })

  it('reports an older API without the limits as unsupported and never sends keys to it', () => {
    expect(limitsSupported(STATUS)).toBe(true)
    expect(limitsSupported({})).toBe(false)
    expect(limitsSupported(undefined)).toBe(false)
    // Untouched defaults are not a change, and even an edit sends nothing:
    // that server would ignore the keys and the panel would lie about a save.
    expect(limitsFormDirty(limitsFormFromStatus({}), {})).toBe(false)
    expect(limitsPutBody({ ...limitsFormFromStatus({}), max_tokens: '8192' }, {})).toEqual({})
  })

  it('leaves the reservations off the form for a server that does not report them', () => {
    const form = limitsFormFromStatus(STATUS)
    expect(reservationsSupported(STATUS)).toBe(false)
    expect(form).not.toHaveProperty('retain_reserved_slots')
    expect(workerSlotPool(form)).toBeNull()
    // 3 slots would leave no shared slot under the default reservations, but
    // this server has none to judge: the save stays allowed, the keys unsent.
    const three = { ...form, worker_slots: '3' }
    expect(limitsFormAllValid(three)).toBe(true)
    expect(limitsPutBody(three, STATUS)).toEqual({ extraction_worker_slots: 3 })
  })

  it('treats an unparseable entry as not-a-change (validity blocks the save instead)', () => {
    const form = { ...limitsFormFromStatus(STATUS), max_tokens: '' }
    expect(limitsPutBody(form, STATUS)).toEqual({})
  })
})

// #3700 — worker-slot reservations.
const RESERVED = {
  extraction_limits: {
    ...STATUS.extraction_limits,
    retain_reserved_slots: 1,
    consolidation_reserved_slots: 2,
    refresh_reserved_slots: 0,
  },
}

describe('worker-slot reservations', () => {
  it('mirrors the schema bounds and defaults', () => {
    const byKey = Object.fromEntries(EXTRACTION_LIMIT_FIELDS.map((f: any) => [f.key, f]))
    expect(byKey.retain_reserved_slots).toMatchObject({ min: 0, max: 31, fallback: 1, putKey: 'retain_reserved_slots', group: 'slots' })
    expect(byKey.consolidation_reserved_slots).toMatchObject({ min: 0, max: 31, fallback: 2, putKey: 'consolidation_reserved_slots' })
    expect(byKey.refresh_reserved_slots).toMatchObject({ min: 0, max: 31, fallback: 0, putKey: 'refresh_reserved_slots' })
    expect(byKey.worker_slots.group).toBe('slots')
  })

  it('seeds the reservations and reports the shared pool they leave', () => {
    expect(reservationsSupported(RESERVED)).toBe(true)
    const form = limitsFormFromStatus(RESERVED)
    expect(form).toMatchObject({ worker_slots: '4', retain_reserved_slots: '1', consolidation_reserved_slots: '2', refresh_reserved_slots: '0' })
    expect(workerSlotPool(form)).toMatchObject({ total: 4, reserved: 3, shared: 1, ok: true })
    expect(workerSlotPool(form)!.message).toBe('1 shared slot left for everything else (3 of 4 reserved)')
    expect(workerSlotPool({ ...form, worker_slots: '8' })!.message).toContain('5 shared slots')
  })

  it('refuses reservations that leave no shared slot, as the server does', () => {
    const form = { ...limitsFormFromStatus(RESERVED), worker_slots: '3' }
    const pool = workerSlotPool(form)!
    expect(pool).toMatchObject({ shared: 0, ok: false })
    expect(pool.message).toContain('take 3 of 3 slots')
    expect(pool.message).toContain('reserve at most 2')
    // Each number is in range on its own; only the pool rule blocks the save.
    expect(Object.values(limitsFormValidity(form)).every(Boolean)).toBe(true)
    expect(limitsFormAllValid(form)).toBe(false)
    expect(limitsFormAllValid({ ...form, retain_reserved_slots: '0' })).toBe(true)
  })

  it('leaves the pool to the per-field check while a number does not parse', () => {
    const form = { ...limitsFormFromStatus(RESERVED), refresh_reserved_slots: '' }
    expect(workerSlotPool(form)).toBeNull()
    expect(limitsFormAllValid(form)).toBe(false)
  })

  it('sends only the reservations that changed, under their PUT keys', () => {
    const form = { ...limitsFormFromStatus(RESERVED), worker_slots: '6', refresh_reserved_slots: '1' }
    expect(limitsPutBody(form, RESERVED)).toEqual({ extraction_worker_slots: 6, refresh_reserved_slots: 1 })
  })
})
