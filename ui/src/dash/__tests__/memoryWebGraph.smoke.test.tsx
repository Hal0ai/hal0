// Memory v2 web graph (task C5) — mount smoke test.
//
// Same rationale/pattern as C2–C4's smoke tests: the web graph isn't
// routed at #memory yet (C6 wires #memory/bank), so the Playwright
// coverage (../../../tests/e2e/specs/memory-v2-web.spec.ts) is written but
// `.skip`-ed with a "C6 unskips" note, and this vitest smoke test instead
// proves `window.MemV2WebGraph` mounts under a real QueryClientProvider
// with the real hook globals installed (via memory-hook-bridge +
// memory-v2-shared, exactly as main.tsx wires them) without throwing.
//
// `fetch` is stubbed to a never-settling promise, and `window.__hal0D3Force`
// isn't fully exercised here (the sim only spins up once the bank-graph
// query actually resolves, which it never does with fetch stubbed this
// way) — this is a mount/crash check for the render path with an empty
// graph, not a data-rendering or simulation-behaviour assertion.
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

vi.stubGlobal(
  'fetch',
  vi.fn(() => new Promise(() => {})),
)

await import('../memory-hook-bridge')
await import('../memory-v2-shared.jsx')
await import('../memory-web-graph.jsx')

describe('MemV2WebGraph (smoke)', () => {
  it('mounts under QueryClientProvider with the real hook globals without throwing', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const MemV2WebGraph = (globalThis as unknown as { MemV2WebGraph: React.ComponentType<any> })
      .MemV2WebGraph
    expect(typeof MemV2WebGraph).toBe('function')

    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(MemV2WebGraph, {
          bank: 'primary',
          sel: null,
          setSel: () => {},
          filters: {},
        }),
      ),
    )

    expect(typeof html).toBe('string')
    expect(html).toContain('mv-web')
    expect(html).toContain('mv-web-zoom')
  })
})
