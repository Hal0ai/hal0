// hal0 dashboard — login view (O19).
//
// Rendered by AuthGate in place of the whole app when this browser session has
// to sign in: either auth enforcement is on (explicit-enable, see
// hal0.api.auth) or the posture-coupled gate (#1822) refuses this caller's
// ADMIN-class requests while enforcement reads off. The copy says which, and
// the posture case — where OPEN/CLIENT reads still work — offers
// "View read-only" (`onViewReadOnly`, supplied by AuthGate only then).
// Admin-key entry only: the login endpoint is admin-key-only by design (the
// client tier is Bearer/?api_key= for programmatic callers, not a browser
// session — routes/auth.py).
//
// Security contract:
//   - The key value is NEVER displayed (masked input) and NEVER persisted
//     (no localStorage) — the browser only ever holds the HttpOnly session
//     cookie the server mints on success.
//   - Errors never echo the key back (see gateDecision.loginErrorMessage).
//
// On success the session cookie is set and we invalidate every query;
// AuthGate re-reads the now-admin posture and swaps in the app, and any read
// that was refused before the login (a lapsed session) refetches instead of
// rendering its cached 401. No reload, no redirect.

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { loginErrorMessage } from './gateDecision.js'

export function LoginView({ status, onViewReadOnly }) {
  const qc = useQueryClient()
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const hasAdminKey = status ? status.has_admin_key !== false : true
  // Enforcement on vs. the posture-coupled gate: different reasons, so
  // different words. Telling an operator "authentication is enabled" while
  // their Security page shows it switched off is how this gate got reported
  // as a bug.
  const enforced = status ? status.auth_required !== false : true

  const login = useMutation({
    mutationFn: (k) => apiPost(ENDPOINTS.authLogin, { key: k }),
    onSuccess: async () => {
      setError(null)
      setKey('')
      // Re-read posture → AuthGate routes to the app. Refetch is awaited so
      // the app doesn't briefly re-flash the login view on the next tick.
      // Everything is invalidated, not just 'auth-status': while this view is
      // up only the status query is mounted, so that is all the await costs,
      // and reads that 401'd before the login refetch when the app remounts.
      await qc.invalidateQueries()
    },
    onError: (err) => setError(loginErrorMessage(err)),
  })

  const submit = (e) => {
    e.preventDefault()
    if (!key || login.isPending) return
    setError(null)
    login.mutate(key)
  }

  return (
    <div
      data-testid="login-view"
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--bg, #0b0b0d)',
        padding: 24,
      }}
    >
      <form
        onSubmit={submit}
        style={{
          width: 'min(400px, 100%)',
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          background: 'var(--bg-1, #141417)',
          border: '1px solid var(--line, rgba(255,255,255,0.08))',
          borderRadius: 12,
          padding: '28px 26px',
        }}
      >
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div className="mono" style={{ fontSize: 11, letterSpacing: '0.08em', color: 'var(--fg-4, #888)', textTransform: 'uppercase' }}>
            hal0
          </div>
          <h1 style={{ margin: 0, fontSize: 19, color: 'var(--fg, #eee)' }}>Log in</h1>
          <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--fg-3, #aaa)' }}>
            {enforced
              ? 'Authentication is enabled on this hal0. Enter the admin key to continue.'
              : 'This hal0 is reachable from your network, so managing it needs the admin key. Enter it to continue.'}
          </p>
        </div>

        <label className="mono" htmlFor="login-key" style={{ fontSize: 11, color: 'var(--fg-4, #888)' }}>
          Admin key
        </label>
        <input
          id="login-key"
          data-testid="login-key-input"
          type="password"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          autoComplete="current-password"
          autoFocus
          spellCheck={false}
          disabled={login.isPending}
          placeholder="admin key"
          className="mono"
          style={{
            padding: '10px 12px',
            fontSize: 13,
            background: 'var(--bg-2, #1c1c20)',
            border: `1px solid ${error ? 'var(--err-line, #a33)' : 'var(--line, rgba(255,255,255,0.1))'}`,
            borderRadius: 7,
            color: 'var(--fg, #eee)',
          }}
        />

        {error && (
          <div
            data-testid="login-error"
            role="alert"
            className="mono"
            style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--err, #e66)' }}
          >
            {error.text}
          </div>
        )}

        {!hasAdminKey && !error && (
          <div
            data-testid="login-no-key-note"
            className="mono"
            style={{ fontSize: 11, lineHeight: 1.5, color: 'var(--warn, #d9a441)' }}
          >
            No admin key is configured on the server yet — set HAL0_ADMIN_KEY, then log in.
          </div>
        )}

        <button
          type="submit"
          data-testid="login-submit"
          disabled={!key || login.isPending}
          className="btn"
          style={{
            marginTop: 2,
            padding: '10px 12px',
            fontSize: 13,
            fontWeight: 600,
            background: 'var(--accent, #6ea8fe)',
            color: 'var(--accent-fg, #06121f)',
            border: 'none',
            borderRadius: 7,
            cursor: !key || login.isPending ? 'default' : 'pointer',
            opacity: !key || login.isPending ? 0.6 : 1,
          }}
        >
          {login.isPending ? 'Logging in…' : 'Log in'}
        </button>

        <div className="mono" style={{ fontSize: 10.5, lineHeight: 1.55, color: 'var(--fg-5, #777)', wordBreak: 'normal', overflowWrap: 'break-word' }}>
          The key lives on the box: HAL0_ADMIN_KEY in /etc/hal0/api.env.
        </div>

        {onViewReadOnly && (
          <div
            style={{
              display: 'flex',
              flexDirection: 'column',
              gap: 4,
              paddingTop: 12,
              borderTop: '1px solid var(--line, rgba(255,255,255,0.08))',
            }}
          >
            <button
              type="button"
              data-testid="login-view-read-only"
              onClick={onViewReadOnly}
              disabled={login.isPending}
              className="btn ghost sm"
              style={{ alignSelf: 'flex-start' }}
            >
              View read-only
            </button>
            <div className="mono" style={{ fontSize: 10.5, lineHeight: 1.55, color: 'var(--fg-5, #777)', wordBreak: 'normal', overflowWrap: 'break-word' }}>
              Slots, models and hardware stay visible. Settings, memory, logs and any change need
              the key — sign in any time from the top bar.
            </div>
          </div>
        )}
      </form>
    </div>
  )
}
