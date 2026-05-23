import { ref, onUnmounted } from 'vue'
import { api } from './useApi.js'

/**
 * useEvents — backfill /api/events?since=<id> then tail /api/events/stream.
 *
 * Backend contract:
 *   GET  /api/events?since=<id>&type=<glob>&severity=<info|warn|error>&limit=200
 *      → { events: [{id, ts, type, severity, source, message, data}], next_since }
 *   GET  /api/events/stream?since=<id>
 *      → SSE; each frame is `data: <event-json>`
 *
 * Local push() prepends client-only synthetic events (negative ids,
 * monotonically decreasing) — useful for "theme changed", "config saved",
 * etc. Frontend ring merges with backend ring keyed by id.
 *
 * Single shared instance — Footer owns the SSE; tabs read the same ref.
 */

const MAX_EVENTS = 500

let _singleton = null

function createInstance() {
  const events = ref([])           // newest LAST (chronological)
  const connected = ref(false)
  const error = ref(null)
  let es = null
  let lastBackendId = 0
  let nextSyntheticId = -1

  function appendOne(evt) {
    if (!evt || typeof evt !== 'object') return
    if (typeof evt.id === 'number' && evt.id > 0 && evt.id > lastBackendId) {
      lastBackendId = evt.id
    }
    events.value.push(evt)
    // Trim oldest. Cheap because push happens once per frame.
    if (events.value.length > MAX_EVENTS) {
      events.value = events.value.slice(-MAX_EVENTS)
    }
  }

  /** Inject a client-only event into the ring. */
  function push({ type, message, severity = 'info', data = null, source = 'ui' }) {
    const evt = {
      id: nextSyntheticId--,
      ts: Date.now() / 1000,
      type: String(type || 'ui.event'),
      severity,
      source,
      message: String(message || ''),
      data: data || {},
      synthetic: true,
    }
    appendOne(evt)
    return evt
  }

  async function backfill() {
    try {
      const data = await api(`/api/events?since=${lastBackendId}&limit=200`)
      const list = Array.isArray(data?.events) ? data.events : []
      for (const e of list) appendOne(e)
    } catch (e) {
      // Backend may not be live yet — log to error ref but keep running so
      // SSE can catch up later.
      error.value = e?.message || String(e)
    }
  }

  function connect() {
    disconnect()
    error.value = null
    try {
      es = new EventSource(`/api/events/stream?since=${lastBackendId}`)
    } catch (e) {
      error.value = e?.message || 'EventSource failed'
      return
    }
    es.onopen = () => { connected.value = true; error.value = null }
    es.onmessage = (evt) => {
      try {
        const parsed = JSON.parse(evt.data)
        appendOne(parsed)
      } catch { /* skip malformed frame */ }
    }
    es.onerror = () => {
      connected.value = false
      // EventSource auto-reconnects.
    }
  }

  function disconnect() {
    if (es) { try { es.close() } catch {} es = null }
    connected.value = false
  }

  async function start() {
    await backfill()
    connect()
  }

  function stop() {
    disconnect()
  }

  function clear() {
    events.value = []
  }

  return { events, connected, error, push, start, stop, clear, backfill }
}

/**
 * Returns the shared events instance. First caller mounts; subsequent
 * callers reuse the same ring + connection.
 */
export function useEvents() {
  if (!_singleton) _singleton = createInstance()
  return _singleton
}

/** Lifecycle helper for the Footer shell — auto stop on unmount only if
 * caller asks for it. Tabs that read events should NOT call this. */
export function useEventsLifecycle() {
  const inst = useEvents()
  onUnmounted(() => inst.stop())
  return inst
}
