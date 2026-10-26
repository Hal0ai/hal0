// @vitest-environment happy-dom
//
// The slot rail beside the Inference pane. The hooks are mocked at the module boundary; what is
// pinned here is what the rail draws for the slots it is given, what a row click and a
// hal0:slot-event do, and that "+ New slot" calls back.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

const { box } = vi.hoisted(() => ({
  box: {
    slots: [] as unknown[],
    arbiter: undefined as unknown,
    memory: undefined as unknown,
  },
}))

vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSlots: () => ({ data: box.slots, isLoading: false }),
}))
vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useArbiter: () => ({ data: box.arbiter }),
}))
vi.mock('@/api/hooks/useStatsHardware', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useMemoryBudget: () => ({ data: box.memory }),
}))

const { SlotRail } = await import('../slot-rail.jsx')

const slot = (name: string, device: string, state: string, extra: Json = {}) => ({
  name,
  type: 'llm',
  device,
  model: `${name}.gguf`,
  state,
  metrics: {},
  ...extra,
})

let host: HTMLDivElement
let root: Root

const mount = async (onNew: () => void = () => {}) => {
  await act(async () => {
    root.render(<SlotRail onNew={onNew} />)
  })
}

const q = (sel: string) => host.querySelector(sel) as HTMLElement | null
const row = (name: string) => q(`[data-testid="slot-rail-row"][data-slot="${name}"]`) as HTMLElement
const slotEvent = (name: string) =>
  act(async () => {
    window.dispatchEvent(new CustomEvent('hal0:slot-event', { detail: { slot: name } }))
  })

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
  box.slots = [
    slot('agent', 'gpu-vulkan', 'stopped'),
    slot('brain', 'gpu-vulkan', 'serving', { metrics: { toks: 42 }, mem_mb: 2048 }),
    slot('flm', 'npu', 'ready', { mem_mb: 1024 }),
  ]
  box.arbiter = { arbiter: { mode: 'llm', unload_set: null } }
  box.memory = { gpu: { total_gb: 96, used_gb: 80, this_box: [] }, render_free_gb: 40 }
})

afterEach(async () => {
  await act(async () => root.unmount())
  host.remove()
  vi.useRealTimers()
  document.body.innerHTML = ''
})

