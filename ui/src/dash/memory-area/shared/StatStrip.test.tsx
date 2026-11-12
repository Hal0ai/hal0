// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { StatStrip } from './StatStrip.jsx'
import { StatusChip } from './StatusChip.jsx'
import { cleanup, mount } from './test-harness'

afterEach(cleanup)

const cell = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-stat="${id}"]`)!

describe('StatStrip', () => {
  it('draws label above, then the number and its unit, then the helper', () => {
    const { host } = mount(
      createElement(StatStrip, {
        label: 'Queue',
        cells: [{ id: 'waiting', label: 'Waiting', value: 1093, unit: 'operations', helper: 'oldest 6 h' }],
      }),
    )
    expect(host.querySelector('section')!.getAttribute('aria-label')).toBe('Queue')
    const c = cell(host, 'waiting')
    expect([...c.children].map((el) => el.tagName)).toEqual(['DT', 'DD', 'DD'])
    expect(c.querySelector('dt')!.textContent).toBe('Waiting')
    const value = c.querySelector('.ma-stat-value')!
    expect([...value.children].map((el) => el.textContent)).toEqual(['1,093', 'operations'])
    expect(value.querySelector('.ma-stat-unit')!.textContent).toBe('operations')
    expect(c.querySelector('.ma-stat-helper')!.textContent).toBe('oldest 6 h')
  })

  it('prints a real zero as 0 with its unit', () => {
    const { host } = mount(
      createElement(StatStrip, { label: 'Queue', cells: [{ id: 'failed', label: 'Failed', value: 0, unit: 'operations' }] }),
    )
    expect(cell(host, 'failed').querySelector('.ma-stat-value')!.textContent).toBe('0operations')
  })

  it.each([[null], [undefined], [Number.NaN], ['']])(
    'shows a missing value (%j) as a dash labelled "not reported", with no unit and no zero',
    (value) => {
      const { host } = mount(
        createElement(StatStrip, {
          label: 'Queue',
          cells: [{ id: 'failed', label: 'Failed', value: value as number | null | undefined, unit: 'operations' }],
        }),
      )
      const missing = cell(host, 'failed').querySelector('[aria-label="not reported"]')!
      expect(missing.textContent).toBe('–')
      expect(missing.getAttribute('role')).toBe('img')
      const value_ = cell(host, 'failed').querySelector('.ma-stat-value')!
      expect(value_.textContent).toBe('–')
      expect(value_.textContent).not.toContain('0')
      expect(value_.querySelector('.ma-stat-unit')).toBeNull()
    },
  )

  it('shows skeletons while loading and never prints a value, even a held 0', () => {
    const { host } = mount(
      createElement(StatStrip, {
        label: 'Queue',
        loading: true,
        cells: [
          { id: 'waiting', label: 'Waiting', value: 0, unit: 'operations', helper: 'oldest 6 h' },
          { id: 'failed', label: 'Failed', value: undefined, unit: 'operations' },
          { id: 'engine', label: 'Engine', value: 'Hindsight 1.2.0' },
        ],
      }),
    )
    expect(host.querySelector('section')!.getAttribute('aria-busy')).toBe('true')
    expect(host.querySelectorAll('.ma-skel')).toHaveLength(3)
    for (const id of ['waiting', 'failed', 'engine']) {
      const value = cell(host, id).querySelector('.ma-stat-value')!
      expect(value.textContent).toBe('')
      expect(value.querySelector('[aria-label="not reported"]')).toBeNull()
    }
    // The labels are known, so they stay. No digit appears anywhere in the strip.
    expect(cell(host, 'waiting').querySelector('dt')!.textContent).toBe('Waiting')
    expect(host.querySelector('dl')!.textContent).not.toMatch(/\d/)
    expect(host.querySelector('.ma-stat-helper')).toBeNull()
    expect(host.querySelector('.sr-only')!.textContent).toBe('Loading')
  })

  it('is not busy once loaded', () => {
    const { host } = mount(createElement(StatStrip, { label: 'Queue', cells: [{ label: 'Waiting', value: 3, unit: 'operations' }] }))
    expect(host.querySelector('section')!.hasAttribute('aria-busy')).toBe(false)
    expect(host.querySelector('.ma-skel')).toBeNull()
  })

  it('sets a name smaller than a number and puts its status in a chip after it', () => {
    const { host } = mount(
      createElement(StatStrip, {
        label: 'Engine facts',
        cells: [
          {
            id: 'engine',
            label: 'Engine',
            value: 'Hindsight 1.2.0',
            chip: createElement(StatusChip, { tone: 'ok' }, 'reachable'),
            helper: 'memory engine',
          },
        ],
      }),
    )
    const value = cell(host, 'engine').querySelector('.ma-stat-value')!
    expect(value.classList.contains('ma-stat-value-name')).toBe(true)
    expect(value.firstElementChild!.textContent).toBe('Hindsight 1.2.0')
    expect(value.querySelector('.chip')!.textContent).toBe('reachable')
    // The value element itself carries no tone class: the chip does.
    expect(value.className).not.toMatch(/\b(ok|warn|err|info)\b/)
  })

  it('drops its frame inside a card', () => {
    const { host } = mount(createElement(StatStrip, { label: 'Dry run', inCard: true, cells: [] }))
    expect(host.querySelector('dl')!.className).toBe('ma-stats ma-stats-incard')
  })
})
