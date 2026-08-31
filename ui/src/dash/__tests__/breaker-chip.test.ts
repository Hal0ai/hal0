// #2038 — render the crash-loop breaker (metadata.breaker, #2012) on slot
// surfaces. The breaker view is stamped into slot metadata whenever it is
// non-closed and absent otherwise; `retry_after_s` is computed at snapshot
// time, so the classifier takes the seconds elapsed since fetch and counts
// down client-side. The classifier is pinned here rather than left to the
// JSX, same as image-status-chip.test.ts.
import { describe, expect, it } from 'vitest'

import type { Slot } from '@/api/hooks/useSlots'

import { breakerChip } from '../slot-status.js'

const BASE: Slot = {
  name: 'agent',
  type: 'llm',
  device: 'gpu-vulkan',
  model: 'chadrock-35b',
  state: 'error',
  metrics: {},
  runtime: 'container',
}

function withBreaker(breaker: unknown): Slot {
  return { ...BASE, metadata: { breaker } } as Slot
}

describe('breakerChip', () => {
  it('is null without a breaker view (breaker closed / pre-#2012 backend)', () => {
    expect(breakerChip(BASE)).toBeNull()
    expect(breakerChip(withBreaker(undefined))).toBeNull()
    expect(breakerChip({ ...BASE, metadata: {} } as Slot)).toBeNull()
  })

  it('backoff → amber retry countdown with the failure count', () => {
    const chip = breakerChip(
      withBreaker({ state: 'backoff', failures: 3, retry_after_s: 40 })
    )
    expect(chip).not.toBeNull()
    expect(chip!.tone).toBe('warn')
    expect(chip!.label).toBe('retry in 40s')
    expect(chip!.tooltip).toContain('3 failures')
  })

  it('parked → red, failures in the label, countdown in the tooltip', () => {
    const chip = breakerChip(
      withBreaker({ state: 'parked', failures: 7, retry_after_s: 512 })
    )
    expect(chip).not.toBeNull()
    expect(chip!.tone).toBe('err')
    expect(chip!.label).toBe('parked · 7 failures')
    expect(chip!.tooltip).toContain('512s')
    // the escape hatch must be named — parked is deliberate refusal, not death
    expect(chip!.tooltip.toLowerCase()).toContain('manual')
  })

  it('half-open → neutral trial-pending hint, no countdown', () => {
    const chip = breakerChip(
      withBreaker({ state: 'half-open', failures: 5, retry_after_s: 0 })
    )
    expect(chip).not.toBeNull()
    expect(chip!.tone).toBe('neutral')
    expect(chip!.label).toBe('trial pending')
    expect(chip!.tooltip.toLowerCase()).toContain('next')
  })

  it('counts down from elapsed seconds and clamps at zero', () => {
    const slot = withBreaker({ state: 'backoff', failures: 2, retry_after_s: 30 })
    expect(breakerChip(slot, 10)!.label).toBe('retry in 20s')
    expect(breakerChip(slot, 90)!.label).toBe('retry in 0s')
  })

  it('singularises a single failure', () => {
    const chip = breakerChip(
      withBreaker({ state: 'parked', failures: 1, retry_after_s: 8 })
    )
    expect(chip!.label).toBe('parked · 1 failure')
  })

  it('ignores an unknown breaker state rather than guessing a colour', () => {
    expect(
      breakerChip(withBreaker({ state: 'closed', failures: 0, retry_after_s: 0 }))
    ).toBeNull()
  })
})

// The backend stamps metadata.last_crash_line — the decisive journal line a
// failed model load died on — alongside the breaker view. The chip's tooltip
// carries it so the operator sees WHY, not just "trial pending".
describe('breakerChip crash line', () => {
  const CRASH =
    'llama_model_load: error loading model: unknown model architecture'

  function withCrash(state: string, line: unknown): Slot {
    return {
      ...BASE,
      metadata: {
        breaker: { state, failures: 2, retry_after_s: 30 },
        last_crash_line: line,
      },
    } as Slot
  }

  it('appends the decisive line to every breaker tooltip', () => {
    for (const state of ['backoff', 'parked', 'half-open']) {
      const chip = breakerChip(withCrash(state, CRASH))
      expect(chip).not.toBeNull()
      expect(chip!.tooltip).toContain(`Last crash: ${CRASH}`)
      // one line in the label area is untouched — the reason is tooltip-only
      expect(chip!.label).not.toContain('llama_model_load')
    }
  })

  it('truncates a long crash line to one readable line', () => {
    const long = 'unable to allocate ROCm0 buffer ' + 'x'.repeat(400)
    const chip = breakerChip(withCrash('parked', long))
    expect(chip).not.toBeNull()
    const suffix = chip!.tooltip.split('Last crash: ')[1]
    expect(suffix.length).toBeLessThanOrEqual(161) // 160 + ellipsis
    expect(suffix.endsWith('…')).toBe(true)
  })

  it('tolerates an absent, empty, or non-string crash line', () => {
    expect(
      breakerChip(withBreaker({ state: 'parked', failures: 2, retry_after_s: 30 }))!
        .tooltip
    ).not.toContain('Last crash')
    expect(breakerChip(withCrash('parked', '   '))!.tooltip).not.toContain(
      'Last crash'
    )
    expect(breakerChip(withCrash('parked', 42))!.tooltip).not.toContain(
      'Last crash'
    )
  })
})
