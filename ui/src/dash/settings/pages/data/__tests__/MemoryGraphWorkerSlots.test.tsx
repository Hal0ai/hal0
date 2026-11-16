// @vitest-environment happy-dom
// #3700 — the "Worker slots" group of the memory graph panel: the total, the
// three reservations, the live shared-pool line, inline validation by the
// server's rule, and the PUT body a save sends.
import React, { act } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { cleanup, mount } from '../../../../memory-area/shared/test-harness'

// primitives.jsx (via ExtractionThroughputViews) reads React as a global.
;(globalThis as any).React = React

const LIMITS = {
  max_concurrent: 1,
  max_tokens: 16384,
  llm_retries: 1,
  task_retries: 2,
  retry_backoff_s: 120,
  worker_slots: 4,
  retain_reserved_slots: 1,
  consolidation_reserved_slots: 2,
  refresh_reserved_slots: 0,
}

const state: { status: any; sent: any[] } = { status: null, sent: [] }

vi.mock('@/api/hooks/useMemory', () => ({
  useMemoryGraphStatus: () => ({ data: state.status, isError: false }),
  useRetryFailedExtractions: () => ({ isPending: false, mutateAsync: async () => ({}) }),
  useUpdateMemoryGraph: () => ({
    isPending: false,
    mutateAsync: async (body: unknown) => {
      state.sent.push(body)
      return {}
    },
  }),
}))

const { MemoryGraphPanel } = await import('../MemoryPage.jsx')
// SRow renders its `sub` through the FieldInfoIcon window global; primitives.jsx
// installs a portal version on import, so the stub goes in after it.
;(globalThis as any).FieldInfoIcon = ({ description }: any) => <span data-sub={description} />

const q = (host: HTMLElement, id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

function setValue(el: Element, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
}

function status(limits: Record<string, number> = LIMITS) {
  return {
    enabled: true,
    extraction_slot: 'memory',
    extraction_model: null,
    available_slots: ['memory'],
    slot_resolves: true,
    llm_timeout_s: 300,
    extraction_limits: limits,
    in_flight: 0,
    builds_ok: 0,
    errors: 0,
  }
}

beforeEach(() => {
  state.status = status()
  state.sent = []
  ;(window as any).__hal0Toast = vi.fn()
})
afterEach(cleanup)

describe('memory graph worker slots', () => {
  it('shows the total, the three reservations and the shared pool they leave', () => {
    const { host } = mount(<MemoryGraphPanel />)
    expect((q(host, 'mem-graph-limit-worker_slots') as HTMLInputElement).value).toBe('4')
    expect((q(host, 'mem-graph-limit-retain_reserved_slots') as HTMLInputElement).value).toBe('1')
    expect((q(host, 'mem-graph-limit-consolidation_reserved_slots') as HTMLInputElement).value).toBe('2')
    expect((q(host, 'mem-graph-limit-refresh_reserved_slots') as HTMLInputElement).value).toBe('0')
    expect(q(host, 'mem-graph-slots-shared')!.textContent).toContain('1 shared slot left for everything else')
    // The save restarts the engine, and the panel says so.
    expect(q(host, 'mem-graph-slots-help')!.textContent).toContain('restarts the engine')
  })

  it('updates the shared pool as the numbers change and blocks a save that leaves none', () => {
    const { host } = mount(<MemoryGraphPanel />)
    const save = q(host, 'mem-graph-save') as HTMLButtonElement
    setValue(q(host, 'mem-graph-limit-worker_slots')!, '6')
    expect(q(host, 'mem-graph-slots-shared')!.textContent).toContain('3 shared slots left')
    expect(save.disabled).toBe(false)

    setValue(q(host, 'mem-graph-limit-refresh_reserved_slots')!, '3')
    const line = q(host, 'mem-graph-slots-shared')!
    expect(line.getAttribute('role')).toBe('alert')
    expect(line.textContent).toContain('take 6 of 6 slots')
    expect(line.textContent).toContain('reserve at most 5')
    expect(q(host, 'mem-graph-limit-refresh_reserved_slots')!.getAttribute('aria-invalid')).toBe('true')
    expect(save.disabled).toBe(true)
  })

  it('sends only the changed slot keys through the memory graph PUT', async () => {
    const { host } = mount(<MemoryGraphPanel />)
    setValue(q(host, 'mem-graph-limit-worker_slots')!, '6')
    setValue(q(host, 'mem-graph-limit-retain_reserved_slots')!, '2')
    await act(async () => {
      ;(q(host, 'mem-graph-save') as HTMLButtonElement).click()
    })
    expect(state.sent).toHaveLength(1)
    expect(state.sent[0]).toMatchObject({ extraction_worker_slots: 6, retain_reserved_slots: 2 })
    expect(state.sent[0]).not.toHaveProperty('consolidation_reserved_slots')
    expect(state.sent[0]).not.toHaveProperty('refresh_reserved_slots')
  })

  it('leaves the reservations out for a hal0-api that does not report them', () => {
    const { worker_slots, max_concurrent, max_tokens, llm_retries, task_retries, retry_backoff_s } = LIMITS
    state.status = status({ worker_slots, max_concurrent, max_tokens, llm_retries, task_retries, retry_backoff_s })
    const { host } = mount(<MemoryGraphPanel />)
    expect(q(host, 'mem-graph-limit-worker_slots')).not.toBeNull()
    expect(q(host, 'mem-graph-limit-retain_reserved_slots')).toBeNull()
    expect(q(host, 'mem-graph-slots-shared')).toBeNull()
    expect(q(host, 'mem-graph-reservations-unsupported')).not.toBeNull()
  })
})
