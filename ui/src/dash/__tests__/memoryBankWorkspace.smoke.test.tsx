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
    // fix round (post C4-review, #2): the "show invalidated" affordance —
    // the only way to browse/recover an archived fact once Inspector's
    // local override state is gone.
    expect(html).toContain('mv-state-invalidated')
  })

  it('renders the Inspector instead of the filter card when a fact is selected', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const MemV2Workspace = (globalThis as unknown as { MemV2Workspace: React.ComponentType<any> })
      .MemV2Workspace

    // fetch never settles, so `unitsPage` is empty — Inspector's own
    // "fact not found" branch still exercises the mv-inspector render path
    // without throwing (the not-found branch is a real, reachable UI state:
    // a fact that left the current page).
    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(MemV2Workspace, { bank: 'primary', setBank: () => {}, sel: 'f1', setSel: () => {} }),
      ),
    )

    expect(html).toContain('mv-inspector')
    expect(html).not.toContain('mv-filter-card')
  })
})

describe('MemV2Workspace — final-review I2 (truncated slab notice)', () => {
  it('renders mv-units-truncated when the units query reports truncated: true', () => {
    // Direct unit test of the label logic (per the fix-wave brief): the
    // mock builder never produces `truncated: true` (its dataset is far
    // under the real 2000-row slab, by design — see
    // mockFixtures.bankUnits.test.ts), so this overrides the
    // window.__hal0UseBankUnits global directly — the same lever the real
    // hook bridge uses — to a stub returning a fixed, already-resolved
    // result, rather than routing a real fetch through react-query's async
    // resolution just to reach a render-time branch.
    const realUseBankUnits = (globalThis as unknown as { __hal0UseBankUnits: unknown })
      .__hal0UseBankUnits
    ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = () => ({
      data: {
        items: [
          {
            id: 'f1',
            text: 't',
            context: 't',
            occurred_start: '2026-01-01',
            fact_type: 'world',
            tags: [],
            salience: 1,
            link_counts_by_type: {},
            state: 'valid',
          },
        ],
        total_matched: 2000,
        next_offset: null,
        truncated: true,
      },
      isError: false,
      isLoading: false,
    })

    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const MemV2Workspace = (globalThis as unknown as { MemV2Workspace: React.ComponentType<any> })
        .MemV2Workspace
      const html = renderToStaticMarkup(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(MemV2Workspace, { bank: 'primary', setBank: () => {}, sel: null, setSel: () => {} }),
        ),
      )

      expect(html).toContain('mv-units-truncated')
      expect(html).toContain('first 2000 matched')
    } finally {
      ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = realUseBankUnits
    }
  })

  it('does not render mv-units-truncated when truncated is false', () => {
    const realUseBankUnits = (globalThis as unknown as { __hal0UseBankUnits: unknown })
      .__hal0UseBankUnits
    ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = () => ({
      data: { items: [], total_matched: 0, next_offset: null, truncated: false },
      isError: false,
      isLoading: false,
    })

    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const MemV2Workspace = (globalThis as unknown as { MemV2Workspace: React.ComponentType<any> })
        .MemV2Workspace
      const html = renderToStaticMarkup(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(MemV2Workspace, { bank: 'primary', setBank: () => {}, sel: null, setSel: () => {} }),
        ),
      )

      expect(html).not.toContain('mv-units-truncated')
    } finally {
      ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = realUseBankUnits
    }
  })
})

describe('Inspector — live crash fix (entities as a comma-joined string)', () => {
  it('renders entities without throwing when the units-list row carries entities as a string, not an array', () => {
    // Live crash (2026-08-22): `f.entities.join is not a function`. The
    // Inspector reuses the units-LIST row verbatim (no separate
    // single-record fetch) — and per the documented backend contract, the
    // list endpoint's `entities` field is a comma-joined STRING, only
    // `GET .../memories/:id` returns a real array. `(f.entities ||
    // []).length` was truthy for a non-empty string too, so the old code
    // reached `.join` on a string and threw for any fact with entities set.
    const real = (globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits
    ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = () => ({
      data: {
        items: [
          {
            id: 'f-entities-string',
            text: 'fact with string-shaped entities',
            context: 'ctx',
            occurred_start: '2026-08-22T00:00:00Z',
            fact_type: 'world',
            tags: [],
            salience: 1,
            link_counts_by_type: {},
            state: 'valid',
            entities: 'hal0, memory-v2, CT105',
          },
        ],
        total_matched: 1,
        next_offset: null,
        truncated: false,
      },
      isError: false,
      isLoading: false,
    })

    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const MemV2Workspace = (globalThis as unknown as { MemV2Workspace: React.ComponentType<any> })
        .MemV2Workspace

      let html = ''
      expect(() => {
        html = renderToStaticMarkup(
          React.createElement(
            QueryClientProvider,
            { client: qc },
            React.createElement(MemV2Workspace, {
              bank: 'primary',
              setBank: () => {},
              sel: 'f-entities-string',
              setSel: () => {},
            }),
          ),
        )
      }).not.toThrow()

      expect(html).toContain('mv-inspector')
      expect(html).toContain('hal0 · memory-v2 · CT105')
    } finally {
      ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = real
    }
  })

  it('still renders a real array of entities correctly (single-record fetch shape, unaffected)', () => {
    const real = (globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits
    ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = () => ({
      data: {
        items: [
          {
            id: 'f-entities-array',
            text: 'fact with array-shaped entities',
            context: 'ctx',
            occurred_start: '2026-08-22T00:00:00Z',
            fact_type: 'world',
            tags: [],
            salience: 1,
            link_counts_by_type: {},
            state: 'valid',
            entities: ['hal0', 'memory-v2'],
          },
        ],
        total_matched: 1,
        next_offset: null,
        truncated: false,
      },
      isError: false,
      isLoading: false,
    })

    try {
      const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
      const MemV2Workspace = (globalThis as unknown as { MemV2Workspace: React.ComponentType<any> })
        .MemV2Workspace
      const html = renderToStaticMarkup(
        React.createElement(
          QueryClientProvider,
          { client: qc },
          React.createElement(MemV2Workspace, {
            bank: 'primary',
            setBank: () => {},
            sel: 'f-entities-array',
            setSel: () => {},
          }),
        ),
      )

      expect(html).toContain('hal0 · memory-v2')
    } finally {
      ;(globalThis as unknown as { __hal0UseBankUnits: unknown }).__hal0UseBankUnits = real
    }
  })
})

describe('MemV2EgoGraph (smoke)', () => {
  it('mounts under QueryClientProvider with the real hook globals without throwing', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const MemV2EgoGraph = (globalThis as unknown as { MemV2EgoGraph: React.ComponentType<any> })
      .MemV2EgoGraph
    expect(typeof MemV2EgoGraph).toBe('function')

    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(MemV2EgoGraph, { bank: 'primary', centerId: 'f1', onGo: () => {} }),
      ),
    )

    expect(html).toContain('mv-ego')
    expect(html).toContain('mv-ego-depth')
  })

  it('renders a select-a-fact empty state with no centerId, without a depth slider', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const MemV2EgoGraph = (globalThis as unknown as { MemV2EgoGraph: React.ComponentType<any> })
      .MemV2EgoGraph

    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(MemV2EgoGraph, { bank: 'primary', centerId: null, onGo: () => {} }),
      ),
    )

    expect(html).toContain('mv-ego')
    expect(html).not.toContain('mv-ego-depth')
  })
})
