import { describe, expect, it } from 'vitest'
import { ageLabel, dutyCells, fmtMs, fmtRate, niceMax, stackSeries, windowTicks } from '../activity-chart-pure'

describe('niceMax', () => {
  it('pads the peak and rounds to a step', () => {
    expect(niceMax(0)).toBe(10)
    expect(niceMax(4)).toBe(5) // 4.6 → 5
    expect(niceMax(24)).toBe(30) // 27.6 → 30
    expect(niceMax(60)).toBe(70) // 69 → 70
  })
})

describe('stackSeries', () => {
  it('stacks bottom-up: the second band sits on the first', () => {
    const { paths, yMax } = stackSeries(
      [
        { key: 'a', values: [10, 0] },
        { key: 'b', values: [5, 5] },
      ],
      100,
    )
    expect(yMax).toBe(20) // 15 * 1.15 = 17.25 → 20
    expect(paths.map((p) => p.key)).toEqual(['a', 'b'])
    // a's top at x=0 is 10/20 of the height → y=50; b's top is 15/20 → y=25.
    expect(paths[0].line.startsWith('M0.0,50.0')).toBe(true)
    expect(paths[1].line.startsWith('M0.0,25.0')).toBe(true)
    // b's area closes back along a's top.
    expect(paths[1].area).toContain('L0.0,50.0 Z')
  })
  it('no series → no paths', () => {
    expect(stackSeries([], 100)).toEqual({ paths: [], yMax: 10 })
  })
})

describe('labels', () => {
  it('ages', () => {
    expect(ageLabel(null)).toBe('—')
    expect(ageLabel(2)).toBe('now')
    expect(ageLabel(42)).toBe('42s')
    expect(ageLabel(250)).toBe('4m')
    expect(ageLabel(7200)).toBe('2h')
  })
  it('rates and latency', () => {
    expect(fmtRate(null)).toBe('—')
    expect(fmtRate(18.24)).toBe('18.2')
    expect(fmtMs(null)).toBe('—')
    expect(fmtMs(410.4)).toBe('410 ms')
    expect(fmtMs(1530)).toBe('1.5 s')
  })
  it('window ticks end at now', () => {
    expect(windowTicks('15m')).toEqual(['-15m', '-12m', '-9m', '-6m', '-3m', 'now'])
  })
})

describe('dutyCells', () => {
  it('null when no bucket has a reading, so the strip hides', () => {
    expect(dutyCells(undefined)).toBeNull()
    expect(dutyCells([null, null])).toBeNull()
  })
  it('maps readings to opacity and keeps gaps empty', () => {
    expect(dutyCells([null, 0, 1])).toEqual([
      { v: null, opacity: 0 },
      { v: 0, opacity: 0.1 },
      { v: 1, opacity: 1 },
    ])
  })
})
