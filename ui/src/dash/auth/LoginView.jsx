// hal0 dashboard — login view (O19).
//
// Rendered by AuthGate in place of the whole app when auth enforcement is on
// (posture is explicit-enable now, see hal0.api.auth) and the browser session
// is still anonymous. Admin-key entry only: the login endpoint is admin-key
// -only by design (the client tier is Bearer/?api_key= for programmatic
// callers, not a browser session — routes/auth.py).
//
// Security contract:
//   - The key value is NEVER displayed (masked input) and NEVER persisted
//     (no localStorage) — the browser only ever holds the HttpOnly session
//     cookie the server mints on success.
//   - Errors never echo the key back (see gateDecision.loginErrorMessage).
//
// On success the session cookie is set and we invalidate the 'auth-status'
// query; AuthGate re-reads the now-admin posture and swaps in the app. No
// reload, no redirect.

import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { loginErrorMessage } from './gateDecision.js'

export function LoginView({ status }) {
  const qc = useQueryClient()
  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const hasAdminKey = status ? status.has_admin_key !== false : true

  const login = useMutation({
    mutationFn: (k) => apiPost(ENDPOINTS.authLogin, { key: k }),
    onSuccess: async () => {
      setError(null)
      setKey('')
      // Re-read posture → AuthGate routes to the app. Refetch is awaited so
      // the app doesn't briefly re-flash the login view on the next tick.
      await qc.invalidateQueries({ queryKey: ['auth-status'] })
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
            Authentication is enabled on this hal0. Enter the admin key to continue.
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
      </form>
    </div>
  )
}
