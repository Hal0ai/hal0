// hal0 v3 dashboard — auth mutations (O19).
//
// Companion to useAuthStatus (read-only posture). These are the WRITE side the
// Security page + login gate drive:
//   - useSetRequireAuth — PUT /api/auth/require: persist the enforcement
//     toggle. Applies live server-side; we invalidate 'auth-status' so the
//     whole shell (AuthGate) re-reads posture immediately.
//   - useLogout — POST /api/auth/logout: clear the HttpOnly session cookie.
//     After it, the next 'auth-status' read is anonymous → AuthGate shows the
//     login view (when this browser has to sign in), and everything fetched
//     under the session is dropped from the query cache (dropSessionData).

import { useMutation, useQueryClient, type QueryClient } from '@tanstack/react-query'
import { apiPost, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface RequireAuthResponse {
  require_auth: boolean
  applies_live: boolean
}

export type KeyTier = 'admin' | 'client'

/** Status-only rotation result — NEVER carries the key value. */
export interface RotateKeyResponse {
  tier: KeyTier
  rotated_at: string
  key_len: number
  fingerprint: string
  applies_live: boolean
  restart_required: boolean
  session_preserved: boolean
  note: string
}

/** PUT /api/auth/require — flip the persisted [security].require_auth toggle. */
export function useSetRequireAuth() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (require_auth: boolean) =>
      apiPut<RequireAuthResponse>(ENDPOINTS.authRequire, { require_auth }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth-status'] }),
  })
}

/**
 * POST /api/auth/rotate — mint + persist a fresh box key for `tier`.
 *
 * The response is status-only (fingerprint + rotated_at + notices); it NEVER
 * carries the key value. We invalidate 'auth-status' so the posture (and the
 * admin-key set/unset pip) re-reads after a rotation.
 */
export function useRotateKey() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (tier: KeyTier) => apiPost<RotateKeyResponse>(ENDPOINTS.authRotate, { tier }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth-status'] }),
  })
}

/**
 * End-of-session cache hygiene: re-read posture, then discard every payload
 * that was fetched under the session.
 *
 * The cookie being gone is not enough. A query keeps its last good data when
 * a refetch fails, so the now-401ing ADMIN reads would go on showing the
 * admin's Settings / Memory / Logs to whoever uses this browser next — and on
 * a posture-gated box the login view's "View read-only" walks straight back
 * into those pages. Posture is awaited first so the shell has already routed
 * (usually unmounting the app) before the reset; the reset itself is not
 * awaited — any still-mounted read just refetches, and logout must not wait
 * on that.
 */
export async function dropSessionData(qc: QueryClient): Promise<void> {
  await qc.invalidateQueries({ queryKey: ['auth-status'] })
  void qc.resetQueries({ predicate: (q) => q.queryKey[0] !== 'auth-status' })
}

/** POST /api/auth/logout — end the browser session (clears the cookie). */
export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost(ENDPOINTS.authLogout),
    onSuccess: () => dropSessionData(qc),
  })
}
