// SERVER ▸ Security (D4, post-R3 surface rework).
//
// Admin/client key management, honestly bounded to what the backend actually
// exposes today. The ONE auth surface is GET /api/auth/status (routes/auth.py):
//   { auth_required, has_admin_key, tier } — posture booleans, never a value.
//
// So this page shows STATUS ONLY. It never renders a key, a fingerprint, or a
// throttle counter — because the endpoint returns none of those. Everything the
// R3 canvas draws that /api/auth/status can't back (client-key set/unset, the
// admin-key fingerprint + last-rotated, live login-throttle counts, key
// rotation, the per-route exposure table) is surfaced as disabled-with-reason —
// each an explicit API-lane request — rather than fabricated. Assumed
// ADMIN-gated: a browser HMAC session is admin-equivalent (spec §22 / KB-1).
//
// Files: this page + RotateKeyDialog.jsx (type-to-confirm + one-time reveal,
// gated on the missing rotation route) + ExposureTable.jsx (static class
// taxonomy + stub-with-reason for the live table). Un-disabled in SettingsNav.

import { useAuthStatus } from '@/api/hooks/useAuthStatus'
import { RotateKeyDialog } from './RotateKeyDialog.jsx'
import { ExposureTable } from './ExposureTable.jsx'

const { useState: useStateS } = React

// A status pip: set/armed (ok) · unset/off (muted) · unknown (probe failed).
function StatusPip({ state }) {
  if (state === 'set') {
    return <span className="chip mono" data-status="set" style={{ color: 'var(--ok)', borderColor: 'var(--ok-line)', background: 'var(--ok-soft)', fontSize: 10.5 }}>● set</span>
  }
  if (state === 'unset') {
    return <span className="chip mono" data-status="unset" style={{ color: 'var(--fg-4)', borderColor: 'var(--line)', background: 'var(--bg-2)', fontSize: 10.5 }}>○ unset</span>
  }
  return <span className="chip mono" data-status="unknown" style={{ color: 'var(--fg-4)', borderColor: 'var(--line)', background: 'var(--bg-2)', fontSize: 10.5 }}>probe unavailable</span>
}

// One key row: label, what it gates, its status pip, and the trailing action.
function KeyRow({ testid, name, gates, state, action }) {
  return (
    <div
      className="s-row"
      data-testid={testid}
      style={{ display: 'grid', gridTemplateColumns: '1fr 120px 130px', gap: 14, alignItems: 'center', padding: '13px 14px' }}
    >
      <div>
        <div style={{ fontSize: 13, color: 'var(--fg)' }}>{name}</div>
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', marginTop: 2 }}>{gates}</div>
      </div>
      <span data-testid={`${testid}-status`} style={{ justifySelf: 'start' }}><StatusPip state={state} /></span>
      <span style={{ justifySelf: 'end' }}>{action}</span>
    </div>
  )
}

const CLIENT_KEY_REASON =
  'Client-key set/unset is not reported by /api/auth/status — it returns admin posture only. (API-lane request: add client_key_configured to the status payload)'
const THROTTLE_REASON =
  'Live login-throttle counters are not exposed — the per-IP limiter runs server-side (routes/auth.py) but publishes no status. (API-lane request: GET /api/auth/throttle)'
const SET_CLIENT_REASON =
  'Setting/clearing the client key has no route yet — keys are configured via HAL0_*_KEY env today. (API-lane request: POST /api/auth/keys/client)'

export function SecurityPage() {
  const auth = useAuthStatus()
  const [rotateOpen, setRotateOpen] = useStateS(false)

  const s = auth.data
  const loading = auth.isPending
  const errored = auth.isError
  // Admin-key posture from has_admin_key; "unknown" if the probe failed.
  const adminState = errored ? 'unknown' : loading ? 'unknown' : s?.has_admin_key ? 'set' : 'unset'
  const authArmed = !!s?.auth_required
  const tier = s?.tier || 'unknown'

  return (
    <div className="s-section" data-testid="security-page">
      <h2>Security &amp; Access</h2>
      <p className="desc">
        API-key status, exposure policy, and login throttle. Status only — hal0&apos;s auth surface
        (<span className="mono">GET /api/auth/status</span>) never returns a key value, and neither does
        this page. Assumes an admin session (a browser HMAC session is admin-equivalent).
      </p>

      {/* ── session posture ─────────────────────────────────────────── */}
      <div className="s-panel" style={{ marginBottom: 16 }}>
        <div className="s-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '11px 14px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
            enforcement {authArmed ? 'armed' : 'open'} · this session:
            <span data-testid="security-tier" style={{ color: authArmed ? 'var(--ok)' : 'var(--warn)', marginLeft: 6 }}>{tier}</span>
          </span>
          {errored && <span className="err" style={{ fontSize: 11 }}>auth status probe failed</span>}
        </div>
      </div>

      {/* ── keys ────────────────────────────────────────────────────── */}
      <h3 style={{ margin: '0 0 6px', fontSize: 13 }}>Keys</h3>
      <div className="s-panel" style={{ marginBottom: 16 }}>
        <KeyRow
          testid="security-key-admin"
          name="admin key"
          gates="gates every ADMIN route"
          state={adminState}
          action={
            <button
              className="btn ghost sm"
              data-testid="security-rotate-admin"
              onClick={() => setRotateOpen(true)}
            >
              Rotate…
            </button>
          }
        />
        <KeyRow
          testid="security-key-client"
          name="client key"
          gates="gates CLIENT routes (/v1/*)"
          state="unknown"
          action={
            <button
              className="btn ghost sm"
              data-testid="security-set-client"
              disabled
              title={SET_CLIENT_REASON}
            >
              Set key…
            </button>
          }
        />
        <div
          data-testid="security-client-reason"
          className="mono"
          style={{ fontSize: 10.5, color: 'var(--fg-5)', padding: '2px 14px 12px', lineHeight: 1.55 }}
        >
          ○ {CLIENT_KEY_REASON}
        </div>
      </div>

      {/* ── login throttle (stub-with-reason) ───────────────────────── */}
      <h3 style={{ margin: '0 0 6px', fontSize: 13 }}>Login throttle</h3>
      <div className="s-panel" style={{ marginBottom: 16 }}>
        <div className="s-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '13px 14px' }}>
          <div>
            <div style={{ fontSize: 13, color: 'var(--fg-3)' }}>Per-IP login rate limit</div>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', marginTop: 2 }}>
              active server-side · counters not published
            </div>
          </div>
          <span className="chip mono" data-testid="security-throttle-status" style={{ fontSize: 10.5, color: 'var(--fg-4)', borderColor: 'var(--line)', background: 'var(--bg-2)' }} title={THROTTLE_REASON}>
            status unavailable
          </span>
        </div>
      </div>

      {/* ── route exposure (static taxonomy + stub-with-reason) ─────── */}
      <ExposureTable />

      <RotateKeyDialog open={rotateOpen} tier="admin" onClose={() => setRotateOpen(false)} />
    </div>
  )
}
