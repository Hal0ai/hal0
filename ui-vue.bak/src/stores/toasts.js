import { defineStore } from 'pinia'
import { ref } from 'vue'

let _nextId = 1

export const useToastsStore = defineStore('toasts', () => {
  const toasts = ref([])

  function add(message, type = 'info', duration = 4000) {
    const id = _nextId++
    toasts.value.push({ id, message, type })
    if (duration > 0) {
      setTimeout(() => remove(id), duration)
    }
    return id
  }

  function remove(id) {
    const idx = toasts.value.findIndex((t) => t.id === id)
    if (idx !== -1) toasts.value.splice(idx, 1)
  }

  function success(message, duration) { return add(message, 'success', duration) }
  function error(message, duration)   { return add(message, 'error', duration) }
  function warning(message, duration) { return add(message, 'warning', duration) }
  function info(message, duration)    { return add(message, 'info', duration) }

  return { toasts, add, remove, success, error, warning, info }
})
