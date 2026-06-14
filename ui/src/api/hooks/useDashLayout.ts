// hal0 dashboard — DashLayout hook + card registry (W3)
//
// Implements §4 of CONTRACTS.md:
//   - DashLayout type (v:2, order, enabled, spans, pinned)
//   - CARD_REGISTRY table
//   - reconcile(layout, slots) pure function
//   - useDashLayout() — GET with fail-soft fallback to DEFAULT_LAYOUT
//   - useSaveDashLayout() — PUT mutation → 204
//
// FAIL-SOFT CONTRACT: if the backend endpoint 404s, returns empty {}, or
// errors for any reason, we silently fall back to DEFAULT_LAYOUT. The whole
// dashboard must never be blocked on a missing endpoint.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'
import type { Slot } from './useSlots'

// ─── Constants ────────────────────────────────────────────────────────────────

export const GRID_COLS = 12
export const ROW_UNIT = 8
export const GRID_GAP = 16

// ─── Types ────────────────────────────────────────────────────────────────────

export type LayoutKey = string  // a CardId or "pin:<slotName>"
export type CardId = string

export interface DashLayout {
  v: 2
  order: LayoutKey[]
  enabled: Record<CardId, boolean>
  spans: Record<LayoutKey, number>
  pinned: string[]
}

// ─── Card registry ────────────────────────────────────────────────────────────
// Single source of truth for all cards: id, display name, default span,
// min span, locked (cannot be disabled), and default-on status.
// "icon" is an optional string key referencing the product icon set.

export interface CardDef {
  id: CardId
  name: string
  icon?: string
  span: number
  min: number
  locked?: boolean
  defaultOn: boolean
}

export const CARD_REGISTRY: CardDef[] = [
  { id: 'slots',       name: 'Slots',              icon: 'slots',     span: 12, min: 8,  locked: true,  defaultOn: true  },
  { id: 'memory',      name: 'Memory',             icon: 'gauge',     span: 8,  min: 4,  locked: false, defaultOn: true  },
  { id: 'throughput',  name: 'Throughput',         icon: 'bolt',      span: 4,  min: 3,  locked: false, defaultOn: true  },
  { id: 'quickchat',   name: 'Quick Chat',         icon: 'chat',      span: 6,  min: 4,  locked: false, defaultOn: true  },
  { id: 'services',    name: 'Services',           icon: 'route',     span: 6,  min: 4,  locked: false, defaultOn: true  },
  { id: 'utilization', name: 'Utilization',        icon: 'gauge',     span: 4,  min: 3,  locked: false, defaultOn: true  },
  { id: 'attention',   name: 'Needs Attention',    icon: 'shield',    span: 4,  min: 3,  locked: false, defaultOn: true  },
  { id: 'slottrack',   name: 'Per-Slot Throughput',icon: 'flow',      span: 4,  min: 3,  locked: false, defaultOn: false },
  { id: 'approvals',   name: 'Agent Approvals',    icon: 'shield',    span: 4,  min: 3,  locked: false, defaultOn: false },
  { id: 'power',       name: 'Power & Thermal',    icon: 'thermo',    span: 4,  min: 3,  locked: false, defaultOn: false },
  { id: 'scheduler',   name: 'Scheduler',          icon: 'clock',     span: 4,  min: 3,  locked: false, defaultOn: false },
]

// Map for fast lookup by id
export const CARD_REGISTRY_MAP: Readonly<Record<CardId, CardDef>> = Object.fromEntries(
  CARD_REGISTRY.map(c => [c.id, c])
)

// ─── Default layout ───────────────────────────────────────────────────────────
// Built from CARD_REGISTRY: all defaultOn cards in order, default spans, no pins.

function buildDefaultLayout(): DashLayout {
  const defaultCards = CARD_REGISTRY.filter(c => c.defaultOn)
  return {
    v: 2,
    order: defaultCards.map(c => c.id),
    enabled: Object.fromEntries(CARD_REGISTRY.map(c => [c.id, c.defaultOn])),
    spans: Object.fromEntries(CARD_REGISTRY.map(c => [c.id, c.span])),
    pinned: [],
  }
}

export const DEFAULT_LAYOUT: DashLayout = buildDefaultLayout()

