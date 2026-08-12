import { describe, expect, it } from 'vitest'
import { orderServices, servicesForWidget } from '../services-widget-pure.js'

const SERVICE_ORDER = ['openwebui', 'comfyui', 'hermes', 'n8n']

describe('orderServices', () => {
  it('returns the named services in SERVICE_ORDER, then the rest', () => {
    const services = [
      { id: 'openwebui', up: true },
      { id: 'comfyui', up: false },
      { id: 'hermes', up: true },
      { id: 'hindsight', up: true },
    ]
    const ordered = orderServices(services, SERVICE_ORDER)
    expect(ordered).toHaveLength(4)
    expect(ordered.map((s) => s.id)).toEqual(['openwebui', 'comfyui', 'hermes', 'hindsight'])
  })

  it('falls back to an empty list when services is undefined', () => {
    expect(orderServices(undefined, SERVICE_ORDER)).toEqual([])
  })

  it('caps the result at the given limit', () => {
    const services = [{ id: 'a' }, { id: 'b' }, { id: 'c' }, { id: 'd' }, { id: 'e' }]
    expect(orderServices(services, [], 4)).toHaveLength(4)
  })
})

describe('servicesForWidget (#1836 regression)', () => {
  // Real shape of useServices()'s return value: { services, mdns, pending }.
  // There is no `data` key — the bug read `svc.data?.services`, which is
  // always undefined, so the widget always rendered "no companion services"
  // no matter what the backend reported.
  const svc = {
    services: [
      { id: 'openwebui', up: true },
      { id: 'comfyui', up: false },
      { id: 'hermes', up: true },
      { id: 'hindsight', up: true },
    ],
    mdns: null,
    pending: false,
  }

  it('reads svc.services directly and returns non-empty ordered services', () => {
    const ordered = servicesForWidget(svc, SERVICE_ORDER)
    expect(ordered.length).toBeGreaterThan(0)
    expect(ordered.map((s) => s.id)).toEqual(['openwebui', 'comfyui', 'hermes', 'hindsight'])
  })

  it('does NOT reproduce the svc.data?.services bug (svc has no data key)', () => {
    // The buggy access always yields [], regardless of real data, because
    // useServices()'s return value has no `data` key.
    const buggyServices = (svc as { data?: { services?: unknown } }).data?.services
    expect(orderServices(buggyServices, SERVICE_ORDER)).toEqual([])
    // The fix must not go through `.data`.
    expect(servicesForWidget(svc, SERVICE_ORDER)).not.toEqual([])
  })
})
