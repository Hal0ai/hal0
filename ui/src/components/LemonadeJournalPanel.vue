<script setup>
/**
 * LemonadeJournalPanel.vue — PR-14 journal surface.
 *
 * Subscribes to ``/api/lemonade/logs/stream`` (the PR-11 SSE proxy of
 * lemond's ``/logs/stream`` WebSocket) and renders every parsed entry
 * the daemon emits. Companion to the nuclear-evict toast banner — the
 * banner surfaces the rare-but-visible escape valve; this panel is
 * the firehose operators page through when triaging.
 *
 * Backend contract (PR-11, src/hal0/api/routes/lemonade_logs.py):
 *   GET /api/lemonade/logs/stream
 *     → SSE; each ``data:`` frame is JSON of one lemond log entry,
 *       shape ``{line, severity, tag, timestamp, seq}`` per the
 *       ``hal0_lemonade_ws_protocol`` memory. The proxy already
 *       flattens ``logs.snapshot`` batches into per-entry yields, so
 *       the consumer only sees individual frames.
 *
 * Severity enum (lemond Trace|Debug|Info|Warning|Error|Fatal) maps to
 * three colour classes (debug/info/warn/error) so the visual scan
 * matches what operators see in the systemd journal tab. Unknown
 * severities fall through to the default ``log-line`` style.
 *
 * Buffer caps at 2000 lines to keep long sessions bounded — older
 * frames drop off the front as new arrive. Filter is a substring
 * match on ``line`` (case-insensitive). Auto-scroll defers to the
 * user: if they scroll back to read, new lines stop pinning to the
 * bottom until they click resume or reach the bottom again.
 */
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import Card from './Card.vue'

const LEMONADE_LOGS_URL = '/api/lemonade/logs/stream'
const BUFFER_CAP = 2000

const entries = ref([])
const connected = ref(false)
const filterText = ref('')
const autoScroll = ref(true)
const logboxEl = ref(null)

let es = null

/**
 * Map lemond's Severity enum onto the three-tier colour classes the
 * existing Logs.vue uses for visual consistency. Lowercase + trim so
 * a future lemond protocol bump (e.g. ``"INFO"`` vs ``"Info"``)
 * keeps working without an extra round-trip.
 */
function severityClass(sev) {
  const s = String(sev || '').trim().toLowerCase()
  if (s === 'error' || s === 'fatal') return 'log-line-error'
  if (s === 'warning' || s === 'warn') return 'log-line-warn'
  if (s === 'debug' || s === 'trace') return 'log-line-debug'
  return ''
}

const filteredEntries = computed(() => {
  const q = filterText.value.trim().toLowerCase()
  if (!q) return entries.value
  return entries.value.filter((e) => {
    const line = (e.line || e.message || e.text || '').toLowerCase()
    return line.includes(q)
  })
})

function entryText(entry) {
  // Lemond ships ``line``; older builds use ``message``/``text``.
  // Match what the backend's _extract_message tolerates so a protocol
  // shift doesn't blank the viewport.
  return entry.line || entry.message || entry.text || entry.msg || ''
}

function entryTag(entry) {
  const t = entry.tag
  return typeof t === 'string' && t.length > 0 ? t : ''
}

async function scrollToBottom() {
  await nextTick()
  if (logboxEl.value) {
    logboxEl.value.scrollTop = logboxEl.value.scrollHeight
  }
}

function onScroll() {
  if (!logboxEl.value) return
  const { scrollTop, scrollHeight, clientHeight } = logboxEl.value
  // Within 40px of bottom → resume auto-scroll. Matches the systemd
  // tab's threshold so both surfaces behave identically.
  autoScroll.value = scrollHeight - scrollTop - clientHeight < 40
}

function connect() {
  disconnect()
  if (typeof window === 'undefined' || !window.EventSource) return
  try {
    es = new EventSource(LEMONADE_LOGS_URL)
  } catch (_err) {
    return
  }
  es.onopen = () => { connected.value = true }
  es.onmessage = (ev) => {
    let entry
    try {
      entry = JSON.parse(ev.data)
    } catch (_err) {
      // Tolerate non-JSON frames by wrapping them as plain lines —
      // an upstream protocol regression should still render text.
      entry = { line: String(ev.data) }
    }
    if (!entry || typeof entry !== 'object') return
    entries.value.push(entry)
    if (entries.value.length > BUFFER_CAP) {
      entries.value = entries.value.slice(-BUFFER_CAP)
    }
    if (autoScroll.value) scrollToBottom()
  }
  es.onerror = () => {
    connected.value = false
    // EventSource auto-reconnects on its own cadence; no manual retry.
  }
}

function disconnect() {
  if (es) {
    try { es.close() } catch (_err) { /* noop */ }
    es = null
  }
  connected.value = false
}

