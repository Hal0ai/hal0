// hal0 dashboard — auth gate decision + login error classification (O19).
//
// Dependency-free so both AuthGate.jsx (the app-shell gate) and the node
// unit test (__tests__/gateDecision.test.mjs) import it without a DOM,
// React, or the query client. Pure functions only.
//
// Posture (2026-07-19): auth is OFF by default (see hal0.api.auth). When it
// IS enabled and the browser session is still anonymous, the shell renders
// the login view instead of the app — never a flash of locked UI, never a
// redirect loop.
//
// Posture-coupled gate (#1822): a LAN-bound box with an admin key refuses
// ADMIN-class requests — reads included — from off-box callers while
// `auth_required` still reads false. Whether that applies depends on this
// caller's own peer, which only the server can see, so GET /api/auth/status
// reports it as `admin_sign_in_required`. The shell treats it as a front-door
// login too (most of the dashboard is ADMIN-class and would render empty),
// with a read-only escape because OPEN/CLIENT reads genuinely still work.

// Tiers the backend's GET /api/auth/status reports as "already authenticated
// enough to use the app". The browser session cookie always resolves to
// admin; client is included so a bearer-embedded surface isn't gated.
const AUTHED_TIERS = new Set(['admin', 'client'])

/**
 * Decide what the app shell should render.
 *
 * @param {{ data?: {auth_required?: boolean, admin_sign_in_required?: boolean, tier?: string, has_admin_key?: boolean}, isPending?: boolean, isError?: boolean }} q
 *   The useAuthStatus() query result (subset).
 * @param {{ readOnly?: boolean }} [opts]
 *   `readOnly` — the operator dismissed the login screen to look around
 *   without signing in. Honoured only for the posture-coupled gate.
 * @returns {'loading'|'login'|'app'}
 *   - 'loading' — first probe in flight, nothing decided yet (render a neutral
 *     splash, NOT the app, to avoid flashing locked UI).
 *   - 'login'   — this session is anonymous and either enforcement is on, or
 *     the posture-coupled gate refuses this caller's ADMIN requests.
 *   - 'app'     — render the dashboard (nothing gated, already authed, viewing
 *     read-only, or the probe failed → fail-open, since /api/auth/status is an
 *     OPEN route and a blip must not brick an open box).
 */
export function authGateView(q, opts) {
  const { data, isPending, isError } = q || {}
  const { readOnly = false } = opts || {}
  // Fail-open the moment we can't determine posture: an errored probe (or a
  // box that simply doesn't answer) renders the app rather than trapping the
  // operator behind a login they may not even need.
  if (isError) return 'app'
  if (!data) return isPending ? 'loading' : 'app'
  const tier = data.tier || 'anon'
  if (AUTHED_TIERS.has(tier)) return 'app'
  // Enforcement on: every data route is gated, so read-only has nothing to show.
  if (data.auth_required) return 'login'
  if (data.admin_sign_in_required) return readOnly ? 'app' : 'login'
  return 'app'
}

/**
 * Can this caller usefully skip the login screen? True only for the
 * posture-coupled gate: enforcement is off, so the OPEN/CLIENT reads (slots,
 * models, hardware, stats) still answer and a read-only dashboard has content.
 *
 * @param {{auth_required?: boolean, admin_sign_in_required?: boolean}} [status]
 * @returns {boolean}
 */
export function canViewReadOnly(status) {
  return !!status && !status.auth_required && !!status.admin_sign_in_required
}

/**
 * What the top-bar session chip shows.
 *
 * @param {{auth_required?: boolean, admin_sign_in_required?: boolean, has_admin_key?: boolean, lan_exposed?: boolean, tier?: string}} [status]
 * @returns {'hidden'|'signin'|'admin'}
 *   - 'signin' — this caller's ADMIN requests are refused; offer the login.
 *   - 'admin'  — signed in on a box where the session is what grants access
 *     (so "log out" means something).
 *   - 'hidden' — nothing is gated for this caller. That includes a keyless
 *     box whose browser happens to hold an agent-chat session cookie (the
 *     same cookie resolves to the admin tier): nothing to sign in to or out of.
 */
export function sessionChipState(status) {
  if (!status) return 'hidden'
  if (status.tier === 'admin') {
    const sessionMatters = !!status.has_admin_key && (!!status.auth_required || !!status.lan_exposed)
    return sessionMatters ? 'admin' : 'hidden'
  }
  return status.admin_sign_in_required ? 'signin' : 'hidden'
}

/**
 * Turn a login POST failure into operator-facing copy. NEVER echoes the key.
 *
 * @param {{ code?: string, status?: number, details?: Record<string, unknown> }} err
 *   A Hal0Error (or a plain object with the same shape).
 * @returns {{ kind: 'invalid'|'rate_limited'|'no_admin_key'|'network', text: string, retryAfterS: number|null }}
 */
export function loginErrorMessage(err) {
  const code = err && err.code
  const status = err && err.status
  const details = (err && err.details) || {}

  if (code === 'auth.rate_limited' || status === 429) {
    const raw = details.retry_after_s
    const retryAfterS = typeof raw === 'number' && raw > 0 ? Math.ceil(raw) : null
    return {
      kind: 'rate_limited',
      text: retryAfterS
        ? `Too many attempts. Try again in ${retryAfterS}s.`
        : 'Too many attempts. Wait a moment and try again.',
      retryAfterS,
    }
  }
  if (code === 'auth.no_admin_key') {
    return {
      kind: 'no_admin_key',
      text: 'No admin key is configured on the server. Set HAL0_ADMIN_KEY, then log in.',
      retryAfterS: null,
    }
  }
  if (code === 'auth.invalid_key' || status === 401 || status === 403) {
    return { kind: 'invalid', text: 'Invalid key. Check it and try again.', retryAfterS: null }
  }
  return {
    kind: 'network',
    text: "Couldn't reach the server. Check your connection and try again.",
    retryAfterS: null,
  }
}
