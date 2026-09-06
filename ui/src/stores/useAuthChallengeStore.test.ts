// #1822 — the auth-challenge store: request() opens the drawer and pins the
// mutation+variables to retry, dismiss() clears it without executing
// anything, and retry() calls Mutation.execute(variables) exactly once then
// closes.

import { describe, expect, it, vi } from 'vitest'
import { useAuthChallengeStore } from './useAuthChallengeStore'

function resetStore() {
  useAuthChallengeStore.setState({ open: false, pending: null })
}

describe('useAuthChallengeStore', () => {
  it('request() opens the drawer and pins the mutation for retry', () => {
    resetStore()
    const execute = vi.fn().mockResolvedValue(undefined)
    useAuthChallengeStore.getState().request({ execute }, { id: '123' })

    const state = useAuthChallengeStore.getState()
    expect(state.open).toBe(true)
    expect(state.pending?.variables).toEqual({ id: '123' })
    expect(execute).not.toHaveBeenCalled()
  })

  it('dismiss() clears the pending challenge without executing it', () => {
    resetStore()
    const execute = vi.fn().mockResolvedValue(undefined)
    useAuthChallengeStore.getState().request({ execute }, { id: '123' })

    useAuthChallengeStore.getState().dismiss()

    const state = useAuthChallengeStore.getState()
    expect(state.open).toBe(false)
    expect(state.pending).toBeNull()
    expect(execute).not.toHaveBeenCalled()
  })

  it('retry() re-executes the pending mutation with its original variables, then closes', async () => {
    resetStore()
    const execute = vi.fn().mockResolvedValue(undefined)
    useAuthChallengeStore.getState().request({ execute }, { id: 'approval-1' })

    await useAuthChallengeStore.getState().retry()

    expect(execute).toHaveBeenCalledTimes(1)
    expect(execute).toHaveBeenCalledWith({ id: 'approval-1' })
    const state = useAuthChallengeStore.getState()
    expect(state.open).toBe(false)
    expect(state.pending).toBeNull()
  })

  it('retry() with nothing pending is a safe no-op', async () => {
    resetStore()
    await expect(useAuthChallengeStore.getState().retry()).resolves.toBeUndefined()
  })
})
