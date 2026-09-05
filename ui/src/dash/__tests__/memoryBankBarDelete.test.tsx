// @vitest-environment happy-dom
//
// Memory v2 BankBar — bank-delete confirm flow (#2107).
//
// The only UI path to delete a bank used to live in the pre-v2 memory.jsx
// MemBankDetail panel, which the v2 rewrite left unreachable — so bank
// deletion had no dashboard surface at all. #2107 moved it onto the Bank
// workspace's BankBar behind the shared ConfirmDialog (same idiom as slot
// delete: destructive + type-the-id-to-confirm). This is a REAL render +
// click test in the runner-images-confirm-flow.test.tsx mold: primitives.jsx's
// ConfirmDialog/Modal render for real, the window.__hal0Use* hook globals the
// no-ES-imports .jsx module reads are test-installed fakes, and the flow is
// driven end to end — trash button → dialog with blast radius → type the bank
// id → Delete bank fires useBankDelete and reselects the next bank.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
// Icon glyphs for primitives.jsx surfaces come from chrome.jsx's window
// global — irrelevant here.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

// primitives.jsx publishes window.ConfirmDialog/Modal (the globals
// memory-bank-bar.jsx reads); memory-v2-shared publishes window.MemV2.
await import('../primitives.jsx')
await import('../memory-v2-shared.jsx')
await import('../memory-bank-bar.jsx')

const BANKS = [
  { bank_id: 'primary', mission: 'the main bank' },
  { bank_id: 'scratch', mission: null },
]

const deleteCalls: unknown[] = []
const setBankCalls: unknown[] = []
const toasts: unknown[] = []

// BankBar reads every hook off window.__hal0Use* (memory-hook-bridge.ts's
// contract) — install contract-shaped fakes so no QueryClient is needed.
Object.assign(globalThis as unknown as Record<string, unknown>, {
  __hal0Toast: (msg: string, kind: string) => toasts.push([msg, kind]),
  __hal0UseMemoryBanks: () => ({ data: { banks: BANKS } }),
  __hal0UseBankStats: () => ({
    data: {
      total_nodes: 12,
      total_links: 3,
      total_documents: 2,
      nodes_by_fact_type: { world: 5, experience: 4, observation: 3 },
    },
  }),
  __hal0UseBankTimeseries: () => ({ data: null }),
  __hal0UseBankOperations: () => ({ data: { operations: [] } }),
  __hal0MemSummarizeOps: () => ({ pending: 0, processing: 0 }),
  __hal0UseReflect: () => ({ mutate: () => {}, isPending: false, data: null, reset: () => {} }),
  __hal0UseDirectives: () => ({ data: { items: [] } }),
  __hal0UseDirectiveUpdate: () => ({ mutate: () => {} }),
  __hal0UseDirectiveDelete: () => ({ mutate: () => {} }),
  __hal0UseMentalModels: () => ({ data: { items: [] } }),
  __hal0UseMentalModelRefresh: () => ({ mutate: () => {} }),
  __hal0UseConsolidate: () => ({ mutate: () => {}, isPending: false }),
  __hal0UseBankDelete: () => ({
    isPending: false,
    mutateAsync: async (bank: string) => {
      deleteCalls.push(bank)
      return {}
    },
  }),
})

const BankBar = (globalThis as unknown as { MemV2BankBar: React.ComponentType<any> }).MemV2BankBar

function mountBar(): { host: HTMLElement; root: ReturnType<typeof createRoot> } {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(
      React.createElement(BankBar, {
        bank: 'primary',
        setBank: (id: unknown) => setBankCalls.push(id),
      }),
    )
  })
  return { host, root }
}

describe('BankBar bank-delete confirm flow (#2107)', () => {
  afterEach(() => {
    deleteCalls.length = 0
    setBankCalls.length = 0
    toasts.length = 0
    document.body.innerHTML = ''
  })

  it('trash button opens a destructive type-to-confirm dialog with the blast radius', () => {
    const { host, root } = mountBar()

    const btn = host.querySelector('[data-testid="mv-bank-delete"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { btn.click() })
    expect(host.querySelector('.modal-backdrop')).toBeTruthy()
    expect(host.textContent).toContain('Delete bank "primary"?')
    // Blast radius sourced from the live bank stats.
    const blast = host.querySelector('[data-testid="mv-bank-delete-blast"]')
    expect(blast?.textContent).toContain('12')
    expect(blast?.textContent).toContain('facts')
    expect(blast?.textContent).toContain('documents')
    // Type-to-confirm gate: Delete bank stays disabled until the id is echoed.
    const confirm = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent === 'Delete bank',
    ) as HTMLButtonElement
    expect(confirm).toBeTruthy()
    expect(confirm.disabled).toBe(true)

    act(() => { root.unmount() })
  })

  it('typing the bank id arms Delete bank; confirm fires the delete and moves selection', async () => {
    const { host, root } = mountBar()
    act(() => {
      ;(host.querySelector('[data-testid="mv-bank-delete"]') as HTMLButtonElement).click()
    })

    const input = host.querySelector('.modal-backdrop input') as HTMLInputElement
    expect(input).toBeTruthy()
    act(() => {
      // React reads value through its own tracker — set via the native setter
      // so the change event isn't swallowed as a no-op.
      const set = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(input),
        'value',
      )?.set
      set?.call(input, 'primary')
      input.dispatchEvent(new Event('input', { bubbles: true }))
    })

    const confirm = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent === 'Delete bank',
    ) as HTMLButtonElement
    expect(confirm.disabled).toBe(false)

    await act(async () => { confirm.click() })
    expect(deleteCalls).toEqual(['primary'])
    // Selection falls over to the surviving bank, never the deleted one.
    expect(setBankCalls).toEqual(['scratch'])
    expect(toasts).toContainEqual(['Bank primary deleted', 'ok'])
    // Dialog is gone.
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { root.unmount() })
  })

  it('cancel closes the dialog without deleting', () => {
    const { host, root } = mountBar()
    act(() => {
      ;(host.querySelector('[data-testid="mv-bank-delete"]') as HTMLButtonElement).click()
    })
    const cancel = Array.from(host.querySelectorAll('button')).find(
      (b) => b.textContent === 'Cancel',
    ) as HTMLButtonElement
    expect(cancel).toBeTruthy()
    act(() => { cancel.click() })
    expect(host.querySelector('.modal-backdrop')).toBeNull()
    expect(deleteCalls).toEqual([])
    act(() => { root.unmount() })
  })
})
