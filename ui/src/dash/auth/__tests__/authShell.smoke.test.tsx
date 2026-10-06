// Front-door login + top-bar session chip for the posture-coupled ADMIN gate.
//
// Server-style renders (renderToStaticMarkup) against a QueryClient whose
// `auth-status` entry is pre-seeded — on the server TanStack Query serves the
// cached entry and never fetches, so each case is exactly "what does the shell
// render for THIS /api/auth/status payload". Click-through behaviour (view
// read-only → sign in from the chip → log out) is covered end-to-end in
// tests/e2e/specs/auth-gate-v3.spec.ts.

import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AuthGate, READ_ONLY_STORAGE_KEY } from '../AuthGate.jsx'
import { SessionChip } from '../SessionChip.jsx'

const POSTURE_GATED = {
  auth_required: false,
  has_admin_key: true,
  lan_exposed: true,
  admin_sign_in_required: true,
  tier: 'anon',
}
const SIGNED_IN = { ...POSTURE_GATED, admin_sign_in_required: false, tier: 'admin' }
const ENFORCED_ANON = { ...POSTURE_GATED, auth_required: true }
const OPEN_BOX = { ...POSTURE_GATED, has_admin_key: false, admin_sign_in_required: false }

function render(status: Record<string, unknown>, node: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  qc.setQueryData(['auth-status'], status)
  return renderToStaticMarkup(React.createElement(QueryClientProvider, { client: qc }, node))
}

const gate = (status: Record<string, unknown>) =>
  render(status, React.createElement(AuthGate, null, React.createElement('div', { 'data-testid': 'the-app' })))

afterEach(() => vi.unstubAllGlobals())

describe('AuthGate', () => {
  it('asks for the key at the front door when the posture gate refuses this caller', () => {
    const html = gate(POSTURE_GATED)
    expect(html).toContain('data-testid="login-view"')
    expect(html).not.toContain('data-testid="the-app"')
  })

  it('explains the posture gate in its own words, not as "authentication is enabled"', () => {
    const html = gate(POSTURE_GATED)
    expect(html).toContain('reachable from your network')
    expect(html).not.toContain('Authentication is enabled')
  })

  it('offers a read-only way in for the posture gate, where open reads still work', () => {
    expect(gate(POSTURE_GATED)).toContain('data-testid="login-view-read-only"')
  })

  it('offers no read-only way in when enforcement is on', () => {
    const html = gate(ENFORCED_ANON)
    expect(html).toContain('data-testid="login-view"')
    expect(html).toContain('Authentication is enabled')
    expect(html).not.toContain('data-testid="login-view-read-only"')
  })

  it('renders the app for a signed-in session and for a box that gates nothing', () => {
    expect(gate(SIGNED_IN)).toContain('data-testid="the-app"')
    expect(gate(OPEN_BOX)).toContain('data-testid="the-app"')
  })

  it('remembers a read-only choice for the tab and renders the app', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: (k: string) => (k === READ_ONLY_STORAGE_KEY ? '1' : null),
      setItem: () => undefined,
      removeItem: () => undefined,
    })
    const html = gate(POSTURE_GATED)
    expect(html).toContain('data-testid="the-app"')
    expect(html).not.toContain('data-testid="login-view"')
  })

  it('does not let a remembered read-only choice bypass real enforcement', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => '1',
      setItem: () => undefined,
      removeItem: () => undefined,
    })
    expect(gate(ENFORCED_ANON)).toContain('data-testid="login-view"')
  })
})

describe('SessionChip', () => {
  const chip = (status: Record<string, unknown>) => render(status, React.createElement(SessionChip))

  it('offers sign-in while this caller is gated (read-only browsing)', () => {
    const html = chip(POSTURE_GATED)
    expect(html).toContain('data-testid="tb-session-signin"')
    expect(html).toContain('Sign in')
  })

  it('shows the admin session with a way to log out', () => {
    const html = chip(SIGNED_IN)
    expect(html).toContain('data-testid="tb-session-admin"')
    expect(html).toContain('data-testid="tb-session-logout"')
    expect(html).not.toContain('data-testid="tb-session-signin"')
  })

  it('renders nothing on a box that gates nothing', () => {
    expect(chip(OPEN_BOX)).toBe('')
  })
})
