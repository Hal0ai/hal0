// @vitest-environment happy-dom
//
// useLogsStream buffers SSE frames and lands them through one setRing per flush window, so a
// backfill that arrives as several bursts is one render (the dock's per-stream readiness counts
// anything that lands on a later render as news).
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useLogsStream } from '../useLogs'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

class FakeEventSource {
  static instances: FakeEventSource[] = []
  onmessage: ((e: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onopen: (() => void) | null = null
  closed = false
  constructor(public url: string) {
    FakeEventSource.instances.push(this)
  }
  close() {
    this.closed = true
  }
  emit(entry: unknown) {
    this.onmessage?.({ data: JSON.stringify(entry) })
  }
}

const line = (i: number) => ({
  id: i,
  ts: `2026-10-26T15:00:${String(i).padStart(2, '0')}Z`,
  source: 'hal0',
  level: 'info',
  msg: `line ${i}`,
})

describe('useLogsStream frame coalescing', () => {
  let renders: number[]
  let host: HTMLElement
  let root: ReturnType<typeof createRoot>

  function Probe({ follow = true }: { follow?: boolean }) {
    const { ring } = useLogsStream({ follow })
    renders.push(ring.length)
    return null
  }

  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.instances = []
    renders = []
    ;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource
    host = document.createElement('div')
    root = createRoot(host)
  })
  afterEach(() => {
    act(() => root.unmount())
    vi.useRealTimers()
  })

  it('a backfill delivered as two bursts inside the flush window is one render', async () => {
    act(() => root.render(<Probe />))
    await act(async () => {
      vi.advanceTimersByTime(250) // the connect debounce
    })
    const es = FakeEventSource.instances[0]
    expect(es).toBeTruthy()
    const before = renders.length
    await act(async () => {
      for (let i = 1; i <= 30; i++) es.emit(line(i))
      vi.advanceTimersByTime(20)
      for (let i = 31; i <= 50; i++) es.emit(line(i))
    })
    expect(renders.length).toBe(before) // nothing landed yet
    await act(async () => {
      vi.advanceTimersByTime(60)
    })
    expect(renders.length).toBe(before + 1)
    expect(renders.at(-1)).toBe(50)
  })

  it('closing the stream flushes what is buffered and keeps the ring', async () => {
    act(() => root.render(<Probe />))
    await act(async () => {
      vi.advanceTimersByTime(250)
    })
    const es = FakeEventSource.instances[0]
    await act(async () => {
      es.emit(line(1))
      es.emit(line(2))
    })
    act(() => root.render(<Probe follow={false} />))
    expect(es.closed).toBe(true)
    expect(renders.at(-1)).toBe(2)
  })
})
