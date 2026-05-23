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
