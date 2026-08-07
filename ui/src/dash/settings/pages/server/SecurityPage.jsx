// GENERAL ▸ Security (D4, post-R3 surface rework).
//
// Admin/client key management, honestly bounded to what the backend actually
// exposes today. The ONE auth surface is GET /api/auth/status (routes/auth.py):
//   { auth_required, has_admin_key, tier } — posture booleans, never a value.
//
// So this page shows STATUS ONLY. It never renders a key VALUE. Key rotation is
// real for BOTH tiers (POST /api/auth/rotate {tier: 'admin'|'client'}) and
// returns status-only fields — after a rotate that row shows the returned
// fingerprint + rotated-at (never the value); RotateKeyDialog is shared,
// parameterised by tier. GET /api/auth/exposure landed (Phase 1 wave 2), so
// the route-exposure table below is LIVE (ExposureTable.jsx /
// useAuthExposure). What /api/auth/status still can't back — client-key
// set/unset live status and login-throttle counters — is simply NOT rendered
// (settings-panel cleanup dropped the permanent "unknown" pip and the
// permanent "status unavailable" throttle panel; a row that can never change
// state is noise, not evidence). Both return when their routes land
// (client_key_configured on the status payload; GET /api/auth/throttle).
// Assumed ADMIN-gated: a browser HMAC session is admin-equivalent (spec §22 /
// KB-1).
//
// Files: this page + RotateKeyDialog.jsx (type-to-confirm → POST /api/auth/
// rotate; shows fingerprint/rotated_at, never the value) + ExposureTable.jsx
// (static class taxonomy + live per-route table from GET /api/auth/exposure).

import { useAuthStatus } from '@/api/hooks/useAuthStatus'
import { useSetRequireAuth, useLogout } from '@/api/hooks/useAuthActions'
import { loginErrorMessage } from '@/dash/auth/gateDecision.js'
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

// One key row: label, what it gates, its status pip (omitted when the
// backend can't report one — no fabricated "unknown"), and the action.
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
      <span data-testid={`${testid}-status`} style={{ justifySelf: 'start' }}>{state ? <StatusPip state={state} /> : null}</span>
      <span style={{ justifySelf: 'end' }}>{action}</span>
    </div>
  )
}

