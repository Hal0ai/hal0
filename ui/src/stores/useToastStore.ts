// hal0 v3 dashboard — toast store (Phase B1).
//
// Ported from ui-vue.bak/src/stores/toast.js. {id, msg, kind} queue with
// auto-removal after `ttl` ms (default 4000). `ttl <= 0` makes a toast
// sticky.
//
// The dash/main.jsx prototype writes to `window.__hal0Toast` directly.
// We mirror that behaviour: this store's `push()` is the canonical
// implementation, and we wire `window.__hal0Toast` to call it so the
// prototype JSX surfaces (which still use the global) keep working.

import { create } from 'zustand'

export type ToastKind = 'info' | 'success' | 'warning' | 'error' | 'ok' | 'warn' | 'err'

export interface Toast {
  id: number
  msg: string
  kind: ToastKind
}

interface ToastState {
  queue: Toast[]
  push: (msg: string, kind?: ToastKind, ttl?: number) => number
  dismiss: (id: number) => void
  clear: () => void
  info: (msg: string, ttl?: number) => number
  success: (msg: string, ttl?: number) => number
  warning: (msg: string, ttl?: number) => number
  error: (msg: string, ttl?: number) => number
}

let nextId = 1
const timers = new Map<number, ReturnType<typeof setTimeout>>()

export const useToastStore = create<ToastState>((set, get) => ({
  queue: [],

  push(msg, kind = 'info', ttl = 4000) {
    const id = nextId++
    set((s) => ({ queue: [...s.queue, { id, msg, kind }] }))
    if (ttl > 0) {
      const handle = setTimeout(() => get().dismiss(id), ttl)
      timers.set(id, handle)
    }
    return id
  },

  dismiss(id) {
    set((s) => ({ queue: s.queue.filter((t) => t.id !== id) }))
    const handle = timers.get(id)
    if (handle) {
      clearTimeout(handle)
      timers.delete(id)
    }
  },

  clear() {
    for (const h of timers.values()) clearTimeout(h)
    timers.clear()
    set({ queue: [] })
  },

  info(msg, ttl) {
    return get().push(msg, 'info', ttl)
  },
  success(msg, ttl) {
    return get().push(msg, 'success', ttl)
  },
  warning(msg, ttl) {
    return get().push(msg, 'warning', ttl)
  },
  error(msg, ttl) {
    return get().push(msg, 'error', ttl)
  },
}))

/**
 * Install `window.__hal0Toast(msg, kind)` so the prototype JSX (still
 * full of `window.__hal0Toast && window.__hal0Toast(...)` calls) routes
 * through the zustand store. Idempotent.
 */
export function installToastGlobal() {
  if (typeof window === 'undefined') return
  if ((window as any).__hal0ToastInstalled) return
  ;(window as any).__hal0Toast = (msg: string, kind: ToastKind = 'info') => {
    useToastStore.getState().push(msg, kind)
  }
  ;(window as any).__hal0ToastInstalled = true
}

/**
 * Install `window.__hal0UseToastQueue()` — a hook bridge, same pattern as
 * `board-hook-bridge.ts`'s `window.__hal0UseBoardChat` — so the strict
 * no-ES-imports prototype file `dash/main.jsx` can subscribe to this
 * store's queue without importing it directly. Idempotent (re-installing
 * would only rebind the same function).
 *
 * GH #1473: this store's `push()` was already the canonical
 * `window.__hal0Toast` implementation (installed above, before React even
 * mounts), but nothing ever rendered `queue` — main.jsx kept its own
 * single-slot `useState` and unconditionally overwrote `window.__hal0Toast`
 * with it on every App mount, so a second toast replaced the first instead
 * of queueing, and anything fired before mount went into this store's
 * queue and was never seen. Wiring a real consumer closes both gaps.
 */
export function installToastQueueHook() {
  if (typeof window === 'undefined') return
  ;(window as any).__hal0UseToastQueue = () => {
    // Select `queue` alone (a stable array reference unless push/dismiss/
    // clear actually ran) rather than a `{queue, dismiss}` object literal —
    // a fresh object every render fails zustand's snapshot-equality check
    // and the resulting resubscribe loop is an infinite "Maximum update
    // depth exceeded" crash. `dismiss` doesn't need a subscription (its
    // reference is stable for the store's lifetime), so it's read directly
    // off getState().
    const queue = useToastStore((s) => s.queue)
    return { queue, dismiss: useToastStore.getState().dismiss }
  }
}
