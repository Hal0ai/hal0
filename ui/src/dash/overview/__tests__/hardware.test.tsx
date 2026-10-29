// @vitest-environment happy-dom
//
// OvHardware: five rows, sample rings, device gating.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as any).React = React

let hw: any
let stats: any
let power: any
let health: any
let slots: any[]
let ram: any

vi.mock('@/api/hooks/useHardware', async () => {
  const actual = await vi.importActual<any>('@/api/hooks/useHardware')
  return { ...actual, useHardware: () => ({ data: hw }) }
})
vi.mock('@/api/hooks/useStatsHardware', () => ({ useStatsHardware: () => ({ data: stats }) }))
vi.mock('@/api/hooks/useStatsPower', () => ({ useStatsPower: () => ({ data: power }) }))
vi.mock('@/api/hooks/useRuntime', () => ({ useHealthSystem: () => ({ data: health }) }))
vi.mock('@/api/hooks/useSlots', () => ({ useSlots: () => ({ data: slots }) }))
vi.mock('@/dash/memory-map', () => ({ useMemoryMapModel: () => ({ ram }) }))

await import('../shell.jsx')
await import('../hardware.jsx')
const OvHardware = (window as any).OvHardware as React.ComponentType<{ onGo: (id: string) => void }>

let host: HTMLDivElement
let root: ReturnType<typeof createRoot>

const GPU_HW = { computeCapable: true, vulkanCapable: true, gpu: 'Radeon 8060S', cores: '16c · 32t', npu: { present: true } }

function render() {
  act(() => root.render(<OvHardware onGo={() => {}} />))
}

beforeEach(() => {
  hw = GPU_HW
  stats = { gpu_util: 0.33, gpu_clock_mhz: 600, gpu_temp_c: 47, cpu_util: 0.12, npu_status: { ok: false } }
  power = { gpu_power_w: 33, cpu_temp_c: 52 }
  health = { status: 'ok', checks: { disk_state: { ok: true, free_mb: 124 * 1024, floor_mb: 512, path: '/var/lib/hal0' } } }
  slots = []
  ram = { usedGb: 13.14, limitGb: 106.2, source: 'container' }
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  document.body.innerHTML = ''
})

const rows = () => Array.from(host.querySelectorAll('.ov-hw'))
const row = (label: string) => rows().find((r) => r.querySelector('.ov-k')!.textContent!.trim() === label)!

describe('OvHardware', () => {
  it('renders gpu, cpu, npu, ram and disk rows', () => {
    render()
    expect(rows()).toHaveLength(5)
    expect(host.textContent).toContain('Radeon 8060S · 0.6 GHz · 47°C · 33 W')
    expect(row('ram').textContent).toContain('13.1 / 106 GB')
    expect(row('disk').textContent).toContain('124 GB free')
    expect(row('disk').textContent).toContain('/var/lib/hal0 · floor 0.5 GB')
    expect(row('npu').textContent).toContain('no FLM slot loaded')
    expect(row('npu').textContent).toContain('idle')
  })

  it('draws a sparkline on the GPU row after two data updates', () => {
    render()
    expect(row('gpu').querySelector('svg')).toBeNull()
    stats = { ...stats, gpu_util: 0.5 }
    render()
    stats = { ...stats, gpu_util: 0.6 }
    render()
    expect(row('gpu').querySelector('svg')).not.toBeNull()
    expect(row('cpu').querySelector('svg')).not.toBeNull()
  })

  it('does not grow the ring on a re-render with unchanged data', () => {
    render()
    render()
    render()
    expect(row('gpu').querySelector('svg')).toBeNull()
  })

  it('CPU-only box: no GPU access, no NPU row', () => {
    hw = { computeCapable: false, vulkanCapable: false, gpu: 'host gpu', cores: '8c · 16t', npu: { present: false } }
    render()
    expect(row('gpu').textContent).toContain('no GPU access')
    expect(row('gpu').textContent).toContain('—')
    expect(rows()).toHaveLength(4)
    expect(host.textContent).not.toContain('npu')
  })

  it('names coresident NPU slots and shows the active pill', () => {
    stats = { ...stats, npu_status: { ok: true, model_mb: 2048 } }
    slots = [{ name: 'flm-a', device: 'npu', coresident: true }, { name: 'flm-b', device: 'npu', coresident: true }]
    render()
    expect(row('npu').textContent).toContain('flm-a + flm-b coresident')
    expect(row('npu').textContent).toContain('active')
  })

  it('shows dashes for unsourced RAM and disk', () => {
    ram = null
    health = undefined
    render()
    expect(row('ram').querySelector('.ov-v')!.textContent).toBe('—')
    expect(row('disk').querySelector('.ov-v')!.textContent).toBe('—')
  })
})
