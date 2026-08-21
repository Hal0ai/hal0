// Memory v2 Bank workspace (task C4) — mount smoke test.
//
// Same rationale/pattern as C2/C3's smoke tests: the workspace isn't
// routed at #memory yet (C6 wires #memory/bank), so the Playwright
// coverage (../../../tests/e2e/specs/memory-v2-workspace.spec.ts) is
// written but `.skip`-ed with a "C6 unskips" note, and this vitest smoke
// test instead proves `window.MemV2Workspace` mounts under a real
// QueryClientProvider with the real hook globals installed (via
// memory-hook-bridge + memory-v2-shared + memory-overview-v2 +
// memory-bank-bar, exactly as main.tsx wires them) without throwing.
//
// `fetch` is stubbed to a never-settling promise — a mount/crash check,
// not a data-rendering assertion.
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { MemoryGraphPanel: unknown }).MemoryGraphPanel = () =>
  React.createElement('div', { 'data-testid': 'stub-memory-graph-panel' }, 'stub')

vi.stubGlobal(
  'fetch',
  vi.fn(() => new Promise(() => {})),
)

await import('../memory-hook-bridge')
await import('../memory-v2-shared.jsx')
await import('../memory-overview-v2.jsx')
await import('../memory-bank-bar.jsx')
await import('../memory-bank-workspace.jsx')

describe('MemV2Workspace (smoke)', () => {
  it('mounts under QueryClientProvider with the real hook globals without throwing', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const MemV2Workspace = (globalThis as unknown as { MemV2Workspace: React.ComponentType<any> })
      .MemV2Workspace
    expect(typeof MemV2Workspace).toBe('function')

    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(MemV2Workspace, { bank: 'primary', setBank: () => {}, sel: null, setSel: () => {} }),
      ),
    )

    expect(typeof html).toBe('string')
    expect(html).toContain('mv-workspace')
    expect(html).toContain('mv-filter-card')
  })
})
