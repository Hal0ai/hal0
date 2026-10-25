// What each button of the GPU status line calls. One hook, so the Engine tab's "Stop image mode"
// is the same code as the line's: the calls, the unpin-first order and the force confirm live
// here once.
//
//   hold           switchover {mode: "generation", pin: true}, after the pin confirm
//   keep           POST /pin {pinned: true}, after the pin confirm
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
// Three actions ask first, because each can cost something the line next to the button does not
// say: `stop` while a render runs (it drops the render), and `hold` and `keep` (a pin refuses GPU
// chat until someone unpins). A declined confirm sends nothing. The confirm is a dialog, so the
// hook cannot draw it: `confirm` carries its state (`kind` says which question) and
// `<ForceStopConfirm confirm={confirm} />` and `<PinConfirm confirm={confirm} />`
// (ImageStatusLine.jsx) draw them. Whoever calls `run('stop', ...)` mounts the first, whoever
// calls `run('hold' | 'keep')` the second.

import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
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

/** Which question the confirm asks. */
export type ConfirmKind = 'stop' | 'hold' | 'keep'

/** The confirm's state, for `<ForceStopConfirm>` and `<PinConfirm>`. */
export interface ActionConfirm {
  open: boolean
  /** The action waiting for an answer; null while nothing is asked. */
  kind: ConfirmKind | null
  accept: () => void
  decline: () => void
}

// ── the dismissed refusal ────────────────────────────────────────────────────

// One key holding the `at` of the refusal the viewer dismissed. A newer refusal has a later `at`
// and shows again. Every storage read and write is guarded: private windows and blocked site
// data throw, and the line must render (and dismiss for the session) without it.
const DISMISS_KEY = 'hal0.imageGen.gateRefusalDismissedAt'

// What this tab dismissed while storage would not take the write. Read alongside the stored
// value: storage can read fine and still refuse a write (quota, a locked-down profile).
let sessionDismissedAt: number | null = null
const listeners = new Set<() => void>()

function readDismissedAt(): number | null {
  let stored: number | null = null
  try {
    const raw = window.localStorage.getItem(DISMISS_KEY)
    const at = raw == null ? NaN : Number(raw)
    stored = Number.isFinite(at) ? at : null
  } catch {
    /* storage blocked: the session value is all there is */
  }
  if (sessionDismissedAt == null) return stored
  return stored == null ? sessionDismissedAt : Math.max(stored, sessionDismissedAt)
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

/** Forget the dismissal: the next refusal (and the last one, if it is still the latest) shows. */
export function clearDismissedRefusal(): void {
  sessionDismissedAt = null
  try {
    window.localStorage.removeItem(DISMISS_KEY)
  } catch {
    /* storage blocked: nothing stored to remove */
  }
  listeners.forEach((notify) => notify())
}

/** `at` of the refusal the viewer last dismissed, or null. */
export function useDismissedRefusalAt(): number | null {
  return useSyncExternalStore(subscribeDismissed, readDismissedAt, () => null)
}

// ── the hook ─────────────────────────────────────────────────────────────────

const message = (e: unknown) => (e instanceof Error && e.message ? e.message : String(e))

/**
 * `resetKey`: the state the buttons belong to (the line passes its `kind`). A failure message is
 * shown only while the key it failed under is still the current one, so it is gone once the box
 * has moved on, and stays while the same state merely counts on. Leave it out to keep a message
 * until the next action.
 */
export function useStatusLineActions({ resetKey = null }: { resetKey?: unknown } = {}) {
  const qc = useQueryClient()
  const switchover = useComfyuiSwitchover()
  const pin = useComfyuiPin()
  const renderCancel = useComfyuiRenderCancel()
  const restart = useComfyuiRestart()

  const [pending, setPending] = useState<StatusAction | null>(null)
  const [failure, setFailure] = useState<{ message: string; key: unknown } | null>(null)
  // The key at the moment a failure lands, not at the click: a failure that arrives after the
  // state moved on belongs to the state it arrives in.
  const keyRef = useRef(resetKey)
  useEffect(() => {
    keyRef.current = resetKey
  }, [resetKey])
  const error = failure && Object.is(failure.key, resetKey) ? failure.message : null
  const [asking, setAsking] = useState<ConfirmKind | null>(null)
  const answer = useRef<((ok: boolean) => void) | null>(null)
  const running = useRef(false)

  const ask = (kind: ConfirmKind) =>
    new Promise<boolean>((resolve) => {
      answer.current = resolve
      setAsking(kind)
    })
  const settle = (ok: boolean) => {
    const resolve = answer.current
    answer.current = null
    setAsking(null)
    resolve?.(ok)
  }

  // Not memoised: the mutation objects change every render, so it would only look stable.
  const run = async (action: StatusAction, ctx: StatusActionCtx = {}): Promise<void> => {
    if (running.current) return
    running.current = true
    setPending(action)
    setFailure(null)
    try {
      switch (action) {
        case 'hold':
          // A pin refuses GPU chat until someone unpins: ask before anything is sent.
          if (!(await ask('hold'))) return
          await switchover.mutateAsync({ mode: 'generation', pin: true })
          break
        case 'keep':
          if (!(await ask('keep'))) return
          await pin.mutateAsync({ pinned: true })
          break
        case 'unpin':
          await pin.mutateAsync({ pinned: false })
          break
        case 'stop': {
          // Ask first: a declined confirm leaves the pin alone too.
          const force = ctx.renderRunning === true
          if (force && !(await ask('stop'))) return
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
      setFailure({ message: message(e), key: keyRef.current })
      // A half-done stop (unpinned, switch refused) changed the box too.
      void qc.invalidateQueries({ queryKey: ['comfyui', 'arbiter'] })
      void qc.invalidateQueries({ queryKey: ['comfyui', 'status'] })
    } finally {
      running.current = false
      setPending(null)
    }
  }

  const confirm: ActionConfirm = {
    open: asking != null,
    kind: asking,
    accept: () => settle(true),
    decline: () => settle(false),
  }

  return { run, pending, error, confirm }
}
