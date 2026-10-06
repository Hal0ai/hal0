// @vitest-environment happy-dom
//
// The per-page login prompt, exercised through a live DOM: the login must come
// back when the hash moves to another section, must stay away for a tab inside
// the dismissed section, and the gate must read the hash the app actually
// settled on at mount (main.jsx rewrites legacy hashes while rendering).

import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { AuthGate, READ_ONLY_STORAGE_KEY } from '../AuthGate.jsx'

const POSTURE_GATED = {
  auth_required: false,
  has_admin_key: true,
  lan_exposed: true,
  admin_gated: true,
  admin_sign_in_required: true,
  tier: 'anon',
}

let container: HTMLDivElement
let root: Root

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, enabled: false } } })
  qc.setQueryData(['auth-status'], POSTURE_GATED)
  act(() => {
    root.render(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(AuthGate, null, React.createElement('div', { 'data-testid': 'the-app' })),
      ),
    )
  })
}

const has = (testId: string) => container.querySelector(`[data-testid="${testId}"]`) !== null

function setHash(hash: string) {
  act(() => {
    window.location.hash = hash
    window.dispatchEvent(new Event('hashchange'))
  })
}

beforeEach(() => {
  ;(globalThis as any).IS_REACT_ACT_ENVIRONMENT = true
  window.location.hash = ''
  sessionStorage.clear()
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('AuthGate — login on every page', () => {
  it('comes back when the hash moves to another section, not for a tab in the same one', () => {
    mount()
    expect(has('login-view')).toBe(true)

    act(() => {
      ;(container.querySelector('[data-testid="login-view-read-only"]') as HTMLButtonElement).click()
    })
    expect(has('the-app')).toBe(true)

    setHash('#slots')
    expect(has('login-view')).toBe(true)
    expect(has('the-app')).toBe(false)

    act(() => {
      ;(container.querySelector('[data-testid="login-view-read-only"]') as HTMLButtonElement).click()
    })
    setHash('#slots/endpoints')
    expect(has('the-app')).toBe(true)
    expect(has('login-view')).toBe(false)

    setHash('#dashboard')
    expect(has('login-view')).toBe(true)
  })

  it('reads the hash the app settled on at mount, even if it changed before the listener attached', () => {
    // A remembered read-only choice for "slots", but the tab is on the
    // dashboard: must prompt. Then the app rewrites the hash to a slots route
    // before the gate's listener exists (main.jsx does this for legacy links).
    sessionStorage.setItem(READ_ONLY_STORAGE_KEY, 'slots')
    window.location.hash = '#slots/endpoints'
    mount()
    expect(has('the-app')).toBe(true)

    sessionStorage.setItem(READ_ONLY_STORAGE_KEY, 'slots')
    act(() => root.unmount())
    root = createRoot(container)
    window.location.hash = '#dashboard'
    mount()
    expect(has('login-view')).toBe(true)
  })
})
