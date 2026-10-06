// hal0 dashboard — top-bar session chip.
//
// The one place the dashboard says whether this browser is signed in. It
// exists because of the posture-coupled gate (#1822): a LAN-bound box with an
// admin key refuses this caller's ADMIN-class requests while "Require
// authentication" reads off, and an operator browsing read-only needs a
// standing way to sign in (and, once in, to see that they are and sign out)
// without first tripping a 401 on some mutation.
//
//   signin — this caller is gated: a "Sign in" button that opens the same
//            admin-key drawer a refused mutation does (AuthChallengeDrawer,
//            via useAuthChallengeStore.prompt — nothing to retry).
//   admin  — signed in on a box where the session is what grants access:
//            "Admin" + "Log out".
//   hidden — nothing is gated for this caller (keyless or loopback box).
//
// State routing is the pure sessionChipState() (gateDecision.js).

import { useAuthStatus } from '@/api/hooks/useAuthStatus'
import { useLogout } from '@/api/hooks/useAuthActions'
import { useAuthChallengeStore } from '@/stores/useAuthChallengeStore'
import { sessionChipState } from './gateDecision.js'

const LockGlyph = ({ open }) => (
  <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="3" y="7" width="10" height="7" rx="1.5" />
    <path d={open ? 'M5.5 7V4.8a2.5 2.5 0 0 1 4.8-1' : 'M5.5 7V4.8a2.5 2.5 0 0 1 5 0V7'} />
  </svg>
)

export function SessionChip() {
  const { data } = useAuthStatus()
  const prompt = useAuthChallengeStore((s) => s.prompt)
  const logout = useLogout()
  const state = sessionChipState(data)

  if (state === 'hidden') return null

  if (state === 'signin') {
    return (
      <button
        type="button"
        className="tb-session signin"
        data-testid="tb-session-signin"
        onClick={prompt}
        title="Viewing read-only — sign in with the admin key to manage this box"
      >
        <LockGlyph />
        <span>Sign in</span>
      </button>
    )
  }

  return (
    <span className="tb-session admin" data-testid="tb-session-admin" title="Signed in with the admin key">
      <LockGlyph open />
      <span>Admin</span>
      <button
        type="button"
        className="tb-session-out"
        data-testid="tb-session-logout"
        disabled={logout.isPending}
        onClick={() => logout.mutate()}
      >
        {logout.isPending ? 'Logging out…' : 'Log out'}
      </button>
    </span>
  )
}
