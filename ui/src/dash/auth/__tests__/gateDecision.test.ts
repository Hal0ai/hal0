// The front-door decision for the posture-coupled ADMIN gate.
//
// A LAN-bound box with an admin key refuses ADMIN-class requests (reads
// included) from off-box callers while `auth_required` still reads false.
// The backend reports that per-caller outcome as `admin_sign_in_required`;
// these tests pin how the shell and the top-bar session chip route on it.
// (The original O19 cases — enforcement on/off, splash, fail-open, login
// error copy — stay in gateDecision.test.mjs.)

import { describe, expect, it } from 'vitest'
import { authGateView, canViewReadOnly, sessionChipState } from '../gateDecision.js'

const POSTURE_GATED = {
  auth_required: false,
  has_admin_key: true,
  lan_exposed: true,
  admin_sign_in_required: true,
  tier: 'anon',
}
const SIGNED_IN = { ...POSTURE_GATED, admin_sign_in_required: false, tier: 'admin' }
const ENFORCED_ANON = { ...POSTURE_GATED, auth_required: true }
const OPEN_BOX = {
  auth_required: false,
  has_admin_key: false,
  lan_exposed: true,
  admin_sign_in_required: false,
  tier: 'anon',
}

describe('authGateView — posture-coupled gate', () => {
  it('shows the login screen when this caller must sign in, even with enforcement off', () => {
    expect(authGateView({ data: POSTURE_GATED })).toBe('login')
  })

  it('renders the app once signed in', () => {
    expect(authGateView({ data: SIGNED_IN })).toBe('app')
  })

  it('renders the app when the operator chose to view read-only', () => {
    expect(authGateView({ data: POSTURE_GATED }, { readOnly: true })).toBe('app')
  })

  it('ignores read-only when enforcement is on — nothing is readable anonymously', () => {
    expect(authGateView({ data: ENFORCED_ANON }, { readOnly: true })).toBe('login')
  })

  it('leaves a box that gates nothing for this caller untouched', () => {
    expect(authGateView({ data: OPEN_BOX })).toBe('app')
  })

  it('treats a status payload without the field (older API) as not gated', () => {
    expect(authGateView({ data: { auth_required: false, tier: 'anon' } })).toBe('app')
  })
})

describe('canViewReadOnly', () => {
  it('offers read-only only for the posture gate, where open reads still work', () => {
    expect(canViewReadOnly(POSTURE_GATED)).toBe(true)
    expect(canViewReadOnly(ENFORCED_ANON)).toBe(false)
    expect(canViewReadOnly(OPEN_BOX)).toBe(false)
    expect(canViewReadOnly(undefined)).toBe(false)
  })
})

describe('sessionChipState', () => {
  it('asks for sign-in when this caller is gated', () => {
    expect(sessionChipState(POSTURE_GATED)).toBe('signin')
  })

  it('shows the admin session where a session is what grants access', () => {
    expect(sessionChipState(SIGNED_IN)).toBe('admin')
    expect(sessionChipState({ ...ENFORCED_ANON, admin_sign_in_required: false, tier: 'admin' })).toBe('admin')
  })

  it('stays hidden on a box that gates nothing', () => {
    expect(sessionChipState(OPEN_BOX)).toBe('hidden')
    // A keyless box can still carry an agent-chat session cookie (tier admin);
    // there is nothing to sign in to or out of, so no chip.
    expect(sessionChipState({ ...OPEN_BOX, tier: 'admin' })).toBe('hidden')
    expect(sessionChipState(undefined)).toBe('hidden')
  })
})
