// hal0 dashboard — the "Remember me" tick-box preference.
//
// Ticking "Remember me" at login asks the server for a 30-day session instead
// of the 8h default (POST /api/auth/login {remember: true}). The session is
// the HttpOnly cookie the server sets; nothing here can extend or forge it.
// This module only remembers whether the operator ticked the box last time,
// so a browser they chose to trust does not ask them to tick it again. It
// stores a boolean — never the key.
//
// Storage may be absent (server render) or blocked; every path degrades to
// "off", the safe default for a browser we know nothing about.

export const REMEMBER_PREF_KEY = 'hal0.auth.rememberMe'

export function readRememberPreference() {
  try {
    return localStorage.getItem(REMEMBER_PREF_KEY) === '1'
  } catch {
    return false
  }
}

export function writeRememberPreference(on) {
  try {
    if (on) localStorage.setItem(REMEMBER_PREF_KEY, '1')
    else localStorage.removeItem(REMEMBER_PREF_KEY)
  } catch {
    // preference not kept — the next login simply starts unticked
  }
}
