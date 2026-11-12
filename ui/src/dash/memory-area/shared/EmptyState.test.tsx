// @vitest-environment happy-dom
import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { EmptyState } from './EmptyState.jsx'
import { cleanup, mount } from './test-harness'

afterEach(cleanup)

describe('EmptyState', () => {
  it('shows a title, one body sentence and one action', () => {
    const { host } = mount(
      createElement(
        EmptyState,
        { title: 'No failed operations', action: createElement('a', { href: '#memory/health' }, 'Open Health') },
        'Nothing has failed in this bank.',
      ),
    )
    const root = host.querySelector('.ma-empty')!
    expect(root.querySelector('h3')!.textContent).toBe('No failed operations')
    expect(root.querySelector('p')!.textContent).toBe('Nothing has failed in this bank.')
    expect(root.querySelector('.ma-empty-action a')!.textContent).toBe('Open Health')
    expect(root.querySelector('svg')!.getAttribute('aria-hidden')).toBe('true')
  })

  it('is not a live region: it is on the page at load', () => {
    const { host } = mount(createElement(EmptyState, { title: 'Nothing here yet' }))
    expect(host.querySelector('[role="status"]')).toBeNull()
    expect(host.querySelector('[aria-live]')).toBeNull()
    // No body and no action leave no empty wrappers behind.
    expect(host.querySelector('p')).toBeNull()
    expect(host.querySelector('.ma-empty-action')).toBeNull()
  })
})
