// Pure core of the dashboard memory model (ADR-0039) — no React, no hooks, no
// fetching, so vitest can pin it without a DOM.
//
// The model used to re-derive the dashboard's memory figures from five raw
// counters (the GTT cap, the equal-split GTT attribution, the static probe's
// unified size, the cgroup branch, RAM totals). The backend now owns all of
// that: `GET /api/stats/hardware` serves a `memory` block
// (`MemoryBudget.as_dict()`, src/hal0/hardware/memory_budget.py) with the pool
// total and usage read live, the usage attributed to this box's slots, the
// image engine and "other guests", and this box's RAM limit. This module only
// reshapes that block, plus the Proxmox host figures, into the keys the
// consumers (top bar, ruler, dashboard cards, Slots page) already read. It
// computes no pool, RAM or attribution number of its own.
//
// What it does compute, and why it is allowed to:
//   * headroom — the budget's free figure less a safety margin, then the
//     Proxmox host's free memory if that is tighter. The host is not part of
//     this box's budget, so the budget cannot fold it in.
//   * the Proxmox host block — host-wide figures from a different endpoint.
//   * the occupant list — a join of the budget's slot rows with the slot list
//     for device / model / container fields the budget does not carry.

import type { ProxmoxTenant } from '@/api/hooks/useProxmoxSettings'
import type { Slot } from '@/api/hooks/useSlots'
import type {
  MemoryBudgetJson,
  PerSlotRow,
  StatsHardwareHost,
} from '@/api/hooks/useStatsHardware'

/** Headroom kept back from "free" for the driver, the compositor and allocator
 *  slop. Subtracted once, from the tightest of pool / host / ram. */
export const SAFETY_MARGIN_GB = 2

const MB_PER_GB = 1024

// Categorical per-slot palette (CSS custom props defined in dashboard.css).
// Each occupant gets its OWN stable colour so co-resident models are
// distinguishable in the bar + legend — device colour alone collapses several
// GPU slots into a single hue.
const SLOT_PALETTE = [
  'var(--mem-slot-1)',
  'var(--mem-slot-2)',
  'var(--mem-slot-3)',
  'var(--mem-slot-4)',
  'var(--mem-slot-5)',
  'var(--mem-slot-6)',
  'var(--mem-slot-7)',
  'var(--mem-slot-8)',
]

/** Name given to the image-engine segment when the slot list has no img slot
 *  to take a name from (the engine can hold memory with its slot not listed). */
const IMAGE_ENGINE_FALLBACK_NAME = 'image engine'

/** Deterministic, render-stable colour for a slot: sort the names and index by
 *  position into the palette. A given name always maps to the same colour
 *  within a render set, and the swatch matches its bar segment. */
export function assignSlotColors(slotNames: Iterable<string>): Map<string, string> {
  const sorted = [...slotNames].sort()
  const map = new Map<string, string>()
  sorted.forEach((name, i) => {
    map.set(name, SLOT_PALETTE[i % SLOT_PALETTE.length])
  })
  return map
}

// ── Types ──────────────────────────────────────────────────────────────

export type PoolKind = 'unified' | 'dedicated' | 'system'
export type PoolLabel = 'GPU pool · shared' | 'VRAM' | 'RAM'
export type SlotDevice = 'npu' | 'cpu' | 'vulkan' | 'rocm'

/** One occupant of the memory bar: a slot (or the image engine) and the GB it
 *  holds. */
export interface MemoryMapSlot {
  name: string
  device: SlotDevice
  color: string
  bytesGb: number
  modelId: string
  /** Always false: the figure is the backend's attribution, not a split the
   *  UI estimated. Kept for the legend, which still reads it. */
  approx: boolean
  /** Container runtime fields, from the slot list. The legend shows the image
   *  tag or profile slug for container slots. */
  isContainer: boolean
  image: string | null
  profile: string | null
  /** True for the segment sized by `gpu.imageEngineGb` (the img slot). */
  isImageEngine: boolean
}

export interface MemoryMapTenant {
  vmid: number
  name: string
  type: string
  memGb: number
  maxGb: number
}

export type MemoryMapHost =
  | {
      mode: 'configured'
      totalGb: number
      usedGb: number
      freeGb: number
      selfShareGb: number
      othersGb: number
      tenants: MemoryMapTenant[]
    }
  | { mode: 'detected_unconfigured'; hint: string }
  | { mode: 'off' }

