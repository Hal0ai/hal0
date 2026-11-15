// @vitest-environment happy-dom
// The Knowledge page refresh panel (ADR-0060): which rows each mode shows, the schedule editor's
// local-time presets and UTC value, and the PUT body a save sends.
import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { cleanup, click, mount } from '../../../../memory-area/shared/test-harness'

// SRow renders its `sub` through the FieldInfoIcon window global.
;(globalThis as any).FieldInfoIcon = ({ description }: any) => <span data-sub={description} />

const state: { live: any; sent: any[]; response: any; notes: string[] } = {
  live: null,
  sent: [],
  response: {},
  notes: [],
}

vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryRefreshPolicy: () => ({ data: { running: true, notes: state.notes } }),
}))

vi.mock('../../../data/settingsClient.js', () => ({
  useSettingsClient: () => ({
    settings: { data: state.live, isError: false },
    update: {
      isPending: false,
      mutateAsync: async (body: unknown) => {
        state.sent.push(body)
        return state.response
      },
    },
  }),
}))

const { KnowledgeRefreshPanel } = await import('../KnowledgeRefreshPanel.jsx')

const NOW = new Date(Date.UTC(2026, 10, 15, 14, 7))
const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

function render(offsetMinutes = 0) {
  return mount(<KnowledgeRefreshPanel registry={{}} offsetMinutes={offsetMinutes} now={NOW} />)
}

