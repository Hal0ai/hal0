// hal0 v3 dashboard — TanStack Query client (Phase B1).
//
// One QueryClient for the whole SPA. Defaults are tuned for a dashboard
// that polls long-lived endpoints (slots, hardware) and short-lived ones:
//
//   - `staleTime: 30s` — most resources are happy with up-to-30s freshness;
//     polled hooks override per-query with `refetchInterval`.
//   - `refetchOnWindowFocus: false` — operators leave the dashboard open
//     all day; refocus pings are noise.
//   - `retry: 1` — surfaces 404 / 5xx quickly so the per-hook fallback
//     (mock data or empty list) can render instead of spinning forever.
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

import { MutationCache, QueryClient } from '@tanstack/react-query'
import { Hal0Error } from '@/api/client'
import { useAuthChallengeStore } from '@/stores/useAuthChallengeStore'

function isPostureReauthChallenge(error: unknown): boolean {
  return error instanceof Hal0Error && error.status === 401 && error.code === 'auth.required'
}

export const queryClient = new QueryClient({
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
      retry: 1,
    },
    mutations: {
      retry: 0,
    },
  },
})
