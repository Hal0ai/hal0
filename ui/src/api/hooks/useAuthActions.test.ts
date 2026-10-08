// Logging out must take the admin session's DATA with it, not just the cookie.
//
// On a posture-gated box the login view offers "View read-only". Without this,
// whoever clicks it after an admin logged out would be shown that admin's
// cached Settings / Memory / Logs payloads: TanStack keeps a query's last good
// data when its refetch fails, so the 401 alone never clears it.

import { QueryClient } from '@tanstack/react-query'
import { describe, expect, it } from 'vitest'
import { dropSessionData } from './useAuthActions'

describe('dropSessionData', () => {
  it('discards every cached payload fetched under the session', async () => {
    const qc = new QueryClient()
    qc.setQueryData(['settings'], { secret_ish: 'value' })
    qc.setQueryData(['memory', 'banks'], [{ id: 'b1' }])
    qc.setQueryData(['auth-status'], { tier: 'admin' })

    await dropSessionData(qc)

    expect(qc.getQueryData(['settings'])).toBeUndefined()
    expect(qc.getQueryData(['memory', 'banks'])).toBeUndefined()
  })

  it('re-reads the auth status rather than discarding it, so the shell can route', async () => {
    const qc = new QueryClient()
    qc.setQueryData(['auth-status'], { tier: 'admin' })

    await dropSessionData(qc)

    expect(qc.getQueryData(['auth-status'])).toEqual({ tier: 'admin' })
    expect(qc.getQueryState(['auth-status'])?.isInvalidated).toBe(true)
  })
})
