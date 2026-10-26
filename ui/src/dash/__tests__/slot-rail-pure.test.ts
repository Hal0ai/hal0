// The slot rail's pure rules: which group a device lands in, how rows are ordered and counted, what
// the state cell and the footer say. The component (slot-rail.test.tsx) only draws the result.
import { describe, expect, it } from 'vitest'
import type { Slot } from '@/api/hooks/useSlots'
import {
  RAIL_GROUPS,
  railCounts,
  railFooter,
  railGroup,
  railRows,
  stateCell,
  type RailRow,
} from '../slot-rail-pure'

// `state` drives the indicator: the slot carries no container_status, so the bare-state fallback
// in slot-status.js classifies it (serving -> serving, ready -> stale, stopped -> offline).
const slot = (name: string, device: string, state: string, extra: Partial<Slot> = {}): Slot =>
  ({ name, type: 'llm', device, model: `${name}.gguf`, state, metrics: {}, ...extra }) as Slot

const row: RailRow = {
  name: 'brain',
  model: 'brain.gguf',
  device: 'gpu-vulkan',
  group: 'vulkan',
  cls: 'serving',
  label: 'serving',
  tooltip: '',
  toks: null,
  mem: '—',
  up: true,
  configured: true,
}

describe('railGroup', () => {
  it('maps a device onto its rail group', () => {
    expect(RAIL_GROUPS.map((g) => g.key)).toEqual(['vulkan', 'rocm', 'npu', 'cpu'])
    expect(RAIL_GROUPS.map((g) => g.label)).toEqual(['GPU · vulkan', 'GPU · rocm', 'NPU', 'CPU'])
    expect(railGroup('gpu-vulkan')).toBe('vulkan')
    expect(railGroup('gpu-rocm')).toBe('rocm')
    expect(railGroup('npu')).toBe('npu')
    expect(railGroup('cpu')).toBe('cpu')
    expect(railGroup(null)).toBe('cpu')
    expect(railGroup(undefined)).toBe('cpu')
    expect(railGroup('')).toBe('cpu')
  })
})

describe('railRows', () => {
  it('groups by device and orders serving first inside a group', () => {
    const rows = railRows(
      [slot('agent', 'gpu-vulkan', 'stopped'), slot('brain', 'gpu-vulkan', 'serving'), slot('flm', 'npu', 'ready')],
      [],
      false,
    )
    expect(rows.map((r) => r.name)).toEqual(['brain', 'agent', 'flm'])
    expect(rows.map((r) => r.group)).toEqual(['vulkan', 'vulkan', 'npu'])
  })

  it('marks an unloaded slot paused only while image mode is on', () => {
    const on = railRows([slot('brain', 'gpu-vulkan', 'stopped')], ['brain'], true)[0]
    expect(on.cls).toBe('paused')
    expect(on.label).toBe('paused')
    expect(on.up).toBe(false)
    expect(railRows([slot('brain', 'gpu-vulkan', 'stopped')], ['brain'], false)[0].cls).toBe('offline')
    expect(railRows([slot('brain', 'gpu-vulkan', 'stopped')], [], true)[0].cls).toBe('offline')
  })

  it('orders serving, warming, paused, stale, error, offline, then input order', () => {
    const rows = railRows(
      [
        slot('off', 'gpu-vulkan', 'stopped'),
        slot('err', 'gpu-vulkan', 'error'),
        slot('rdy', 'gpu-vulkan', 'ready'),
        slot('pau', 'gpu-vulkan', 'stopped'),
        slot('wrm', 'gpu-vulkan', 'warming'),
        slot('srv2', 'gpu-vulkan', 'serving'),
        slot('srv1', 'gpu-vulkan', 'serving'),
      ],
      ['pau'],
      true,
    )
    expect(rows.map((r) => r.name)).toEqual(['srv2', 'srv1', 'wrm', 'pau', 'rdy', 'err', 'off'])
  })

  it('carries the model, tok/s and the memory label the caller supplies', () => {
    const [r] = railRows(
      [slot('brain', 'gpu-rocm', 'serving', { metrics: { toks: 42 }, model: '' })],
      [],
      false,
      Date.now(),
      () => '5.0 GB',
    )
    expect(r.model).toBeNull()
    expect(r.toks).toBe(42)
    expect(r.mem).toBe('5.0 GB')
    expect(r.up).toBe(true)
    expect(r.device).toBe('gpu-rocm')
  })
})

describe('railCounts', () => {
  it('counts ready as serving plus stale, and up per group', () => {
    const rows = railRows(
      [
        slot('a', 'gpu-vulkan', 'serving'),
        slot('b', 'gpu-vulkan', 'ready'),
        slot('c', 'gpu-vulkan', 'stopped'),
        slot('d', 'npu', 'warming'),
        slot('e', 'cpu', 'error'),
        // Listed, but no model bound: not counted, as in the dock's slots fraction.
        slot('f', 'gpu-vulkan', 'stopped', { model: '' }),
      ],
      [],
      false,
    )
    expect(rows).toHaveLength(6)
    expect(rows.find((r) => r.name === 'f')!.configured).toBe(false)
    expect(rows.find((r) => r.name === 'a')!.configured).toBe(true)
    const c = railCounts(rows)
    expect(c.ready).toBe(2)
    expect(c.total).toBe(5)
    expect(c.byGroup.vulkan).toEqual({ up: 2, total: 3 })
    expect(c.byGroup.npu).toEqual({ up: 0, total: 1 })
    expect(c.byGroup.cpu).toEqual({ up: 0, total: 1 })
    expect(c.byGroup.rocm).toEqual({ up: 0, total: 0 })
  })
})

describe('stateCell', () => {
  it('shows tok/s only while serving with a figure', () => {
    expect(stateCell({ ...row, cls: 'serving', toks: 42 })).toEqual({ text: '42 tok/s', tone: 'live' })
    expect(stateCell({ ...row, cls: 'serving', toks: 0 })).toEqual({ text: 'serving', tone: 'live' })
    expect(stateCell({ ...row, cls: 'paused', label: 'paused' })).toEqual({ text: 'paused', tone: 'paused' })
  })

  it('tones warming, error, ready and stopped rows', () => {
    expect(stateCell({ ...row, cls: 'warming', label: 'starting' })).toEqual({ text: 'starting', tone: 'warn' })
    expect(stateCell({ ...row, cls: 'error', label: 'error' })).toEqual({ text: 'error', tone: 'err' })
    expect(stateCell({ ...row, cls: 'stale', label: 'ready' })).toEqual({ text: 'ready', tone: '' })
    expect(stateCell({ ...row, cls: 'offline', label: 'stopped' })).toEqual({ text: 'stopped', tone: '' })
  })
})

describe('railFooter', () => {
  it('sums loaded GB over live gpu and npu rows and counts paused', () => {
    const slots = [
      slot('a', 'gpu-vulkan', 'serving', { mem_mb: 2048 }),
      slot('b', 'npu', 'ready', { mem_mb: 1024 }),
      slot('c', 'cpu', 'ready', { mem_mb: 4096 }), // cpu group: not counted
      slot('d', 'gpu-rocm', 'stopped', { mem_mb: 8192 }), // not up
      slot('e', 'gpu-vulkan', 'stopped', { mem_mb: 512 }), // paused
    ]
    const rows = railRows(slots, ['e'], true)
    expect(railFooter(slots, rows, 12.5)).toEqual({ loadedGb: 3, poolFreeGb: 12.5, paused: 1 })
    expect(railFooter(slots, rows, null).poolFreeGb).toBeNull()
  })
})
