// hal0 dashboard — component-update pure helpers (Task 12, spec
// 2026-08-30 component-updates §4).
//
// No React, no API imports — vitest exercises these without a DOM, mirroring
// services-widget-pure.ts. Consumed by services.jsx (per-service version
// cell + retry), services-card.jsx and UpdatesPage.jsx (pending badge).
//
// Row shape mirrors GET /api/updates/components (Task 8,
// hal0.components.status.component_status_snapshot). `PENDING` below is
// deliberately the same set as that route's `_PENDING_STATUSES` — pending +
// stale + the three failure statuses all count toward the "N updates
// pending" badge.

export type ComponentRow = {
  id: string
  name?: string
  service_id: string | null
  installed: string | null
  pinned: string | null
  pin_label?: string | null
  status: string
  error?: string | null
  remedy?: string | null
  detail?: Array<{ key: string; image: string }>
}

// Failure statuses a retry (POST .../converge) makes sense for. Never
// includes 'pending' — the spec (§4) is explicit that pending rows get no
// update-now button, only a passive arrow label; retry is failure recovery.
export const RETRYABLE_STATUSES = ['build_failed', 'snapshot_failed', 'rolled_back'] as const

const PENDING = new Set(['pending', 'stale', ...RETRYABLE_STATUSES])

const short = (v: string | null | undefined, label?: string | null) =>
  label || (v && v.startsWith('sha256:') ? v.slice(7, 19) : v) || '—'

export function componentForService(rows: ComponentRow[] | null | undefined, serviceId: string) {
  return (rows ?? []).find(r => r.service_id === serviceId)
}

export function componentCell(row: ComponentRow): { label: string; tone: 'ok' | 'pending' | 'failed' | 'muted' } {
  if ((RETRYABLE_STATUSES as readonly string[]).includes(row.status))
    return { label: `${short(row.installed)} — ${row.status}`, tone: 'failed' }
  if (row.status === 'not-installed') return { label: 'not installed', tone: 'muted' }
  if (row.status === 'converged' || row.status === 'override')
    return { label: `${short(row.installed, row.pin_label)} ✓`, tone: 'ok' }
  return { label: `${short(row.installed)} → ${short(row.pinned, row.pin_label)} pending`, tone: 'pending' }
}

export function pendingCount(rows: ComponentRow[] | null | undefined): number {
  return (rows ?? []).filter(r => PENDING.has(r.status)).length
}
