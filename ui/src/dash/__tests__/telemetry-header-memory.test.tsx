// @vitest-environment happy-dom
// Top bar (CPU · RAM cell) and memory ruler read the backend memory budget
// (ADR-0039) through useMemoryMapModel, not the raw RAM / GTT counters.
//
// The real memory-map model runs here: only the data hooks are mocked, so a
// regression in either the model or the consumers shows up in these figures.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React
;(globalThis as any).FieldInfoIcon = () => null

// A shared-pool box shaped like the reference one: occupants (30 + 12 + the
// 4.4 image engine = 46.4) overshoot the live pool counter (43.4) by 3.0, so
// the budget's free (72.6) and the occupants together exceed the 116 total.
const gpuBlock = (over: Record<string, unknown> = {}) => ({
  kind: 'shared',
  total_gb: 116,
  used_gb: 43.4,
  this_box: [
    { slot: 'brain', gb: 30, state: 'serving' },
    { slot: 'agent', gb: 12, state: 'serving' },
  ],
  image_engine_gb: 4.4,
  image_engine_slot: 'img',
  image_engine_estimated: false,
  other_guests_gb: 0,
  attribution_excess_gb: 3,
  free_gb: 72.6,
  ...over,
})
const ramBlock = (over: Record<string, unknown> = {}) => ({
  limit_gb: 105.5,
  used_gb: 16.1,
  free_gb: 89.4,
  source: 'container',
  ...over,
})
const budget = (over: Record<string, unknown> = {}) => ({
  gpu: gpuBlock(),
  ram: ramBlock(),
  render_free_gb: 60,
  ...over,
})

const SLOTS = [
  { name: 'brain', type: 'llm', device: 'gpu-rocm', model: 'm-brain', state: 'serving' },
  { name: 'agent', type: 'llm', device: 'gpu-rocm', model: 'm-agent', state: 'serving' },
  { name: 'img', type: 'image', device: 'gpu-rocm', model: 'm-img', state: 'serving' },
]

// Per-test data; the mocks below read it at call time.
const state: { memory: any; slots: any[] } = { memory: budget(), slots: SLOTS }

vi.mock('@/api/hooks/useSlots', () => ({
  useSlots: () => ({ data: state.slots, isLoading: false }),
}))
vi.mock('@/api/hooks/useHardware', () => ({
  useHardware: () => ({ data: { cpu: 'test cpu', name: 'box', platform_label: 'test' }, isLoading: false }),
}))
vi.mock('@/api/hooks/useStatsHardware', () => ({
  useStatsHardware: () => ({
    data: { cpu_util: 0.25, memory: state.memory, per_slot: {} },
    isLoading: false,
  }),
  useMemoryBudget: () => ({ data: state.memory }),
}))
vi.mock('@/api/hooks/useStatsPower', () => ({ useStatsPower: () => ({ data: null }) }))
vi.mock('@/api/hooks/useThroughputHistory', () => ({ useThroughputHistory: () => ({ data: null }) }))
vi.mock('@/api/hooks/useNpuOccupancy', () => ({ useNpuOccupancy: () => ({ data: null }) }))
vi.mock('@/api/hooks/useProxmoxSettings', () => ({
  useProxmoxSettings: () => ({ data: null, isLoading: false }),
}))

// telemetry-header.jsx reads React / FieldInfoIcon off the window globals.
const { TelemetryHeader } = await import('../telemetry-header.jsx')

function render(memory: any, slots: any[] = SLOTS): HTMLElement {
  state.memory = memory
  state.slots = slots
  const host = document.createElement('div')
  document.body.appendChild(host)
  act(() => {
    createRoot(host).render(<TelemetryHeader slots={slots} />)
  })
  return host
}

afterEach(() => {
  document.body.innerHTML = ''
})

/** The CPU · RAM cell: the `.th-cell` whose eyebrow reads "CPU · RAM". */
function cpuCell(host: HTMLElement): HTMLElement {
  const cell = [...host.querySelectorAll<HTMLElement>('.th-cell')].find((c) =>
    c.querySelector('.th-eyebrow')?.textContent?.startsWith('CPU'),
  )
  if (!cell) throw new Error('CPU cell not rendered')
  return cell
}

const ruler = (host: HTMLElement) => host.querySelector('.th-ruler') as HTMLElement
const widthPct = (el: Element) => parseFloat((el as HTMLElement).style.width)

