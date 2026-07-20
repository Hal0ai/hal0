// Dependency-free tests for the auth-gate decision + login error classifier
// (O19). Run: node ui/src/dash/auth/__tests__/gateDecision.test.mjs
//
// Pins the status→view routing (open box → app, enabled+anon → login,
// enabled+admin → app, loading splash, fail-open on probe error) and the
// login error states (invalid key / rate limit with retry-after / no admin
// key / network) — including that no error text ever echoes a key.

import { authGateView, loginErrorMessage } from '../gateDecision.js'

let failures = 0
const fail = (msg) => {
  failures += 1
  console.error('  ✗ ' + msg)
}
const eq = (a, b, msg) => {
  if (JSON.stringify(a) !== JSON.stringify(b)) {
    fail(`${msg} — expected ${JSON.stringify(b)}, got ${JSON.stringify(a)}`)
  }
}
const ok = (cond, msg) => {
  if (!cond) fail(msg)
}

// ── authGateView: status → view routing ─────────────────────────────
// Auth off (the shipped default) → straight to the app, zero change.
eq(authGateView({ data: { auth_required: false, tier: 'anon' } }), 'app', 'auth off → app')
eq(authGateView({ data: { auth_required: false, tier: 'open' } }), 'app', 'auth off (open tier) → app')

// Auth on + anonymous session → login.
eq(authGateView({ data: { auth_required: true, tier: 'anon' } }), 'login', 'on + anon → login')
eq(
  authGateView({ data: { auth_required: true, has_admin_key: false, tier: 'anon' } }),
  'login',
  'on + anon + no key → still login',
)

// Auth on + already authenticated (cookie→admin, or client) → app.
eq(authGateView({ data: { auth_required: true, tier: 'admin' } }), 'app', 'on + admin → app')
eq(authGateView({ data: { auth_required: true, tier: 'client' } }), 'app', 'on + client → app')

// First probe in flight → neutral splash (never flash the locked app).
eq(authGateView({ isPending: true }), 'loading', 'pending, no data → loading')

// Fail-open: a probe error (or no answer) must not brick an open box.
eq(authGateView({ isError: true }), 'app', 'probe error → app (fail-open)')
eq(authGateView({ isError: true, data: { auth_required: true, tier: 'anon' } }), 'app', 'error wins → app')
eq(authGateView({ data: undefined, isPending: false }), 'app', 'settled, no data → app')
eq(authGateView({}), 'app', 'empty query → app')

// ── loginErrorMessage: error states ─────────────────────────────────
const invalid = loginErrorMessage({ code: 'auth.invalid_key', status: 401 })
eq(invalid.kind, 'invalid', 'invalid_key → invalid')
ok(/invalid key/i.test(invalid.text), 'invalid text mentions invalid key')

const rl = loginErrorMessage({ code: 'auth.rate_limited', status: 429, details: { retry_after_s: 12 } })
eq(rl.kind, 'rate_limited', '429 → rate_limited')
eq(rl.retryAfterS, 12, 'retry-after carried through')
ok(/12s/.test(rl.text), 'retry-after shown in text')

const rlNoDetail = loginErrorMessage({ status: 429 })
eq(rlNoDetail.kind, 'rate_limited', '429 w/o details → rate_limited')
eq(rlNoDetail.retryAfterS, null, 'no retry-after → null')
ok(!/undefined|NaN/.test(rlNoDetail.text), 'no retry-after → clean text')

const noKey = loginErrorMessage({ code: 'auth.no_admin_key', status: 400 })
eq(noKey.kind, 'no_admin_key', 'no_admin_key classified')

const net = loginErrorMessage({ message: 'Failed to fetch' })
eq(net.kind, 'network', 'unknown → network')

// Security: an error must never echo a key value even if one leaks into the
// error object. loginErrorMessage only reads code/status/details.retry_after_s.
const leaky = loginErrorMessage({ code: 'auth.invalid_key', status: 401, details: { key: 'SECRET-KEY-123' } })
ok(!leaky.text.includes('SECRET-KEY-123'), 'error text never echoes the key')

// ── summary ─────────────────────────────────────────────────────────
if (failures) {
  console.error(`\ngateDecision.test.mjs: ${failures} failure(s)`)
  process.exit(1)
}
console.log('gateDecision.test.mjs: all passed')
