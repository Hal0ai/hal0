// hal0 v3 dashboard — TanStack Query client (Phase B1).
//
// One QueryClient for the whole SPA. Defaults are tuned for a dashboard
// that polls long-lived endpoints (slots, hardware) and short-lived ones:
//
//   - `staleTime: 30s` — most resources are happy with up-to-30s freshness;
//     polled hooks override per-query with `refetchInterval`.
//   - `refetchOnWindowFocus: false` — operators leave the dashboard open
//     all day; refocus pings are noise.
//   - one retry — surfaces 404 / 5xx quickly so the per-hook fallback
//     (mock data or empty list) can render instead of spinning forever.
//     Except a 401 `auth.required`, which is never retried: no retry can
//     succeed without a session, and an operator browsing read-only has a
//     dozen ADMIN reads polling — retrying each doubled the refused traffic.
//
// `mutationCache.onError` (#1822): a LAN-bound box with auth off still
// requires an admin session for ADMIN-class mutations from off-box callers
// (hal0.api.auth's posture-coupled gate). That 401 (`Hal0Error` with
// `code: 'auth.required'`) can come from ANY mutation anywhere in the
// dashboard, so it's caught here once — globally — instead of threading a
// reauth callback through every `useMutation` call site. It hands the
// failed `mutation` (a `Mutation.execute(variables)`-capable instance) to
// `useAuthChallengeStore`, which `AuthChallengeDrawer` renders; a successful
// login re-runs `execute(variables)` so the original caller's own
// onSuccess/cache-invalidation still fires normally.
//
// `queryCache.onError`: the same gate is per route CLASS, not per method, so
// an ADMIN-classified GET (settings, memory, approvals, the activity stream)
// is refused exactly like a mutation — and a session cookie lapses after 8h
// mid-session. A refused read does NOT open the drawer (a page fires a dozen
// reads at once; that is the front door's job, not a per-request prompt).
// Instead it marks `auth-status` stale so AuthGate re-reads posture and puts
// the login screen back. The guard in `shouldRecheckAuthStatus` keeps that to
// the moment the session is LOST: once status already says sign-in is needed
// (the operator is browsing read-only), polling reads keep 401ing by design
// and must not turn into a status-refetch storm. It is also rate-limited:
// not every 401 `auth.required` is about this browser's session (a slot's own
// 401 passed through a polled read; an API older than the status field), and
// in those cases status would keep reading "signed in" and re-trigger on
// every poll.

import { MutationCache, QueryCache, QueryClient } from '@tanstack/react-query'
import { Hal0Error } from '@/api/client'
import { useAuthChallengeStore } from '@/stores/useAuthChallengeStore'

// Mirrors useAuthStatus()'s query key (api/hooks/useAuthStatus.ts).
const AUTH_STATUS_KEY = ['auth-status'] as const

function isPostureReauthChallenge(error: unknown): boolean {
  return error instanceof Hal0Error && error.status === 401 && error.code === 'auth.required'
}

/** Floor between two refused-read rechecks of /api/auth/status. */
export const AUTH_RECHECK_MIN_INTERVAL_MS = 10_000

let lastAuthRecheckAt = Number.NEGATIVE_INFINITY

/**
 * Should a refused request trigger a re-read of /api/auth/status?
 *
 * Yes when the server says "authentication required" but the posture we have
 * cached doesn't already say so — i.e. the session we thought we had is gone —
 * and we haven't just rechecked (`sinceLastRecheckMs`).
 */
export function shouldRecheckAuthStatus(
  error: unknown,
  cachedStatus: { admin_sign_in_required?: boolean } | undefined,
  sinceLastRecheckMs: number = Number.POSITIVE_INFINITY,
): boolean {
  if (!isPostureReauthChallenge(error)) return false
  if (cachedStatus?.admin_sign_in_required) return false
  return sinceLastRecheckMs >= AUTH_RECHECK_MIN_INTERVAL_MS
}

/** Default query retry policy: once, but never for a missing-session refusal. */
export function retryUnlessAuthRequired(failureCount: number, error: unknown): boolean {
  if (isPostureReauthChallenge(error)) return false
  return failureCount < 1
}

export const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({
    onError: (error, query) => {
      if (query.queryKey[0] === AUTH_STATUS_KEY[0]) return
      const now = Date.now()
      const cached = queryClient.getQueryData<{ admin_sign_in_required?: boolean }>(AUTH_STATUS_KEY)
      if (shouldRecheckAuthStatus(error, cached, now - lastAuthRecheckAt)) {
        lastAuthRecheckAt = now
        void queryClient.invalidateQueries({ queryKey: AUTH_STATUS_KEY })
      }
    },
  }),
  mutationCache: new MutationCache({
    onError: (error, variables, _context, mutation) => {
      if (isPostureReauthChallenge(error)) {
        useAuthChallengeStore.getState().request(mutation, variables)
      }
    },
  }),
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      gcTime: 5 * 60_000,
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
      retry: retryUnlessAuthRequired,
    },
    mutations: {
      retry: 0,
    },
  },
})
