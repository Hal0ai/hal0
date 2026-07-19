// hal0 v3 dashboard — auth mutations (O19).
//
// Companion to useAuthStatus (read-only posture). These are the WRITE side the
// Security page + login gate drive:
//   - useSetRequireAuth — PUT /api/auth/require: persist the enforcement
//     toggle. Applies live server-side; we invalidate 'auth-status' so the
//     whole shell (AuthGate) re-reads posture immediately.
//   - useLogout — POST /api/auth/logout: clear the HttpOnly session cookie.
//     After it, the next 'auth-status' read is anonymous → AuthGate shows the
//     login view (when enforcement is on).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiPost, apiPut } from '../client'
import { ENDPOINTS } from '../endpoints'

export interface RequireAuthResponse {
  require_auth: boolean
  applies_live: boolean
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

/** POST /api/auth/logout — end the browser session (clears the cookie). */
export function useLogout() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => apiPost(ENDPOINTS.authLogout),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['auth-status'] }),
  })
}
