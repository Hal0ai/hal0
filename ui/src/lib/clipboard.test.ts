// @vitest-environment happy-dom
//
// copyTextToClipboard (#2214) — the async-safe copy idiom. The old house
// pattern try/caught around navigator.clipboard.writeText, which only catches
// a synchronous throw; a rejected write (permission denial, insecure origin)
// still toasted success. The helper must answer the REAL outcome: true only
// after the promise resolves (or the legacy fallback succeeds), false on
// rejection/sync-throw when the fallback can't copy either.
import { afterEach, describe, expect, it, vi } from 'vitest'

import { copyTextToClipboard } from './clipboard'

const setClipboard = (value: unknown) => {
  Object.defineProperty(globalThis.navigator, 'clipboard', {
    value,
    configurable: true,
  })
}

const setExecCommand = (fn: ((cmd: string) => boolean) | undefined) => {
  Object.defineProperty(globalThis.document, 'execCommand', {
    value: fn,
    configurable: true,
  })
}

afterEach(() => {
  setClipboard(undefined)
  setExecCommand(undefined)
})

describe('copyTextToClipboard', () => {
  it('resolves true when the async write resolves', async () => {
    const copied: string[] = []
    setClipboard({ writeText: (t: string) => { copied.push(t); return Promise.resolve() } })
    await expect(copyTextToClipboard('hello')).resolves.toBe(true)
    expect(copied).toEqual(['hello'])
  })

  it('an ASYNC rejection is a failure, not a false success (the #2214 bug)', async () => {
    setClipboard({ writeText: () => Promise.reject(new Error('NotAllowedError')) })
    setExecCommand(() => false)
    await expect(copyTextToClipboard('x')).resolves.toBe(false)
  })

  it('a synchronous throw is a failure too', async () => {
    setClipboard({ writeText: () => { throw new Error('NotAllowedError') } })
    setExecCommand(() => false)
    await expect(copyTextToClipboard('x')).resolves.toBe(false)
  })

  it('no async clipboard at all (insecure LAN origin) → legacy execCommand fallback', async () => {
    setClipboard(undefined)
    const exec = vi.fn(() => true)
    setExecCommand(exec)
    await expect(copyTextToClipboard('fallback me')).resolves.toBe(true)
    expect(exec).toHaveBeenCalledWith('copy')
  })

  it('rejection + working legacy fallback still copies (resolves true)', async () => {
    setClipboard({ writeText: () => Promise.reject(new Error('denied')) })
    setExecCommand(() => true)
    await expect(copyTextToClipboard('x')).resolves.toBe(true)
  })

  it('nothing available anywhere resolves false (never throws)', async () => {
    setClipboard(undefined)
    setExecCommand(undefined)
    await expect(copyTextToClipboard('x')).resolves.toBe(false)
  })
})
