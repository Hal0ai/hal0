// Memory v2 Overview (task C2) — mount smoke test.
//
// The Overview isn't routed at #memory yet (C6 wires that up), and the
// brief forbids touching memory.jsx/agent-view.jsx to force a route just
// for this spec. Per the brief's fallback, the Playwright coverage
// (../../../tests/e2e/specs/memory-v2-overview.spec.ts) is written but
// `.skip`-ed with a "C6 unskips" note, and this vitest smoke test instead
// proves `window.MemV2Overview` mounts under a real QueryClientProvider
// with the real hook globals installed (via memory-hook-bridge +
// memory-v2-shared, exactly as main.tsx wires them) without throwing.
//
// `window.MemoryGraphPanel` (ADR-0023, from dash/agents/memory-tab.jsx) is
// stubbed rather than imported for real — pulling in memory-tab.jsx drags
// in unrelated globals (Icons, its own hook bridge) that are that file's
// concern, not C2's; this keeps the smoke test's blast radius to exactly
// what C2 produces + the real hooks it consumes.
//
// `fetch` is stubbed to a never-settling promise so react-query's queries
// sit in a stable `isLoading` state for this single synchronous render —
// this test is a mount/crash check, not a data-rendering assertion (the
// mock-fixture filter/sort behaviour and param serialization already have
// their own unit coverage elsewhere).
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

describe('MemV2Overview (smoke)', () => {
  it('mounts under QueryClientProvider with the real hook globals without throwing', () => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const MemV2Overview = (globalThis as unknown as { MemV2Overview: React.ComponentType<any> })
      .MemV2Overview
    expect(typeof MemV2Overview).toBe('function')

    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(MemV2Overview, {
          onExplore: () => {},
          growthBank: 'primary',
          setGrowthBank: () => {},
        }),
      ),
    )

    expect(typeof html).toBe('string')
    expect(html.length).toBeGreaterThan(0)
    expect(html).toContain('mv-page')
  })
})
