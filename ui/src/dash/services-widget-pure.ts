// Pure helpers for the Overview "Services" widget (RDServicesCard) — no
// React, no API imports, so vitest can exercise them without a DOM.
//
// Regression test for #1836: the widget read `svc.data?.services`, but
// `useServices()` returns `{ services, mdns, pending }` (no `data` key), so
// `services` was always `undefined` and the widget always fell through to
// the "no companion services" empty state, regardless of what the backend
// reported.
//
// TypeScript, not JS, deliberately: typing the parameter as the hook's real
// return shape makes a future `svc.data` re-introduction a compile error at
// this boundary rather than a silent `undefined`. The `.jsx` call site
// itself still sits outside tsc's view — what covers that is the e2e spec
// tests/e2e/specs/overview-services-widget-v3.spec.ts, which renders the
// widget against a real /api/services response.

import type { ManagedService } from '@/api/hooks/useServices'

/** The subset of `useServices()`'s return value this widget consumes. */
export interface ServicesHookResult {
  services: ManagedService[]
}

// Order the services list: named services first (in SERVICE_ORDER), then
// any remaining services, capped at `limit` entries total.
export function orderServices(
  services: ManagedService[] | undefined | null,
  order: string[],
  limit = 4,
): ManagedService[] {
  const list = services ?? []
  const ordered = [
    ...order
      .map((id) => list.find((s) => s.id === id))
      .filter((s): s is ManagedService => Boolean(s)),
    ...list.filter((s) => !order.includes(s.id)),
  ]
  return ordered.slice(0, limit)
}

// Extract + order the widget's list directly from useServices()'s return
// value ({ services, mdns, pending } — no `data` key). Isolates the exact
// call-site defect from #1836 (`svc.data?.services` instead of
// `svc.services`).
export function servicesForWidget(
  svc: ServicesHookResult | null | undefined,
  order: string[],
  limit = 4,
): ManagedService[] {
  return orderServices(svc?.services, order, limit)
}
