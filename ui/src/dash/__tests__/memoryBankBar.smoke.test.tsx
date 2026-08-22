// Memory v2 BankBar + Add modal (task C3) — mount smoke test.
//
// Same rationale/pattern as memoryOverviewV2.smoke.test.tsx (task C2):
// neither component is routed at #memory yet (C6 wires #memory/bank), and
// the brief forbids touching memory.jsx/agent-view.jsx to force a route
// just for this spec — so the Playwright coverage
// (../../../tests/e2e/specs/memory-v2-bankbar.spec.ts) is written but
// `.skip`-ed with a "C6 unskips" note, and this vitest smoke test instead
// proves `window.MemV2BankBar` and `window.MemV2AddModal` mount under a
// real QueryClientProvider with the real hook globals installed (via
// memory-hook-bridge + memory-v2-shared + memory-overview-v2, exactly as
// main.tsx wires them) without throwing.
//
// `fetch` is stubbed to a never-settling promise, same as C2's smoke test —
// this is a mount/crash check (queries sit in `isLoading`), not a
// data-rendering assertion.
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
await import('../memory-overview-v2.jsx') // publishes window.TypeBar / window.Spark, reused by BankBar
await import('../memory-bank-bar.jsx')

function mount(el: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return renderToStaticMarkup(React.createElement(QueryClientProvider, { client: qc }, el))
}

describe('MemV2BankBar (smoke)', () => {
  it('mounts under QueryClientProvider with the real hook globals without throwing', () => {
    const MemV2BankBar = (globalThis as unknown as { MemV2BankBar: React.ComponentType<any> })
      .MemV2BankBar
    expect(typeof MemV2BankBar).toBe('function')

    // No bank data resolves synchronously (fetch never settles), so BankBar
    // returns null (its own `if (!b) return null` guard) — this still
    // proves the hook wiring/render path doesn't throw, which is the bar
    // for a mount smoke test.
    const html = mount(React.createElement(MemV2BankBar, { bank: 'primary', setBank: () => {} }))
    expect(typeof html).toBe('string')
  })
})

describe('MemV2AddModal (smoke)', () => {
  it('mounts under QueryClientProvider with the real hook globals without throwing', () => {
    const MemV2AddModal = (globalThis as unknown as { MemV2AddModal: React.ComponentType<any> })
      .MemV2AddModal
    expect(typeof MemV2AddModal).toBe('function')

    const html = mount(
      React.createElement(MemV2AddModal, { bank: 'primary', tab0: 'fact', onClose: () => {} }),
    )
    expect(typeof html).toBe('string')
    expect(html).toContain('mv-add-modal')
  })
})
