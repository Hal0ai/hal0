// @vitest-environment happy-dom
// Dashboard health strip and "Unified memory" card read the backend memory
// budget (ADR-0039) through useMemoryMapModel: RAM figures from `mm.ram`, the
// bar total from the pool, the striped block from the budget's other guests.
//
// The real memory-map model runs here: only the data hooks are mocked.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React
;(globalThis as any).FieldInfoIcon = () => null

// Same reference-box shape as telemetry-header-memory.test.tsx: occupants
// (30 + 12 + the 4.4 image engine = 46.4) overshoot the live counter (43.4),
// so the budget's free (72.6) is larger than the 69.6 the drawn blocks leave.
const budget = (gpuOver: Record<string, unknown> | null = {}, ramOver: Record<string, unknown> = {}) => ({
  gpu:
    gpuOver === null
      ? null
      : {
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
          ...gpuOver,
        },
  ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container', ...ramOver },
  render_free_gb: gpuOver === null ? null : 60,
})

const SLOTS = [
  { name: 'brain', type: 'llm', device: 'gpu-rocm', model: 'm-brain', state: 'serving' },
  { name: 'agent', type: 'llm', device: 'gpu-rocm', model: 'm-agent', state: 'serving' },
  { name: 'img', type: 'image', device: 'gpu-rocm', model: 'm-img', state: 'serving' },
]

const state: { memory: any; host: any } = { memory: budget(), host: undefined }

// dashboard-redesign.jsx imports every hook the whole dashboard uses. The two
// components under test need only the ones mocked for real below; the rest are
// inert (their data is undefined).
const inert = vi.hoisted(
  () => () =>
    new Proxy({}, { get: (_t, k) => (k === 'then' ? undefined : () => ({ data: undefined })) }),
)

vi.mock('@/api/hooks/useSlots', () => ({ useSlots: () => ({ data: SLOTS, isLoading: false }) }))
vi.mock('@/api/hooks/useHardware', () => ({
  useHardware: () => ({ data: { platform_label: 'test' }, isLoading: false }),
  hasGpuAccess: () => true,
  hasNpuAccess: () => false,
}))
vi.mock('@/api/hooks/useStatsHardware', () => ({
  useStatsHardware: () => ({
    data: { memory: state.memory, per_slot: {}, host: state.host },
    isLoading: false,
  }),
  useMemoryBudget: () => ({ data: state.memory }),
}))
vi.mock('@/api/hooks/useProxmoxSettings', () => ({
  useProxmoxSettings: () => ({ data: null, isLoading: false }),
}))
vi.mock('@/api/hooks/useStatsPower', inert)
vi.mock('@/api/hooks/useThroughputHistory', inert)
vi.mock('@/api/hooks/useRequestsRollup', inert)
vi.mock('@/api/hooks/useServices', inert)
vi.mock('@/api/hooks/useConfigUrls', inert)
vi.mock('@/api/hooks/useActivity', inert)
vi.mock('@/api/hooks/useAgents', inert)
vi.mock('@/api/hooks/useUpdates', inert)
vi.mock('@/api/hooks/useDashLayout', inert)
vi.mock('../notifications.jsx', inert)
vi.mock('../services-widget-pure', inert)

const { HealthStrip, MemoryHeroCard } = await import('../dashboard-redesign.jsx')

function render(memory: any, pveHost?: any): HTMLElement {
  state.memory = memory
  state.host = pveHost
  const host = document.createElement('div')
  document.body.appendChild(host)
  act(() => {
    createRoot(host).render(
      <>
        <HealthStrip slots={[]} heroTps={null} attentionItems={[]} />
        <MemoryHeroCard swap={null} onGo={() => {}} />
      </>,
    )
  })
  return host
}

afterEach(() => {
  document.body.innerHTML = ''
})

const striped = (host: HTMLElement) => host.querySelector('.rd-mem-seg-system') as HTMLElement | null
const freeLabel = (host: HTMLElement) => host.querySelector('.rd-mem-free')?.textContent
const legend = (host: HTMLElement) => host.querySelector('.rd-mem-legend')?.textContent ?? ''

