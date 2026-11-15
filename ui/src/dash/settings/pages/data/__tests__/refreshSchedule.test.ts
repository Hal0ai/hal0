// [memory.refresh] (ADR-0060): the cron reader must agree with the server's
// (src/hal0/config/cron.py, tests/memory/test_refresh_policy.py), and the presets must
// round-trip through UTC in any zone.
import { describe, expect, it } from 'vitest'

import {
  REFRESH_DEFAULTS,
  cronError,
  cronFromPreset,
  formFromLive,
  formValid,
  formValidity,
  nextFire,
  offsetLabel,
  presetFromCron,
  presetWords,
  refreshPutBody,
  saveOutcome,
} from '../refreshSchedule'

const NOW = new Date(Date.UTC(2026, 10, 15, 14, 7)) // a Sunday
const utc = (y: number, mo: number, d: number, h: number, mi: number) => new Date(Date.UTC(y, mo - 1, d, h, mi))

describe('nextFire (same answers as the server)', () => {
  it.each([
    ['0 3 * * *', utc(2026, 11, 16, 3, 0)],
    ['*/15 * * * *', utc(2026, 11, 15, 14, 15)],
    ['0 */6 * * *', utc(2026, 11, 15, 18, 0)],
    ['30 4 * * 1', utc(2026, 11, 16, 4, 30)],
    ['0 4 * * 7', utc(2026, 11, 22, 4, 0)],
    ['0 0 1 1 *', utc(2027, 1, 1, 0, 0)],
    ['0 9 13 * 5', utc(2026, 11, 20, 9, 0)],
    ['5,35 1-2 * * *', utc(2026, 11, 16, 1, 5)],
  ])('%s', (expr, want) => {
    expect(nextFire(expr, NOW)?.toISOString()).toBe(want.toISOString())
  })

  it('is strictly after the given minute', () => {
    expect(nextFire('0 3 * * *', utc(2026, 11, 15, 3, 0))?.toISOString()).toBe(utc(2026, 11, 16, 3, 0).toISOString())
  })

  it('is null for an invalid expression', () => {
    expect(nextFire('nope', NOW)).toBeNull()
  })
})

describe('cronError', () => {
  it.each(['', '* * * *', 'a * * * *', '*/0 * * * *', '5-1 * * * *', '0 24 * * *', '0 0 * * 8', '0 0 31 2 *', '@daily'])(
    'refuses %j',
    (bad) => {
      expect(cronError(bad)).toEqual(expect.any(String))
    },
  )

  it('accepts what the presets write', () => {
    for (const ok of ['0 3 * * *', '15 */6 * * *', '30 22 * * 6', '0 0 1,15 * *']) expect(cronError(ok)).toBeNull()
  })
})

