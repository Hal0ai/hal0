// hal0 dashboard — window-globals bridge for FirstRun auto-routing (D6).
//
// dash/main.jsx is a no-ES-imports prototype file, so it can't import the
// install-state hook directly. This republishes it under
// `window.__hal0UseInstallState` (mirroring memory-hook-bridge.ts). Must be
// imported in main.tsx BEFORE dash/main.jsx evaluates.

import { useInstallState } from '@/api/hooks/useInstallState'

Object.assign(window as unknown as Record<string, unknown>, {
  __hal0UseInstallState: () => {
    const q = useInstallState()
    // `pending` guards the redirect against the transient loading window —
    // useInstallState returns no data while the first /api/install/state
    // query is in flight, and we must not bounce to FirstRun until it settles.
    return { firstRun: q.data?.first_run === true, pending: q.isLoading && !q.data }
  },
})