describe('top bar CPU · RAM cell', () => {
  it('cpu cell divides ram by the ram limit', () => {
    const cell = cpuCell(render(budget()))
    // used 16.1 over the 105.5 container limit, rounded to 106
    expect(cell.querySelector('.th-gauge-v')?.textContent).toBe('16.1')
    expect(cell.querySelector('.th-gauge-cap')?.textContent).toBe('/ 106 GB limit')
    expect(cell.querySelector('.th-eyebrow')?.textContent).toContain('CPU · RAM')
    // the gauge fill is used / limit, not used / the 116 GB GPU pool
    const fill = cell.querySelector('path.fill') as SVGPathElement
    expect(fill.style.strokeDasharray).toMatch(/^15\.\d+ 100$/)
  })

  it('drops the "limit" suffix when the RAM limit is the system total', () => {
    const cell = cpuCell(render(budget({ ram: ramBlock({ source: 'system', limit_gb: 64 }) })))
    expect(cell.querySelector('.th-gauge-cap')?.textContent).toBe('/ 64 GB')
  })

  it('shows no RAM figure, and never 0 / 0, when the backend could not read RAM', () => {
    const cell = cpuCell(render(budget({ ram: ramBlock({ limit_gb: 0, used_gb: 0, free_gb: 0 }) })))
    expect(cell.querySelector('.th-gauge-v')?.textContent).toBe('—')
    expect(cell.textContent).not.toMatch(/0 \/ 0|NaN|Infinity/)
    const fill = cell.querySelector('path.fill') as SVGPathElement
    expect(fill.style.strokeDasharray).toBe('0 100')
  })
})