describe('presets in local time', () => {
  it('reads the default schedule as daily, in the viewer zone', () => {
    expect(presetFromCron('0 3 * * *', 0)).toEqual({ kind: 'daily', time: '03:00' })
    expect(presetFromCron('0 3 * * *', 120)).toEqual({ kind: 'daily', time: '05:00' })
    expect(presetFromCron('0 3 * * *', -300)).toEqual({ kind: 'daily', time: '22:00' })
  })

  it('moves the weekday when local time crosses midnight', () => {
    // 02:00 UTC Monday is 21:00 Sunday at UTC-5.
    expect(presetFromCron('0 2 * * 1', -300)).toEqual({ kind: 'weekly', day: 0, time: '21:00' })
    expect(cronFromPreset({ kind: 'weekly', day: 0, time: '21:00' }, -300)).toBe('0 2 * * 1')
    // 23:30 UTC Saturday is 05:00 Sunday at UTC+5:30.
    expect(presetFromCron('30 23 * * 6', 330)).toEqual({ kind: 'weekly', day: 0, time: '05:00' })
    expect(cronFromPreset({ kind: 'weekly', day: 0, time: '05:00' }, 330)).toBe('30 23 * * 6')
  })

  it('reads every N hours, and moves the minute in a half-hour zone', () => {
    expect(presetFromCron('0 */6 * * *', 120)).toEqual({ kind: 'hours', every: 6, minute: 0 })
    expect(presetFromCron('0 */4 * * *', -330)).toEqual({ kind: 'hours', every: 4, minute: 30 })
    expect(cronFromPreset({ kind: 'hours', every: 4, minute: 30 }, -330)).toBe('0 */4 * * *')
  })

  it('calls anything else custom', () => {
    for (const expr of ['0 3 1 * *', '0 1,13 * * *', '0 */5 * * *', '*/10 * * * *', 'garbage']) {
      expect(presetFromCron(expr, 0).kind).toBe('custom')
    }
  })

  it.each([0, 60, -300, 330, 345, -570, 840])('round-trips daily and weekly presets at offset %i', (offset) => {
    for (const expr of ['0 3 * * *', '45 23 * * *', '0 0 * * *', '15 12 * * 3', '0 0 * * 0']) {
      const preset = presetFromCron(expr, offset)
      expect(preset.kind).not.toBe('custom')
      expect(cronFromPreset(preset, offset)).toBe(expr)
    }
  })

  it('refuses a bad time or a bad custom cron', () => {
    expect(cronFromPreset({ kind: 'daily', time: '25:00' }, 0)).toBeNull()
    expect(cronFromPreset({ kind: 'custom', cron: '0 3 * *' }, 0)).toBeNull()
    expect(cronFromPreset({ kind: 'custom', cron: ' 0  3 * * * ' }, 0)).toBe('0 3 * * *')
  })

  it('says a preset in words', () => {
    expect(presetWords({ kind: 'daily', time: '03:00' })).toBe('every day at 03:00')
    expect(presetWords({ kind: 'weekly', day: 1, time: '04:30' })).toBe('every Monday at 04:30')
    expect(presetWords({ kind: 'hours', every: 6, minute: 0 })).toBe('every 6 hours, on the hour')
    expect(presetWords({ kind: 'hours', every: 1, minute: 5 })).toBe('every hour, at 05 past')
    expect(presetWords({ kind: 'custom', cron: '0 3 1 * *' })).toBe('on the schedule 0 3 1 * * (UTC)')
  })

  it('labels an offset', () => {
    expect(offsetLabel(0)).toBe('UTC')
    expect(offsetLabel(120)).toBe('UTC+2')
    expect(offsetLabel(-330)).toBe('UTC-5:30')
  })
})

describe('the form', () => {
  it('starts from the server defaults when the config says nothing', () => {
    expect(formFromLive(null)).toEqual({
      pages: 'scheduled',
      schedule: '0 3 * * *',
      minIntervalHours: '24',
      consolidation: 'continuous',
      consolidationSchedule: '0 2 * * *',
    })
    expect(refreshPutBody(formFromLive(null), null)).toBeNull()
  })

  it('sends only what changed', () => {
    const live = { ...REFRESH_DEFAULTS }
    const form = { ...formFromLive(live), pages: 'manual' as const, minIntervalHours: '6' }
    expect(refreshPutBody(form, live)).toEqual({ memory: { refresh: { pages: 'manual', min_interval_s: 21600 } } })
  })

  it('checks a schedule only when its mode uses it', () => {
    const form = { ...formFromLive(null), pages: 'manual' as const, schedule: 'bad' }
    expect(formValidity(form).schedule).toBeNull()
    expect(formValid(form)).toBe(true)
    // ... and does not send the bad value.
    expect(refreshPutBody(form, null)).toEqual({ memory: { refresh: { pages: 'manual' } } })
    expect(formValid({ ...form, pages: 'scheduled' })).toBe(false)
    expect(formValid({ ...formFromLive(null), consolidation: 'scheduled', consolidationSchedule: 'x' })).toBe(false)
  })

  it('bounds the interval to 0..720 hours', () => {
    expect(formValidity({ ...formFromLive(null), minIntervalHours: '721' }).minInterval).not.toBeNull()
    expect(formValidity({ ...formFromLive(null), minIntervalHours: '-1' }).minInterval).not.toBeNull()
    expect(formValidity({ ...formFromLive(null), minIntervalHours: '0' }).minInterval).toBeNull()
    expect(formValidity({ ...formFromLive(null), minIntervalHours: '0.5' }).minInterval).toBeNull()
  })

  it('tells the operator when the reconcile could not be queued', () => {
    expect(saveOutcome({ _hal0: { refresh_policy: { reconcile: 'queued' } } }).kind).toBe('ok')
    const warn = saveOutcome({ _hal0: { refresh_policy: { reconcile: 'unavailable' } } })
    expect(warn.kind).toBe('warn')
    expect(warn.text).toContain('hal0 memory refresh-policy apply')
  })
})
