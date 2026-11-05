// @vitest-environment happy-dom
//
// useActivityStream buffers SSE frames for FRAME_FLUSH_MS before landing them. When the stream
// closes inside that window (the dock closes its streams when the tab hides, so follow flips
// false while the consumer stays mounted), the buffered frames must reach the live ring through
// setRecords, not only the module cache: sinceRef has already moved past them, so nothing would
// replay them until a remount.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useActivityStream } from '../useActivity'

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
  emit(frame: unknown) {
    this.onmessage?.({ data: JSON.stringify(frame) })
  }
}

const frame = (id: number) => ({
  epoch: 'e1',
  record: {
    id,
    ts: `2026-10-26T15:00:${String(id).padStart(2, '0')}Z`,
    message: `event ${id}`,
  },
})

describe('useActivityStream cleanup flush', () => {
  let seen: number[][]
  let host: HTMLElement
  let root: ReturnType<typeof createRoot>
  // Each test uses its own filter so the module-level ring cache never leaks between tests.
  let filterSeq = 0
  let search: string

  function Probe({ follow = true }: { follow?: boolean }) {
    const { records } = useActivityStream({ follow, search })
    seen.push(records.map((r) => r.id as number))
    return null
  }

  beforeEach(() => {
    vi.useFakeTimers()
    FakeEventSource.instances = []
    seen = []
    search = `cleanup-flush-${filterSeq++}`
    ;(globalThis as unknown as { EventSource: unknown }).EventSource = FakeEventSource
    host = document.createElement('div')
    root = createRoot(host)
  })
  afterEach(() => {
    act(() => root.unmount())
    vi.useRealTimers()
  })

  it('frames still inside the coalesce window reach the live ring when follow flips off', async () => {
    act(() => root.render(<Probe />))
    await act(async () => {
      vi.advanceTimersByTime(250) // the connect debounce
    })
    const es = FakeEventSource.instances[0]
    expect(es).toBeTruthy()
    await act(async () => {
      es.emit(frame(1))
      es.emit(frame(2))
    })
    expect(seen.at(-1)).toEqual([]) // still buffered, window not elapsed

    act(() => root.render(<Probe follow={false} />))

    expect(es.closed).toBe(true)
    expect(seen.at(-1)).toEqual([2, 1]) // newest-first
  })

  it('does not double-add when the flush timer already landed the frames', async () => {
    act(() => root.render(<Probe />))
    await act(async () => {
      vi.advanceTimersByTime(250)
    })
    const es = FakeEventSource.instances[0]
    await act(async () => {
      es.emit(frame(1))
      vi.advanceTimersByTime(60) // window elapsed: flushed normally
    })
    expect(seen.at(-1)).toEqual([1])

    act(() => root.render(<Probe follow={false} />))
    expect(seen.at(-1)).toEqual([1])
  })

  it('a real unmount still leaves the buffered frames in the cache for the next mount', async () => {
    act(() => root.render(<Probe />))
    await act(async () => {
      vi.advanceTimersByTime(250)
    })
    await act(async () => {
      FakeEventSource.instances[0].emit(frame(7))
    })
    act(() => root.unmount())

    root = createRoot(host)
    seen = []
    act(() => root.render(<Probe />))
    expect(seen[0]).toEqual([7]) // rehydrated from the cache on first render
  })
})
