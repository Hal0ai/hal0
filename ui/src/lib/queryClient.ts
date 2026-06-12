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

import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
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
