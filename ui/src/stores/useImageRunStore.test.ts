// The own-run store: the Generate panel's run of this browser (running flag + its cancel), held
// where the status dock can read it from outside the Image Gen pane.

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useImageRunStore } from './useImageRunStore'

describe('useImageRunStore', () => {
  beforeEach(() => {
    useImageRunStore.setState({ run: null })
  })

  it('starts with no run', () => {
    expect(useImageRunStore.getState().run).toBeNull()
  })

  it('holds the running flag and the cancel, and clears to null', () => {
    const cancel = vi.fn()
    useImageRunStore.getState().setRun({ running: true, cancel })
    expect(useImageRunStore.getState().run?.running).toBe(true)
    useImageRunStore.getState().run?.cancel()
    expect(cancel).toHaveBeenCalledTimes(1)
    useImageRunStore.getState().setRun(null)
    expect(useImageRunStore.getState().run).toBeNull()
  })

  it('keeps setRun the same reference across updates', () => {
    const before = useImageRunStore.getState().setRun
    before({ running: true, cancel: vi.fn() })
    expect(useImageRunStore.getState().setRun).toBe(before)
  })
})
