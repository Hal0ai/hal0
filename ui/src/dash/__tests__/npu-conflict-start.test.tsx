// @vitest-environment happy-dom
// useNpuConflictStart: one dialog at a time; a second run() while it is open does not drop the
// first call's onSettled.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { describe, expect, it, vi } from 'vitest'
;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

const { posts, gate } = vi.hoisted(() => ({
  posts: [] as unknown[],
  gate: { wait: null as Promise<void> | null },
}))
vi.mock('@/api/hooks/useSlots', async () => {
  const { Hal0Error } = await import('@/api/client')
  const mut = {
    isPending: false,
    mutateAsync: async (v: unknown) => {
      posts.push(v)
      if (typeof v === 'string')
        throw new Hal0Error('held', { code: 'slot.npu_conflict', status: 409, details: { slot: v, holders: ['flm'], holder_engine: 'flm', unloads: ['flm'] } })
      if (gate.wait) await gate.wait
      return {}
    },
  }
  return { useSlotLoad: () => mut, useSlotRestart: () => mut }
})
vi.mock('../primitives.jsx', () => ({
  ConfirmDialog: (p: { title: string; onCancel: () => void; onConfirm: () => void }) => (
    <div data-testid="dlg"><b>{p.title}</b><button data-testid="cancel" onClick={p.onCancel} /><button data-testid="ok" onClick={p.onConfirm} /></div>
  ),
}))
const { useNpuConflictStart } = await import('../npu/useNpuConflictStart.jsx')

describe('useNpuConflictStart', () => {
  it('a second run while the dialog is open keeps the first dialog and its onSettled', async () => {
    let h!: ReturnType<typeof useNpuConflictStart>
    const P = () => ((h = useNpuConflictStart()), h.dialog)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    act(() => root.render(<P />))
    const first = vi.fn()
    const second = vi.fn()
    let r1: unknown, r2: unknown
    await act(async () => { r1 = await h.run('a', 'load', { onSettled: first }) })
    await act(async () => { r2 = await h.run('b', 'load', { onSettled: second }) })
    expect([r1, r2]).toEqual(['prompted', 'prompted'])
    expect(posts).toEqual(['a'])
    expect(host.textContent).toContain('Unload flm to start a?')
    await act(async () => { (host.querySelector('[data-testid="cancel"]') as HTMLElement).click() })
    expect(first).toHaveBeenCalledTimes(1)
    expect(second).not.toHaveBeenCalled()
    expect(host.querySelector('[data-testid="dlg"]')).toBeNull()
  })

  it('closing while the confirmed retry is in flight is a no-op, so onSettled fires once', async () => {
    let h!: ReturnType<typeof useNpuConflictStart>
    const P = () => ((h = useNpuConflictStart()), h.dialog)
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    act(() => root.render(<P />))
    const settled = vi.fn()
    let release!: () => void
    gate.wait = new Promise<void>((r) => { release = r })
    await act(async () => { await h.run('c', 'load', { onSettled: settled }) })
    await act(async () => { (host.querySelector('[data-testid="ok"]') as HTMLElement).click() })
    // Escape / Cancel while the retry is still posting: ignored.
    await act(async () => { (host.querySelector('[data-testid="cancel"]') as HTMLElement).click() })
    expect(settled).not.toHaveBeenCalled()
    expect(host.querySelector('[data-testid="dlg"]')).not.toBeNull()
    await act(async () => { release(); await gate.wait })
    gate.wait = null
    expect(settled).toHaveBeenCalledTimes(1)
    expect(host.querySelector('[data-testid="dlg"]')).toBeNull()
    expect(posts.at(-1)).toEqual({ name: 'c', unloadNpuHolder: true })
  })
})
