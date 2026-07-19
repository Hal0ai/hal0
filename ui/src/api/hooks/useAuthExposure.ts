// hal0 v3 dashboard — route-exposure hook (D4 Security page, UI-API-2).
//
// Backs the ExposureTable's live per-route classification. GET
// /api/auth/exposure (routes/auth.py, ADMIN-gated) serialises the real
// deny-by-default table (security/exposure.py's RULES + OPEN_ALLOWLIST) — no
// hardcoded copy to rot when a rule changes:
//
//   { classes: string[],                          // the four AuthClass values
//     rules: { label, auth_class, methods, pattern, kind }[],
//     open_allowlist: { method, path }[] }
//
// NOTE (CONTRACTS lane): this route has no ENDPOINTS entry yet — endpoints.ts
// only carries authStatus/authLogin/authLogout/authRequire/authRotate. Until
// an `authExposure` const lands there this hook uses the literal path
// directly; swap it for `ENDPOINTS.authExposure` once added.

import { useQuery } from '@tanstack/react-query'
import { apiGet } from '../client'

const AUTH_EXPOSURE_PATH = '/api/auth/exposure'

export interface ExposureRule {
  label: string
  auth_class: string
  methods: string[] | null
  pattern: string | null
  kind: string
}

export interface ExposureAllowlistEntry {
  method: string
  path: string
}

export interface ExposureStatus {
  classes: string[]
  rules: ExposureRule[]
  open_allowlist: ExposureAllowlistEntry[]
}

/** GET /api/auth/exposure — live deny-by-default route classification table. */
export function useAuthExposure() {
  return useQuery({
    queryKey: ['auth-exposure'],
    queryFn: () => apiGet<ExposureStatus>(AUTH_EXPOSURE_PATH),
    retry: false,
  })
}
