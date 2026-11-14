// Start or restart an NPU slot, and turn the backend's 409 `slot.npu_conflict` into a confirm.
// The NPU holds one engine at a time (#3666): starting a per-op xdna slot while FLM is up (or the
// reverse) is refused. Confirming retries once with `unloadNpuHolder`, which makes the backend
// unload the holder first. Traffic never passes the flag; only this click does.
import { useRef, useState } from 'react'
import { useSlotLoad, useSlotRestart } from '@/api/hooks/useSlots'
import { ConfirmDialog } from '../primitives.jsx'
import { npuConflictCopy, npuConflictFromError } from './npu-conflict-pure'

const toast = (msg, kind = 'info') => typeof window !== 'undefined' && window.__hal0Toast && window.__hal0Toast(msg, kind)

/**
 * `run(name, kind, opts?)` resolves to `'ok'` when the first attempt succeeded and `'prompted'` when
 * a confirm dialog took over. Any other error (and a `pending` conflict, after its toast) is the
 * caller's: other errors re-throw, `pending` resolves `'blocked'`. `opts.onSettled` fires once the
 * dialog path ends (confirmed retry done, failed, or cancelled).
 */
export function useNpuConflictStart() {
  const loadMut = useSlotLoad()
  const restartMut = useSlotRestart()
  const [ask, setAsk] = useState(null)
  // Ref mirror of `ask`: a second run() in the same tick must see the open dialog.
  const asking = useRef(false)
  const [busy, setBusy] = useState(false)
  // Ref mirror of `busy`: Escape (the Modal's onClose) must not settle the dialog while the
  // confirmed retry is in flight, or onSettled would fire twice (here and in confirm's finally).
  const inFlight = useRef(false)

  const post = (name, kind, unloadNpuHolder) => {
    const mut = kind === 'restart' ? restartMut : loadMut
    return mut.mutateAsync(unloadNpuHolder ? { name, unloadNpuHolder: true } : name)
  }

  const run = async (name, kind, opts = {}) => {
    // One dialog at a time: a second start while it is open must not replace it (that would drop
    // the first call's onSettled).
    if (asking.current) return 'prompted'
    try {
      await post(name, kind, false)
      return 'ok'
    } catch (err) {
      const c = npuConflictFromError(err)
      if (!c) throw err
      if (c.pending) {
        toast(err?.message || `${name}: the NPU is busy loading`, 'warn')
        return 'blocked'
      }
      asking.current = true
      setAsk({ name, kind, c, onSettled: opts.onSettled })
      return 'prompted'
    }
  }

  const close = () => {
    if (inFlight.current) return
    const a = ask
    asking.current = false
    setAsk(null)
    a?.onSettled?.()
  }
  const confirm = async () => {
    const a = ask
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    try {
      await post(a.name, a.kind, true)
    } catch (err) {
      // By design a second slot.npu_conflict on the retry is just this toast: no re-prompt loop.
      toast(err?.message ? `${a.name}: ${err.message}` : `${a.name}: action failed`, 'warn')
    } finally {
      inFlight.current = false
      setBusy(false)
      asking.current = false
      setAsk(null)
      a.onSettled?.()
    }
  }

  const copy = ask ? npuConflictCopy(ask.c) : null
  const dialog = ask ? (
    <ConfirmDialog
      open
      onCancel={close}
      onConfirm={confirm}
      title={copy.title}
      message={copy.body}
      confirmLabel={copy.confirmLabel}
      footerNote="Nothing is unloaded unless you confirm."
      confirmDisabled={busy}
    />
  ) : null

  return { run, dialog }
}
