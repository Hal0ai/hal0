// @vitest-environment happy-dom
//
// The three tabs that are not built yet: each says so under its own heading and links to where
// its capability lives today.
import { createElement } from 'react'
import { afterEach, describe, expect, it } from 'vitest'
import { AgentsTab } from './AgentsTab.jsx'
import { KnowledgeTab } from './KnowledgeTab.jsx'
import { OperationsTab } from './OperationsTab.jsx'
import { cleanup, mount } from './shared/test-harness'

afterEach(cleanup)

const textOf = (el: Element | null) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()

describe.each([
  { name: 'Knowledge', tab: KnowledgeTab, testId: 'ma-knowledge', href: '#memworkspace/kb' },
  { name: 'Operations', tab: OperationsTab, testId: 'ma-operations', href: '#memworkspace/operations' },
  { name: 'Agents', tab: AgentsTab, testId: 'ma-agents', href: '#memworkspace/agents' },
])('$name stub', ({ name, tab, testId, href }) => {
  it('has the tab heading, and is named by it', () => {
    const { host } = mount(createElement(tab))
    const card = host.querySelector(`[data-testid="${testId}-stub"]`)!
    const heading = card.querySelector('h2')!
    expect(heading.textContent).toBe(name)
    expect(card.getAttribute('aria-labelledby')).toBe(heading.id)
  })

  it('says the view is being moved here, and until then where it is', () => {
    const { host } = mount(createElement(tab))
    const words = textOf(host)
    expect(words).toMatch(/being moved (here|into this tab)/)
    expect(words).toContain('legacy tools page')
    // It does not pretend to be the view: no table, no figure.
    expect(host.querySelector('table')).toBeNull()
    expect(words).not.toMatch(/\d/)
  })

  it('links to its section of the legacy tools page', () => {
    const { host } = mount(createElement(tab))
    const link = host.querySelector(`[data-testid="${testId}-legacy"]`)!
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe(href)
  })
})

describe('Knowledge stub', () => {
  it('also links to mental models and directives, which live on their own legacy tabs', () => {
    const { host } = mount(createElement(KnowledgeTab))
    const hrefs = [...host.querySelectorAll('a')].map((a) => a.getAttribute('href'))
    expect(hrefs).toEqual(
      expect.arrayContaining(['#memworkspace/kb', '#memworkspace/mental-models', '#memworkspace/directives']),
    )
  })
})
