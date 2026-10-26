// The dashboard memory model is a pure function of the backend memory budget
// (ADR-0039): /api/stats/hardware `memory` is the only source of the pool,
// the RAM limit and the attribution. These tests pin what the function does
// with that block; the hook in memory-map.jsx only feeds it.
//
// Fixtures are plain objects shaped like the served JSON (snake_case),
// numbers only. "Reference box" is a shared-pool box whose slots are
// attributed 46.4 GB against a kernel counter of 43.4; "capped second guest"
// is a 48 GB container on that same pool whose own slots hold nothing.
import { describe, expect, it } from 'vitest'

import type { MemoryBudgetJson, PerSlotRow, StatsHardwareHost } from '@/api/hooks/useStatsHardware'
import type { ProxmoxTenant } from '@/api/hooks/useProxmoxSettings'
import type { Slot } from '@/api/hooks/useSlots'

import {
  assignSlotColors,
  buildMemoryMapModel,
  gpuPoolLabel,
  SAFETY_MARGIN_GB,
  type MemoryMapInput,
} from '../memory-map-pure'

const referenceBox: MemoryBudgetJson = {
  gpu: {
    kind: 'shared',
    total_gb: 116,
    used_gb: 43.4,
    this_box: [
      { slot: 'brain', gb: 2.8, state: 'ready' },
      { slot: 'tts', gb: 6.2, state: 'ready' },
      { slot: 'utility', gb: 30.8, state: 'serving' },
    ],
    image_engine_gb: 6.6,
    // ComfyUI is not answering here: the img slot's own row stands in.
    image_engine_slot: 'img',
    image_engine_estimated: true,
    other_guests_gb: 0,
    attribution_excess_gb: 3.0,
    free_gb: 72.6,
  },
  ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container' },
  render_free_gb: 107.4,
  render_ram_free_gb: 89.4,
}

const cappedSecondGuest: MemoryBudgetJson = {
  gpu: {
    kind: 'shared',
    total_gb: 116,
    used_gb: 43.4,
    this_box: [],
    image_engine_gb: 0,
    image_engine_slot: 'img',
    image_engine_estimated: false,
    other_guests_gb: 43.4,
    attribution_excess_gb: 0,
    free_gb: 72.6,
  },
  ram: { limit_gb: 48, used_gb: 2.2, free_gb: 45.8, source: 'container' },
  render_free_gb: 70.6,
  render_ram_free_gb: 45.8,
}

const discreteGpu: MemoryBudgetJson = {
  gpu: {
    kind: 'dedicated',
    total_gb: 24,
    used_gb: 9.5,
    this_box: [{ slot: 'brain', gb: 9.0, state: 'ready' }],
    image_engine_gb: 0,
    image_engine_slot: null,
    image_engine_estimated: false,
    other_guests_gb: 0.5,
    attribution_excess_gb: 0,
    free_gb: 14.5,
  },
  ram: { limit_gb: 64, used_gb: 8, free_gb: 56, source: 'system' },
  // 24 pool - 0.5 other guests - 0 engine - 0 starting - 2 margin.
  render_free_gb: 21.5,
  render_ram_free_gb: 56,
}

const cpuOnly: MemoryBudgetJson = {
  gpu: null,
  ram: { limit_gb: 32, used_gb: 10, free_gb: 22, source: 'system' },
  render_free_gb: null,
  render_ram_free_gb: 22,
}

function slot(name: string, extra: Record<string, unknown> = {}): Slot {
  return {
    name,
    type: 'llm',
    device: 'gpu-rocm',
    model: '',
    state: 'ready',
    metrics: {},
    ...extra,
  } as Slot
}

const referenceSlots: Slot[] = [
  slot('brain', {
    model: 'Qwen3.8-27B',
    runtime: 'container',
    image: 'ghcr.io/example/hal0-runner-standard:1',
    profile: 'agent',
  }),
  slot('tts', { type: 'tts', device: 'gpu-vulkan', model: 'kokoro' }),
  slot('utility', { model: 'Qwen3-30B', container_status: 'running' }),
  slot('img', { type: 'image', model: 'qwen-image', runtime: 'container' }),
]

function input(over: Partial<MemoryMapInput> = {}): MemoryMapInput {
  return {
    memory: referenceBox,
    perSlot: {},
    slots: referenceSlots,
    statsHost: undefined,
    pveTenants: [],
    selfHostname: '',
    platformLabel: 'Strix Halo',
    ...over,
  }
}