function setValue(el: Element, value: string) {
  const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype : HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event(el instanceof HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}

function radio(host: HTMLElement, group: string, id: string) {
  return host.querySelector(`[data-testid="${group}"] [data-option-id="${id}"]`) as HTMLElement
}

beforeEach(() => {
  state.live = { memory: { refresh: { pages: 'scheduled', schedule: '0 3 * * *', min_interval_s: 86400, consolidation: 'continuous', consolidation_schedule: '0 2 * * *' } } }
  state.sent = []
  state.notes = []
  state.response = { _hal0: { refresh_policy: { reconcile: 'queued' } } }
  ;(window as any).__hal0Toast = vi.fn()
})
afterEach(cleanup)

describe('KnowledgeRefreshPanel', () => {
  it('shows the saved schedule in local time with its UTC value and next run', () => {
    const { host } = render(120)
    expect(radio(host, 'mem-refresh-pages', 'scheduled').getAttribute('aria-checked')).toBe('true')
    expect((q(host, 'mem-refresh-schedule-kind') as HTMLSelectElement).value).toBe('daily')
    expect((q(host, 'mem-refresh-schedule-time') as HTMLInputElement).value).toBe('05:00')
    expect(q(host, 'mem-refresh-schedule-utc')!.textContent).toBe('0 3 * * *')
    expect(q(host, 'mem-refresh-schedule-next')!.textContent).not.toBe('—')
    expect(host.textContent).toContain('UTC+2')
    // Consolidation is continuous: no consolidation schedule.
    expect(q(host, 'mem-refresh-consolidation-schedule')).toBeNull()
    expect((q(host, 'mem-refresh-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('hides the schedule for the other modes and disables the interval for manual', () => {
    const { host } = render()
    click(radio(host, 'mem-refresh-pages', 'after_consolidation'))
    expect(q(host, 'mem-refresh-schedule')).toBeNull()
    expect(q(host, 'mem-refresh-after-note')).not.toBeNull()
    click(radio(host, 'mem-refresh-pages', 'manual'))
    expect(q(host, 'mem-refresh-manual-note')).not.toBeNull()
    expect((q(host, 'mem-refresh-interval') as HTMLInputElement).disabled).toBe(true)
  })

  it('saves a weekly local time as UTC cron, with only the changed keys', async () => {
    const { host } = render(-300)
    setValue(q(host, 'mem-refresh-schedule-kind')!, 'weekly')
    setValue(q(host, 'mem-refresh-schedule-day')!, '0')
    setValue(q(host, 'mem-refresh-schedule-time')!, '21:00')
    expect(q(host, 'mem-refresh-schedule-utc')!.textContent).toBe('0 2 * * 1')
    setValue(q(host, 'mem-refresh-interval')!, '12')
    const save = q(host, 'mem-refresh-save') as HTMLButtonElement
    expect(save.disabled).toBe(false)
    await act(async () => {
      save.click()
    })
    expect(state.sent).toEqual([{ memory: { refresh: { schedule: '0 2 * * 1', min_interval_s: 43200 } } }])
    expect((window as any).__hal0Toast).toHaveBeenCalledWith(expect.stringContaining('applying'), 'ok')
  })

  it('every N hours and a custom cron', () => {
    const { host } = render()
    setValue(q(host, 'mem-refresh-schedule-kind')!, 'hours')
    setValue(q(host, 'mem-refresh-schedule-every')!, '4')
    expect(q(host, 'mem-refresh-schedule-utc')!.textContent).toBe('0 */4 * * *')
    setValue(q(host, 'mem-refresh-schedule-start')!, '3')
    expect(q(host, 'mem-refresh-schedule-utc')!.textContent).toBe('0 3/4 * * *')
    setValue(q(host, 'mem-refresh-schedule-kind')!, 'custom')
    setValue(q(host, 'mem-refresh-schedule-cron')!, '0 3 1')
    expect(q(host, 'mem-refresh-schedule-error')).not.toBeNull()
    expect((q(host, 'mem-refresh-save') as HTMLButtonElement).disabled).toBe(true)
    setValue(q(host, 'mem-refresh-schedule-cron')!, '0 3 1 * *')
    expect(q(host, 'mem-refresh-schedule-error')).toBeNull()
    expect(q(host, 'mem-refresh-schedule-utc')!.textContent).toBe('0 3 1 * *')
    expect((q(host, 'mem-refresh-save') as HTMLButtonElement).disabled).toBe(false)
  })

  it('scheduled consolidation shows its own schedule and saves the mode', async () => {
    const { host } = render()
    click(radio(host, 'mem-refresh-consolidation', 'scheduled'))
    expect(q(host, 'mem-refresh-consolidation-schedule-utc')!.textContent).toBe('0 2 * * *')
    await act(async () => {
      ;(q(host, 'mem-refresh-save') as HTMLButtonElement).click()
    })
    expect(state.sent).toEqual([{ memory: { refresh: { consolidation: 'scheduled' } } }])
  })

  it('every N hours starts at the chosen local hour', () => {
    const { host } = render(120)
    setValue(q(host, 'mem-refresh-schedule-kind')!, 'hours')
    setValue(q(host, 'mem-refresh-schedule-every')!, '6')
    setValue(q(host, 'mem-refresh-schedule-start')!, '1')
    // Local 01, 07, 13, 19 at UTC+2 are UTC 23, 05, 11, 17.
    expect(q(host, 'mem-refresh-schedule-utc')!.textContent).toBe('0 5/6 * * *')
    expect(q(host, 'mem-refresh-schedule-words')!.textContent).toBe('every 6 hours from 01:00')
  })

  it("shows the reconcile's notes, such as an override hal0 did not set", () => {
    state.notes = ['auto-consolidation is off on hal0:shared by an override hal0 did not set; hal0 leaves it alone']
    const { host } = render()
    expect(q(host, 'mem-refresh-note')!.textContent).toContain('hal0:shared')
  })

  it('reset puts the form back', () => {
    const { host } = render()
    click(radio(host, 'mem-refresh-pages', 'manual'))
    click(q(host, 'mem-refresh-reset')!)
    expect(radio(host, 'mem-refresh-pages', 'scheduled').getAttribute('aria-checked')).toBe('true')
    expect(q(host, 'mem-refresh-reset')).toBeNull()
  })

  it('a config without [memory.refresh] reads as the defaults', () => {
    state.live = { memory: {} }
    const { host } = render()
    expect(q(host, 'mem-refresh-schedule-utc')!.textContent).toBe('0 3 * * *')
    expect((q(host, 'mem-refresh-interval') as HTMLInputElement).value).toBe('24')
  })
})
