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

// Post-smoke-review regression fix: EnginePanel's "graph built / errors /
// live" cells must render when — and only when — extraction is OFF, since
// that's exactly the state MemoryGraphPanel (stubbed above) hides its own
// copy of the same three numbers. Overrides window.__hal0UseMemoryGraphStatus
// directly (same lever the real hook bridge uses) with fixed, already-
// resolved data — no need to wait on the never-settling fetch stub above.
describe('MemV2Overview — EnginePanel graph-stat cells vs extraction toggle', () => {
  it('renders graph built/errors/live when extraction is OFF (nonzero counters)', () => {
    const real = (globalThis as unknown as { __hal0UseMemoryGraphStatus: unknown })
      .__hal0UseMemoryGraphStatus
    ;(globalThis as unknown as { __hal0UseMemoryGraphStatus: unknown }).__hal0UseMemoryGraphStatus = () => ({
      data: { enabled: false, builds_ok: 1301, errors: 383, in_flight: 1 },
    })

    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const MemV2Overview = (globalThis as unknown as { MemV2Overview: React.ComponentType<any> })
        .MemV2Overview
      const html = renderToStaticMarkup(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(MemV2Overview, { onExplore: () => {}, growthBank: 'primary', setGrowthBank: () => {} }),
        ),
      )

      expect(html).toContain('graph built')
      expect(html).toContain('1,301')
      expect(html).toContain('errors')
      expect(html).toContain('383')
      expect(html).toContain('pending')
    } finally {
      ;(globalThis as unknown as { __hal0UseMemoryGraphStatus: unknown }).__hal0UseMemoryGraphStatus = real
    }
  })

  it('does not duplicate graph built/errors/live when extraction is ON', () => {
    const real = (globalThis as unknown as { __hal0UseMemoryGraphStatus: unknown })
      .__hal0UseMemoryGraphStatus
    ;(globalThis as unknown as { __hal0UseMemoryGraphStatus: unknown }).__hal0UseMemoryGraphStatus = () => ({
      data: { enabled: true, builds_ok: 1301, errors: 383, in_flight: 1 },
    })

    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const MemV2Overview = (globalThis as unknown as { MemV2Overview: React.ComponentType<any> })
        .MemV2Overview
      const html = renderToStaticMarkup(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(MemV2Overview, { onExplore: () => {}, growthBank: 'primary', setGrowthBank: () => {} }),
        ),
      )

      expect(html).not.toContain('graph built')
      expect(html).not.toContain('lifetime')
    } finally {
      ;(globalThis as unknown as { __hal0UseMemoryGraphStatus: unknown }).__hal0UseMemoryGraphStatus = real
    }
  })
})
