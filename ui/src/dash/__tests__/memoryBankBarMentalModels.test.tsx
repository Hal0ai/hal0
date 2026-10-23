// @vitest-environment happy-dom
//
// Memory v2 BankBar — mental-model rows and the per-bank refresh floor (#2920).
//
// Hindsight's `is_stale` means "the bank was written since this model last
// refreshed", so on a busy bank every model is stale minutes after a refresh
// and the bank bar painted every row amber ("14 stale") although the models
// refresh constantly. Rows now always show when they last refreshed; the
// warning is reserved for a refresh that FAILED after its last success, and
// new-memories-since is a quiet hint. The bank's
// `mental_model_min_refresh_interval_seconds` is editable here (it caps how
// often consolidation can trigger a refresh).
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, describe, expect, it } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

await import('../primitives.jsx')
await import('../memory-v2-shared.jsx')
await import('../memory-bank-bar.jsx')

const MODELS = [
  {
    id: 'mm-ok', name: 'Component map', source_query: 'q', is_stale: true,
    last_refreshed_at: '2026-10-23T00:00:33+00:00', last_refresh_failed_at: null,
  },
  {
    id: 'mm-failed', name: 'Drafters', source_query: 'q', is_stale: true,
    last_refreshed_at: '2026-10-21T06:28:04+00:00', last_refresh_failed_at: '2026-10-22T23:57:12+00:00',
  },
  {
    id: 'mm-recovered', name: 'Core concepts', source_query: 'q', is_stale: false,
    last_refreshed_at: '2026-10-23T01:00:00+00:00', last_refresh_failed_at: '2026-10-22T10:00:00+00:00',
  },
]

const configCalls: unknown[] = []
let bankConfig: Record<string, unknown> | null = { mental_model_min_refresh_interval_seconds: 14400 }

Object.assign(globalThis as unknown as Record<string, unknown>, {
  __hal0Toast: () => {},
  __hal0UseMemoryBanks: () => ({ data: { banks: [{ bank_id: 'primary', mission: 'm' }] } }),
  __hal0UseBankStats: () => ({ data: { total_nodes: 1, total_links: 0, total_documents: 0 } }),
  __hal0UseBankTimeseries: () => ({ data: null }),
  __hal0UseBankOperations: () => ({ data: { operations: [] } }),
  __hal0MemSummarizeOps: () => ({ pending: 0, processing: 0 }),
  __hal0UseReflect: () => ({ mutate: () => {}, isPending: false, data: null, reset: () => {} }),
  __hal0UseDirectives: () => ({ data: { items: [] } }),
  __hal0UseDirectiveUpdate: () => ({ mutate: () => {} }),
  __hal0UseDirectiveDelete: () => ({ mutate: () => {} }),
  __hal0UseMentalModels: () => ({ data: { items: MODELS } }),
  __hal0UseMentalModelRefresh: () => ({ mutate: () => {} }),
  __hal0UseConsolidate: () => ({ mutate: () => {}, isPending: false }),
  __hal0UseBankDelete: () => ({ isPending: false, mutateAsync: async () => ({}) }),
  __hal0UseBankConfig: () => ({ data: bankConfig ? { bank_id: 'primary', config: bankConfig } : undefined }),
  __hal0UseBankConfigUpdate: () => ({
    isPending: false,
    mutateAsync: async (vars: unknown) => {
      configCalls.push(vars)
      return {}
    },
  }),
})

const BankBar = (globalThis as unknown as { MemV2BankBar: React.ComponentType<any> }).MemV2BankBar

function mountRules(): { host: HTMLElement; root: ReturnType<typeof createRoot> } {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(BankBar, { bank: 'primary', setBank: () => {} }))
  })
  act(() => {
    ;(host.querySelector('[data-testid="mv-rules-tab"]') as HTMLButtonElement).click()
  })
  return { host, root }
}

describe('BankBar mental-model rows (#2920)', () => {
  afterEach(() => {
    configCalls.length = 0
    bankConfig = { mental_model_min_refresh_interval_seconds: 14400 }
    document.body.innerHTML = ''
  })

  it('a stale-but-refreshing model shows its refresh date, not an amber "stale" chip', () => {
    const { host, root } = mountRules()
    const row = host.querySelector('[data-testid="mv-rule-row-mm-ok"]') as HTMLElement
    expect(row.textContent).toContain('2026-10-23')
    expect(row.querySelector('.mv-chip.warn')).toBeNull()
    expect(row.querySelector('[data-testid="mv-mm-new-since"]')).toBeTruthy()
    act(() => { root.unmount() })
  })

  it('only a refresh that failed after its last success is a warning', () => {
    const { host, root } = mountRules()
    const failed = host.querySelector('[data-testid="mv-rule-row-mm-failed"]') as HTMLElement
    expect(failed.querySelector('.mv-chip.warn')?.textContent).toBe('refresh failed')
    expect(failed.textContent).toContain('2026-10-21')
    const recovered = host.querySelector('[data-testid="mv-rule-row-mm-recovered"]') as HTMLElement
    expect(recovered.querySelector('.mv-chip.warn')).toBeNull()
    act(() => { root.unmount() })
  })

  it('the header counts failed refreshes, not stale models', () => {
    const { host, root } = mountRules()
    const head = host.querySelector('[data-testid="mv-mm-head"]') as HTMLElement
    expect(head.textContent).toContain('1 failed')
    expect(head.textContent).not.toContain('stale')
    act(() => { root.unmount() })
  })

  it('shows the bank refresh floor and saves a new one through the config PATCH', async () => {
    const { host, root } = mountRules()
    const sel = host.querySelector('[data-testid="mv-mm-floor"]') as HTMLSelectElement
    expect(sel.value).toBe('14400')
    await act(async () => {
      const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(sel), 'value')?.set
      set?.call(sel, '3600')
      sel.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(configCalls).toEqual([
      { bank: 'primary', updates: { mental_model_min_refresh_interval_seconds: 3600 } },
    ])
    act(() => { root.unmount() })
  })

  it('an unset floor reads as refresh after every consolidation', () => {
    bankConfig = { mental_model_min_refresh_interval_seconds: null }
    const { host, root } = mountRules()
    const sel = host.querySelector('[data-testid="mv-mm-floor"]') as HTMLSelectElement
    expect(sel.value).toBe('0')
    act(() => { root.unmount() })
  })

  it('while the config is loading or failed the selector does not claim a value', () => {
    bankConfig = null
    const { host, root } = mountRules()
    const sel = host.querySelector('[data-testid="mv-mm-floor"]') as HTMLSelectElement
    expect(sel.disabled).toBe(true)
    expect(sel.value).toBe('')
    expect(sel.textContent).not.toContain('after every consolidation')
    act(() => { root.unmount() })
  })

  it('keeps a custom floor value pickable', () => {
    bankConfig = { mental_model_min_refresh_interval_seconds: 5400 }
    const { host, root } = mountRules()
    const sel = host.querySelector('[data-testid="mv-mm-floor"]') as HTMLSelectElement
    expect(sel.value).toBe('5400')
    act(() => { root.unmount() })
  })
})
