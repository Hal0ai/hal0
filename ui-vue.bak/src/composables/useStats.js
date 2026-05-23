import { ref, onMounted, onUnmounted } from 'vue'
import { api } from './useApi.js'

/**
 * Poll /api/stats/hardware on a fixed interval.
 *
 * Returns the latest snapshot in `stats`; null until the first successful
 * fetch.  Errors are absorbed silently (the dashboard already surfaces
 * upstream-unavailable elsewhere) so a transient blip doesn't black out
 * the bars on every render.
 */
export function useStats(intervalMs = 2500) {
  const stats = ref(null)
  const error = ref(null)
  let timer = null

  async function fetchOnce() {
    try {
      const data = await api('/api/stats/hardware')
      stats.value = data
      error.value = null
    } catch (e) {
      error.value = e?.message ?? String(e)
    }
  }

  onMounted(() => {
    fetchOnce()
    timer = setInterval(fetchOnce, intervalMs)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { stats, error, refresh: fetchOnce }
}

/**
 * Poll /api/slots/metrics — proxied from upstream haloai-style endpoints.
 * Shape: { [slotName]: { tokens_per_sec, requests_processing, gtt_mb, vram_mb, rss_mb, ... } }
 */
const MAX_HISTORY = 60

export function useSlotMetrics(intervalMs = 2500) {
  const metrics = ref({})
  const history = ref({})
  const aggHistory = ref({ tps: [], reqs: [] })
  let timer = null

  function pushPoint(arr, v) {
    arr.push(v || 0)
    if (arr.length > MAX_HISTORY) arr.shift()
  }

  async function fetchOnce() {
    try {
      const data = await api('/api/slots/metrics')
      metrics.value = data || {}
      let totalTps = 0
      let totalReqs = 0
      for (const [name, m] of Object.entries(metrics.value)) {
        if (!history.value[name]) history.value[name] = { tps: [], pps: [] }
        pushPoint(history.value[name].tps, m?.tokens_per_sec ?? m?.tps)
        pushPoint(history.value[name].pps, m?.prompt_tokens_per_sec ?? m?.prompt_tps)
        totalTps += (m?.tokens_per_sec ?? m?.tps ?? 0)
        totalReqs += (m?.requests_total ?? 0)
      }
      pushPoint(aggHistory.value.tps, totalTps)
      pushPoint(aggHistory.value.reqs, totalReqs)
    } catch {
      /* swallow — dashboard tolerates transient failures */
    }
  }

  onMounted(() => {
    fetchOnce()
    timer = setInterval(fetchOnce, intervalMs)
  })
  onUnmounted(() => {
    if (timer) clearInterval(timer)
  })

  return { metrics, history, aggHistory, refresh: fetchOnce }
}
