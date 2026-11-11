// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { GoLink } from './GoLink.jsx'
import { cleanup, mount } from './test-harness'

afterEach(cleanup)

describe('GoLink', () => {
  it('is a real link with underlined text and an arrow after it', () => {
    const { host } = mount(createElement(GoLink, { href: '#memory/operations', 'data-testid': 'go' }, 'Open Operations'))
    const link = host.querySelector('a')!
    expect(link.getAttribute('href')).toBe('#memory/operations')
    expect(link.dataset.testid).toBe('go')
    // The accessible name is the text alone: the arrow is decoration.
    expect(link.textContent).toBe('Open Operations')
    expect(link.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true')
    const parts = [...link.children].map((el) => el.tagName.toLowerCase())
    expect(parts).toEqual(['span', 'svg'])
    expect(link.querySelector('.ma-go-text')!.textContent).toBe('Open Operations')
  })

  it('puts the arrow first for a link that goes back', () => {
    const { host } = mount(createElement(GoLink, { href: '#memory/health', back: true }, 'Back to Health'))
    const link = host.querySelector('a')!
    expect([...link.children].map((el) => el.tagName.toLowerCase())).toEqual(['svg', 'span'])
    expect(link.textContent).toBe('Back to Health')
  })
})