function clearBuffer() {
  entries.value = []
}

onMounted(connect)
onUnmounted(disconnect)

defineExpose({ connect, disconnect, clearBuffer })
</script>

<template>
  <div class="lemonade-journal" data-testid="lemonade-journal">
    <div class="journal-toolbar">
      <div class="status-dot-wrap" :title="connected ? 'SSE connected' : 'disconnected'">
        <span
          class="dot"
          :class="connected ? 'dot-live' : 'dot-off'"
          aria-hidden="true"
        />
        <span class="dot-label">{{ connected ? 'live' : 'offline' }}</span>
      </div>

      <div class="search-wrap">
        <svg
          width="12"
          height="12"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
          stroke-width="2"
          class="search-icon"
          aria-hidden="true"
        >
          <path
            stroke-linecap="round"
            stroke-linejoin="round"
            d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z"
          />
        </svg>
        <input
          v-model="filterText"
          class="filter-search"
          placeholder="Filter lemond log…"
          aria-label="Filter Lemonade journal lines"
          data-testid="lemonade-journal-filter"
        />
      </div>

      <label class="autoscroll-toggle">
        <input
          v-model="autoScroll"
          type="checkbox"
          data-testid="lemonade-journal-autoscroll"
        />
        <span>Auto-scroll</span>
      </label>

      <button
        type="button"
        class="btn-secondary"
        data-testid="lemonade-journal-clear"
        @click="clearBuffer"
      >
        Clear
      </button>

      <span class="line-count" data-testid="lemonade-journal-count">
        {{ filteredEntries.length }} / {{ entries.length }} lines
      </span>
    </div>

    <Card :padded="false" class="logbox-card">
      <div
        ref="logboxEl"
        class="logbox"
        role="log"
        aria-live="polite"
        aria-label="Lemonade daemon log"
        @scroll="onScroll"
      >
        <div v-if="entries.length === 0" class="logbox-empty">
          No log entries yet. Lemonade may be idle or unreachable.
        </div>
        <div v-else-if="filteredEntries.length === 0" class="logbox-empty">
          No lines match the current filter.
        </div>
        <div
          v-for="(entry, i) in filteredEntries"
          :key="entry.seq != null ? entry.seq : i"
          class="log-line"
          :class="severityClass(entry.severity)"
          data-testid="lemonade-journal-line"
        >
          <span v-if="entryTag(entry)" class="log-tag">[{{ entryTag(entry) }}]</span>
          <span class="log-text">{{ entryText(entry) }}</span>
        </div>
      </div>
    </Card>
  </div>
</template>

<style scoped>
.lemonade-journal { display: flex; flex-direction: column; gap: 10px; min-height: 0; flex: 1; }

.journal-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.status-dot-wrap { display: flex; align-items: center; gap: 6px; }
.dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-live { background: var(--hal0-accent); box-shadow: 0 0 8px var(--hal0-accent); }
.dot-off { background: var(--color-fg-faint); }
.dot-label {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-faint);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.search-wrap { position: relative; }
.search-icon {
  position: absolute;
  left: 8px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--color-fg-faint);
  pointer-events: none;
}
.filter-search {
  padding: 5px 8px 5px 26px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 12px;
  font-family: var(--font-mono);
  outline: none;
  width: 200px;
  transition: border-color 0.1s;
}
.filter-search:focus { border-color: var(--color-border-hi); }
.filter-search::placeholder { color: var(--color-fg-faint); }

.autoscroll-toggle {
  display: flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  color: var(--color-fg-muted);
  cursor: pointer;
}
.autoscroll-toggle input[type='checkbox'] { cursor: pointer; }

.btn-secondary {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 5px 11px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-secondary:hover {
  border-color: var(--color-border-hi);
  color: var(--color-fg);
}

.line-count {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-faint);
  margin-left: auto;
}

.logbox-card { flex: 1; display: flex; flex-direction: column; min-height: 0; }
.logbox {
  flex: 1;
  overflow-y: auto;
  background: var(--hal0-bg-sunken);
  padding: 12px 16px;
  min-height: 400px;
  font-family: var(--font-mono);
  font-size: 11.5px;
  line-height: 1.55;
  font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1;
}
.logbox-empty { color: var(--color-fg-faint); padding: 8px 0; }
.log-line {
  color: var(--hal0-fg-dim);
  white-space: pre-wrap;
  word-break: break-all;
}
.log-line.log-line-error { color: var(--color-danger); }
.log-line.log-line-warn { color: var(--color-warning); }
.log-line.log-line-debug { color: var(--color-fg-faint); opacity: 0.7; }

.log-tag {
  display: inline-block;
  margin-right: 6px;
  color: var(--color-fg-faint);
  font-weight: 500;
}
</style>