const configuredHost = (freeMb: number): StatsHardwareHost => ({
  configured: true,
  ok: true,
  host_mem_total_mb: 131072,
  host_mem_used_mb: 131072 - freeMb,
  host_mem_free_mb: freeMb,
})

describe('buildMemoryMapModel', () => {
  it('reference box: shared pool label, total 116, other guests 0, headroom 70.6 limited by pool', () => {
    const m = buildMemoryMapModel(input())
    expect(m.pool).toEqual({
      totalGb: 116,
      kind: 'unified',
      label: 'GPU pool · shared',
      platformLabel: 'Strix Halo',
    })
    expect(m.gpu).toEqual({
      kind: 'shared',
      totalGb: 116,
      usedGb: 43.4,
      freeGb: 72.6,
      otherGuestsGb: 0,
      imageEngineGb: 6.6,
      attributionExcessGb: 3.0,
      renderFreeGb: 107.4,
    })
    expect(m.headroom).toEqual({ availableGb: 70.6, limitedBy: 'pool' })
    // The safety margin is the one the headroom subtracts.
    expect(SAFETY_MARGIN_GB).toBe(2)
    expect(m.headroom.availableGb).toBe(m.gpu!.freeGb - SAFETY_MARGIN_GB)
    expect(m.loading).toBe(false)
  })

  it('capped second guest: other guests 43.4, ram limit 48 container, headroom 70.6', () => {
    const m = buildMemoryMapModel(input({ memory: cappedSecondGuest, slots: [] }))
    expect(m.gpu!.otherGuestsGb).toBe(43.4)
    expect(m.ram).toEqual({ limitGb: 48, usedGb: 2.2, freeGb: 45.8, source: 'container' })
    // The RAM cap does not bind the pool: GTT is not charged to the guest.
    expect(m.headroom).toEqual({ availableGb: 70.6, limitedBy: 'pool' })
    expect(m.self.slots).toEqual([])
    expect(m.self.modelUsedGb).toBe(0)
    // The counter still reads 43.4, all of it other guests'.
    expect(m.self.gttUsedGb).toBe(43.4)
  })

  it('discrete gpu: kind dedicated, label VRAM', () => {
    const m = buildMemoryMapModel(input({ memory: discreteGpu, slots: [slot('brain')] }))
    expect(m.pool.kind).toBe('dedicated')
    expect(m.pool.label).toBe('VRAM')
    expect(m.pool.totalGb).toBe(24)
    expect(m.gpu!.kind).toBe('dedicated')
    expect(m.headroom).toEqual({ availableGb: 12.5, limitedBy: 'pool' })
  })

  it('cpu only: kind system, label RAM, total = ram limit, headroom from ram', () => {
    const perSlot: Record<string, PerSlotRow> = {
      worker: { vram_mb: 0, ram_mb: 4096, mem_mb: 4096, state: 'ready', model_id: 'm' },
    }
    const m = buildMemoryMapModel(
      input({ memory: cpuOnly, perSlot, slots: [slot('worker', { device: 'cpu', model: 'tiny' })] }),
    )
    expect(m.gpu).toBeNull()
    expect(m.pool).toEqual({ totalGb: 32, kind: 'system', label: 'RAM', platformLabel: 'Strix Halo' })
    expect(m.ram).toEqual({ limitGb: 32, usedGb: 10, freeGb: 22, source: 'system' })
    expect(m.headroom).toEqual({ availableGb: 20, limitedBy: 'ram' })
    // Without a GPU pool the occupants are the perSlot RAM rows.
    expect(m.self.slots.map((s) => [s.name, s.bytesGb, s.device])).toEqual([['worker', 4, 'cpu']])
    expect(m.self.modelUsedGb).toBe(4)
    expect(m.self.gttUsedGb).toBe(0)
  })

  it('proxmox host free below pool free limits by host', () => {
    // 5 GB free on the host, 72.6 free in the pool.
    const m = buildMemoryMapModel(input({ statsHost: configuredHost(5 * 1024) }))
    expect(m.host.mode).toBe('configured')
    expect(m.headroom).toEqual({ availableGb: 3, limitedBy: 'host' })
  })

  it('proxmox host free above pool free leaves the pool as the limit', () => {
    const m = buildMemoryMapModel(input({ statsHost: configuredHost(100 * 1024) }))
    expect(m.headroom).toEqual({ availableGb: 70.6, limitedBy: 'pool' })
  })

  it('a host with less free than the box ram limits a cpu-only box too', () => {
    const m = buildMemoryMapModel(input({ memory: cpuOnly, slots: [], statsHost: configuredHost(6 * 1024) }))
    expect(m.headroom).toEqual({ availableGb: 4, limitedBy: 'host' })
  })

  it('headroom never goes negative', () => {
    const tight: MemoryBudgetJson = {
      ...cpuOnly,
      ram: { limit_gb: 32, used_gb: 31.5, free_gb: 0.5, source: 'system' },
    }
    const m = buildMemoryMapModel(input({ memory: tight, slots: [] }))
    expect(m.headroom.availableGb).toBe(0)
  })

  it('img slot segment is sized by imageEngineGb', () => {
    const m = buildMemoryMapModel(input())
    const img = m.self.slots.find((s) => s.name === 'img')
    expect(img).toBeDefined()
    expect(img!.bytesGb).toBe(6.6)
    expect(img!.isImageEngine).toBe(true)
    // joined to the slot entry by name
    expect(img!.modelId).toBe('qwen-image')
    expect(img!.isContainer).toBe(true)
    // the llm rows are not image-engine rows
    expect(m.self.slots.filter((s) => s.isImageEngine).map((s) => s.name)).toEqual(['img'])
    // model memory is the sum of the occupants, engine included
    expect(m.self.modelUsedGb).toBe(46.4)
  })

  it('img slot is left out when the engine holds nothing', () => {
    const idle: MemoryBudgetJson = {
      ...referenceBox,
      gpu: { ...referenceBox.gpu!, image_engine_gb: 0 },
    }
    const m = buildMemoryMapModel(input({ memory: idle }))
    expect(m.self.slots.map((s) => s.name)).toEqual(['brain', 'tts', 'utility'])
  })

  it('an engine with no img slot named by the backend still gets a segment', () => {
    const noImg: MemoryBudgetJson = {
      ...referenceBox,
      gpu: { ...referenceBox.gpu!, image_engine_slot: null },
    }
    // The slot list has an image slot, but the backend named none: the UI does
    // not go looking for one.
    const m = buildMemoryMapModel(input({ memory: noImg }))
    const img = m.self.slots.find((s) => s.isImageEngine)
    expect(img).toBeDefined()
    expect(img!.bytesGb).toBe(6.6)
    expect(img!.name).toBe('image engine')
  })

  it('the engine takes the name the backend gives it, whatever the slot list says', () => {
    const named: MemoryBudgetJson = {
      ...referenceBox,
      gpu: { ...referenceBox.gpu!, image_engine_slot: 'render' },
    }
    // 'img' is the only image-typed slot in the list; the backend says 'render'.
    const withRender = [...referenceSlots, slot('render', { type: 'image', model: 'flux' })]
    const m = buildMemoryMapModel(input({ memory: named, slots: withRender }))
    expect(m.self.slots.filter((s) => s.isImageEngine).map((s) => s.name)).toEqual(['render'])
    // ...and it is joined to that slot's entry, not the one a guess would pick.
    expect(m.self.slots.find((s) => s.isImageEngine)!.modelId).toBe('flux')
    // a backend slot name with no matching entry in the list still draws
    const unlisted = buildMemoryMapModel(input({ memory: named, slots: referenceSlots }))
    expect(unlisted.self.slots.find((s) => s.isImageEngine)!.name).toBe('render')
  })

  it('marks the engine estimated only when the backend says so; slots always are', () => {
    const measured: MemoryBudgetJson = {
      ...referenceBox,
      gpu: { ...referenceBox.gpu!, image_engine_estimated: false },
    }
    const engine = (memory: MemoryBudgetJson) =>
      buildMemoryMapModel(input({ memory })).self.slots.find((s) => s.isImageEngine)!
    expect(engine(referenceBox).estimated).toBe(true)
    expect(engine(measured).estimated).toBe(false)
    const slotsOnly = buildMemoryMapModel(input({ memory: measured })).self.slots.filter(
      (s) => !s.isImageEngine,
    )
    expect(slotsOnly.length).toBeGreaterThan(0)
    expect(slotsOnly.every((s) => s.estimated)).toBe(true)
  })

  it('ruler free never negative when attribution exceeds the counter', () => {
    // Reference box: slots + engine sum to 46.4 against a counter of 43.4.
    const ref = buildMemoryMapModel(input())
    expect(ref.self.modelUsedGb).toBe(46.4)
    expect(ref.self.gttUsedGb).toBe(43.4)
    // The overshoot is reported, never drawn as a negative other-guests block
    // and never taken off the free figure.
    expect(ref.gpu!.attributionExcessGb).toBe(3.0)
    expect(ref.gpu!.otherGuestsGb).toBe(0)
    expect(ref.gpu!.freeGb).toBe(72.6)

    // Attribution larger than the whole pool: the backend clamps used/free,
    // and nothing the model hands the ruler goes below zero.
    const overshoot: MemoryBudgetJson = {
      ...referenceBox,
      gpu: {
        ...referenceBox.gpu!,
        total_gb: 40,
        used_gb: 40,
        other_guests_gb: 0,
        attribution_excess_gb: 6.4,
        free_gb: 0,
      },
      // 40 pool - 0 other guests - 6.6 engine - 0 starting - 2 margin: the
      // slots are unloaded for a render, so the overshoot does not touch it.
      render_free_gb: 31.4,
    }
    const m = buildMemoryMapModel(input({ memory: overshoot }))
    const figures = [
      m.gpu!.freeGb,
      m.gpu!.otherGuestsGb,
      m.gpu!.attributionExcessGb,
      m.gpu!.renderFreeGb,
      m.headroom.availableGb,
      m.self.gttUsedGb,
    ]
    for (const v of figures) expect(v).toBeGreaterThanOrEqual(0)
    expect(m.headroom.availableGb).toBe(0)
  })

  it('a slot that is still starting is listed by the backend but not drawn', () => {
    // Its partial bytes already sit in other guests; drawing the estimate as
    // well would count them twice and push the bar past the pool.
    const withStarting: MemoryBudgetJson = {
      ...referenceBox,
      gpu: {
        ...referenceBox.gpu!,
        this_box: [...referenceBox.gpu!.this_box, { slot: 'warm', gb: 12.3, state: 'starting' }],
      },
      // 116 pool - 0 other guests - 6.6 engine - 12.3 starting - 2 margin.
      render_free_gb: 95.1,
    }
    const m = buildMemoryMapModel(input({ memory: withStarting, slots: [...referenceSlots, slot('warm')] }))
    expect(m.self.slots.map((s) => s.name)).not.toContain('warm')
    expect(m.self.modelUsedGb).toBe(46.4)
  })

  it('joins this_box rows to useSlots entries for device, model and container fields', () => {
    const m = buildMemoryMapModel(input())
    const byName = Object.fromEntries(m.self.slots.map((s) => [s.name, s]))
    expect(byName.brain).toMatchObject({
      device: 'rocm',
      modelId: 'Qwen3.8-27B',
      isContainer: true,
      image: 'ghcr.io/example/hal0-runner-standard:1',
      profile: 'agent',
      bytesGb: 2.8,
      estimated: true,
    })
    expect(byName.tts).toMatchObject({ device: 'vulkan', modelId: 'kokoro', isContainer: false, image: null })
    // container_status alone marks a container slot, as it always has
    expect(byName.utility.isContainer).toBe(true)
    expect(byName.utility.profile).toBeNull()
  })

  it('a this_box row with no slot entry yet still renders, as a gpu row', () => {
    const m = buildMemoryMapModel(input({ slots: [], perSlot: { brain: { mem_mb: 1, model_id: 'from-per-slot' } } }))
    const brain = m.self.slots.find((s) => s.name === 'brain')!
    expect(brain.device).toBe('rocm')
    expect(brain.modelId).toBe('from-per-slot')
    expect(brain.isContainer).toBe(false)
    expect(brain.bytesGb).toBe(2.8)
  })

  it('colours every occupant from the palette by sorted name, and the colours differ', () => {
    const m = buildMemoryMapModel(input())
    const expected = assignSlotColors(m.self.slots.map((s) => s.name))
    for (const s of m.self.slots) expect(s.color).toBe(expected.get(s.name))
    expect(new Set(m.self.slots.map((s) => s.color)).size).toBe(m.self.slots.length)
    expect(m.self.slots[0].color).toMatch(/^var\(--mem-slot-\d\)$/)
  })

  it('memory block absent returns an empty, loading model without throwing', () => {
    const m = buildMemoryMapModel(input({ memory: undefined }))
    expect(m.loading).toBe(true)
    expect(m.gpu).toBeNull()
    expect(m.ram).toBeNull()
    expect(m.pool.totalGb).toBe(0)
    expect(m.self.slots).toEqual([])
    expect(m.self.modelUsedGb).toBe(0)
    expect(m.headroom.availableGb).toBe(0)
    expect(m.host.mode).toBe('off')
  })

  it('an absent block does not fail on missing inputs either', () => {
    expect(() =>
      buildMemoryMapModel({
        memory: undefined,
        perSlot: undefined as unknown as Record<string, PerSlotRow>,
        slots: undefined as unknown as Slot[],
        statsHost: undefined,
        pveTenants: undefined as unknown as ProxmoxTenant[],
        selfHostname: '',
        platformLabel: '',
      }),
    ).not.toThrow()
  })

  it('ram limit of zero is treated as unread', () => {
    const unread: MemoryBudgetJson = {
      ...cpuOnly,
      ram: { limit_gb: 0, used_gb: 0, free_gb: 0, source: 'system' },
    }
    // CPU-only: no pool at all, so no total to divide by and no headroom.
    const bare = buildMemoryMapModel(input({ memory: unread, slots: [] }))
    expect(bare.ram).toBeNull()
    expect(bare.pool.totalGb).toBe(0)
    expect(bare.pool.label).toBe('RAM')
    expect(bare.headroom).toEqual({ availableGb: 0, limitedBy: 'ram' })
    expect(bare.self.ramUsedGb).toBe(0)

    // With a GPU pool the pool figures are untouched; only the RAM block goes.
    const withGpu = buildMemoryMapModel(input({ memory: { ...referenceBox, ram: unread.ram } }))
    expect(withGpu.ram).toBeNull()
    expect(withGpu.pool.totalGb).toBe(116)
    expect(withGpu.headroom).toEqual({ availableGb: 70.6, limitedBy: 'pool' })
  })

  it('keeps the proxmox host block: totals, self share and tenants minus this box', () => {
    const statsHost: StatsHardwareHost = {
      configured: true,
      ok: true,
      host_mem_total_mb: 131072, // 128
      host_mem_used_mb: 92160, // 90
      host_mem_free_mb: 38912, // 38
    }
    const tenants = [
      { vmid: 101, name: 'Self-Box', type: 'lxc', status: 'running', mem_mb: 2048, maxmem_mb: 4096 },
      { vmid: 105, name: 'other-guest', type: 'lxc', status: 'running', mem_mb: 51200, maxmem_mb: 61440 },
    ] as ProxmoxTenant[]
    const m = buildMemoryMapModel(input({ statsHost, pveTenants: tenants, selfHostname: ' SELF-BOX ' }))
    expect(m.host).toEqual({
      mode: 'configured',
      totalGb: 128,
      usedGb: 90,
      freeGb: 38,
      selfShareGb: 46.4,
      othersGb: 43.6,
      // the LXC running hal0 itself is already "this hal0 LXC"
      tenants: [{ vmid: 105, name: 'other-guest', type: 'lxc', memGb: 50, maxGb: 60 }],
    })
    expect(m.self.selfShareGb).toBe(46.4)
  })

  it('host modes: unconfigured but detected nudges, otherwise off', () => {
    const detected = buildMemoryMapModel(
      input({ statsHost: { configured: false, detected: true, hint: 'Configure it.' } }),
    )
    expect(detected.host).toEqual({ mode: 'detected_unconfigured', hint: 'Configure it.' })
    expect(buildMemoryMapModel(input({ statsHost: { configured: false } })).host).toEqual({ mode: 'off' })
    expect(buildMemoryMapModel(input({ statsHost: { configured: true, ok: false } })).host).toEqual({
      mode: 'off',
    })
  })

  it('keeps the legacy self keys: ram used from the budget, npu model from per_slot', () => {
    const perSlot: Record<string, PerSlotRow> = {
      npu0: { vram_mb: 3072, ram_mb: 0, mem_mb: 3072, state: 'ready', model_id: 'flm' },
    }
    const m = buildMemoryMapModel(
      input({ perSlot, slots: [...referenceSlots, slot('npu0', { device: 'npu' })] }),
    )
    expect(m.self.ramUsedGb).toBe(16.1)
    expect(m.self.npuModelGb).toBe(3)
    // The NPU is not in the GPU pool, so it is not one of the pool's occupants.
    expect(m.self.slots.map((s) => s.name)).not.toContain('npu0')
  })
})

describe('gpuPoolLabel', () => {
  it('names the pool for every screen that shows it', () => {
    expect(gpuPoolLabel('shared')).toBe('GPU pool · shared')
    expect(gpuPoolLabel('dedicated')).toBe('VRAM')
  })
})
