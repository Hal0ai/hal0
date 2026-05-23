import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useToastsStore } from './toasts.js'

export const useSystemStore = defineStore('system', () => {
  const status = ref(null)    // raw /api/status response
  const hardware = ref(null)  // hardware section
  // slots array — per slot the backend returns at least:
  //   { name, kind, type, device, backend, provider, model, port,
  //     status, lemonade_state?, coresident_group?, backend_url? }
  // The ``device`` field (gpu-rocm / gpu-vulkan / cpu / npu) was added
  // in PR-11 (#163) for the v2 dashboard's per-card device badge.
  const slots = ref([])       // slots array
  const loading = ref(false)
  const error = ref(null)

  // Convenience getter — Footer bar uses this to render the
  // `**hal0** · <hostname>` brand line. Falls back to '' so the
  // separator dot can be conditionally hidden.
  const hostname = computed(() => status.value?.hostname || '')

  async function fetchStatus() {
    loading.value = true
    error.value = null
    try {
      const res = await fetch('/api/status')
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      status.value = data
      hardware.value = data.hardware ?? null

      // /api/status historically only returns synthetic upstream-backed
      // slots — dynamically created local slots don't appear there until
      // the backend merge fix (PR #26) lands.  Always fall back to
      // /api/slots which IS authoritative, and union real slots over
      // whatever /api/status returned so a just-created slot is visible
      // immediately on the next poll.
      const statusSlots = data.slots ?? []
      try {
        const slotsRes = await fetch('/api/slots')
        if (slotsRes.ok) {
          const realSlots = await slotsRes.json()
          const byName = new Map()
          for (const s of statusSlots) byName.set(s.name, s)
          for (const s of realSlots) byName.set(s.name, s)  // real wins
          slots.value = [...byName.values()]
        } else {
          slots.value = statusSlots
        }
      } catch {
        slots.value = statusSlots
      }
    } catch (err) {
      error.value = err.message
      // Don't toast on every background poll failure — only surface to store
    } finally {
      loading.value = false
    }
  }

  return { status, hardware, slots, loading, error, hostname, fetchStatus }
})
