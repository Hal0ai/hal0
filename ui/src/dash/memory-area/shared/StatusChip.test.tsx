// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { STATUS_WORDS, StatusChip } from './StatusChip.jsx'
import { cleanup, mount } from './test-harness'

afterEach(cleanup)

const chipOf = (host: HTMLElement) => host.querySelector<HTMLElement>('.chip')!

describe('StatusChip', () => {
  it.each([
    ['ok', 'reachable', 'chip ma-chip ok'],
    ['warn', 'stale', 'chip ma-chip warn'],
    ['err', 'failing', 'chip ma-chip err'],
    ['info', 'running', 'chip ma-chip info'],
    ['neutral', 'waiting', 'chip ma-chip ma-chip-neutral'],
  ] as const)('tone %s says "%s"', (tone, word, className) => {
    const { host } = mount(createElement(StatusChip, { tone }, word))
    const chip = chipOf(host)
    expect(chip.className).toBe(className)
    // The word is the chip's whole text: the meaning does not depend on the tint.
    expect(chip.textContent).toBe(word)
  })

  it('hides its dot from assistive tech', () => {
    const { host } = mount(createElement(StatusChip, { tone: 'ok' }, 'done'))
    const dot = chipOf(host).querySelector('.ma-chip-dot')!
    expect(dot.getAttribute('aria-hidden')).toBe('true')
    expect(dot.textContent).toBe('')
  })

  it('lower-cases its word', () => {
    const { host } = mount(createElement(StatusChip, { tone: 'warn' }, 'Stale'))
    expect(chipOf(host).textContent).toBe('stale')
  })

  it('is neutral when no tone, or an unknown one, is given', () => {
    const a = mount(createElement(StatusChip, null, 'off'))
    expect(chipOf(a.host).className).toBe('chip ma-chip ma-chip-neutral')
    const b = mount(createElement(StatusChip, { tone: 'purple' as never }, 'off'))
    expect(chipOf(b.host).className).toBe('chip ma-chip ma-chip-neutral')
  })

  it('keeps a counted form as it is given', () => {
    const { host } = mount(createElement(StatusChip, { tone: 'err' }, '37 failures in 6 h'))
    expect(chipOf(host).textContent).toBe('37 failures in 6 h')
  })

  it('publishes the fixed word list, all lower-case', () => {
    expect(STATUS_WORDS).toContain('reachable')
    expect(STATUS_WORDS).toContain('not started')
    expect(STATUS_WORDS).toHaveLength(15)
    for (const word of STATUS_WORDS) expect(word).toBe(word.toLowerCase())
    expect(Object.isFrozen(STATUS_WORDS)).toBe(true)
  })
})
