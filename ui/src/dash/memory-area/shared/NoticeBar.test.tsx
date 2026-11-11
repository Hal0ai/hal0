// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { NoticeBar } from './NoticeBar.jsx'
import { cleanup, mount } from './test-harness'

afterEach(cleanup)

describe('NoticeBar', () => {
  it('carries its tone in the chip, with the state as a word, and one sentence', () => {
    const { host } = mount(
      createElement(
        NoticeBar,
        { tone: 'warn', status: 'stale', action: createElement('a', { href: '#memory/health' }, 'Open Health') },
        'Shown as of 5 Nov 2026 until a refresh succeeds.',
      ),
    )
    const bar = host.querySelector('.ma-notice')!
    expect(bar.className).toBe('ma-notice') // no tone class on the bar itself
    expect(bar.querySelector('.chip')!.className).toBe('chip ma-chip warn')
    expect(bar.querySelector('.chip')!.textContent).toBe('stale')
    expect(bar.querySelector('p')!.textContent).toBe('Shown as of 5 Nov 2026 until a refresh succeeds.')
    expect(bar.querySelector('a')!.textContent).toBe('Open Health')
  })

  it('has no live role unless it appears after an action', () => {
    const atLoad = mount(createElement(NoticeBar, { status: 'off' }, 'The decision model is off.'))
    expect(atLoad.host.querySelector('.ma-notice')!.hasAttribute('role')).toBe(false)
    const after = mount(createElement(NoticeBar, { status: 'running', tone: 'info', live: true }, 'The refresh is running.'))
    expect(after.host.querySelector('.ma-notice')!.getAttribute('role')).toBe('status')
  })

  it('squares off at the top of a card', () => {
    const { host } = mount(createElement(NoticeBar, { status: 'off', inCard: true }, 'x'))
    expect(host.querySelector('.ma-notice')!.className).toBe('ma-notice ma-notice-incard')
  })
})
