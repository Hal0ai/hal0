/**
 * stores/toast.js — v2 dashboard toast queue.
 *
 * NOTE: this is a NEW store for the dash-v2 surface; the existing
 * ``stores/toasts.js`` (plural, ``useToastsStore``) is unchanged and
 * still wired to every v1 component and ``useApi.js``. Keeping the
 * v2 store separate means slice #165 introduces zero behavioural
 * regression and gives the v2 views their own queue with the
 * design-spec'd shape ({id, msg, kind}).
 *
 * Auto-removal: each ``push()`` schedules a ``dismiss()`` after
 * ``ttl`` ms (default 4000). ``ttl <= 0`` makes the toast sticky.
 */
import { defineStore } from 'pinia'
import { ref } from 'vue'

let _nextId = 1

export const useToastStore = defineStore('toast', () => {
  const queue = ref([])
  // Active timers so dismiss(id) can pre-empt the scheduled removal.
  const _timers = new Map()

  function push(msg, kind = 'info', ttl = 4000) {
    const id = _nextId++
    queue.value.push({ id, msg, kind })
    if (ttl > 0) {
      const handle = setTimeout(() => dismiss(id), ttl)
      _timers.set(id, handle)
    }
    return id
  }

  function dismiss(id) {
    const idx = queue.value.findIndex((t) => t.id === id)
    if (idx !== -1) queue.value.splice(idx, 1)
    const handle = _timers.get(id)
    if (handle) {
      clearTimeout(handle)
      _timers.delete(id)
    }
  }

  function clear() {
    for (const handle of _timers.values()) clearTimeout(handle)
    _timers.clear()
    queue.value = []
  }

  // Convenience aliases — match the v1 store's signature so callers
  // copy-pasting between v1 and v2 surfaces don't trip on naming.
  function info(msg, ttl)    { return push(msg, 'info', ttl) }
  function success(msg, ttl) { return push(msg, 'success', ttl) }
  function warning(msg, ttl) { return push(msg, 'warning', ttl) }
  function error(msg, ttl)   { return push(msg, 'error', ttl) }

  return {
    queue,
    push, dismiss, clear,
    info, success, warning, error,
  }
})
