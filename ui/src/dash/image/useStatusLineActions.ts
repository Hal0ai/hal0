// What each button of the GPU status line calls. One hook, so the Engine tab's "Stop image mode"
// is the same code as the line's: the calls, the unpin-first order and the force confirm live
// here once.
//
//   hold           switchover {mode: "generation", pin: true}
//   keep           POST /pin {pinned: true}
//   unpin          POST /pin {pinned: false}
//   stop           POST /pin {pinned: false} first when `ctx.pinned`, then switchover {mode:
//                  "inference"}; `force: true` only after the confirm, and only while
//                  `ctx.renderRunning`
//   cancel         `ctx.onCancel()`: the Generate panel owns the AbortController of its own run
//   cancel_render  POST /render/cancel (also clears editor prompts)
//   restart_engine POST /restart
//   open_editor    `ctx.editorUrl` in a new tab
//   dismiss        remembers `ctx.refusalAt` (localStorage; the session when storage is blocked)
//
// The confirm is a dialog, so the hook cannot draw it: `confirm` carries its state and
// `<ForceStopConfirm confirm={confirm} />` (ImageStatusLine.jsx) draws it. Whoever calls
// `run('stop', ...)` mounts one.

import { useRef, useState, useSyncExternalStore } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  useComfyuiPin,
  useComfyuiRenderCancel,
  useComfyuiRestart,
  useComfyuiSwitchover,
} from '@/api/hooks/useComfyui'
import type { StatusAction } from './status-line-pure'

export interface StatusActionCtx {
  // Image mode is pinned (`actionFacts(...).pinned`): stop unpins first.
  pinned?: boolean
  // A render is running (`actionFacts(...).renderRunning`): stop asks, then forces.
  renderRunning?: boolean
  editorUrl?: string | null
  // `actionFacts(...).refusalAt`: the refusal dismiss remembers.
  refusalAt?: number | null
  // Aborts this browser's own Generate run (the `cancel` action).
  onCancel?: () => void
}

/** The force confirm's state, for `<ForceStopConfirm>`. */
export interface ForceConfirm {
  open: boolean
  accept: () => void
  decline: () => void
}

// ── the dismissed refusal ────────────────────────────────────────────────────

// One key holding the `at` of the refusal the viewer dismissed. A newer refusal has a later `at`
// and shows again. Every storage read and write is guarded: private windows and blocked site
// data throw, and the line must render (and dismiss for the session) without it.
const DISMISS_KEY = 'hal0.imageGen.gateRefusalDismissedAt'

let sessionDismissedAt: number | null = null
const listeners = new Set<() => void>()

function readDismissedAt(): number | null {
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY)
    const at = raw == null ? NaN : Number(raw)
    return Number.isFinite(at) ? at : null
  } catch {
    return sessionDismissedAt
  }
}

function writeDismissedAt(at: number): void {
  try {
    window.localStorage.setItem(DISMISS_KEY, String(at))
  } catch {
    sessionDismissedAt = Math.max(at, sessionDismissedAt ?? at)
  }
  listeners.forEach((notify) => notify())
}

function subscribeDismissed(notify: () => void): () => void {
  listeners.add(notify)
  // Another tab dismissing.
  const onStorage = (e: StorageEvent) => {
    if (e.key === DISMISS_KEY) notify()
  }
  window.addEventListener('storage', onStorage)
  return () => {
    listeners.delete(notify)
    window.removeEventListener('storage', onStorage)
  }
}

/** `at` of the refusal the viewer last dismissed, or null. */
export function useDismissedRefusalAt(): number | null {
  return useSyncExternalStore(subscribeDismissed, readDismissedAt, () => null)
}

// ── the hook ─────────────────────────────────────────────────────────────────

const message = (e: unknown) => (e instanceof Error && e.message ? e.message : String(e))

export function useStatusLineActions() {
  const qc = useQueryClient()
  const switchover = useComfyuiSwitchover()
  const pin = useComfyuiPin()
  const renderCancel = useComfyuiRenderCancel()
  const restart = useComfyuiRestart()
  const dismissedAt = useDismissedRefusalAt()

  const [pending, setPending] = useState<StatusAction | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const answer = useRef<((ok: boolean) => void) | null>(null)
  const running = useRef(false)

  const askForce = () =>
    new Promise<boolean>((resolve) => {
      answer.current = resolve
      setConfirmOpen(true)
    })
  const settle = (ok: boolean) => {
    const resolve = answer.current
    answer.current = null
    setConfirmOpen(false)
    resolve?.(ok)
  }

  // Not memoised: the mutation objects change every render, so it would only look stable.
  const run = async (action: StatusAction, ctx: StatusActionCtx = {}): Promise<void> => {
    if (running.current) return
    running.current = true
    setPending(action)
    setError(null)
    try {
      switch (action) {
        case 'hold':
          await switchover.mutateAsync({ mode: 'generation', pin: true })
          break
        case 'keep':
          await pin.mutateAsync({ pinned: true })
          break
        case 'unpin':
          await pin.mutateAsync({ pinned: false })
          break
        case 'stop': {
          // Ask first: a declined confirm leaves the pin alone too.
          const force = ctx.renderRunning === true
          if (force && !(await askForce())) return
          if (ctx.pinned) await pin.mutateAsync({ pinned: false })
          await switchover.mutateAsync(force ? { mode: 'inference', force: true } : { mode: 'inference' })
          break
        }
        case 'cancel':
          ctx.onCancel?.()
          return
        case 'cancel_render':
          await renderCancel.mutateAsync()
          break
        case 'restart_engine':
          await restart.mutateAsync()
          break
        case 'open_editor':
          if (ctx.editorUrl) window.open(ctx.editorUrl, '_blank', 'noopener,noreferrer')
          return
        case 'dismiss':
          if (ctx.refusalAt != null) writeDismissedAt(ctx.refusalAt)
          return
      }
      // The line reads the arbiter and /status: ask for the new state now, not at the next tick.
      void qc.invalidateQueries({ queryKey: ['comfyui', 'arbiter'] })
      void qc.invalidateQueries({ queryKey: ['comfyui', 'status'] })
    } catch (e) {
      setError(message(e))
      // A half-done stop (unpinned, switch refused) changed the box too.
      void qc.invalidateQueries({ queryKey: ['comfyui', 'arbiter'] })
      void qc.invalidateQueries({ queryKey: ['comfyui', 'status'] })
    } finally {
      running.current = false
      setPending(null)
    }
  }

  const confirm: ForceConfirm = {
    open: confirmOpen,
    accept: () => settle(true),
    decline: () => settle(false),
  }

  return { run, pending, error, confirm, dismissedAt }
}
