// hal0 v3 dashboard — auth-challenge store (#1822).
//
// A LAN-bound box with `require_auth` OFF still requires an admin
// session/key for ADMIN-class mutations from off-box callers
// (hal0.api.auth's posture-coupled gate). That 401 (`auth.required`) can
// land on ANY mutation anywhere in the dashboard, so it's caught once,
// globally, via the TanStack Query `MutationCache`'s `onError` (see
// `lib/queryClient.ts`) rather than threaded through every `useMutation`
// call site.
//
// This store holds the ONE pending challenge (a second 401 while a drawer
// is already open replaces the retry target rather than queueing — the
// operator only has one login form to fill in at a time) and the retry
// path: `Mutation.execute(variables)` re-runs the exact call that got
// refused, through the SAME mutation instance, so its own onSuccess/
// onError/cache-invalidation still fire normally once the session exists.

import { create } from 'zustand'

// Structurally: a `@tanstack/query-core` `Mutation` instance. Typed loosely
// here (rather than importing the generic-heavy `Mutation<...>` type) since
// this store only ever calls `.execute(variables)` on it.
interface ExecutableMutation {
  execute: (variables: unknown) => Promise<unknown>
}

interface PendingChallenge {
  mutation: ExecutableMutation
  variables: unknown
}

interface AuthChallengeState {
  open: boolean
  pending: PendingChallenge | null
  /** Called by the global MutationCache.onError when a mutation 401s with auth.required. */
  request: (mutation: ExecutableMutation, variables: unknown) => void
  /** Dismiss without retrying — the original mutation stays failed. */
  dismiss: () => void
  /** Re-run the pending mutation (call after a successful login). */
  retry: () => Promise<void>
}

export const useAuthChallengeStore = create<AuthChallengeState>((set, get) => ({
  open: false,
  pending: null,

  request(mutation, variables) {
    set({ open: true, pending: { mutation, variables } })
  },

  dismiss() {
    set({ open: false, pending: null })
  },

  async retry() {
    const pending = get().pending
    set({ open: false, pending: null })
    if (!pending) return
    await pending.mutation.execute(pending.variables)
  },
}))
