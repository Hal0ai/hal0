import { describe, expect, it, vi } from 'vitest'
import { allClearLine, mergeAttention } from '../needs-you-pure'

describe('mergeAttention', () => {
  it('orders err, warn, accent and keeps both a notification and a diagnosis about one slot', () => {
    const out = mergeAttention({
      notifications: [
        { key: 'slot-error:code', kind: 'error', tone: 'warn', eyebrow: 'slot · error', body: 'code failed', actions: [] },
        { key: 'update', kind: 'update', tone: 'accent', eyebrow: 'update', body: '1.7.2', actions: [] },
      ],
      diagnoses: [
        { id: 'HAL0-SLOT-CODE-OOM', severity: 'fail', summary: 'code', detail: 'exit 137', fixable: true, next_steps: [] },
        { id: 'HAL0-API', severity: 'info', summary: 'api', detail: 'ok', fixable: false, next_steps: [] },
      ],
      failing: [],
      drift: null,
    })
    expect(out.map((i) => i.key)).toEqual(['doctor:HAL0-SLOT-CODE-OOM', 'slot-error:code', 'update'])
  })

  it('adds one drift item with a restart action', () => {
    const restart = vi.fn()
    const out = mergeAttention({
      notifications: [],
      diagnoses: [],
      failing: [],
      drift: { count: 2, slots: ['agent', 'brain'] },
      handlers: { restartDrift: restart },
    })
    expect(out[0]).toMatchObject({ key: 'drift', tone: 'warn' })
    expect(out[0].actions[0].label).toBe('restart drifted slots')
    out[0].actions[0].run()
    expect(restart).toHaveBeenCalledOnce()
  })

  it('keeps the drift wording from the old banner', () => {
    const [d] = mergeAttention({ notifications: [], diagnoses: [], failing: [], drift: { count: 1, slots: ['agent'] } })
    expect(d.eyebrow).toBe('update · slots need restart')
    expect(d.body).toContain('1 slot needs restart')
    expect(d.body).toContain('agent')
  })

  it('does not double-list drift when the notification source carries its own drift row', () => {
    const out = mergeAttention({
      notifications: [{ key: 'update:drift', kind: 'drift', tone: 'warn', eyebrow: 'update · slot drift', body: 'x', actions: [] }],
      diagnoses: [],
      failing: [],
      drift: { count: 1, slots: ['agent'] },
    })
    expect(out.map((i) => i.key)).toEqual(['drift'])
  })

  it('maps critical and fail to err, warn to warn, and drops info', () => {
    const d = (id: string, severity: any) => ({ id, severity, summary: id, detail: '', fixable: false, next_steps: [] })
    const out = mergeAttention({
      notifications: [],
      diagnoses: [d('A', 'warn'), d('B', 'critical'), d('C', 'info'), d('D', 'fail')],
      failing: [],
      drift: null,
    })
    expect(out.map((i) => [i.key, i.tone])).toEqual([
      ['doctor:B', 'err'],
      ['doctor:D', 'err'],
      ['doctor:A', 'warn'],
    ])
  })

  it('turns failing health checks into err items keyed by check name', () => {
    const out = mergeAttention({ notifications: [], diagnoses: [], failing: ['slot_manager: errored: code', 'disk_state'], drift: null })
    expect(out.map((i) => [i.key, i.tone])).toEqual([
      ['health:slot_manager', 'err'],
      ['health:disk_state', 'err'],
    ])
    expect(out[0].body).toBe('slot_manager: errored: code')
  })

  it('offers a run action only for command steps that map to a typed action', () => {
    const run = vi.fn()
    const mapped = { kind: 'command' as const, label: 'restart slot code', target: 'hal0 slot restart code' }
    const unmapped = { kind: 'command' as const, label: 'weird', target: 'rm -rf /' }
    const manual = { kind: 'manual' as const, label: 'check cable', target: '' }
    const [item] = mergeAttention({
      notifications: [],
      diagnoses: [{ id: 'X', severity: 'fail', summary: 's', detail: 'd', fixable: true, next_steps: [mapped, unmapped, manual] }],
      failing: [],
      drift: null,
      handlers: { runStep: run },
    })
    expect(item.actions.map((a) => a.label)).toEqual(['restart slot code'])
    item.actions[0].run()
    expect(run).toHaveBeenCalledWith(mapped)
  })

  it('falls back to an open-doctor action when nothing is runnable', () => {
    const open = vi.fn()
    const [item] = mergeAttention({
      notifications: [],
      diagnoses: [{ id: 'X', severity: 'warn', summary: 's', detail: 'd', fixable: false, next_steps: [] }],
      failing: [],
      drift: null,
      handlers: { openDoctor: open },
    })
    expect(item.actions.map((a) => a.label)).toEqual(['open doctor'])
    item.actions[0].run()
    expect(open).toHaveBeenCalledOnce()
  })

  it('maps notification actions onto run and keeps the primary flag', () => {
    const go = vi.fn()
    const [item] = mergeAttention({
      notifications: [
        { key: 'approval:1', kind: 'approval', tone: 'accent', eyebrow: 'approval', body: 'b', actions: [{ label: 'Approve', onClick: go, primary: true }, { label: 'Deny', onClick: vi.fn() }] },
      ],
      diagnoses: [],
      failing: [],
      drift: null,
    })
    expect(item.actions.map((a) => [a.label, !!a.primary])).toEqual([['Approve', true], ['Deny', false]])
    item.actions[0].run()
    expect(go).toHaveBeenCalledOnce()
  })
})

describe('allClearLine', () => {
  const base = { slotsHealthy: [13, 13] as [number, number], diskFreeMb: 124429, channel: 'nightly', available: null, checkedAt: new Date(2026, 9, 29, 15, 10) }
  it('composes the line', () =>
    expect(allClearLine(base)).toBe('All clear · doctor ok · 13/13 slots healthy · disk 124 GB free · up to date on nightly · checked 15:10'))
  it('renders an em dash for an unsourced disk figure and drops an unknown channel', () =>
    expect(allClearLine({ ...base, diskFreeMb: null, channel: null })).toBe('All clear · doctor ok · 13/13 slots healthy · disk — free · checked 15:10'))
  it('omits the slot segment when no slot list is known', () =>
    expect(allClearLine({ ...base, slotsHealthy: [0, 0] })).not.toContain('slots healthy'))
  it('zero-pads the clock', () =>
    expect(allClearLine({ ...base, checkedAt: new Date(2026, 9, 29, 7, 5) })).toContain('checked 07:05'))
})
