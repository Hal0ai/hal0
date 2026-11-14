// @vitest-environment happy-dom
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const profiles = [
  { name: 'flm', runner: 'flm', seed: true, ctx_size: 16384, flags: '' },
  { name: 'xdna', runner: 'xdna', seed: true, ctx_size: 8192, vision: 'off', flags: '' },
]
const refetch = vi.fn(async () => ({}))
vi.mock('@/api/hooks/useProfiles', () => ({ useProfiles: () => ({ data: profiles, isLoading: false }) }))
vi.mock('@/api/hooks/useFlmCatalog', () => ({ useFlmCatalog: () => ({ data: { models: [] }, refetch, isFetching: false }) }))
vi.mock('../profiles.jsx', () => ({ ProfileDrawer: ({ mode, source, existing }: { mode: string; source: { name: string }; existing: string[] }) => <div data-testid="drawer" data-existing={JSON.stringify(existing)}>{mode}:{source.name}</div> }))
vi.mock('../npu/GgmlSlotDrawer.jsx', () => ({
  GgmlSlotDrawer: (p: { open: boolean; mode: string; slot?: { name: string } }) =>
    p.open ? <div data-testid="ggml-drawer" data-mode={p.mode} data-slot={p.slot?.name} /> : null,
}))
const copy = vi.fn(async () => true)
vi.mock('@/lib/clipboard', () => ({ copyOrSelect: (...a: unknown[]) => (copy as (...a: unknown[]) => unknown)(...a) }))

const { NpuEnginesPanel } = await import('../npu/NpuEnginesPanel')

let host: HTMLDivElement
let root: Root
const Panel = NpuEnginesPanel as unknown as React.FC<Record<string, unknown>>
const render = (props: Record<string, unknown>) => act(() => root.render(<Panel slots={[{ name: 'gg1', profile: 'xdna', binary: 'xdna', device_class: 'npu' }, { name: 'flm1', profile: 'flm', type: 'llm', device_class: 'npu' }]} {...props} />))
const q = (id: string) => host.querySelector(`[data-testid="${id}"]`) as HTMLElement | null

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
})

describe('NpuEnginesPanel', () => {
  it('null facts render unknown / —, never ok', () => {
    render({ occ: { npu_host: null, npu_engines: null }, hw: {} })
    expect(q('ready-pmode')?.dataset.state).toBe('unknown')
    expect(q('engine-flm-image')?.textContent).toBe('—')
    expect(q('engine-xdna-pulled')?.textContent).toBe('unknown')
  })
  it('pmode default shows the command with a Copy button', () => {
    render({ occ: { npu_host: null }, hw: { npu_pmode: 'default' } })
    expect(q('ready-pmode')?.textContent).toContain('sudo xrt-smi configure --pmode turbo')
    act(() => q('ready-pmode-copy')!.click())
    expect(copy).toHaveBeenCalledWith('sudo xrt-smi configure --pmode turbo', expect.anything())
  })
  it('engines show image facts and the locked chip', () => {
    render({ occ: { npu_engines: { flm: { image: 'flm:1', version: '0.9' }, xdna: { image: 'x:2', tag: 'v2', pulled: true } } }, hw: {} })
    expect(q('engine-flm-version')?.textContent).toBe('0.9')
    expect(q('engine-xdna-pulled')?.textContent).toBe('yes')
    expect(q('engine-xdna-locked')?.textContent).toBe('GGML_XDNA_PGEMM_GLU_A=0')
  })
  it('profiles list used-by as slot edit links; Clone opens the drawer', () => {
    render({ occ: {}, hw: {} })
    const a = q('npu-profile-xdna-usedby')!.querySelector('a')!
    expect(a.getAttribute('href')).toBe('#slots/gg1')
    act(() => q('npu-profile-clone-xdna')!.click())
    expect(q('drawer')?.textContent).toBe('clone:xdna')
    expect(JSON.parse(q('drawer')!.dataset.existing!)).toEqual(['flm', 'xdna'])
  })
  it('refresh refetches the FLM catalogue', () => {
    render({ occ: {}, hw: {} })
    act(() => q('engine-flm-refresh')!.click())
    expect(refetch).toHaveBeenCalled()
  })
  it('an xdna slot in used-by opens the GGML drawer in edit mode, in the page', () => {
    window.location.hash = '#slots/npu/engines'
    render({ occ: {}, hw: {} })
    act(() => q('npu-used-gg1')!.click())
    expect(q('ggml-drawer')?.dataset).toMatchObject({ mode: 'edit', slot: 'gg1' })
    expect(window.location.hash).toBe('#slots/npu/engines')
  })
  it('an FLM slot in used-by leaves the tab, but not while FLM changes are staged', () => {
    render({ occ: {}, hw: {}, flmDirty: true })
    const blocked = q('npu-used-flm1')!
    expect(blocked.tagName).toBe('SPAN')
    expect(blocked.getAttribute('aria-disabled')).toBe('true')
    expect(blocked.title).toBe('Apply or discard the staged FLM changes first')
    act(() => blocked.click())
    expect(window.location.hash).not.toBe('#slots/flm1')
    render({ occ: {}, hw: {}, flmDirty: false })
    expect(q('npu-used-flm1')!.tagName).toBe('A')
    act(() => q('npu-used-flm1')!.click())
    expect(window.location.hash).toBe('#slots/flm1')
  })
})
