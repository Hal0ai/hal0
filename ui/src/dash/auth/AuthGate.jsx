// hal0 dashboard — app-shell auth gate (O19).
//
// Wraps <App/> (main.jsx, inside the QueryClientProvider). On load it reads
// GET /api/auth/status once; when enforcement is on and the session is still
// anonymous it renders the login view INSTEAD of the app — no flash of a
// locked dashboard, no redirect loop. Open boxes (auth off, the shipped
// default) see zero change: the gate falls straight through to the app.
//
// All routing lives in the pure, unit-tested authGateView() (gateDecision.js);
// this component only binds it to the live query + renders.

import { useAuthStatus } from '@/api/hooks/useAuthStatus'
import { authGateView } from './gateDecision.js'
import { LoginView } from './LoginView.jsx'

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
  const view = authGateView(q)
  if (view === 'loading') return <AuthSplash />
  if (view === 'login') return <LoginView status={q.data} />
  return children
}