export function SecurityPage() {
  const auth = useAuthStatus()
  const setRequireAuth = useSetRequireAuth()
  const logout = useLogout()
  const [rotateOpen, setRotateOpen] = useStateS(false)
  // Which tier the open RotateKeyDialog targets — both the admin and client
  // rows share the one dialog instance (#1467: rotate is real for both).
  const [rotateTier, setRotateTier] = useStateS('admin')
  // Last rotation result per tier (status-only: { fingerprint, rotated_at }).
  // Never a key value — the endpoint never returns one.
  const [lastRotated, setLastRotated] = useStateS({ admin: null, client: null })

  const openRotate = (tier) => {
    setRotateTier(tier)
    setRotateOpen(true)
  }

  const s = auth.data
  const loading = auth.isPending
  const errored = auth.isError
  // Admin-key posture from has_admin_key; "unknown" if the probe failed.
  const adminState = errored ? 'unknown' : loading ? 'unknown' : s?.has_admin_key ? 'set' : 'unset'
  const authArmed = !!s?.auth_required
  const tier = s?.tier || 'unknown'
  const hasAdminKey = adminState === 'set'
  const isAdminSession = tier === 'admin'

  // Enabling enforcement with no admin key would lock EVERYONE out (the login
  // endpoint rejects every attempt with no key), so the control is gated on a
  // configured key — the same guard the backend enforces (400 auth.no_admin_key).
  const canEnable = hasAdminKey
  const toggleErr = setRequireAuth.isError ? loginErrorMessage(setRequireAuth.error) : null

  return (
    <div className="s-section" data-testid="security-page">
      <h2>Security &amp; Access</h2>
      <p className="desc">
        Authentication posture, API-key status, and route exposure policy. hal0&apos;s auth
        surface (<span className="mono">GET /api/auth/status</span>) never returns a key value, and
        neither does this page. Assumes an admin session (a browser HMAC session is admin-equivalent).
      </p>

      {/* ── session posture ─────────────────────────────────────────── */}
      <div className="s-panel" style={{ marginBottom: 16 }}>
        <div className="s-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '11px 14px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--fg-4)' }}>
            enforcement {authArmed ? 'armed' : 'open'} · this session:
            <span data-testid="security-tier" style={{ color: authArmed ? 'var(--ok)' : 'var(--warn)', marginLeft: 6 }}>{tier}</span>
          </span>
          <span style={{ display: 'inline-flex', gap: 10, alignItems: 'center' }}>
            {errored && <span className="err" style={{ fontSize: 11 }}>auth status probe failed</span>}
            {isAdminSession && (
              <button
                className="btn ghost sm"
                data-testid="security-logout"
                disabled={logout.isPending}
                onClick={() => logout.mutate()}
                title="End this browser session (clears the session cookie)"
              >
                {logout.isPending ? 'Logging out…' : 'Log out'}
              </button>
            )}
          </span>
        </div>
      </div>

      {/* ── enforcement toggle (real, live-applied) ─────────────────── */}
      <h3 style={{ margin: '0 0 6px', fontSize: 13 }}>Authentication</h3>
      <div className="s-panel" data-testid="security-enforcement" style={{ marginBottom: 16 }}>
        <div className="s-row" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 14, padding: '13px 14px' }}>
          <div>
            <div style={{ fontSize: 13, color: 'var(--fg)' }}>
              Require authentication
              <span
                data-testid="security-enforcement-state"
                className="chip mono"
                style={{
                  marginLeft: 8,
                  fontSize: 10,
                  color: authArmed ? 'var(--ok)' : 'var(--fg-4)',
                  borderColor: authArmed ? 'var(--ok-line)' : 'var(--line)',
                  background: authArmed ? 'var(--ok-soft)' : 'var(--bg-2)',
                }}
              >
                {authArmed ? '● on' : '○ off'}
              </span>
            </div>
            <div className="mono" style={{ fontSize: 10.5, color: 'var(--fg-5)', marginTop: 3, lineHeight: 1.55, maxWidth: 460 }}>
              {authArmed
                ? 'Every route requires the admin key (or a logged-in session). Applies live — no restart.'
                : 'Auth is off — hal0 runs trusted-LAN open. Enable to require a login; you’ll be asked for the admin key on the next load.'}
            </div>
          </div>
          {authArmed ? (
            <button
              className="btn ghost sm"
              data-testid="security-enforcement-disable"
              disabled={setRequireAuth.isPending}
              onClick={() => setRequireAuth.mutate(false)}
            >
              {setRequireAuth.isPending ? 'Saving…' : 'Disable'}
            </button>
          ) : (
            <button
              className="btn sm"
              data-testid="security-enforcement-enable"
              disabled={!canEnable || setRequireAuth.isPending}
              title={!canEnable ? 'Configure an admin key first — enabling auth with no key locks everyone out.' : undefined}
              onClick={() => setRequireAuth.mutate(true)}
            >
              {setRequireAuth.isPending ? 'Saving…' : 'Enable'}
            </button>
          )}
        </div>
        {!authArmed && !canEnable && (
          <div
            data-testid="security-enforcement-blocked"
            className="mono"
            style={{ fontSize: 10.5, color: 'var(--warn)', padding: '0 14px 12px', lineHeight: 1.55 }}
          >
            ○ No admin key configured — set <span style={{ color: 'var(--fg-3)' }}>HAL0_ADMIN_KEY</span> before enabling, or you&apos;ll lock yourself out.
          </div>
        )}
        {toggleErr && (
          <div
            data-testid="security-enforcement-error"
            className="mono err"
            style={{ fontSize: 10.5, padding: '0 14px 12px', lineHeight: 1.55 }}
          >
            {toggleErr.text}
          </div>
        )}
      </div>

      {/* ── keys ────────────────────────────────────────────────────── */}
      <h3 style={{ margin: '0 0 6px', fontSize: 13 }}>Keys</h3>
      <div className="s-panel" style={{ marginBottom: 16 }}>
        <KeyRow
          testid="security-key-admin"
          name="admin key"
          gates={
            lastRotated.admin
              ? `fingerprint ${lastRotated.admin.fingerprint} · rotated ${lastRotated.admin.rotated_at}`
              : 'gates every ADMIN route'
          }
          state={adminState}
          action={
            <button
              className="btn ghost sm"
              data-testid="security-rotate-admin"
              onClick={() => openRotate('admin')}
            >
              Rotate…
            </button>
          }
        />
        <KeyRow
          testid="security-key-client"
          name="client key"
          gates={
            lastRotated.client
              ? `fingerprint ${lastRotated.client.fingerprint} · rotated ${lastRotated.client.rotated_at}`
              : 'gates CLIENT routes (/v1/*) · live status not reported by the API'
          }
          action={
            <button
              className="btn ghost sm"
              data-testid="security-rotate-client"
              onClick={() => openRotate('client')}
            >
              Rotate…
            </button>
          }
        />
      </div>

      {/* ── route exposure (static taxonomy + live per-route table) ─── */}
      <ExposureTable />

      <RotateKeyDialog
        open={rotateOpen}
        tier={rotateTier}
        onRotated={(r) =>
          setLastRotated((prev) => ({
            ...prev,
            [rotateTier]: { fingerprint: r.fingerprint, rotated_at: r.rotated_at },
          }))
        }
        onClose={() => setRotateOpen(false)}
      />
    </div>
  )
}
