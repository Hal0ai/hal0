// @vitest-environment happy-dom
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
// SRow renders its `sub` through the FieldInfoIcon window global.
;(globalThis as any).FieldInfoIcon = ({ description }: any) => <span data-sub={description} />

// Per-test data; the mocks below read it at call time.
const state: { memory: any } = { memory: undefined }

vi.mock('@/api/hooks/useStatsHardware', () => ({
  useStatsHardware: () => ({ data: { cpu_util: 0.1, memory: state.memory }, isPending: false }),
  useMemoryBudget: () => ({ data: state.memory }),
}))
vi.mock('@/api/hooks/useHardware', async (importActual) => ({
  ...(await importActual<object>()),
  useHardware: () => ({ data: { computeCapable: true } }),
}))

import { HardwarePanel, _pct } from './OverviewPage.jsx'

describe('_pct', () => {
  it('renders the 0..1 API fraction as a percentage', () => {
    expect(_pct(0.32)).toBe('32%')
    expect(_pct(1)).toBe('100%')
    expect(_pct(0)).toBe('0%')
  })

  it('keeps the missing-value placeholder', () => {
    expect(_pct(null)).toBe('—')
    expect(_pct(undefined)).toBe('—')
  })
})

const ram = (over: Record<string, unknown> = {}) => ({
  limit_gb: 105.5,
  used_gb: 16.1,
  free_gb: 89.4,
  source: 'container',
  ...over,
})
const gpu = (over: Record<string, unknown> = {}) => ({
  kind: 'shared',
  total_gb: 116,
  used_gb: 43.4,
  this_box: [],
  image_engine_gb: 0,
  other_guests_gb: 0,
  attribution_excess_gb: 0,
  free_gb: 72.6,
  ...over,
})

function rows(memory: any): Map<string, { v: string; sub: string | null }> {
  state.memory = memory
  const host = document.createElement('div')
  document.body.appendChild(host)
  act(() => {
    createRoot(host).render(<HardwarePanel />)
  })
  const out = new Map()
  host.querySelectorAll('.s-row').forEach((row) => {
    const k = row.querySelector('.k span')?.textContent
    if (!k) return
    out.set(k, {
      v: row.querySelector('.v')?.textContent ?? '',
      sub: row.querySelector('[data-sub]')?.getAttribute('data-sub') ?? null,
    })
  })
  return out
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('Live hardware panel reads the memory budget', () => {
  it('shows this box RAM as used over its container limit', () => {
    const r = rows({ gpu: gpu(), ram: ram(), render_free_gb: 60 }).get('RAM')!
    expect(r.v).toBe('16.1 GB / 105.5 GB')
    expect(r.sub).toBe('This box · container limit')
  })

  it('drops the container suffix when the limit is the system total', () => {
    const r = rows({ gpu: gpu(), ram: ram({ source: 'system' }), render_free_gb: 60 }).get('RAM')!
    expect(r.sub).toBe('This box')
  })

  it('shows no RAM figure when the backend could not read RAM', () => {
    const r = rows({ gpu: null, ram: ram({ limit_gb: 0, used_gb: 0, free_gb: 0 }), render_free_gb: null }).get('RAM')!
    expect(r.v).toBe('—')
  })

  it('names the GPU pool row after the pool and sizes it from the budget', () => {
    const shared = rows({ gpu: gpu(), ram: ram(), render_free_gb: 60 })
    expect(shared.get('GPU pool · shared')!.v).toBe('43.4 GB / 116.0 GB')
    expect(shared.has('GPU VRAM')).toBe(false)
    const dedicated = rows({ gpu: gpu({ kind: 'dedicated', total_gb: 24, used_gb: 5 }), ram: ram(), render_free_gb: 9 })
    expect(dedicated.get('VRAM')!.v).toBe('5.0 GB / 24.0 GB')
  })

  it('hides the pool row when there is no GPU pool', () => {
    const r = rows({ gpu: null, ram: ram(), render_free_gb: null })
    expect([...r.keys()].some((k) => k === 'VRAM' || k.startsWith('GPU pool'))).toBe(false)
  })
})
