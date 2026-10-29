// @vitest-environment happy-dom
// hermesRow / runRow / inProgress: the Agents panel's pure model.
import { afterEach, describe, expect, it } from 'vitest'
import { OV_HERMES, RUN } from './agents-fixtures'
import { hermesRow, inProgress, piRow, runRow } from '../agents-pure'

afterEach(() => {
  delete (window as any).__hal0DeriveCardModel
})

describe('hermesRow', () => {
  it('reads served profiles, platforms and ctx from the primary slot', () => {
    const r = hermesRow(OV_HERMES, { active: 'Hermes' } as any, { metrics: { ctx: 263 }, ctx_effective: 66000 })
    expect(r.profiles).toEqual(['default', 'coder', 'hal0-brain', 'homelab', 'research'])
    expect(r.channels).toEqual(['telegram', 'webhook', 'api'])
    expect(r.ctx).toEqual({ used: 263, max: 66000, pct: 0 })
    expect(r.version).toBe('v0.22.4')
    expect(r.pyLine).toBe('py 3.14')
    expect(r.persona).toBe('Hermes')
    expect(r.approvals).toBe('smart')
  })

  it('is down when the unit is inactive and no card model is available', () =>
    expect(hermesRow({ ...OV_HERMES, units: { agent: 'inactive' } } as any, null, null).state).toBe('down'))

  it('is ready when the unit is active and no card model is available', () =>
    expect(hermesRow(OV_HERMES, null, null).state).toBe('ready'))

  it('is serving on the fallback when the slot is producing tokens', () =>
    expect(hermesRow(OV_HERMES, null, { metrics: { toks: 12 } }).state).toBe('serving'))

  it('is unknown without an overview or a record', () => expect(hermesRow(null, null, null).state).toBe('unknown'))

  it('falls back to the record liveness when the overview is absent', () => {
    expect(hermesRow(null, null, null, { name: 'hermes', status: 'installed', unit_active: true }).state).toBe('ready')
    expect(hermesRow(null, null, null, { name: 'hermes', status: 'installed', unit_active: false }).state).toBe('down')
  })

  it('takes the state from the card model when it is published', () => {
    ;(window as any).__hal0DeriveCardModel = () => ({ phase: 'serving' })
    expect(hermesRow({ ...OV_HERMES, units: { agent: 'inactive' } } as any, null, null).state).toBe('serving')
    ;(window as any).__hal0DeriveCardModel = () => ({ phase: 'broken' })
    expect(hermesRow(OV_HERMES, null, null).state).toBe('down')
    ;(window as any).__hal0DeriveCardModel = () => ({ phase: 'degraded' })
    expect(hermesRow(OV_HERMES, null, null).state).toBe('ready')
  })

  it('names the persona by display name and has no ctx without a slot', () => {
    const r = hermesRow(
      OV_HERMES,
      { active: 'p1', personas: [{ id: 'p1', display_name: 'Pixel' }] } as any,
      null,
    )
    expect(r.persona).toBe('Pixel')
    expect(r.ctx).toBeNull()
  })

  it('has empty lists when the overview carries none', () => {
    const r = hermesRow({ name: 'hermes', kind: 'daemon', installed: true } as any, null, null)
    expect(r.profiles).toEqual([])
    expect(r.channels).toEqual([])
    expect(r.version).toBeNull()
    expect(r.pyLine).toBeNull()
  })
})

describe('piRow', () => {
  it('reads the installed version, adapter and a state word', () => {
    expect(
      piRow({ name: 'pi', installed: true, version: { installed: { pi: '0.9.1', adapter: '2' } } } as any),
    ).toEqual({ label: 'installed', version: 'v0.9.1', adapter: 'adapter 2' })
  })
  it('says not installed and unknown honestly', () => {
    expect(piRow({ name: 'pi', installed: false } as any).label).toBe('not installed')
    expect(piRow(null)).toEqual({ label: 'unknown', version: null, adapter: null })
  })
})

describe('runRow', () => {
  it('formats a running run with elapsed time', () =>
    expect(runRow({ ...RUN, status: 'running', created_at: 1000 }, 1252)).toMatchObject({
      key: 'r1',
      who: 'claude-code → hermes/coder',
      what: 'Fix the flaky test',
      time: 'running · 4m 12s',
      dot: 'serving',
    }))

  it('formats a finished run with relative time', () =>
    expect(runRow({ ...RUN, status: 'done' as any, updated_at: 1000 }, 1000 + 7200)).toMatchObject({
      time: 'done · 2 h ago',
      dot: '',
    }))

  it('treats completed like done', () =>
    expect(runRow({ ...RUN, status: 'completed', updated_at: 1000 }, 1030).time).toBe('done · just now'))

  it('formats a failed run', () =>
    expect(runRow({ ...RUN, status: 'failed', updated_at: 1000 }, 1000 + 600)).toMatchObject({
      time: 'failed · 10 min ago',
      dot: 'err',
    }))

  it('says waiting on approval when pending is set', () =>
    expect(runRow({ ...RUN, status: 'running', pending: { kind: 'approval' } }, 1100)).toMatchObject({
      time: 'waiting on approval',
      dot: 'ready',
    }))

  it('formats a queued run', () =>
    expect(runRow({ ...RUN, status: 'queued' }, 1100)).toMatchObject({ time: 'queued', dot: 'ready' }))

  it('formats elapsed hours and seconds', () => {
    expect(runRow({ ...RUN, created_at: 1000 }, 1042).time).toBe('running · 42s')
    expect(runRow({ ...RUN, created_at: 1000 }, 1000 + 3725).time).toBe('running · 1h 2m')
  })

  it('has a dash for an empty input preview', () =>
    expect(runRow({ ...RUN, input_preview: '' }, 1100).what).toBe('—'))
})

describe('inProgress', () => {
  it('sums in_progress and running', () =>
    expect(inProgress({ by_status: { in_progress: 2, running: 1, done: 9 } })).toBe(3))
  it('is zero when neither key is present', () => expect(inProgress({ by_status: { done: 4 } })).toBe(0))
  it('is null when stats are unread', () => {
    expect(inProgress(undefined)).toBeNull()
    expect(inProgress({})).toBeNull()
  })
})
