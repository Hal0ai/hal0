import { describe, expect, it } from 'vitest'
import type { ManagedService } from '@/api/hooks/useServices'
import { orderServices, servicesForWidget } from '../services-widget-pure'

const SERVICE_ORDER = ['openwebui', 'comfyui', 'hermes', 'n8n']

/** Minimal but type-complete ManagedService — only `id`/`up` vary per case. */
function svcOf(id: string, up = true): ManagedService {
  return {
    id,
    name: id,
    description: '',
    managed: true,
    unit: `hal0-${id}.service`,
    unit_state: null,
    up,
    detail: up ? 'reachable' : 'unreachable',
    stat: null,
    url: null,
    mdns_url: null,
    loopback_port: null,
    actions: [],
    mdns_capable: false,
    hints: [],
  }
}

describe('orderServices', () => {
  it('returns the named services in SERVICE_ORDER, then the rest', () => {
    const services = [
      svcOf('openwebui'),
      svcOf('comfyui', false),
      svcOf('hermes'),
      svcOf('hindsight'),
    ]
    const ordered = orderServices(services, SERVICE_ORDER)
    expect(ordered).toHaveLength(4)
    expect(ordered.map((s) => s.id)).toEqual(['openwebui', 'comfyui', 'hermes', 'hindsight'])
  })

  it('falls back to an empty list when services is undefined', () => {
    expect(orderServices(undefined, SERVICE_ORDER)).toEqual([])
  })

  it('caps the result at the given limit', () => {
    const services = ['a', 'b', 'c', 'd', 'e'].map((id) => svcOf(id))
    expect(orderServices(services, [], 4)).toHaveLength(4)
  })
})

describe('servicesForWidget (#1836)', () => {
  // Real shape of useServices()'s return value: { services, mdns, pending }.
  // There is no `data` key — the bug read `svc.data?.services`, which is
  // always undefined, so the widget always rendered "no companion services"
  // no matter what the backend reported.
  const svc = {
    services: [svcOf('openwebui'), svcOf('comfyui', false), svcOf('hermes'), svcOf('hindsight')],
    mdns: null,
    pending: false,
  }

  it('reads svc.services and returns the ordered list', () => {
    const ordered = servicesForWidget(svc, SERVICE_ORDER)
    expect(ordered.map((s) => s.id)).toEqual(['openwebui', 'comfyui', 'hermes', 'hindsight'])
  })

  // Discriminating assertion, not a restatement of the fixture: the input
  // carries BOTH a `data.services` decoy and the real `services`, so any
  // implementation that consults `.data` first (`svc.data?.services ?? …`,
  // the shape of the original bug) returns the decoys and fails here.
  it('ignores a `data.services` key entirely', () => {
    const decoyed = {
      ...svc,
      data: { services: [svcOf('decoy-a'), svcOf('decoy-b')] },
    }
    const ordered = servicesForWidget(decoyed, SERVICE_ORDER)
    expect(ordered.map((s) => s.id)).toEqual(['openwebui', 'comfyui', 'hermes', 'hindsight'])
    expect(ordered.some((s) => s.id.startsWith('decoy'))).toBe(false)
  })

  it('returns [] rather than throwing when the hook result is not yet populated', () => {
    expect(servicesForWidget(undefined, SERVICE_ORDER)).toEqual([])
    expect(servicesForWidget({ services: [] }, SERVICE_ORDER)).toEqual([])
  })
})
