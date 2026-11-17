import { describe, expect, it } from 'vitest'
import {
  catalogDot,
  catalogStatus,
  matchesStateFilter,
  npuFitOf,
  pageSections,
  runsOn,
  sectionKey,
  slotsUsing,
} from './catalog-pure'

describe('catalogStatus', () => {
  it('names a free installed model installed', () => {
    expect(catalogStatus({ installed: true })).toMatchObject({ kind: 'installed', label: 'installed', tone: 'ok' })
  })
  it('names the first user of a model in use, and all of them in the tooltip', () => {
    const s = catalogStatus({ installed: true, inUseBy: ['agent', 'utility'] })
    expect(s).toMatchObject({ kind: 'inuse', label: 'in use · agent' })
    expect(s.title).toBe('In use by agent, utility')
  })
  it('puts a pending update ahead of who uses the model', () => {
    expect(catalogStatus({ installed: true, update: true, inUseBy: ['agent'] }).kind).toBe('update')
  })
  it('puts a running download ahead of everything, with a rounded percentage', () => {
    expect(catalogStatus({ installed: true, update: true, download: { pct: 41.6 } }).label).toBe('downloading 42%')
    expect(catalogStatus({ installed: false, download: { pct: null } }).label).toBe('downloading')
  })
  it('says not installed, or that it will not fit', () => {
    expect(catalogStatus({ installed: false }).label).toBe('not installed')
    expect(catalogStatus({ installed: false, wontFit: true })).toMatchObject({ kind: 'wontfit', tone: 'err' })
  })
  it('never calls an upstream model installed or downloadable', () => {
    expect(catalogStatus({ installed: true, remote: true })).toMatchObject({ kind: 'remote', label: 'upstream', tone: 'info' })
    expect(catalogStatus({ installed: false, remote: true, update: true }).kind).toBe('remote')
    const used = catalogStatus({ installed: true, remote: true, inUseBy: ['memory'] })
    expect(used.label).toBe('upstream')
    expect(used.title).toContain('In use by memory')
  })
})

describe('catalogDot', () => {
  it('follows the same facts as the chip', () => {
    expect(catalogDot({ installed: false })).toBe('empty')
    expect(catalogDot({ installed: true })).toBe('installed')
    expect(catalogDot({ installed: true, inUseBy: ['a'] })).toBe('inuse')
    expect(catalogDot({ installed: true, remote: true })).toBe('remote')
    expect(catalogDot({ installed: false, download: { pct: 3 } })).toBe('busy')
  })
})

describe('matchesStateFilter', () => {
  const inUse = { installed: true, inUseBy: ['agent'] }
  const free = { installed: true }
  const missing = { installed: false }
  const upstream = { installed: true, remote: true }
  it('shows everything when nothing is selected', () => {
    for (const s of [inUse, free, missing, upstream]) expect(matchesStateFilter([], s)).toBe(true)
  })
  it('ORs the selected states', () => {
    expect(matchesStateFilter(['inuse', 'available'], inUse)).toBe(true)
    expect(matchesStateFilter(['inuse', 'available'], missing)).toBe(true)
    expect(matchesStateFilter(['inuse', 'available'], free)).toBe(false)
  })
  it('does not count an upstream model as installed or as not installed', () => {
    expect(matchesStateFilter(['installed'], upstream)).toBe(false)
    expect(matchesStateFilter(['available'], upstream)).toBe(false)
  })
  it('matches a pending update', () => {
    expect(matchesStateFilter(['update'], { installed: true, update: true })).toBe(true)
    expect(matchesStateFilter(['update'], free)).toBe(false)
  })
})

describe('sectionKey', () => {
  it('sorts rows into in use, installed and available', () => {
    expect(sectionKey({ installed: true, inUseBy: ['a'] })).toBe('inuse')
    expect(sectionKey({ installed: true })).toBe('installed')
    expect(sectionKey({ installed: false })).toBe('available')
    expect(sectionKey({ installed: false, remote: true })).toBe('installed')
  })
})

describe('pageSections', () => {
  const sections = [
    { key: 'a', label: 'A', rows: [1, 2, 3] },
    { key: 'b', label: 'B', rows: [4, 5] },
    { key: 'c', label: 'C', rows: [] as number[] },
  ]
  it('shows everything on one page for "all" and drops empty sections', () => {
    const p = pageSections(sections, 1, 'all')
    expect(p.sections.map((s) => s.key)).toEqual(['a', 'b'])
    expect(p).toMatchObject({ page: 1, totalPages: 1, total: 5 })
  })
  it('cuts a page across a section boundary and keeps each section total', () => {
    const p = pageSections(sections, 2, 2)
    expect(p.sections).toEqual([
      { key: 'a', label: 'A', rows: [3], total: 3 },
      { key: 'b', label: 'B', rows: [4], total: 2 },
    ])
    expect(p.totalPages).toBe(3)
  })
  it('clamps a page past the end to the last page', () => {
    const p = pageSections(sections, 9, 2)
    expect(p.page).toBe(3)
    expect(p.sections).toEqual([{ key: 'b', label: 'B', rows: [5], total: 2 }])
  })
  it('gives one empty page for no rows', () => {
    expect(pageSections([], 4, 25)).toEqual({ sections: [], page: 1, totalPages: 1, total: 0 })
  })
})

describe('npuFitOf and runsOn', () => {
  const fit = {
    small: { xdna_path: 'fused' as const, reason: 'fused geometry' },
    mid: { xdna_path: 'per-op' as const, reason: 'no fused kernel' },
    huge: { xdna_path: 'per-op' as const, reason: 'no fused kernel', npu_ok: false, npu_note: 'too large' },
    odd: { xdna_path: null, reason: 'not GGUF' },
  }
  it('returns the fit only when the NPU can practically run the model', () => {
    expect(npuFitOf('small', fit)?.xdna_path).toBe('fused')
    expect(npuFitOf('mid', fit)?.xdna_path).toBe('per-op')
    expect(npuFitOf('huge', fit)).toBeNull()
    expect(npuFitOf('odd', fit)).toBeNull()
    expect(npuFitOf('absent', fit)).toBeNull()
    expect(npuFitOf('small', undefined)).toBeNull()
  })
  it('reads accelerator backends as GPU and adds the NPU from the fit', () => {
    expect(runsOn({ id: 'small', backends: ['vulkan', 'rocm', 'cpu'] }, fit)).toEqual(['gpu', 'cpu', 'npu'])
    expect(runsOn({ id: 'huge', backends: ['rocm'] }, fit)).toEqual(['gpu'])
    expect(runsOn({ id: 'x', backends: ['cpu'] })).toEqual(['cpu'])
    expect(runsOn({ id: 'x', backends: [], device: 'npu' })).toEqual(['npu'])
    expect(runsOn({ id: 'x' })).toEqual([])
  })
})

describe('slotsUsing', () => {
  it('lists each slot once, by model_id or by model', () => {
    const slots = [
      { name: 'agent', model_id: 'm1' },
      { name: 'utility', model: 'm1' },
      { name: 'agent', model: 'm1' },
      { name: 'code', model_id: 'm2' },
      { model_id: 'm1' },
    ]
    expect(slotsUsing(slots, 'm1')).toEqual(['agent', 'utility'])
    expect(slotsUsing(undefined, 'm1')).toEqual([])
  })
})