/** This box's RAM, from `memory.ram`. */
export interface MemoryMapRam {
  limitGb: number
  usedGb: number
  freeGb: number
  /** "container": the limit is a container cgroup / LXC limit; the UI suffixes
   *  it "container limit". "system": the machine's total. */
  source: 'container' | 'system'
}

/** The GPU pool, from `memory.gpu`. */
export interface MemoryMapGpu {
  /** "shared": UMA pool (GTT), shared with the host and other guests.
   *  "dedicated": discrete VRAM. */
  kind: 'shared' | 'dedicated'
  totalGb: number
  usedGb: number
  freeGb: number
  otherGuestsGb: number
  imageEngineGb: number
  /** How far the attributed footprints overshoot the live usage. Reported, not
   *  drawn, not subtracted from anything. */
  attributionExcessGb: number
  /** Pool a render can use once image mode unloads this box's GPU slots. */
  renderFreeGb: number | null
}

export interface MemoryMapModel {
  pool: {
    /** `gpu.totalGb`, or `ram.limitGb` with no GPU pool (0 when RAM is unread). */
    totalGb: number
    kind: PoolKind
    label: PoolLabel
    platformLabel: string
  }
  host: MemoryMapHost
  self: {
    /** Sum of `slots`: what this box's occupants hold in the pool. */
    modelUsedGb: number
    /** This box's share of the Proxmox host's memory (= `modelUsedGb`). */
    selfShareGb: number
    /** `ram.usedGb`; 0 when RAM is unread. */
    ramUsedGb: number
    /** The live pool usage, `gpu.usedGb`; 0 with no GPU pool. */
    gttUsedGb: number
    /** NPU slots' resident memory, from `per_slot`. The NPU is not in the GPU
     *  pool, so this is not part of `modelUsedGb`. */
    npuModelGb: number
    slots: MemoryMapSlot[]
  }
  headroom: {
    availableGb: number
    limitedBy: 'pool' | 'host' | 'ram'
  }
  /** This box's RAM. `null` when the backend could not read it
   *  (`memory.ram.limit_gb === 0`) or the `memory` block is absent: consumers
   *  must show no RAM figure then — never "0 / 0 GB", never divide by the
   *  limit. */
  ram: MemoryMapRam | null
  /** The GPU pool. `null` with no GPU lane or no readable pool counters. */
  gpu: MemoryMapGpu | null
  /** True while the `memory` block is absent. The hook ORs in the queries'
   *  own loading flags. */
  loading: boolean
}

export interface MemoryMapInput {
  /** `/api/stats/hardware` `memory`. Undefined before the first response, and
   *  when the backend could not build it. */
  memory: MemoryBudgetJson | undefined
  /** `/api/stats/hardware` `per_slot`: resident slot rows, in MiB. */
  perSlot: Record<string, PerSlotRow>
  /** `useSlots()`: joined by name for device and container fields. */
  slots: Slot[]
  statsHost: StatsHardwareHost | undefined
  /** `useProxmoxSettings().data.status.tenants` (the slim stats host block
   *  has none). */
  pveTenants: ProxmoxTenant[]
  /** This box's hostname; the tenant running hal0 itself is dropped. */
  selfHostname: string
  platformLabel: string
}

// ── Helpers ────────────────────────────────────────────────────────────

const round1 = (n: number): number => Math.round(n * 10) / 10

const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0)

function mbToGb(mb: unknown): number {
  return round1(num(mb) / MB_PER_GB)
}

function deviceFor(slot: Pick<Slot, 'device'> | undefined): SlotDevice {
  const d = (slot?.device || '').toLowerCase()
  if (d === 'npu') return 'npu'
  if (d === 'cpu') return 'cpu'
  if (d === 'gpu-vulkan' || d === 'vulkan') return 'vulkan'
  if (d === 'gpu-rocm' || d === 'rocm' || d.startsWith('gpu')) return 'rocm'
  return 'cpu'
}

/** The slot that hosts the image engine: an image-type (or ComfyUI-profile)
 *  slot on a GPU device. A missing device is the backend's default, a GPU. */
function isImageSlot(s: Slot): boolean {
  if (String(s.type) !== 'image' && s.profile !== 'comfyui') return false
  const dev = deviceFor(s)
  return !s.device || dev === 'rocm' || dev === 'vulkan'
}

