// Pure helpers for the Overview "Services" widget (RDServicesCard) — no
// React, no API imports, so vitest can exercise them without a DOM.
//
// Regression test for #1836: the widget read `svc.data?.services`, but
// `useServices()` returns `{ services, mdns, pending }` (no `data` key), so
// `services` was always `undefined` and the widget always fell through to
// the "no companion services" empty state, regardless of what the backend
// reported.

// Order the services list: named services first (in SERVICE_ORDER), then
// any remaining services, capped at `limit` entries total.
export function orderServices(services, order, limit = 4) {
  const list = services ?? []
  const ordered = [
    ...order.map((id) => list.find((s) => s.id === id)).filter(Boolean),
    ...list.filter((s) => !order.includes(s.id)),
  ]
  return ordered.slice(0, limit)
}

// Extract + order the widget's list directly from useServices()'s return
// value ({ services, mdns, pending } — no `data` key). Isolates the exact
// call-site defect from #1836 (`svc.data?.services` instead of
// `svc.services`) so it's covered without rendering the component.
export function servicesForWidget(svc, order, limit = 4) {
  return orderServices(svc?.services, order, limit)
}
