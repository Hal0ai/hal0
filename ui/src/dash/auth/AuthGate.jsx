// hal0 dashboard — app-shell auth gate (O19).
//
// Wraps <App/> (main.jsx, inside the QueryClientProvider). It reads
// GET /api/auth/status and renders the login view INSTEAD of the app whenever
// this session has to sign in — no flash of a locked dashboard, no redirect
// loop. That covers two postures:
//
//   - enforcement on (`auth_required`) and the session is anonymous;
//   - the posture-coupled gate (#1822): enforcement reads off, but the box is
//     LAN-bound with an admin key, so this caller's ADMIN-class requests —
//     reads included — are refused (`admin_sign_in_required`). Most of the
//     dashboard is ADMIN-class, so without a front door it rendered half-empty
//     with no way to sign in until some mutation happened to 401.
//
// Boxes that gate nothing for this caller (keyless, loopback) see zero change:
// the gate falls straight through to the app.
//
// The posture gate leaves OPEN/CLIENT reads working, so its login view offers
// "View read-only". That choice is remembered for the tab (sessionStorage) and
// dropped again on sign-in, so a later session expiry returns to the login
// view rather than silently to read-only.
//
// All routing lives in the pure, unit-tested authGateView() (gateDecision.js);
// this component only binds it to the live query + renders.

import { useEffect, useState } from 'react'
import { useAuthStatus } from '@/api/hooks/useAuthStatus'
import { authGateView, canViewReadOnly } from './gateDecision.js'
import { LoginView } from './LoginView.jsx'

export const READ_ONLY_STORAGE_KEY = 'hal0.auth.viewReadOnly'

// sessionStorage can be absent (server render) or throw (blocked storage);
// either way the gate must still render, just without remembering the choice.
function readStoredReadOnly() {
  try {
    return sessionStorage.getItem(READ_ONLY_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

function storeReadOnly(on) {
  try {
    if (on) sessionStorage.setItem(READ_ONLY_STORAGE_KEY, '1')
    else sessionStorage.removeItem(READ_ONLY_STORAGE_KEY)
  } catch {
    // not remembered across reloads — the in-memory state below still holds
  }
}

// Neutral splash shown only during the very first status probe, so the app
// never flashes behind a login that's about to appear (and vice versa).
function AuthSplash() {
  return (
    <div
      data-testid="auth-splash"
      aria-hidden="true"
      style={{ minHeight: '100vh', background: 'var(--bg, #0b0b0d)' }}
    />
  )
}

export function AuthGate({ children }) {
  const q = useAuthStatus()
  const [readOnly, setReadOnly] = useState(readStoredReadOnly)

  const signedIn = q.data?.tier === 'admin'
  useEffect(() => {
    if (signedIn && readOnly) {
      setReadOnly(false)
      storeReadOnly(false)
    }
  }, [signedIn, readOnly])

  const view = authGateView(q, { readOnly })
  if (view === 'loading') return <AuthSplash />
  if (view === 'login') {
    const viewReadOnly = canViewReadOnly(q.data)
      ? () => {
          setReadOnly(true)
          storeReadOnly(true)
        }
      : undefined
    return <LoginView status={q.data} onViewReadOnly={viewReadOnly} />
  }
  return children
}
