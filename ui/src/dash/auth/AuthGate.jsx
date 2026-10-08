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
// The login is asked for on EVERY page while signed out. The posture gate
// leaves OPEN/CLIENT reads working, so its login view offers "View read-only",
// but that dismisses it for the current page only (pageOfHash: the top-level
// section of the hash route). Navigating to another section brings the login
// back; a reload of the same page does not (the dismissed page is kept in
// sessionStorage). The choice is dropped on sign-in, so a later session expiry
// returns to the login view rather than silently to read-only.
//
// Whenever the login view takes over, whatever the previous session left
// behind is cleared, so nothing of it survives into the next one or into
// read-only browsing:
//   - every cached query payload except the posture itself (a query keeps its
//     last good data when its refetch 401s, so a lapsed session's Settings /
//     Memory / Logs would otherwise still render under "View read-only");
//   - a pending admin-key challenge (a mutation refused as the session lapsed
//     opens the drawer just before the shell swaps to this view; left alone it
//     would greet the operator with a second key prompt after they sign in
//     here, and replay the action long after its context is gone).
//
// All routing lives in the pure, unit-tested authGateView() (gateDecision.js);
// this component only binds it to the live query + renders.

import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { resetSessionQueries } from '@/api/hooks/useAuthActions'
import { useAuthStatus } from '@/api/hooks/useAuthStatus'
import { useAuthChallengeStore } from '@/stores/useAuthChallengeStore'
import { authGateView, canViewReadOnly, pageOfHash } from './gateDecision.js'
import { LoginView } from './LoginView.jsx'

export const READ_ONLY_STORAGE_KEY = 'hal0.auth.viewReadOnlyPage'

function currentPage() {
  return pageOfHash(typeof window === 'undefined' ? '' : window.location.hash)
}

// sessionStorage can be absent (server render) or throw (blocked storage);
// either way the gate must still render, just without remembering the choice.
function readStoredReadOnlyPage() {
  try {
    return sessionStorage.getItem(READ_ONLY_STORAGE_KEY) || null
  } catch {
    return null
  }
}

function storeReadOnlyPage(page) {
  try {
    if (page) sessionStorage.setItem(READ_ONLY_STORAGE_KEY, page)
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
  // The page the operator chose to view read-only (null: none), and the page
  // currently shown. Read-only holds only while the two match.
  const [readOnlyPage, setReadOnlyPage] = useState(readStoredReadOnlyPage)
  const [page, setPage] = useState(currentPage)
  useEffect(() => {
    const onHash = () => setPage(currentPage())
    window.addEventListener('hashchange', onHash)
    // The app may have rewritten a legacy hash while rendering, before this
    // listener existed — read the hash it settled on.
    onHash()
    return () => window.removeEventListener('hashchange', onHash)
  }, [])
  const readOnly = readOnlyPage !== null && readOnlyPage === page

  const signedIn = q.data?.tier === 'admin'
  useEffect(() => {
    if (signedIn && readOnlyPage !== null) {
      setReadOnlyPage(null)
      storeReadOnlyPage(null)
    }
  }, [signedIn, readOnlyPage])

  const view = authGateView(q, { readOnly })

  const qc = useQueryClient()
  const dismissChallenge = useAuthChallengeStore((s) => s.dismiss)
  useEffect(() => {
    if (view !== 'login') return
    dismissChallenge()
    resetSessionQueries(qc)
  }, [view, qc, dismissChallenge])

  if (view === 'loading') return <AuthSplash />
  if (view === 'login') {
    const viewReadOnly = canViewReadOnly(q.data)
      ? () => {
          setReadOnlyPage(page)
          storeReadOnlyPage(page)
        }
      : undefined
    return <LoginView status={q.data} onViewReadOnly={viewReadOnly} />
  }
  return children
}
