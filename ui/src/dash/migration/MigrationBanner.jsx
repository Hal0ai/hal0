// hal0 dashboard — flag-migration banner (D5, production surface).
//
// The persistent dashboard banner that sits under the topbar when the migrator
// has refused one or more models. It carries the same HAL0 id as the doctor
// diagnosis, keeps you working (snooze is session-local; the box runs fine —
// refused models keep their old behavior), and its Resolve action opens the
// resolution view on the LIVE report.
//
// Driven by the typed stub (useMigrationReport). No endpoint exists yet, so the
// stub returns an empty report and this renders nothing in production today —
// the same self-rendering pattern as UpdateBanner / GpuImageModeBanner. The
// Tweaks-panel demo drives the identical view via MigrationResolveHost instead.

import { useMigrationReport } from '@/api/hooks/useMigrationReport'
import { MigrationResolveView } from './MigrationResolveView.jsx'

const { useState: useStateB } = React

export function MigrationBanner() {
  const { report, count, hasWork } = useMigrationReport()
  const [snoozed, setSnoozed] = useStateB(false)
  const [resolveOpen, setResolveOpen] = useStateB(false)

  if (!hasWork || snoozed) return null

  return (
    <>
      <div
        className="banner banner-warn"
        role="status"
        data-testid="migration-banner"
        style={{ border: '1px solid var(--warn-line)', background: 'var(--warn-soft)', borderRadius: 8, padding: '11px 14px', display: 'flex', alignItems: 'center', gap: 14 }}
      >
        <div style={{ flex: 1 }}>
          <div className="mono" style={{ fontSize: 10, letterSpacing: '.05em', color: 'var(--warn)' }}>
            ⚠ Migration · needs resolution <span style={{ color: 'var(--fg-4)' }}>{report.id}</span>
          </div>
          <div style={{ fontSize: 12.5, color: 'var(--fg-2)', marginTop: 3, lineHeight: 1.5 }}>
            {count} model{count !== 1 ? 's' : ''} need flag-migration resolution — slots shared them with
            different launch overrides. They keep their old behavior until you resolve.
          </div>
        </div>
        <span style={{ display: 'inline-flex', gap: 8 }}>
          <button className="btn sm" data-testid="migration-banner-resolve" onClick={() => setResolveOpen(true)}>Resolve</button>
          <button className="btn ghost sm" data-testid="migration-banner-snooze" onClick={() => setSnoozed(true)}>Snooze</button>
        </span>
      </div>

      <MigrationResolveView open={resolveOpen} report={report} onClose={() => setResolveOpen(false)} />
    </>
  )
}