/** A `per_slot` row's resident footprint in MiB. */
function rowMb(row: PerSlotRow | undefined): number {
  if (!row) return 0
  return num(row.mem_mb) || num(row.ram_mb) + num(row.vram_mb)
}

type Joined = { name: string; gb: number; fallbackDevice: SlotDevice; isImageEngine: boolean }

function toSlotEntry(
  j: Joined,
  slotsByName: Map<string, Slot>,
  perSlot: Record<string, PerSlotRow>,
  colors: Map<string, string>,
): MemoryMapSlot {
  const slot = slotsByName.get(j.name)
  return {
    name: j.name,
    device: slot ? deviceFor(slot) : j.fallbackDevice,
    color: colors.get(j.name) as string,
    bytesGb: j.gb,
    modelId: slot?.model || perSlot[j.name]?.model_id || '',
    approx: false,
    // Container runtime fields — present when slot.runtime="container".
    // `image` is the full image tag; `profile` is the profile slug. The legend
    // uses them for container slots (the device chip is uninformative there:
    // every one is "rocm").
    isContainer: !!slot && (slot.runtime === 'container' || slot.container_status != null),
    image: slot?.image || null,
    profile: slot?.profile || null,
    isImageEngine: j.isImageEngine,
  }
}

/** The Proxmox host block. Stats endpoint host: `{ configured, [detected],
 *  [hint], ok?, host_mem_*, ... }`. Tenants come from the FULL-shape
 *  /api/settings/proxmox response (the slim stats response has no tenants[]).
 *
 *  Cadence note: stats refresh at 2.5 s, settings at 10 s, so `selfShareGb`
 *  and tenants[] can be briefly out of sync (<7.5 s) — accepted, tenant
 *  allocations change slowly. Don't unify the cadences; the slim/full split is
 *  by design. */
function buildHost(
  statsHost: StatsHardwareHost | undefined,
  pveTenants: ProxmoxTenant[],
  selfHostname: string,
  selfShareGb: number,
): MemoryMapHost {
  const host = statsHost || { configured: false }
  if (host.configured && host.ok !== false) {
    const totalGb = mbToGb(host.host_mem_total_mb)
    const usedGb = mbToGb(host.host_mem_used_mb)
    const freeGb = mbToGb(host.host_mem_free_mb)
    // Filter out the LXC running hal0 itself — it's already represented as
    // 'this hal0 LXC' via selfShareGb. The static probe exposes our hostname;
    // tenants whose `name` matches are dropped.
    const self = (selfHostname || '').toLowerCase().trim()
    const tenants = (pveTenants || [])
      .filter((t) => !self || (t.name || '').toLowerCase().trim() !== self)
      .map((t) => ({
        vmid: t.vmid,
        name: t.name,
        type: t.type,
        memGb: mbToGb(t.mem_mb),
        maxGb: mbToGb(t.maxmem_mb),
      }))
    return {
      mode: 'configured',
      totalGb,
      usedGb,
      freeGb,
      selfShareGb,
      othersGb: Math.max(0, round1(usedGb - selfShareGb)),
      tenants,
    }
  }
  if (host.detected) {
    return {
      mode: 'detected_unconfigured',
      hint: host.hint || 'Configure Proxmox to see host pressure.',
    }
  }
  return { mode: 'off' }
}

// ── The model ──────────────────────────────────────────────────────────

/** Reshape the backend memory budget into the dashboard's memory-map model.
 *
 *  Never throws: an absent `memory` block (first poll, an old backend, or a
 *  budget the backend could not build) yields the empty, loading model. */
