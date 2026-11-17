// @vitest-environment happy-dom
// Settings > Memory: the Models panel is mounted, the old Reranker panel is now the Fallback
// reranker with an explanation and human row labels, and a `#settings/memory/models/<group>`
// sub-path reaches the group.
import React from 'react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { cleanup, mount } from '../../../../memory-area/shared/test-harness'

// primitives.jsx (via ExtractionThroughputViews) reads React as a global.
;(globalThis as any).React = React

const noop = { isPending: false, mutateAsync: async () => ({}) }

vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryGraphStatus: () => ({ data: undefined, isError: false }),
  useRetryFailedExtractions: () => noop,
  useUpdateMemoryGraph: () => noop,
  useMemoryRefreshPolicy: () => ({ data: undefined }),
  useMemoryModels: () => ({ data: undefined, isError: false, refetch: vi.fn() }),
}))
vi.mock('@/api/hooks/useCapabilities', () => ({ useCapabilities: () => ({ data: undefined }) }))
vi.mock('../../../data/settingsClient.js', () => ({
  useSettingsClient: () => ({
    registry: {},
    settings: { data: { memory: {} }, isError: false },
    schema: { data: { properties: {} } },
    update: noop,
  }),
}))

const { MemoryPage } = await import('../MemoryPage.jsx')
;(globalThis as any).FieldInfoIcon = ({ description }: any) => <span data-sub={description} />

afterEach(cleanup)

describe('MemoryPage', () => {
  it('mounts the Models panel above the fallback reranker', () => {
    const { host } = mount(<MemoryPage />)
    const titles = Array.from(host.querySelectorAll('h3, .s-row .k > span')).map((n) => n.textContent)
    expect(host.querySelector('[data-testid="mem-models-panel"]')).not.toBeNull()
    expect(titles.indexOf('Engine reranker')).toBeGreaterThan(-1)
    expect(titles.indexOf('Fallback reranker')).toBeGreaterThan(titles.indexOf('Engine reranker'))
  })

  it('renames the old Reranker panel and says what it is', () => {
    const { host } = mount(<MemoryPage />)
    const spans = Array.from(host.querySelectorAll('.s-row .k > span')).map((n) => n.textContent)
    expect(spans).toContain('Fallback reranker')
    expect(spans).not.toContain('Reranker')
    expect(host.querySelector('[data-testid="mem-fallback-rerank-intro"]')!.textContent).toContain(
      "used when the engine's reranker is unavailable",
    )
    expect(Array.from(host.querySelectorAll('button')).map((b) => b.textContent)).toContain('Save fallback reranker')
  })

  it('labels the fallback rows in words, not key names', () => {
    const { host } = mount(<MemoryPage />)
    const spans = Array.from(host.querySelectorAll('.s-row .k > span')).map((n) => n.textContent)
    for (const label of ['Fallback behaviour', 'Rerank gateway URL', 'Rerank model', 'Connect timeout (s)', 'Read timeout (s)']) {
      expect(spans).toContain(label)
    }
    expect(spans.some((t) => /^embedding\./.test(t || ''))).toBe(false)
  })

  it('scrolls to the group the sub-path names', () => {
    const scrolled: string[] = []
    ;(Element.prototype as any).scrollIntoView = function () {
      scrolled.push(this.id)
    }
    mount(<MemoryPage sub="models/reranker" />)
    expect(scrolled).toEqual(['memory-models-reranker-title'])
  })
})
