// @vitest-environment happy-dom
//
// Memory v2 BankBar — per-bank description (reflect mission) inline edit.
//
// The description under the bank picker is Hindsight's `reflect_mission`
// config override for that bank. It was display-only; the only writer was
// `hal0 memory bank profile set --reflect-mission`. This drives the inline
// editor end to end in the memoryBankBarDelete.test.tsx mold: the
// window.__hal0Use* hook globals are contract-shaped fakes, the component
// renders for real, and the save must hit useBankConfigUpdate with exactly
// `{bank, updates: {reflect_mission}}` — the CLI's wire shape.
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

const BANKS = [
  { bank_id: 'primary', mission: 'You are the memory of the primary repo.' },
  { bank_id: 'scratch', mission: null },
]

const configCalls: unknown[] = []
const toasts: unknown[] = []
let failNextSave: Error | null = null

Object.assign(globalThis as unknown as Record<string, unknown>, {
  __hal0Toast: (msg: string, kind: string) => toasts.push([msg, kind]),
  __hal0UseMemoryBanks: () => ({ data: { banks: BANKS } }),
  __hal0UseBankStats: () => ({ data: { total_nodes: 1, total_links: 0, total_documents: 0 } }),
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
  __hal0UseBankDelete: () => ({ isPending: false, mutateAsync: async () => ({}) }),
  __hal0UseBankConfigUpdate: () => ({
    isPending: false,
    mutateAsync: async (vars: unknown) => {
      configCalls.push(vars)
      if (failNextSave) {
        const err = failNextSave
        failNextSave = null
        throw err
      }
      return {}
    },
  }),
})

const BankBar = (globalThis as unknown as { MemV2BankBar: React.ComponentType<any> }).MemV2BankBar

function mountBar(bank = 'primary'): { host: HTMLElement; root: ReturnType<typeof createRoot> } {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(BankBar, { bank, setBank: () => {} }))
  })
  return { host, root }
}

function setNativeValue(el: HTMLTextAreaElement, value: string): void {
  const set = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set
  set?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('BankBar per-bank description (reflect mission) edit', () => {
  afterEach(() => {
    configCalls.length = 0
    toasts.length = 0
    failNextSave = null
    document.body.innerHTML = ''
  })

  it('shows the selected bank\'s own mission and opens the editor pre-filled with it', () => {
    const { host, root } = mountBar()
    const btn = host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.textContent).toBe('You are the memory of the primary repo.')
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()

    act(() => { btn.click() })
    const input = host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement
    expect(input).toBeTruthy()
    expect(input.value).toBe('You are the memory of the primary repo.')
    act(() => { root.unmount() })
  })

  it('a bank without a mission shows the placeholder, not another bank\'s text', () => {
    const { host, root } = mountBar('scratch')
    const btn = host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement
    expect(btn.textContent).toContain('no description set')
    act(() => { btn.click() })
    const input = host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement
    expect(input.value).toBe('')
    act(() => { root.unmount() })
  })

  it('Save writes reflect_mission for THIS bank through the config PATCH and closes the editor', async () => {
    const { host, root } = mountBar()
    act(() => {
      ;(host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement).click()
    })
    const input = host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement
    act(() => { setNativeValue(input, '  You are the release-notes memory.  ') })

    const save = host.querySelector('[data-testid="mv-bank-mission-save"]') as HTMLButtonElement
    await act(async () => { save.click() })

    expect(configCalls).toEqual([
      { bank: 'primary', updates: { reflect_mission: 'You are the release-notes memory.' } },
    ])
    expect(toasts).toContainEqual(['Description saved', 'ok'])
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()
    act(() => { root.unmount() })
  })

  it('an unchanged draft closes without a write; Cancel and Escape never write', async () => {
    const { host, root } = mountBar()
    const open = () =>
      act(() => {
        ;(host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement).click()
      })

    open()
    await act(async () => {
      ;(host.querySelector('[data-testid="mv-bank-mission-save"]') as HTMLButtonElement).click()
    })
    expect(configCalls).toEqual([])
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()

    open()
    act(() => {
      const input = host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement
      setNativeValue(input, 'draft that must not land')
      const cancel = Array.from(host.querySelectorAll('[data-testid="mv-bank-mission-form"] button')).find(
        (b) => b.textContent === 'Cancel',
      ) as HTMLButtonElement
      cancel.click()
    })
    expect(configCalls).toEqual([])
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()

    open()
    act(() => {
      const input = host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    expect(configCalls).toEqual([])
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()
    act(() => { root.unmount() })
  })

  // Codex review on #2916: the draft must stay bound to the bank that opened
  // the editor, a bank the list does not know must not be editable (a config
  // PATCH to an unknown id would create that bank), and Escape must not leak
  // to the workspace's window-level handler (which closes the fact inspector).

  it('switching banks while editing closes the editor instead of saving onto the new bank', async () => {
    const { host, root } = mountBar('primary')
    act(() => {
      ;(host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement).click()
    })
    act(() => {
      setNativeValue(
        host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement,
        'text meant for primary',
      )
    })
    // The bank selector changes underneath the open editor.
    act(() => {
      root.render(React.createElement(BankBar, { bank: 'scratch', setBank: () => {} }))
    })
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()
    expect((host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement).textContent).toContain(
      'no description set',
    )
    expect(configCalls).toEqual([])
    act(() => { root.unmount() })
  })

  it('a bank id the list does not know cannot be edited (no PATCH that would mint it)', () => {
    const { host, root } = mountBar('ghost-from-stale-localstorage')
    const btn = host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    expect(btn.disabled).toBe(true)
    act(() => { btn.click() })
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()
    expect(configCalls).toEqual([])
    act(() => { root.unmount() })
  })

  it('Escape cancels the editor without reaching window-level keydown handlers', () => {
    const seen: string[] = []
    const spy = (e: KeyboardEvent) => { seen.push(e.key) }
    window.addEventListener('keydown', spy)
    const { host, root } = mountBar()
    act(() => {
      ;(host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement).click()
    })
    act(() => {
      const input = host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    })
    window.removeEventListener('keydown', spy)
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeNull()
    expect(seen).toEqual([])
    act(() => { root.unmount() })
  })

  it('a failed save keeps the editor open and surfaces the error', async () => {
    failNextSave = new Error('memory engine returned an error')
    const { host, root } = mountBar()
    act(() => {
      ;(host.querySelector('[data-testid="mv-bank-mission"]') as HTMLButtonElement).click()
    })
    act(() => {
      setNativeValue(
        host.querySelector('[data-testid="mv-bank-mission-input"]') as HTMLTextAreaElement,
        'new text',
      )
    })
    await act(async () => {
      ;(host.querySelector('[data-testid="mv-bank-mission-save"]') as HTMLButtonElement).click()
    })
    expect(configCalls).toHaveLength(1)
    expect(toasts).toContainEqual(['Save failed: memory engine returned an error', 'err'])
    expect(host.querySelector('[data-testid="mv-bank-mission-input"]')).toBeTruthy()
    act(() => { root.unmount() })
  })
})
