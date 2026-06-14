// hal0 dashboard — window-globals bridge for DashGrid / DashboardOverhaulView (W3).
//
// dash-grid.jsx uses the no-ES-imports window-globals pattern. This bridge
// republishes the TanStack-Query hooks it needs under `window.__hal0Use*`
// so the .jsx file can call them at runtime without ES imports.
//
// Must be imported in main.tsx BEFORE dash/dash-grid.jsx evaluates.

import { useSlots } from '@/api/hooks/useSlots'
import { useStatsHardware } from '@/api/hooks/useStatsHardware'
import { useDashLayout, useSaveDashLayout } from '@/api/hooks/useDashLayout'

Object.assign(window as unknown as Record<string, unknown>, {
  __hal0UseDashLayout: useDashLayout,
  __hal0UseSaveDashLayout: useSaveDashLayout,
  __hal0UseSlots: useSlots,
  __hal0UseStatsHardware: useStatsHardware,
})
