// "Remember me" stores the operator's PREFERENCE for the tick-box — never the
// key, never anything the server trusts. The session itself is the HttpOnly
// cookie; this only saves re-ticking the box on the next login.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { REMEMBER_PREF_KEY, readRememberPreference, writeRememberPreference } from '../rememberPreference.js'

function fakeStorage(initial: Record<string, string> = {}) {
  const data = new Map(Object.entries(initial))
  return {
    data,
    getItem: (k: string) => (data.has(k) ? (data.get(k) as string) : null),
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  }
}

afterEach(() => vi.unstubAllGlobals())

describe('remember-me preference', () => {
  it('defaults to off — a shared browser must not default to a month-long session', () => {
    vi.stubGlobal('localStorage', fakeStorage())
    expect(readRememberPreference()).toBe(false)
  })

  it('round-trips the choice', () => {
    const store = fakeStorage()
    vi.stubGlobal('localStorage', store)

    writeRememberPreference(true)
    expect(readRememberPreference()).toBe(true)

    writeRememberPreference(false)
    expect(readRememberPreference()).toBe(false)
    expect(store.data.has(REMEMBER_PREF_KEY)).toBe(false)
  })

  it('degrades to off when storage is unavailable or throws', () => {
    expect(readRememberPreference()).toBe(false) // no localStorage at all
    expect(() => writeRememberPreference(true)).not.toThrow()

    vi.stubGlobal('localStorage', {
      getItem: () => {
        throw new Error('blocked')
      },
      setItem: () => {
        throw new Error('blocked')
      },
      removeItem: () => undefined,
    })
    expect(readRememberPreference()).toBe(false)
    expect(() => writeRememberPreference(true)).not.toThrow()
  })
})
