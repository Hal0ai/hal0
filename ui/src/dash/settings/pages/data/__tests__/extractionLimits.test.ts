// #1834 — the pure half of the extraction-limits panel.

import { describe, expect, it } from 'vitest'
import {
  EXTRACTION_LIMIT_FIELDS,
  limitsFormAllValid,
  limitsFormDirty,
  limitsFormFromStatus,
  limitsFormValidity,
  limitsPutBody,
} from '../extractionLimits.js'

const STATUS = {
  extraction_limits: { max_concurrent: 1, max_tokens: 4096, llm_retries: 1, task_retries: 2, retry_backoff_s: 120 },
}

describe('extraction limits form', () => {
  it('mirrors the schema bounds and defaults', () => {
    const byKey = Object.fromEntries(EXTRACTION_LIMIT_FIELDS.map((f: any) => [f.key, f]))
    expect(byKey.max_concurrent).toMatchObject({ min: 1, max: 8, fallback: 1, putKey: 'extraction_max_concurrent' })
    expect(byKey.max_tokens).toMatchObject({ min: 3072, max: 32768, fallback: 4096 })
    expect(byKey.llm_retries).toMatchObject({ min: 0, max: 5, fallback: 1 })
    expect(byKey.task_retries).toMatchObject({ min: 0, max: 10, fallback: 2 })
    expect(byKey.retry_backoff_s).toMatchObject({ min: 10, max: 3600, fallback: 120 })
  })

  it('seeds the form from status, falling back to defaults for an older API', () => {
    expect(limitsFormFromStatus(STATUS)).toEqual({
      max_concurrent: '1', max_tokens: '4096', llm_retries: '1', task_retries: '2', retry_backoff_s: '120',
    })
    expect(limitsFormFromStatus({})).toEqual({
      max_concurrent: '1', max_tokens: '4096', llm_retries: '1', task_retries: '2', retry_backoff_s: '120',
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
    // Older API (no echo): untouched defaults are not a change; an edit is.
    expect(limitsFormDirty(limitsFormFromStatus({}), {})).toBe(false)
    expect(limitsPutBody({ ...limitsFormFromStatus({}), max_tokens: '8192' }, {})).toEqual({ extraction_max_tokens: 8192 })
  })

  it('treats an unparseable entry as not-a-change (validity blocks the save instead)', () => {
    const form = { ...limitsFormFromStatus(STATUS), max_tokens: '' }
    expect(limitsPutBody(form, STATUS)).toEqual({})
  })
})
