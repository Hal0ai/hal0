// @vitest-environment happy-dom
//
// The paused slot card: the chip and the wait note SlotCards / MiniCards draw while image mode has
// paused a slot (SlotPause.jsx, slotPause in status-line-parts-pure). The hooks the cards read are
// mocked at the module boundary, the way inference-pane-pickers.test.tsx mocks its own.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
// primitives.jsx reads `Icons` off the window at render time.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: [], isLoading: false }),
  useSlotLoad: () => ({ mutate: () => {} }),
  useSlotUnload: () => ({ mutate: () => {} }),
  useSlotSwap: () => ({ mutate: () => {} }),
  useSlotRestart: () => ({ mutate: () => {} }),
  useSlotEdit: () => ({ isPending: false, mutate: () => {}, mutateAsync: async () => {} }),
}))
vi.mock('@/api/hooks/useModelsFeasibility', () => ({
  useModelsFeasibility: () => ({ data: undefined, mutate: () => {} }),
}))
vi.mock('@/api/hooks/useProfiles', () => ({ useProfiles: () => ({ data: [] }) }))
vi.mock('@/api/hooks/useRuntimes', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSystemInfo: () => ({ data: { backends: {} } }),
}))

const { SlotCards, MiniCards } = await import('../inference-pane.jsx')

const arbiterBlock = (over: Json = {}) => ({
  mode: 'llm',
  pinned: false,
  saved_llm_slots: [],
  switching: false,
  image_jobs: 0,
  phase: null,
  restore_at: null,
  handoff_until: null,
  drain_in_flight: 0,
  unload_set: ['brain', 'coder'],
  llm_waiters: { count: 0, oldest_s: null },
  ...over,
})

const roots: { root: Root; host: HTMLElement }[] = []

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  act(() => {
    root.render(<QueryClientProvider client={client}>{el}</QueryClientProvider>)
  })
  roots.push({ root, host })
  return host
}

afterEach(() => {
  for (const { root, host } of roots.splice(0)) {
    act(() => root.unmount())
    host.remove()
  }
  document.body.innerHTML = ''
})

describe('paused slot cards', () => {
  const handlers = {
    onSwap: () => {},
    onStart: () => {},
    onStop: () => {},
    onRestart: () => {},
    onLogs: () => {},
    onEdit: () => {},
    onSetDefault: () => {},
  }
  const stopped = (name: string) => ({
    name,
    type: 'llm',
    device: 'gpu-vulkan',
    profile: 'rocm',
    binary: '',
    container_status: 'exited',
    container_health: false,
  })
  const running = (name: string) => ({ ...stopped(name), container_status: 'running', container_health: true })

  const cards = (slots: Json[], arbiter: unknown) =>
    mount(
      <SlotCards
        rows={slots.map((s) => ({ s, ind: { cls: 'offline', tooltip: '' } }))}
        full={false}
        models={[]}
        allSlots={slots}
        busyName={null}
        handlers={handlers}
        loading={false}
        modelRows={() => null}
        arbiter={arbiter}
      />,
    )

  it('slot card shows paused chip and wait line in image mode', () => {
    const host = cards(
      [stopped('brain'), stopped('other')],
      arbiterBlock({ mode: 'img', phase: 'grace', unload_set: ['brain'], llm_waiters: { count: 3, oldest_s: 65 } }),
    )
    const chip = host.querySelector('[data-testid="slot-paused-brain"]')!
    expect(chip.textContent).toBe('paused · image mode')
    const card = host.querySelector('[data-testid="infer-slot-brain"]')!
    expect(card.textContent).toContain('Reloads about 10 s after the last image.')
    expect(card.textContent).toContain('3 chat requests waiting, longest 1:05 of 2:00.')
    // A slot outside the set is not paused.
    expect(host.querySelector('[data-testid="slot-paused-other"]')).toBeNull()
    expect(host.querySelector('[data-testid="infer-slot-other"]')!.textContent).not.toContain('Reloads about')
  })

  it('shows no chip in chat mode, or on a slot that is still running', () => {
    const chat = cards([stopped('brain')], arbiterBlock({ mode: 'llm', unload_set: ['brain'] }))
    expect(chat.querySelector('[data-testid="slot-paused-brain"]')).toBeNull()

    const still = cards([running('brain')], arbiterBlock({ mode: 'img', unload_set: ['brain'] }))
    expect(still.querySelector('[data-testid="slot-paused-brain"]')).toBeNull()
  })

  it('puts the chip, without the note, on a paused utility slot', () => {
    const slots = [stopped('embed'), stopped('rerank')]
    const host = mount(
      <MiniCards
        rows={slots.map((s) => ({ s, ind: { cls: 'offline', tooltip: '' } }))}
        busyName={null}
        handlers={handlers}
        arbiter={arbiterBlock({ mode: 'img', unload_set: ['embed'], llm_waiters: { count: 1, oldest_s: 3 } })}
      />,
    )
    expect(host.querySelector('[data-testid="slot-paused-embed"]')!.textContent).toBe('paused · image mode')
    expect(host.querySelector('[data-testid="slot-paused-rerank"]')).toBeNull()
    expect(host.querySelector('[data-testid="slot-paused-note-embed"]')).toBeNull()
  })

  it('shows no chip without an arbiter answer', () => {
    const host = cards([stopped('brain')], undefined)
    expect(host.querySelector('[data-testid="slot-paused-brain"]')).toBeNull()
  })
})