describe('dashboard memory card', () => {
  it('dashboard card striped block is other guests on a gpu box', () => {
    const host = render(budget({ used_gb: 60, other_guests_gb: 20, attribution_excess_gb: 0, free_gb: 56 }))
    const block = striped(host)!
    expect(block).not.toBeNull()
    expect(block.getAttribute('title')).toBe('other guests · 20.0 GB')
    // 20 of the 116 GB pool, and not a RAM-derived number (RAM used is 16.1)
    expect(parseFloat(block.style.width)).toBeCloseTo((20 / 116) * 100, 5)
    expect(legend(host)).toContain('other guests 20.0G')
    expect(legend(host)).not.toMatch(/system/)
    // its free figure is the budget's, not total minus the drawn blocks (116 - 60)
    expect(freeLabel(host)).toBe('free · 56.0 GB')
  })

  it('card free label is the budget free, the same figure the ruler shows', () => {
    const host = render(budget())
    expect(freeLabel(host)).toBe('free · 72.6 GB')
    // nothing else holds the pool on the reference box, so no striped block
    expect(striped(host)).toBeNull()
  })

  it('names the image engine and says per-slot figures are estimated', () => {
    const titles = [...render(budget()).querySelectorAll('.rd-mem-seg[role="link"]')].map(
      (e) => e.getAttribute('title') || '',
    )
    expect(titles.find((t) => t.startsWith('image engine'))).toMatch(/^image engine · 4\.4 GB · /)
    expect(titles.find((t) => t.startsWith('brain'))).toMatch(/^brain · 30\.0 GB \(estimated\) · /)
  })

  it('calls the engine an estimate only when the backend says it is one', () => {
    const engineTitle = (host: HTMLElement) =>
      [...host.querySelectorAll('.rd-mem-seg[role="link"]')]
        .map((e) => e.getAttribute('title') || '')
        .find((t) => t.startsWith('image engine'))
    expect(engineTitle(render(budget()))).toMatch(/^image engine · 4\.4 GB · /)
    document.body.innerHTML = ''
    expect(engineTitle(render(budget({ image_engine_estimated: true })))).toMatch(
      /^image engine · 4\.4 GB \(estimated\) · /,
    )
  })

  it('stays RAM-based on a box without a GPU', () => {
    const host = render(budget(null))
    // the bar is this box's RAM: system + page cache = RAM in use beyond the models
    expect(striped(host)!.getAttribute('title')).toBe('system + page cache · 16.1 GB')
    expect(legend(host)).toContain('system 16.1G')
    expect(freeLabel(host)).toBe('free · 89.4 GB')
    expect(host.querySelector('.rd-card-note')?.textContent).toContain('106 GB pool')
  })
})

describe('dashboard memory card on a Proxmox-configured GPU box', () => {
  // Host: 128 GB DIMMs, 90 GB in use, 38 GB free. This box's occupants hold
  // 30 + 12 + 4.4 = 46.4 GB of the pool, so the host's "others" are 43.6 GB:
  // that already includes the other guests' GTT.
  const host = {
    configured: true,
    ok: true,
    host_mem_total_mb: 131072,
    host_mem_used_mb: 92160,
    host_mem_free_mb: 38912,
  }
  // Other guests hold 20 GB of this box's pool: drawn as their own block on
  // the pool bar, but inside the host's "others" once the bar is host-wide.
  const withOtherGuests = () =>
    budget({ used_gb: 66.4, other_guests_gb: 20, attribution_excess_gb: 0, free_gb: 49.6 })

  it('does not draw the pool\'s other guests on top of the host\'s others', () => {
    const h = render(withOtherGuests(), host)
    const blocks = [...h.querySelectorAll<HTMLElement>('.rd-mem-seg-system')]
    expect(blocks.map((b) => b.getAttribute('title'))).toEqual([
      'proxmox host (other tenants) · 43.6 GB',
    ])
    expect(legend(h)).not.toMatch(/other guests/)
    expect(legend(h)).toContain('proxmox host 43.6G')
  })

  it('the free label is what the host-wide bar leaves, not the pool free', () => {
    const h = render(withOtherGuests(), host)
    // 128 total - (46.4 occupants + 43.6 host others) = 38.0, the host's free
    expect(freeLabel(h)).toBe('free · 38.0 GB')
    expect(h.querySelector('.rd-card-note')?.textContent).toContain('128 GB pool')
  })

  it('the same budget without a host is the pool bar, other guests drawn', () => {
    const h = render(withOtherGuests())
    expect(striped(h)!.getAttribute('title')).toBe('other guests · 20.0 GB')
    expect(freeLabel(h)).toBe('free · 49.6 GB')
  })
})

describe('dashboard health strip RAM cell', () => {
  const cell = (host: HTMLElement) =>
    [...host.querySelectorAll('.rd-health-cell')].find((c) => c.querySelector('.rd-health-k')?.textContent === 'RAM')!

  it('is RAM used over the RAM limit, not the GPU pool', () => {
    const c = cell(render(budget()))
    expect(c.querySelector('.rd-health-v')?.textContent).toBe('16.1/106 GB')
    expect(c.querySelector('.rd-health-v')?.getAttribute('title')).toBe('container limit')
  })

  it('shows no figure when the backend could not read RAM', () => {
    const c = cell(render(budget({}, { limit_gb: 0, used_gb: 0, free_gb: 0 })))
    expect(c.querySelector('.rd-health-v')?.textContent).toBe('—')
  })
})