// ─── reconcile ────────────────────────────────────────────────────────────────
// Pure function — run on load, both FE and BE.
// Rules (per CONTRACTS §4):
//   1. Every name in layout.pinned has a "pin:<name>" key in order (insert
//      right after "slots", after existing pin: keys).
//   2. Drop "pin:<name>" keys in order for slots that no longer exist in the
//      live slot list.
//   3. Clamp spans to [card.min, 12]; for pin cards use min=3, max=12.
//   4. Ensure locked cards are always enabled.

export function reconcile(layout: DashLayout, slots: Slot[] | null | undefined): DashLayout {
  const slotNames = new Set((slots ?? []).map(s => s.name))

  // 1. Clean up pinned: remove entries for slots that no longer exist
  const pinned = (layout.pinned ?? []).filter(name => slotNames.has(name))

  // 2. Rebuild order: keep non-pin keys, re-insert pin:<name> after "slots"
  const existingNonPin = (layout.order ?? []).filter(k => !k.startsWith('pin:'))
  const newOrder: LayoutKey[] = []

  for (const key of existingNonPin) {
    newOrder.push(key)
    // After "slots" entry, insert all currently-pinned slot keys
    if (key === 'slots') {
      for (const name of pinned) {
        newOrder.push(`pin:${name}`)
      }
    }
  }

  // If "slots" wasn't in order at all (shouldn't happen with valid layout),
  // append pin keys at the front after slots is added
  const hasSlotsKey = newOrder.some(k => k === 'slots')
  if (!hasSlotsKey && pinned.length > 0) {
    // Prepend: slots (locked), then pins
    newOrder.unshift(...pinned.map(n => `pin:${n}`), 'slots')
  }

  // Ensure "slots" is always present (locked)
  if (!newOrder.includes('slots')) {
    newOrder.unshift('slots')
  }

  // 3. Clamp spans
  const spans: Record<LayoutKey, number> = {}
  for (const key of newOrder) {
    const rawSpan = layout.spans?.[key] ?? 0
    if (key.startsWith('pin:')) {
      const pinMin = 3
      spans[key] = Math.max(pinMin, Math.min(12, rawSpan || pinMin))
    } else {
      const def = CARD_REGISTRY_MAP[key]
      const min = def?.min ?? 3
      const defaultSpan = def?.span ?? 4
      spans[key] = Math.max(min, Math.min(12, rawSpan || defaultSpan))
    }
  }

  // 4. Ensure enabled reflects locked cards + keep existing enabled state
  const enabled: Record<CardId, boolean> = { ...(layout.enabled ?? {}) }
  for (const card of CARD_REGISTRY) {
    if (card.locked) enabled[card.id] = true
    if (!(card.id in enabled)) enabled[card.id] = card.defaultOn
  }

  return { v: 2, order: newOrder, enabled, spans, pinned }
}

// ─── useDashLayout ────────────────────────────────────────────────────────────

const LAYOUT_QUERY_KEY = ['dash', 'layout']

export function useDashLayout() {
  return useQuery<DashLayout>({
    queryKey: LAYOUT_QUERY_KEY,
    queryFn: async () => {
      try {
        const raw = await apiGet<DashLayout | Record<string, never>>(ENDPOINTS.dashboardLayout)
        // Empty object {} means no layout saved yet → use default
        if (!raw || typeof raw !== 'object' || !('v' in raw)) {
          return DEFAULT_LAYOUT
        }
        return raw as DashLayout
      } catch {
        // 404, network error, or any other failure → fail soft to default
        return DEFAULT_LAYOUT
      }
    },
    // Layout rarely changes; 30s stale time avoids hammering a new/missing endpoint
    staleTime: 30_000,
    // Never error the query — we always return a value from queryFn
    retry: false,
  })
}

// ─── useSaveDashLayout ────────────────────────────────────────────────────────

export function useSaveDashLayout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (layout: DashLayout) =>
      api<void>(ENDPOINTS.dashboardLayout, { method: 'PUT', body: layout as unknown as Record<string, unknown>, raw: true }),
    onSuccess: (_data, layout) => {
      // Optimistically update the local cache so the saved state is immediately
      // reflected without a round-trip re-fetch
      qc.setQueryData(LAYOUT_QUERY_KEY, layout)
    },
    onError: () => {
      // Backend not yet shipping this endpoint — silently swallow. The grid
      // continues to work in-memory; persistence will activate when BE lands.
    },
  })
}
