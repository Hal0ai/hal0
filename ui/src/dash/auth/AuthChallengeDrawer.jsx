// hal0 dashboard — sign-in-required drawer (#1822).
//
// A LAN-bound box with `require_auth` OFF still gates ADMIN-class
// mutations (model pulls, slot deletes, config writes, approval execution)
// for callers that didn't arrive over loopback — see
// hal0.api.auth's posture-coupled gate. Full-page login (AuthGate/
// LoginView) never fires for this case: `auth_required` genuinely reads
// false, so the shell renders the app as usual. The FIRST time a mutation
// hits that 401, `lib/queryClient.ts`'s global MutationCache.onError routes
// it here via useAuthChallengeStore instead.
//
// The drawer is also what the top-bar session chip opens (SessionChip →
// useAuthChallengeStore.prompt) for an operator browsing read-only: then no
// mutation is pending, the copy drops the "will retry" promise, and a
// successful login just closes it.
//
// Consequence-first copy (COMMON.md): say what happens to the operator,
// then the mechanism. On a successful login, `retry()` re-executes the
// SAME mutation that was refused (same variables, same `useMutation`
// instance), so the original caller's own onSuccess / toast / cache
// invalidation fires exactly as if the 401 had never happened.

import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { Drawer } from '@/dash/primitives.jsx'
import { loginErrorMessage } from './gateDecision.js'
import { RememberMeField } from './RememberMeField.jsx'
import { readRememberPreference, writeRememberPreference } from './rememberPreference.js'
import { useAuthChallengeStore } from '@/stores/useAuthChallengeStore'

export function AuthChallengeDrawer() {
  const open = useAuthChallengeStore((s) => s.open)
  const dismiss = useAuthChallengeStore((s) => s.dismiss)
  const retry = useAuthChallengeStore((s) => s.retry)
  const hasPending = useAuthChallengeStore((s) => s.pending !== null)
  const qc = useQueryClient()

  const [key, setKey] = useState('')
  const [error, setError] = useState(null)
  const [remember, setRemember] = useState(readRememberPreference)

  useEffect(() => {
    if (!open) {
      setKey('')
      setError(null)
    }
  }, [open])

  const login = useMutation({
    mutationFn: (body) => apiPost(ENDPOINTS.authLogin, body),
    onSuccess: async (_data, body) => {
      setError(null)
      setKey('')
      writeRememberPreference(body.remember)
      await qc.invalidateQueries({ queryKey: ['auth-status'] })
      // The gate refuses ADMIN-class READS too, so every panel that 401'd
      // while signed out is sitting on a cached error. Refetch them all —
      // not awaited: the retry below must not wait on a page full of reads.
      void qc.invalidateQueries()
      await retry()
    },
    onError: (err) => setError(loginErrorMessage(err)),
  })

  const submit = (e) => {
    e.preventDefault()
    if (!key || login.isPending) return
    setError(null)
    login.mutate({ key, remember })
  }

  // Mount NOTHING until a challenge is actually raised. `Drawer` renders its
  // <aside class="drawer" role="dialog"> whether or not it is open — `open`
  // only adds a class — so an always-mounted instance at the app root would
  // put a SECOND .drawer / [role="dialog"] on every page, and every spec that
  // addresses the page's one drawer by class or role becomes a strict-mode
  // violation. After the hooks above, so hook order stays unconditional.
  if (!open) return null

  return (
    <div className="auth-challenge-layer">
      <Drawer
        open={open}
        onClose={dismiss}
        eyebrow="Sign-in required"
        title="This box is reachable from your network"
        width={420}
        foot={
          <button
            type="submit"
            form="auth-challenge-form"
            className="btn"
            data-testid="auth-challenge-submit"
            disabled={!key || login.isPending}
          >
            {login.isPending ? 'Signing in…' : hasPending ? 'Sign in & retry' : 'Sign in'}
          </button>
        }
      >
        <form id="auth-challenge-form" data-testid="auth-challenge-drawer" onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
          <p style={{ margin: 0, fontSize: 12.5, lineHeight: 1.55, color: 'var(--fg-3, #aaa)' }}>
            {hasPending
              ? 'Other devices on your network can reach this box, so changes need the admin key — sign in once and the action you just tried will retry automatically.'
              : 'Other devices on your network can reach this box, so settings, memory, logs and any change need the admin key. Sign in once to manage it from this browser.'}
          </p>

          <label className="mono" htmlFor="auth-challenge-key" style={{ fontSize: 11, color: 'var(--fg-4, #888)' }}>
            Admin key
          </label>
          <input
            id="auth-challenge-key"
            data-testid="auth-challenge-key-input"
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoComplete="current-password"
            autoFocus
            spellCheck={false}
            disabled={login.isPending}
            placeholder="admin key"
            className="input mono"
          />

          <RememberMeField
            testId="auth-challenge-remember"
            checked={remember}
            onChange={setRemember}
            disabled={login.isPending}
          />

          {error && (
            <div data-testid="auth-challenge-error" role="alert" className="mono" style={{ fontSize: 11.5, lineHeight: 1.5, color: 'var(--err, #e66)' }}>
              {error.text}
            </div>
          )}
        </form>
      </Drawer>
    </div>
  )
}
