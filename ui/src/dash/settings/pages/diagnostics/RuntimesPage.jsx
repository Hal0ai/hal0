// hal0 dashboard — Settings → Runtimes (D3, post-R3 surface rework).
//
// One page for the runner/image axis — EVIDENCE UI, not config UI. A row per
// runner (RUNNER_IMAGES): family, image ref + digest (read-only), on-disk state
// vs the shipped registry, and the reverse index of which slots resolve to it
// (plus the models transitively bound through those slots). Nothing here edits
// an image string — images ship with hal0 releases and the updater reconciles
// drift; to change what a slot runs on, set its BINARY in the slot editor
// (spec-hw-slot-ownership §8: hardware/placement is owned by the slot, not the
// model). Lives in the SYSTEM & UPDATES (Diagnostics) nav group.
//
// Backend reality (hardware.py GET /api/system-info): per-runner state is
// installed | installable | unavailable (local podman-images presence only).
// Digest-drift ("stale vs shipped") and per-runner pull progress are NOT
// surfaced by the endpoint, and there is no per-runner pull route — so the pull
// affordance is shown disabled-with-reason (an API-lane request), never faked.

import { useRuntimes } from '@/api/hooks/useRuntimes'

// Runner hue by backend — the slot/model device hues, reused here.
function hueFor(backend, deviceClass) {
  const b = String(backend || '').toLowerCase()
  const d = String(deviceClass || '').toLowerCase()
  if (b.includes('rocm')) return 'var(--dev-rocm)'
  if (b.includes('vulkan')) return 'var(--dev-vulkan)'
  if (b.includes('flm') || d.includes('npu')) return 'var(--dev-npu)'
  return 'var(--dev-cpu)'
}

function StatusChip({ state }) {
  if (state === 'installed') {
    return <span className="chip" data-status="installed" style={{ color: 'var(--ok)', borderColor: 'var(--ok-line)', background: 'var(--ok-soft)' }}>● installed</span>
  }
  if (state === 'installable') {
    return <span className="chip" data-status="installable" style={{ color: 'var(--warn)', borderColor: 'var(--warn-line)', background: 'var(--warn-soft)' }}>○ not pulled</span>
  }
  return <span className="chip" data-status="unavailable" style={{ color: 'var(--fg-4)', borderColor: 'var(--line)', background: 'var(--bg-2)' }}>probe unavailable</span>
}

const PULL_DISABLED_REASON =
  'Per-runner image pull is not wired yet — images arrive with hal0 releases; run `hal0 update` to reconcile every runner. (API-lane request)'

function RuntimeRow({ r }) {
  const hue = hueFor(r.backend, r.deviceClass)
  const models = r.models.length
  const slots = r.slots.length
  const degraded = r.state === 'unavailable'
  return (
    <div
      className="s-row"
      data-testid={`runtime-row-${r.key}`}
      style={{ display: 'grid', gridTemplateColumns: '150px 1fr 150px 150px', gap: 14, alignItems: 'center' }}
    >
      {/* runner */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: hue }} />
        <span className="mono" style={{ fontSize: 12.5, color: 'var(--fg)' }}>{r.key}</span>
      </div>
      {/* image · digest (read-only) */}
      <div className="mono" data-testid={`runtime-image-${r.key}`} style={{ fontSize: 11, color: 'var(--fg-3)', lineHeight: 1.5, wordBreak: 'break-all' }}>
        <span style={{ color: 'var(--fg-2)' }}>{r.imageRepo}</span>
        {r.tag && <span style={{ color: 'var(--fg-5)' }}>:{r.tag}</span>}
        <br />
        <span style={{ color: 'var(--fg-4)' }}>{r.digest ? r.digest : `${r.family} · digest pinned by release`}</span>
      </div>
      {/* resolves to */}
      <div className="mono" data-testid={`runtime-resolves-${r.key}`} style={{ fontSize: 11, color: 'var(--fg-3)' }}>
        {models === 0 && slots === 0 ? (
          <span style={{ color: 'var(--fg-5)' }}>not in use</span>
        ) : (
          <>
            <a href="#models" style={{ color: 'var(--fg-2)' }}>{models} model{models !== 1 ? 's' : ''}</a>
            {' · '}
            <a href="#slots" style={{ color: 'var(--fg-2)' }}>{slots} slot{slots !== 1 ? 's' : ''}</a>
          </>
        )}
      </div>
      {/* status + pull */}
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 6 }}>
        <span data-testid={`runtime-status-${r.key}`}><StatusChip state={r.state} /></span>
        {r.state === 'installable' && (
          <button
            className="btn ghost sm"
            data-testid={`runtime-prepull-${r.key}`}
            disabled
            title={PULL_DISABLED_REASON}
          >Pre-pull</button>
        )}
        {degraded && (
          <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-5)' }} title={PULL_DISABLED_REASON}>pull unavailable</span>
        )}
      </div>
    </div>
  )
}

export function RuntimesPage() {
  const rt = useRuntimes()

  return (
    <div className="s-section" data-testid="runtimes-page">
      <h2>Runtimes</h2>
      <p className="desc">
        Container images pinned per runner. Images ship with hal0 releases and are reconciled by the
        updater — this page shows what each runner resolves to, never edits an image. To change what a
        slot runs on, set its BINARY in the slot editor.
      </p>

      {rt.probeUnavailable && (
        <div
          data-testid="runtimes-degraded"
          className="banner banner-warn"
          role="status"
          style={{ border: '1px solid var(--warn-line)', background: 'var(--warn-soft)', borderRadius: 8, padding: '11px 14px', margin: '4px 0 12px', fontSize: 12.5, lineHeight: 1.5, color: 'var(--fg-2)' }}
        >
          Installed state unknown — the <span className="mono">system-info</span> probe is unavailable (no
          podman on this host). Showing the shipped registry; pull actions are disabled until the probe returns.
        </div>
      )}

      <div className="s-panel">
        <div className="s-row" style={{ borderBottom: '1px solid var(--line)', display: 'grid', gridTemplateColumns: '150px 1fr 150px 150px', gap: 14 }}>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)' }}>runner</span>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)' }}>image · digest</span>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)' }}>resolves to</span>
          <span className="mono" style={{ fontSize: 9.5, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--fg-4)', textAlign: 'right' }}>status</span>
        </div>

        {rt.isLoading && (
          <div className="s-row"><span className="mono" style={{ fontSize: 12, color: 'var(--fg-4)' }}>Probing runtimes…</span></div>
        )}
        {rt.isError && (
          <div className="s-row"><span className="err">{rt.error?.message || 'Failed to read /api/system-info'}</span></div>
        )}
        {!rt.isLoading && !rt.isError && rt.rows.length === 0 && (
          <div className="s-row"><span className="mono" style={{ fontSize: 12, color: 'var(--fg-4)' }}>No runners reported.</span></div>
        )}
        {rt.rows.map((r) => <RuntimeRow key={r.key} r={r} />)}
      </div>

      <div className="mono" style={{ marginTop: 12, fontSize: 10.5, color: 'var(--fg-5)' }}>
        Image &amp; digest are pinned by the release. <span style={{ color: 'var(--fg-4)' }}>hal0 update --channel stable</span> reconciles all runners in one pass.
      </div>
    </div>
  )
}
