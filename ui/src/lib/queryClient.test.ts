// Refused READS, not just refused mutations.
//
// The posture-coupled ADMIN gate (hal0.api.auth) is per route class, not per
// method, so an ADMIN-classified GET 401s exactly like a mutation — and a
// session cookie expires after 8h mid-session. The global QueryCache.onError
// must notice that and re-read /api/auth/status so the shell (AuthGate) can
// put the login screen back, instead of leaving pages silently empty.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Hal0Error } from '@/api/client'
import { QueryObserver } from '@tanstack/react-query'
import {
  AUTH_RECHECK_MIN_INTERVAL_MS,
  queryClient,
  retryUnlessAuthRequired,
  shouldRecheckAuthStatus,
} from './queryClient'

const authRequired = () =>
  new Hal0Error('authentication required', { code: 'auth.required', status: 401 })

const SIGNED_IN = { auth_required: false, admin_sign_in_required: false, tier: 'admin' }
const KNOWN_SIGNED_OUT = { auth_required: false, admin_sign_in_required: true, tier: 'anon' }

// The recheck is rate-limited on wall-clock time, so every test starts a full
// hour after the previous one — well clear of the limiter's window.
let clock = Date.parse('2026-01-01T00:00:00Z')
beforeEach(() => {
  clock += 60 * 60_000
  vi.useFakeTimers({ toFake: ['Date'] })
  vi.setSystemTime(clock)
})
afterEach(() => {
  queryClient.clear()
  vi.useRealTimers()
})

describe('shouldRecheckAuthStatus', () => {
  it('rechecks when a read is refused but the cached posture says we are signed in', () => {
    expect(shouldRecheckAuthStatus(authRequired(), SIGNED_IN)).toBe(true)
  })

  it('rechecks when there is no cached posture at all', () => {
    expect(shouldRecheckAuthStatus(authRequired(), undefined)).toBe(true)
  })

  it('does not recheck when the posture already says sign-in is needed (read-only browsing)', () => {
    expect(shouldRecheckAuthStatus(authRequired(), KNOWN_SIGNED_OUT)).toBe(false)
  })

  it('does not recheck again inside the rate-limit window', () => {
    expect(shouldRecheckAuthStatus(authRequired(), SIGNED_IN, AUTH_RECHECK_MIN_INTERVAL_MS - 1)).toBe(false)
    expect(shouldRecheckAuthStatus(authRequired(), SIGNED_IN, AUTH_RECHECK_MIN_INTERVAL_MS)).toBe(true)
  })

  it('ignores errors that are not the auth challenge', () => {
    expect(shouldRecheckAuthStatus(new Hal0Error('nope', { code: 'slot.not_found', status: 404 }), SIGNED_IN)).toBe(false)
    expect(shouldRecheckAuthStatus(new Hal0Error('bad key', { code: 'auth.invalid_key', status: 401 }), SIGNED_IN)).toBe(false)
    expect(shouldRecheckAuthStatus(new Error('Failed to fetch'), SIGNED_IN)).toBe(false)
  })
})

describe('queryClient — a refused read', () => {
  const refusedRead = () =>
    queryClient
      .fetchQuery({ queryKey: ['settings'], queryFn: () => Promise.reject(authRequired()), retry: false })
      .catch(() => undefined)

  it('marks the cached auth status stale so the shell re-reads it (session expired)', async () => {
    queryClient.setQueryData(['auth-status'], SIGNED_IN)

    await refusedRead()

    expect(queryClient.getQueryState(['auth-status'])?.isInvalidated).toBe(true)
  })

  it('leaves the auth status alone while browsing read-only, so polling reads cannot storm it', async () => {
    queryClient.setQueryData(['auth-status'], KNOWN_SIGNED_OUT)

    await refusedRead()

    expect(queryClient.getQueryState(['auth-status'])?.isInvalidated).toBe(false)
  })

  it('rechecks at most once per window when status keeps saying we are signed in', async () => {
    // A 401 `auth.required` that is NOT about this session (an API older than
    // the status field, or a slot's own 401 passed through a polled read)
    // leaves status reading "signed in" forever — the recheck must not then
    // fire on every poll.
    queryClient.setQueryData(['auth-status'], SIGNED_IN)
    await refusedRead()
    expect(queryClient.getQueryState(['auth-status'])?.isInvalidated).toBe(true)

    queryClient.setQueryData(['auth-status'], SIGNED_IN) // the recheck came back unchanged
    await refusedRead()
    expect(queryClient.getQueryState(['auth-status'])?.isInvalidated).toBe(false)

    vi.setSystemTime(clock + AUTH_RECHECK_MIN_INTERVAL_MS)
    await refusedRead()
    expect(queryClient.getQueryState(['auth-status'])?.isInvalidated).toBe(true)
  })
})

describe('retry policy', () => {
  it('never retries a request refused for lack of a session — it cannot succeed', () => {
    expect(retryUnlessAuthRequired(0, authRequired())).toBe(false)
  })

  it('still retries any other failure once', () => {
    const boom = new Hal0Error('upstream down', { code: 'system.unknown', status: 502 })
    expect(retryUnlessAuthRequired(0, boom)).toBe(true)
    expect(retryUnlessAuthRequired(1, boom)).toBe(false)
    expect(retryUnlessAuthRequired(0, new Error('Failed to fetch'))).toBe(true)
  })

  it('is the default for mounted reads: a refused read hits the server once per poll, not twice', async () => {
    queryClient.setQueryData(['auth-status'], KNOWN_SIGNED_OUT)
    let calls = 0
    const observer = new QueryObserver(queryClient, {
      queryKey: ['memory', 'banks'],
      queryFn: () => {
        calls += 1
        return Promise.reject(authRequired())
      },
    })
    const settled = new Promise<void>((resolve) => {
      const unsubscribe = observer.subscribe((result) => {
        if (result.isError) {
          unsubscribe()
          resolve()
        }
      })
    })

    await settled

    expect(calls).toBe(1)
  })
})
