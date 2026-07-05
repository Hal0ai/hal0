// hal0 dashboard — DashLayout hook + fixed-band cell registry (v3).
//
// Dashboard-redesign (design_handoff_dashboard_redesign): the free-form
// 12-col grid (v:2 — order/enabled/spans/pinned, drag-reorder, resize,
// card library) is replaced by a FIXED-BAND layout with swap-in-place
// customization. Rows and cell widths are defined by the system; the user
// can only (a) swap which widget occupies a swappable cell, from a curated
// per-cell whitelist, and (b) toggle the quick-actions strip.
//
// Schema:  { v: 3, cells: Record<cellId, widgetId>, quickActions: boolean }
//
// FAIL-SOFT CONTRACT (unchanged): if the backend endpoint 404s, returns
// empty {}, an old v:2 payload, or errors for any reason, we silently fall
// back to DEFAULT_LAYOUT. The dashboard must never block on the endpoint.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

// ─── Types ────────────────────────────────────────────────────────────────────

export type CellId = string
export type WidgetId = string

export interface DashLayout {
  v: 3
  cells: Record<CellId, WidgetId>
  quickActions: boolean
}

// ─── Widget registry ──────────────────────────────────────────────────────────
// Display names for everything a cell whitelist can offer. `built` gates the
// swap picker: unbuilt widgets stay listed in the cell tooltip (design intent)
// but cannot be selected until they ship.

export interface WidgetDef {
  id: WidgetId
  name: string
  built: boolean
}

export const WIDGET_DEFS: WidgetDef[] = [
  { id: 'memorybar',   name: 'memory bar',          built: true  },
  { id: 'memtreemap',  name: 'treemap',             built: false },
  { id: 'memring',     name: 'ring',                built: false },
  { id: 'throughput',  name: 'throughput',          built: true  },
  { id: 'slottrack',   name: 'per-slot throughput', built: true  },
  { id: 'power',       name: 'power & thermal',     built: true  },
  { id: 'utilization', name: 'utilization',         built: true  },
  { id: 'gauges',      name: 'gauges',              built: false },
  { id: 'requests',    name: 'requests',            built: true  },
  { id: 'clients',     name: 'clients',             built: false },
  { id: 'slots',       name: 'slots',               built: true  },
  { id: 'activity',    name: 'activity feed',       built: true  },
  { id: 'heatmap',     name: '24h heatmap',         built: false },
  { id: 'services',    name: 'services',            built: true  },
  { id: 'quickchat',   name: 'quick chat',          built: true  },
  { id: 'attention',   name: 'needs attention',     built: true  },
]

export const WIDGET_MAP: Readonly<Record<WidgetId, WidgetDef>> = Object.fromEntries(
  WIDGET_DEFS.map(w => [w.id, w]),
)

// ─── Cell registry ────────────────────────────────────────────────────────────
// One entry per fixed cell, top → bottom / left → right. `accepts` is the
// swap whitelist (WIDGETS.md); locked cells never swap (slots, attention)
// or accept only same-family visualizations not yet built (memory hero).

export interface CellDef {
  id: CellId
  /** Cell whitelist — what the ⇄ picker offers, in display order. */
  accepts: WidgetId[]
  defaultWidget: WidgetId
  /** Locked cells render no ⇄ affordance at all. */
  locked?: boolean
}

export const CELL_DEFS: CellDef[] = [
  { id: 'memory', accepts: ['memorybar', 'memtreemap', 'memring'], defaultWidget: 'memorybar' },
  { id: 'a1', accepts: ['throughput', 'slottrack', 'power'], defaultWidget: 'throughput' },
  { id: 'a2', accepts: ['utilization', 'power', 'gauges'], defaultWidget: 'utilization' },
  { id: 'a3', accepts: ['requests', 'clients'], defaultWidget: 'requests' },
  { id: 'slots', accepts: ['slots'], defaultWidget: 'slots', locked: true },
  { id: 'c1', accepts: ['activity', 'heatmap'], defaultWidget: 'activity' },
  { id: 'c2', accepts: ['services', 'quickchat'], defaultWidget: 'services' },
  { id: 'c3', accepts: ['attention'], defaultWidget: 'attention', locked: true },
]

export const CELL_MAP: Readonly<Record<CellId, CellDef>> = Object.fromEntries(
  CELL_DEFS.map(c => [c.id, c]),
)

// ─── Default layout ───────────────────────────────────────────────────────────

function buildDefaultLayout(): DashLayout {
  return {
    v: 3,
    cells: Object.fromEntries(CELL_DEFS.map(c => [c.id, c.defaultWidget])),
    quickActions: true,
  }
}

export const DEFAULT_LAYOUT: DashLayout = buildDefaultLayout()

// ─── reconcile ────────────────────────────────────────────────────────────────
// Pure function, run on load. Guarantees every cell exists and holds a
// widget from its own whitelist that is actually built — an unknown /
// unbuilt / out-of-whitelist assignment falls back to the cell default, so
// a stale saved layout can never blank a band.

export function reconcile(layout: Partial<DashLayout> | null | undefined): DashLayout {
  const rawCells = (layout && typeof layout.cells === 'object' && layout.cells) || {}
  const cells: Record<CellId, WidgetId> = {}
  for (const cell of CELL_DEFS) {
    const assigned = rawCells[cell.id]
    const valid =
      typeof assigned === 'string' &&
      cell.accepts.includes(assigned) &&
      WIDGET_MAP[assigned]?.built
    cells[cell.id] = valid ? assigned : cell.defaultWidget
  }
  return {
    v: 3,
    cells,
    quickActions: layout?.quickActions !== false,
  }
}

// ─── useDashLayout ────────────────────────────────────────────────────────────

const LAYOUT_QUERY_KEY = ['dash', 'layout']

export function useDashLayout() {
  return useQuery<DashLayout>({
    queryKey: LAYOUT_QUERY_KEY,
    queryFn: async () => {
      try {
        const raw = await apiGet<Partial<DashLayout> | Record<string, never>>(
          ENDPOINTS.dashboardLayout,
        )
        // {} (nothing saved) or an old v:2 payload → defaults.
        if (!raw || typeof raw !== 'object' || (raw as { v?: number }).v !== 3) {
          return DEFAULT_LAYOUT
        }
        return reconcile(raw as Partial<DashLayout>)
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
    onMutate: (layout) => {
      // Optimistically update the local cache — a swap must reflect
      // immediately even while the PUT is in flight (or 404s).
      qc.setQueryData(LAYOUT_QUERY_KEY, layout)
    },
    onError: () => {
      // Backend not yet shipping this endpoint — silently swallow. The view
      // continues to work in-memory; persistence activates when BE lands.
    },
  })
}