export function buildMemoryMapModel(input: MemoryMapInput): MemoryMapModel {
  const { memory, statsHost, selfHostname, platformLabel } = input
  const perSlot = input.perSlot || {}
  const slots = input.slots || []

  if (!memory) {
    return {
      pool: { totalGb: 0, kind: 'system', label: 'RAM', platformLabel },
      host: { mode: 'off' },
      self: { modelUsedGb: 0, selfShareGb: 0, ramUsedGb: 0, gttUsedGb: 0, npuModelGb: 0, slots: [] },
      headroom: { availableGb: 0, limitedBy: 'ram' },
      ram: null,
      gpu: null,
      loading: true,
    }
  }

  // RAM: limit_gb === 0 means the backend could not read /proc/meminfo
  // (read_ram returns zeros then). That is "unread", not a 0 GB box.
  const ram: MemoryMapRam | null =
    memory.ram && memory.ram.limit_gb > 0
      ? {
          limitGb: memory.ram.limit_gb,
          usedGb: memory.ram.used_gb,
          freeGb: memory.ram.free_gb,
          source: memory.ram.source,
        }
      : null

  const g = memory.gpu
  const gpu: MemoryMapGpu | null = g
    ? {
        kind: g.kind,
        totalGb: g.total_gb,
        usedGb: g.used_gb,
        freeGb: g.free_gb,
        otherGuestsGb: g.other_guests_gb,
        imageEngineGb: g.image_engine_gb,
        attributionExcessGb: g.attribution_excess_gb,
        renderFreeGb: memory.render_free_gb,
      }
    : null

  // ── Occupants ──
  const slotsByName = new Map(slots.map((s) => [s.name, s]))
  const joined: Joined[] = []
  if (g) {
    // The GPU pool's occupants: this box's GPU slots, then the image engine.
    // A slot still "starting" is listed by the backend but is not a resident
    // footprint: its partial bytes already sit in `other_guests`, so drawing
    // its estimate too would count them twice.
    for (const r of g.this_box || []) {
      if (String(r.state || '').toLowerCase() === 'starting') continue
      joined.push({ name: r.slot, gb: num(r.gb), fallbackDevice: 'rocm', isImageEngine: false })
    }
    if (num(g.image_engine_gb) > 0) {
      const imgSlot = slots.find(isImageSlot)
      joined.push({
        name: imgSlot?.name ?? IMAGE_ENGINE_FALLBACK_NAME,
        gb: num(g.image_engine_gb),
        fallbackDevice: 'rocm',
        isImageEngine: true,
      })
    }
  } else {
    // No GPU pool: the occupants are the resident per_slot rows, which count
    // against this box's RAM.
    for (const name of Object.keys(perSlot).sort()) {
      const row = perSlot[name]
      if (String(row?.state || '').toLowerCase() === 'starting') continue
      joined.push({ name, gb: mbToGb(rowMb(row)), fallbackDevice: 'cpu', isImageEngine: false })
    }
  }
  const colors = assignSlotColors(joined.map((j) => j.name))
  const occupants = joined.map((j) => toSlotEntry(j, slotsByName, perSlot, colors))
  const modelUsedGb = round1(occupants.reduce((acc, s) => acc + s.bytesGb, 0))

  // NPU slots are outside the GPU pool; their resident memory is its own figure.
  const npuModelGb = mbToGb(
    slots
      .filter((s) => deviceFor(s) === 'npu')
      .reduce((acc, s) => acc + rowMb(perSlot[s.name]), 0),
  )

  const host = buildHost(statsHost, input.pveTenants, selfHostname, modelUsedGb)

  // ── Headroom ──
  // What still fits for a new model: the budget's free figure for the
  // resource that binds (the pool, or RAM with no pool), less the safety
  // margin. A configured Proxmox host with less free memory than that is the
  // tighter ceiling. The old cgroup branch is gone: GTT is not charged to the
  // guest's cgroup, so the container limit never bounds the pool.
  let candidate = gpu ? gpu.freeGb : ram ? ram.freeGb : 0
  let limitedBy: 'pool' | 'host' | 'ram' = gpu ? 'pool' : 'ram'
  if (host.mode === 'configured' && host.freeGb < candidate) {
    candidate = host.freeGb
    limitedBy = 'host'
  }

  return {
    pool: gpu
      ? {
          totalGb: gpu.totalGb,
          kind: gpu.kind === 'shared' ? 'unified' : 'dedicated',
          label: gpu.kind === 'shared' ? 'GPU pool · shared' : 'VRAM',
          platformLabel,
        }
      : { totalGb: ram ? ram.limitGb : 0, kind: 'system', label: 'RAM', platformLabel },
    host,
    self: {
      modelUsedGb,
      selfShareGb: modelUsedGb,
      ramUsedGb: ram ? ram.usedGb : 0,
      gttUsedGb: gpu ? gpu.usedGb : 0,
      npuModelGb,
      slots: occupants,
    },
    headroom: { availableGb: Math.max(0, round1(candidate - SAFETY_MARGIN_GB)), limitedBy },
    ram,
    gpu,
    loading: false,
  }
}
