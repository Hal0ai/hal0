// hal0 v3 dashboard — auth-status hook (D4 Security page, post-R3 surface rework).
//
// Backs Settings → Security. GET /api/auth/status is the ONLY auth-posture
// surface that exists today (routes/auth.py) and it is deliberately narrow:
//
//   { auth_required: bool,   // is the enforcement gate armed at all?
//     has_admin_key: bool,   // is HAL0_ADMIN_KEY configured? (set/unset)
//     tier: "open"|"client"|"admin" }  // THIS caller's resolved identity
//
// It NEVER returns a key value, and it does NOT report: the admin-key
// fingerprint / last-rotated timestamp, the CLIENT-key set/unset state, or
// login-throttle counters. The Security page surfaces those as
// disabled-with-reason (API-lane requests, see SecurityPage.jsx) rather than
// inventing data the backend doesn't send. There is likewise no key-rotation
// route — the rotate flow is built but gated disabled-with-reason.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'
import { ENDPOINTS } from '../endpoints'

export type AuthTier = 'open' | 'client' | 'admin'

export interface AuthStatus {
  auth_required: boolean
  has_admin_key: boolean
  tier: AuthTier
}

const POLL_MS = 30_000

/** GET /api/auth/status — posture only, never secrets. */
export function useAuthStatus() {
  return useQuery({
    queryKey: ['auth-status'],
    queryFn: () => apiGet<AuthStatus>(ENDPOINTS.authStatus),
    refetchInterval: POLL_MS,
    retry: false,
  })
}