describe('SlotRail', () => {
  it('renders one row per slot under its device group with the indicator class', async () => {
    await mount()
    const rail = q('[data-testid="slot-rail"]')!
    expect(rail.tagName).toBe('ASIDE')
    expect(rail.getAttribute('aria-label')).toBe('Slots')
    expect(
      Array.from(host.querySelectorAll('[data-testid="slot-rail-row"]')).map((r) => r.getAttribute('data-slot')),
    ).toEqual(['brain', 'agent', 'flm'])
    expect(Array.from(host.querySelectorAll('.rail-grp')).map((g) => g.textContent)).toEqual([
      expect.stringContaining('GPU · vulkan'),
      expect.stringContaining('NPU'),
    ])
    expect(host.querySelector('.rail-grp')!.textContent).toContain('1 / 2 up')
    expect(row('brain').querySelector('.sdot')!.classList.contains('serving')).toBe(true)
    expect(row('flm').querySelector('.sdot')!.classList.contains('stale')).toBe(true)
    expect(row('flm').querySelector('.sdot')!.classList.contains('npu')).toBe(true)
    expect(row('agent').querySelector('.sdot')!.classList.contains('offline')).toBe(true)
    expect(row('agent').classList.contains('off')).toBe(true)
    expect(row('brain').classList.contains('off')).toBe(false)
    expect(row('brain').querySelector('.st')!.textContent).toBe('42 tok/s')
    expect(row('brain').querySelector('.mdl')!.textContent).toBe('brain.gguf')
    expect(row('brain').querySelector('.mem')!.textContent).toBe('2.0 GB')
    expect(row('agent').querySelector('.mem')!.textContent).toBe('—')
    expect(row('brain').getAttribute('role')).toBe('button')
    expect(row('brain').getAttribute('tabindex')).toBe('0')
    expect(q('[data-testid="slot-rail-count"]')!.textContent).toBe('2 / 3 ready')
    expect(q('[data-testid="slot-rail-count"] b')!.textContent).toBe('2')
    const foot = q('[data-testid="slot-rail-footer"]')!.textContent!
    expect(foot).toContain('loaded 3.0 GB')
    expect(foot).toContain('pool 16.0 GB free')
  })

  it('shows a slot with no model, and paused rows while image mode holds them', async () => {
    box.slots = [slot('agent', 'gpu-vulkan', 'stopped', { model: '' }), slot('brain', 'gpu-vulkan', 'stopped')]
    box.arbiter = { arbiter: { mode: 'img', unload_set: ['brain'] } }
    box.memory = { gpu: null, render_free_gb: null }
    await mount()
    expect(row('agent').querySelector('.mdl')!.textContent).toBe('no model configured')
    // Listed, but not counted: one configured slot, none ready.
    expect(q('[data-testid="slot-rail-count"]')!.textContent).toBe('0 / 1 ready')
    expect(row('brain').querySelector('.sdot')!.classList.contains('paused')).toBe(true)
    expect(row('brain').querySelector('.st')!.textContent).toBe('paused')
    const foot = q('[data-testid="slot-rail-footer"]')!.textContent!
    expect(foot).toContain('1 paused for image mode')
    expect(foot).not.toContain('pool')
  })

  it('a hal0:slot-event flashes the slot row, and ignores a name that is not a slot', async () => {
    vi.useFakeTimers()
    await mount()
    await slotEvent('brain')
    expect(row('brain').classList.contains('flash')).toBe(true)
    expect(row('agent').classList.contains('flash')).toBe(false)
    await slotEvent('model')
    expect(host.querySelectorAll('.srow.flash').length).toBe(1)
    await act(async () => {
      vi.advanceTimersByTime(1700)
    })
    expect(row('brain').classList.contains('flash')).toBe(false)
  })

  it('a second event restarts the flash window', async () => {
    vi.useFakeTimers()
    await mount()
    await slotEvent('brain')
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    await slotEvent('brain')
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    expect(row('brain').classList.contains('flash')).toBe(true)
    await act(async () => {
      vi.advanceTimersByTime(700)
    })
    expect(row('brain').classList.contains('flash')).toBe(false)
  })

  it('clicking a row scrolls the pane card into view and flashes it', async () => {
    vi.useFakeTimers()
    const card = document.createElement('div')
    card.setAttribute('data-testid', 'infer-slot-brain')
    card.scrollIntoView = vi.fn()
    document.body.appendChild(card)
    await mount()
    await act(async () => {
      row('brain').click()
    })
    expect(card.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
    expect(card.classList.contains('flash')).toBe(true)
    await act(async () => {
      vi.advanceTimersByTime(1700)
    })
    expect(card.classList.contains('flash')).toBe(false)
  })

  it("clicking an npu row scrolls the NPU card's model row into view", async () => {
    vi.useFakeTimers()
    const target = document.createElement('div')
    target.setAttribute('data-testid', 'npu-model-flm')
    target.scrollIntoView = vi.fn()
    document.body.appendChild(target)
    await mount()
    await act(async () => {
      row('flm').click()
    })
    expect(target.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' })
    expect(target.classList.contains('flash')).toBe(true)
    await act(async () => {
      vi.advanceTimersByTime(1700)
    })
    expect(target.classList.contains('flash')).toBe(false)
  })

  it('a row with no card target is a no-op', async () => {
    await mount()
    await act(async () => {
      row('flm').click()
    })
    await act(async () => {
      row('brain').click()
    })
    const flashing = Array.from(document.querySelectorAll('.flash')).filter(
      (el) => !el.closest('[data-testid="slot-rail"]'),
    )
    expect(flashing).toEqual([])
  })

  it('Enter on a row does the same as a click', async () => {
    const card = document.createElement('div')
    card.setAttribute('data-testid', 'infer-slot-agent')
    card.scrollIntoView = vi.fn()
    document.body.appendChild(card)
    await mount()
    await act(async () => {
      row('agent').dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    })
    expect(card.scrollIntoView).toHaveBeenCalledTimes(1)
  })

  it('+ New slot calls onNew', async () => {
    const onNew = vi.fn()
    await mount(onNew)
    await act(async () => {
      q('[data-testid="slot-rail-new"]')!.click()
    })
    expect(onNew).toHaveBeenCalledTimes(1)
  })
})