describe('memory ruler', () => {
  it('ruler header names the pool', () => {
    expect(ruler(render(budget())).querySelector('.th-ruler-h')?.textContent).toContain(
      'memory · GPU pool · shared',
    )
    expect(
      ruler(render(budget({ gpu: gpuBlock({ kind: 'dedicated' }) }))).querySelector('.th-ruler-h')
        ?.textContent,
    ).toContain('memory · VRAM')
    // no GPU lane: the ruler falls back to RAM
    expect(ruler(render(budget({ gpu: null, render_free_gb: null }))).querySelector('.th-ruler-h')
      ?.textContent).toContain('memory · RAM')
  })

  it('header free is the budget free figure, not total minus occupants', () => {
    const h = ruler(render(budget())).querySelector('.th-ruler-h')?.textContent
    // 116 - 46.4 = 69.6 would be the old arithmetic; the budget says 72.6
    expect(h).toContain('free 72.6 GB')
    expect(h).not.toContain('69.6')
  })

  it('ruler free label equals budget free', () => {
    // One screen, one free number: the header and the free block both read the
    // budget's free (72.6), not the 69.6 the drawn segments leave over.
    const r = ruler(render(budget()))
    expect(r.querySelector('.th-ruler-free')?.textContent).toBe('free · 72.6 GB')
    expect(r.querySelector('.th-ruler-h')?.textContent).toContain('free 72.6 GB')
    expect(r.textContent).not.toContain('69.6')
    // with no GPU pool both read this box's free RAM
    const cpuOnly = ruler(render(budget({ gpu: null, render_free_gb: null })))
    expect(cpuOnly.querySelector('.th-ruler-free')?.textContent).toBe('free · 89.4 GB')
    expect(cpuOnly.querySelector('.th-ruler-h')?.textContent).toContain('free 89.4 GB')
  })

  it('ruler striped block is other guests from the budget', () => {
    const host = render(
      budget({ gpu: gpuBlock({ used_gb: 60, other_guests_gb: 20, attribution_excess_gb: 0, free_gb: 56 }) }),
    )
    const block = ruler(host).querySelector('.th-ruler-seg-system') as HTMLElement
    expect(block).not.toBeNull()
    expect(block.getAttribute('title')).toBe('other guests · 20.0 GB')
    // 20 of the 116 GB pool
    expect(widthPct(block)).toBeCloseTo((20 / 116) * 100, 5)
    expect(block.getAttribute('title')).not.toContain('KV + runtime')
  })

  it('has no striped block when nothing else holds the pool', () => {
    // other_guests_gb is 0 on the reference box (the excess is not drawn)
    expect(ruler(render(budget())).querySelector('.th-ruler-seg-system')).toBeNull()
    // and none at all with no GPU lane
    expect(
      ruler(render(budget({ gpu: null, render_free_gb: null }))).querySelector('.th-ruler-seg-system'),
    ).toBeNull()
  })

  it('names the image engine segment and says per-slot figures are estimated', () => {
    const segs = [...ruler(render(budget())).querySelectorAll('.th-ruler-seg[role="link"]')]
    const titles = segs.map((s) => s.getAttribute('title') || '')
    expect(titles.find((t) => t.startsWith('image engine'))).toMatch(/^image engine · 4\.4 GB · /)
    // the img slot's name no longer labels the engine's segment
    expect(titles.some((t) => t.startsWith('img '))).toBe(false)
    const brain = titles.find((t) => t.startsWith('brain'))
    expect(brain).toMatch(/^brain · 30\.0 GB \(estimated\) · /)
  })

  it('calls the engine segment an estimate only when the backend says it is one', () => {
    const engineTitle = (host: HTMLElement) =>
      [...ruler(host).querySelectorAll('.th-ruler-seg[role="link"]')]
        .map((s) => s.getAttribute('title') || '')
        .find((t) => t.startsWith('image engine'))
    // ComfyUI's own reading: a measurement
    expect(engineTitle(render(budget()))).toMatch(/^image engine · 4\.4 GB · /)
    // the img slot's row standing in for it: an estimate
    const estimated = render(budget({ gpu: gpuBlock({ image_engine_estimated: true }) }))
    expect(engineTitle(estimated)).toMatch(/^image engine · 4\.4 GB \(estimated\) · /)
  })

  it('links the engine segment to the slot the backend names', () => {
    const host = render(budget({ gpu: gpuBlock({ image_engine_slot: 'img' }) }))
    const seg = [...ruler(host).querySelectorAll('.th-ruler-seg[role="link"]')].find((s) =>
      (s.getAttribute('title') || '').startsWith('image engine'),
    ) as HTMLElement
    act(() => seg.click())
    expect(window.location.hash).toBe('#slots/img')
    // with no slot named there is no page to go to
    window.location.hash = '#slots'
    const noSlot = render(budget({ gpu: gpuBlock({ image_engine_slot: null }) }))
    const orphan = [...ruler(noSlot).querySelectorAll('.th-ruler-seg[role="link"]')].find((s) =>
      (s.getAttribute('title') || '').startsWith('image engine'),
    ) as HTMLElement
    act(() => orphan.click())
    expect(window.location.hash).toBe('#slots')
  })

  it('draws the segments inside the bar even when the attribution overshoots the pool', () => {
    // 30 + 12 + 4.4 = 46.4 GB attributed against a 20 GB pool that is full
    const host = render(
      budget({ gpu: gpuBlock({ total_gb: 20, used_gb: 20, attribution_excess_gb: 26.4, free_gb: 0 }) }),
    )
    const r = ruler(host)
    const drawn = [...r.querySelectorAll('.th-ruler-seg')].reduce((acc, el) => acc + widthPct(el), 0)
    expect(drawn).toBeLessThanOrEqual(100.0001)
    const free = r.querySelector('.th-ruler-free')?.textContent
    expect(free).toBe('free · 0.0 GB')
    expect(r.querySelector('.th-ruler-h')?.textContent).toContain('free 0.0 GB')
  })
})

describe('memory ruler headroom', () => {
  const header = (host: HTMLElement) => ruler(host).querySelector('.th-ruler-h')?.textContent ?? ''
  /** The header from "headroom" on: the model figure before it may legitimately read 0.0 GB. */
  const headroomText = (host: HTMLElement) => header(host).slice(header(host).indexOf('headroom'))

  it('is the budget free less the safety margin, and says what limits it', () => {
    expect(header(render(budget()))).toContain('headroom 70.6 GB — limited by pool')
  })

  it('is RAM-limited on a box with no GPU pool', () => {
    expect(header(render(budget({ gpu: null, render_free_gb: null })))).toContain(
      'headroom 87.4 GB — limited by ram',
    )
  })

  it('shows a dash, and no "limited by", when neither a pool nor RAM could be read', () => {
    const unreadable = budget({
      gpu: null,
      ram: ramBlock({ limit_gb: 0, used_gb: 0, free_gb: 0 }),
      render_free_gb: null,
    })
    const host = render(unreadable)
    expect(headroomText(host)).toBe('headroom —')
    expect(ruler(host).querySelector('.th-ruler-h .lim')).toBeNull()
  })

  it('shows a dash before the memory block has arrived', () => {
    expect(headroomText(render(undefined))).toBe('headroom —')
  })
})
