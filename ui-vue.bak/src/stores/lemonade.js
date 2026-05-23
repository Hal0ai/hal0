/**
 * stores/lemonade.js — Pinia store for the Lemonade runtime snapshot.
 *
 * Polls ``GET /v1/health`` every 2s and exposes the rollup the dashboard
 * v2 views need (loaded models, throughput hint, version, error state).
 * Per the hal0_lemonade_ws_protocol memory there's NO model-load WS
 * event on /logs/stream, so polling /v1/health is the canonical way to
 * observe load-state changes from the UI.
 *
 * Refactor notes (slice #165 / PR dash-v2-1b):
 *   PR-11 (#163) landed ``useNuclearEvictBanner`` as a SSE-only
 *   composable against ``/api/lemonade/events/stream`` — it does NOT
 *   poll. This store stands up the polling surface; the composable now
 *   also calls ``store.init()`` so the dashboard has one place to
 *   subscribe (polling + SSE-toast both kicked from App.vue's
 *   ``useNuclearEvictBanner()`` call). The existing SSE toast path is
 *   untouched so PR-11's e2e spec keeps passing.
 *
 * State shape
 * -----------
 *   loadedModels[]  — list of {model_name, backend_url, last_use?} from
 *                     /v1/health.loaded[]. Caller treats unknown fields
 *                     permissively.
 *   maxModels       — int or null; Lemonade's configured pool budget.
 *   version         — Lemonade version string (e.g. "v10.6.0") or null.
 *   lastUse         — Map<model_name, ts_seconds>; freshest per-model.
 *   health          — 'up' | 'degraded' | 'down'; degraded covers
 *                     2xx-but-malformed bodies.
 *   error           — last error message (null when healthy).
 *
 * Getters
 * -------
 *   loadedByName    — Map<model_name, entry> for O(1) lookup.
 *   isLoaded(name)  — convenience for SlotCard chips.
 *   throughput      — MB/s if present in /v1/health, else null.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { mockFetch } from '../composables/useMock.js'

const HEALTH_URL = '/v1/health'
const STATS_URL = '/v1/stats'
const POLL_MS = 2000
const STATS_POLL_MS = 5000

export const useLemonadeStore = defineStore('lemonade', () => {
  // ── State ────────────────────────────────────────────────────────
  const loadedModels = ref([])
  const maxModels = ref(null)
  const version = ref(null)
  const lastUse = ref(new Map())
  const health = ref('down')      // optimistic 'up' on first 200
  const error = ref(null)
  const throughputMbps = ref(null)

  // /v1/stats — last-request snapshot from Lemonade native (PR-12
  // #179 also consumes this serverside for MetricsShim). Shape:
  //   { time_to_first_token, tokens_per_second, prompt_tokens,
  //     output_tokens, input_tokens }
  // Empty object until first successful poll. Mock-substituted via
  // `mockFetch` so dev/offline UI gets plausible numbers.
  const lastStats = ref({})

  // ── Polling lifecycle ────────────────────────────────────────────
  let timer = null
  let statsTimer = null
  let inFlight = false
  let statsInFlight = false
  let refCount = 0  // multiple callers (composable + views) -> single timer

  async function tick() {
    if (inFlight) return
    inFlight = true
    try {
      const res = await fetch(HEALTH_URL, { headers: { Accept: 'application/json' } })
      if (!res.ok) {
        health.value = 'down'
        error.value = `HTTP ${res.status}`
        return
      }
      let body
      try {
        body = await res.json()
      } catch (e) {
        health.value = 'degraded'
        error.value = 'unparseable /v1/health body'
        return
      }
      // /v1/health shape per lemonade docs (permissive — extra fields ok):
      //   { loaded: [{model_name, backend_url, last_use?}], max_loaded?,
      //     version?, throughput_mbps?, ... }
      const loaded = Array.isArray(body?.loaded) ? body.loaded : []
      loadedModels.value = loaded
      maxModels.value = (typeof body?.max_loaded === 'number') ? body.max_loaded : null
      version.value = typeof body?.version === 'string' ? body.version : null
      throughputMbps.value = (typeof body?.throughput_mbps === 'number')
        ? body.throughput_mbps
        : null
      // freshen lastUse map (preserve previous entries for models that
      // disappeared this tick — UI shows "last seen" decay)
      const next = new Map(lastUse.value)
      for (const m of loaded) {
        if (m && m.model_name && typeof m.last_use === 'number') {
          next.set(m.model_name, m.last_use)
        }
      }
      lastUse.value = next
      health.value = 'up'
      error.value = null
    } catch (e) {
      health.value = 'down'
      error.value = e?.message || String(e)
    } finally {
      inFlight = false
    }
  }

  async function tickStats() {
    if (statsInFlight) return
    statsInFlight = true
    try {
      // `mockFetch` falls back to MOCK_DATA on 404 / VITE_MOCK_LEMONADE
      // so the dashboard's derived "last TTFT / last decode tok/s" tiles
      // render in offline dev too.
      const res = await mockFetch(STATS_URL, { headers: { Accept: 'application/json' } })
      if (!res.ok) return  // soft-fail; keep previous snapshot
      const body = await res.json()
      if (body && typeof body === 'object') {
        lastStats.value = body
      }
    } catch {
      // soft-fail; /v1/stats is non-critical
    } finally {
      statsInFlight = false
    }
  }

  function init() {
    refCount += 1
    if (timer) return
    // immediate tick + interval; covers the "subscribed within 2s"
    // acceptance criteria.
    tick()
    timer = setInterval(tick, POLL_MS)
    tickStats()
    statsTimer = setInterval(tickStats, STATS_POLL_MS)
  }

  function stop() {
    refCount = Math.max(0, refCount - 1)
    if (refCount > 0) return
    if (timer) {
      clearInterval(timer)
      timer = null
    }
    if (statsTimer) {
      clearInterval(statsTimer)
      statsTimer = null
    }
  }

  function _forceTick() {
    // exposed for tests / manual refresh; safe to call without init().
    return tick()
  }

  // ── Getters ──────────────────────────────────────────────────────
  const loadedByName = computed(() => {
    const m = new Map()
    for (const entry of loadedModels.value) {
      if (entry && entry.model_name) m.set(entry.model_name, entry)
    }
    return m
  })

  function isLoaded(name) {
    if (!name) return false
    return loadedByName.value.has(name)
  }

  const throughput = computed(() => throughputMbps.value)

  return {
    // state
    loadedModels, maxModels, version, lastUse, health, error, lastStats,
    // getters
    loadedByName, throughput,
    // helpers
    isLoaded,
    // actions
    init, stop, _forceTick,
    // test-only
    _tickStats: tickStats,
  }
})
