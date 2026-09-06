// #1822 — AuthChallengeDrawer mount smoke test.
//
// `renderToStaticMarkup` is a server-style render, and zustand's React
// binding deliberately serves `getInitialState()` (never live state) as the
// SSR snapshot (zustand/esm/react.mjs's `useStore`) — so a `setState()` made
// right before an SSR render is invisible to the component, by design (real
// SSR never mutates a store ahead of hydration). That means this file can
// only prove the drawer's CLOSED default mounts cleanly; the open →
// key-entry → submit → retry flow needs a live client render and is covered
// end-to-end instead by tests/e2e/specs/auth-challenge-v3.spec.ts (a real
// mutation, a real 401, a real retry) plus the pure-logic coverage in
// useAuthChallengeStore.test.ts (request/dismiss/retry, no React involved).
//
// `@/dash/primitives.jsx`'s `Drawer` is the shared window-globals prototype
// component (React/Icons read as bare globals at module-eval time); it's
// stubbed here so this file stays scoped to AuthChallengeDrawer's own logic.

import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { describe, expect, it, vi } from 'vitest'
import { useAuthChallengeStore } from '@/stores/useAuthChallengeStore'

vi.mock('@/dash/primitives.jsx', () => ({
  Drawer: ({ open, eyebrow, title, children, foot }: any) =>
    open
      ? React.createElement('div', { 'data-testid': 'stub-drawer' }, eyebrow, title, children, foot)
      : null,
}))

const { AuthChallengeDrawer } = await import('../AuthChallengeDrawer.jsx')

describe('AuthChallengeDrawer (smoke)', () => {
  it('mounts closed (the default, no challenge pending) without throwing', () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const html = renderToStaticMarkup(
      React.createElement(QueryClientProvider, { client: qc }, React.createElement(AuthChallengeDrawer)),
    )
    expect(html).not.toContain('stub-drawer')
  })

  it('the store starts closed with nothing pending (the state this mount reflects)', () => {
    expect(useAuthChallengeStore.getState()).toMatchObject({ open: false, pending: null })
  })
})
