// hal0 dashboard — flag-migration resolution host (D5, demo/preview wiring).
//
// The production MigrationBanner is dormant until the report endpoint exists,
// so the resolution view would otherwise be unreachable to preview. This host
// bridges the Tweaks-panel banner registry to the real view: the demo banner
// (BANNER_CATALOG id "migration-unresolved", primitives.jsx) fires a
// `hal0:migration-resolve` window event on Resolve, and this host opens the
// SAME MigrationResolveView off DEMO_MIGRATION_REPORT.
//
// Mounted once in main.jsx's view-banners strip (alongside UpdateBanner et al.).
// Renders nothing until the event fires — no cost on the steady-state dashboard.

import { DEMO_MIGRATION_REPORT } from '@/api/hooks/useMigrationReport'
import { MigrationResolveView } from './MigrationResolveView.jsx'

const { useState: useStateH, useEffect: useEffectH } = React

export const MIGRATION_RESOLVE_EVENT = 'hal0:migration-resolve'

export function MigrationResolveHost() {
  const [open, setOpen] = useStateH(false)

  useEffectH(() => {
    const onOpen = () => setOpen(true)
    window.addEventListener(MIGRATION_RESOLVE_EVENT, onOpen)
    return () => window.removeEventListener(MIGRATION_RESOLVE_EVENT, onOpen)
  }, [])

  return (
    <MigrationResolveView open={open} report={DEMO_MIGRATION_REPORT} onClose={() => setOpen(false)} />
  )
}
