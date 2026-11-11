// @vitest-environment happy-dom
//
// What the Memory area asks hal0 for while Health is open, with the real hooks and a real query
// client: the health answer, and the two older reads the engine facts strip still needs. Never a
// request per bank: no `/api/memory/banks/<id>/stats` and no per-bank mental models.
import { createElement } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GRAPH, healthReport, modelsAnswer } from './area-test-fixtures'
import { cleanup, mount, settle } from './shared/test-harness'

const asked = vi.hoisted(() => [] as string[])

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return {
    ...actual,
    apiGet: vi.fn(async (url: string) => {
      asked.push(url)
      if (url === '/api/memory/health') return healthReport()
      if (url === '/api/memory/graph/status') return GRAPH
      if (url === '/api/memory/models') return modelsAnswer()
      if (url === '/api/memory/engine') return { enabled: true, engine: 'hindsight', reachable: true }
      if (url.startsWith('/api/memory/banks')) return { banks: [] }
      return {}
    }),
  }
})

const { MemoryArea } = await import('./MemoryArea.jsx')

afterEach(() => {
  cleanup()
  asked.length = 0
})

function open(hash: string) {
  ;(window as unknown as { happyDOM: { setURL: (url: string) => void } }).happyDOM.setURL(`http://localhost:9120/${hash}`)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return mount(createElement(QueryClientProvider, { client }, createElement(MemoryArea))).host
}

describe('Memory area on the Health tab', () => {
  it('asks for the health answer once and for nothing per bank', async () => {
    const host = open('#memory/health')
    await settle()
    await settle()
    expect(host.querySelector('[data-testid="ma-health-summary"]')).not.toBeNull()
    expect(asked.filter((url) => url === '/api/memory/health')).toHaveLength(1)
    expect(asked.some((url) => /^\/api\/memory\/banks\/[^/]+\/stats/.test(url))).toBe(false)
    expect(asked.some((url) => /mental-models/.test(url))).toBe(false)
    // Neither the engine check nor the bank list: the health answer carries both.
    expect(asked).not.toContain('/api/memory/engine')
    expect(asked).not.toContain('/api/memory/banks')
    // What is left is the strip's two older reads.
    expect([...new Set(asked)].sort()).toEqual(['/api/memory/graph/status', '/api/memory/health', '/api/memory/models'])
  })
})
