// @vitest-environment happy-dom
import { createElement, useState } from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Segmented } from './Segmented.jsx'
import { cleanup, click, mount, press } from './test-harness'

const OPTIONS = [
  { id: 'all', label: 'All', count: 1093 },
  { id: 'pending', label: 'Waiting' },
  { id: 'failed', label: 'Failed', count: 105 },
]

/** The control with the state a caller would hold, and a record of every change. */
function Held(props: { initial: string | null; options?: typeof OPTIONS; onChange?: (id: string) => void }) {
  const [value, setValue] = useState<string | null>(props.initial)
  return createElement(Segmented, {
    label: 'Status',
    options: props.options ?? OPTIONS,
    value,
    onChange: (id: string) => {
      props.onChange?.(id)
      setValue(id)
    },
  })
}

const radios = (host: HTMLElement) => [...host.querySelectorAll<HTMLButtonElement>('[role="radio"]')]
const checkedIds = (host: HTMLElement) =>
  radios(host)
    .filter((r) => r.getAttribute('aria-checked') === 'true')
    .map((r) => r.dataset.optionId)

afterEach(cleanup)

describe('Segmented', () => {
  it('is a labelled radio group with exactly one checked radio', () => {
    const { host } = mount(createElement(Held, { initial: 'pending' }))
    const group = host.querySelector('[role="radiogroup"]')!
    expect(group.getAttribute('aria-label')).toBe('Status')
    expect(radios(host)).toHaveLength(3)
    expect(checkedIds(host)).toEqual(['pending'])
    // Every radio states its state; none uses aria-pressed.
    for (const r of radios(host)) {
      expect(['true', 'false']).toContain(r.getAttribute('aria-checked'))
      expect(r.hasAttribute('aria-pressed')).toBe(false)
      expect(r.getAttribute('type')).toBe('button')
    }
  })

  it('takes its name from another element when labelledBy is given', () => {
    const { host } = mount(
      createElement(Segmented, { labelledBy: 'lbl', label: 'ignored', options: OPTIONS, value: 'all', onChange: () => {} }),
    )
    const group = host.querySelector('[role="radiogroup"]')!
    expect(group.getAttribute('aria-labelledby')).toBe('lbl')
    expect(group.hasAttribute('aria-label')).toBe(false)
  })

  it('marks the choice with aria-checked alone, which the stylesheet turns into fill and weight', () => {
    const { host } = mount(createElement(Held, { initial: 'all' }))
    // Every segment has the same class, so no tone class can stand in for the state.
    // memory-area.css.test.ts pins what `[aria-checked="true"]` draws.
    expect(new Set(radios(host).map((r) => r.className))).toEqual(new Set(['ma-seg-btn']))
  })

  it('formats a count with a thousands separator', () => {
    const { host } = mount(createElement(Held, { initial: 'all' }))
    expect(radios(host)[0].textContent).toBe('All1,093')
    expect(radios(host)[0].querySelector('.ma-seg-count')!.textContent).toBe('1,093')
    expect(radios(host)[1].querySelector('.ma-seg-count')).toBeNull()
  })

  it('has one tab stop, on the checked radio (roving tabindex)', () => {
    const { host } = mount(createElement(Held, { initial: 'pending' }))
    expect(radios(host).map((r) => r.tabIndex)).toEqual([-1, 0, -1])
  })

  it('keeps the group reachable when the value matches no option', () => {
    const { host } = mount(createElement(Held, { initial: 'gone' }))
    expect(checkedIds(host)).toEqual([])
    expect(radios(host).map((r) => r.tabIndex)).toEqual([0, -1, -1])
  })

  it('selects on click', () => {
    const onChange = vi.fn()
    const { host } = mount(createElement(Held, { initial: 'all', onChange }))
    click(radios(host)[2])
    expect(onChange).toHaveBeenCalledWith('failed')
    expect(checkedIds(host)).toEqual(['failed'])
    // Clicking the checked radio again changes nothing.
    click(radios(host)[2])
    expect(onChange).toHaveBeenCalledTimes(1)
  })

  it.each([
    ['ArrowRight', 'pending'],
    ['ArrowDown', 'pending'],
    ['ArrowLeft', 'failed'], // wraps from the first to the last
    ['ArrowUp', 'failed'],
    ['End', 'failed'],
  ])('%s from the first radio moves focus and selection to %s', (key, expected) => {
    const onChange = vi.fn()
    const { host } = mount(createElement(Held, { initial: 'all', onChange }))
    radios(host)[0].focus()
    const allowed = press(radios(host)[0], key)
    expect(allowed).toBe(false) // the key is consumed: the page does not scroll
    expect(onChange).toHaveBeenCalledWith(expected)
    expect(checkedIds(host)).toEqual([expected])
    expect((document.activeElement as HTMLElement).dataset.optionId).toBe(expected)
    // The tab stop follows the selection.
    expect(radios(host).find((r) => r.tabIndex === 0)!.dataset.optionId).toBe(expected)
  })

  it('wraps from the last radio to the first, and Home returns to the first', () => {
    const { host } = mount(createElement(Held, { initial: 'failed' }))
    radios(host)[2].focus()
    press(radios(host)[2], 'ArrowRight')
    expect(checkedIds(host)).toEqual(['all'])
    press(radios(host)[0], 'End')
    expect(checkedIds(host)).toEqual(['failed'])
    press(radios(host)[2], 'Home')
    expect(checkedIds(host)).toEqual(['all'])
    expect((document.activeElement as HTMLElement).dataset.optionId).toBe('all')
  })

  it('skips a disabled radio', () => {
    const options = [OPTIONS[0], { ...OPTIONS[1], disabled: true }, OPTIONS[2]]
    const { host } = mount(createElement(Held, { initial: 'all', options }))
    radios(host)[0].focus()
    press(radios(host)[0], 'ArrowRight')
    expect(checkedIds(host)).toEqual(['failed'])
    expect(radios(host)[1].disabled).toBe(true)
  })

  it('leaves other keys alone', () => {
    const onChange = vi.fn()
    const { host } = mount(createElement(Held, { initial: 'all', onChange }))
    expect(press(radios(host)[0], 'a')).toBe(true)
    expect(press(radios(host)[0], 'Tab')).toBe(true)
    expect(onChange).not.toHaveBeenCalled()
  })
})
