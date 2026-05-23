/**
 * useNuclearEvictBanner — subscribe to /api/lemonade/events/stream and
 * surface every `nuclear_evict` event as a transient toast banner.
 *
 * Per ADR-0008 §3, the nuclear-evict escape valve is rare-but-visible:
 * the dashboard pops a warning toast for each occurrence so the
 * operator sees the eviction inline, without digging into the journal
 * panel. The toast auto-dismisses after 8s (longer than the usual 4s
 * default — operators need time to read the message).
 *
 * Mounted once at the App level; child views don't subscribe. The
 * EventSource auto-reconnects when lemond's log stream drops, so the
 * banner survives across daemon restarts.
 */
import { onMounted, onUnmounted } from 'vue'
import { useToastsStore } from '../stores/toasts.js'

const EVENTS_URL = '/api/lemonade/events/stream'
const TOAST_DURATION_MS = 8000

export function useNuclearEvictBanner() {
  const toasts = useToastsStore()
  let es = null

  function connect() {
    if (typeof window === 'undefined' || !window.EventSource) return
    try {
      es = new EventSource(EVENTS_URL)
    } catch (_err) {
      return
    }
    es.addEventListener('nuclear_evict', (evt) => {
      let payload = {}
      try {
        payload = JSON.parse(evt.data)
      } catch (_err) {
        payload = { message: String(evt.data) }
      }
      const msg = payload.message
        || 'Lemonade evicted all loaded models after a load failure.'
      toasts.warning(`Nuclear evict: ${msg}`, TOAST_DURATION_MS)
    })
    es.onerror = () => {
      // EventSource auto-reconnects on its own cadence — no action.
    }
  }

  function disconnect() {
    if (es) {
      try { es.close() } catch (_err) { /* noop */ }
      es = null
    }
  }

  onMounted(connect)
  onUnmounted(disconnect)

  return { disconnect }
}
