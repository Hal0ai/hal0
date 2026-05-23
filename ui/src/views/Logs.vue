<script setup>
/**
 * Logs.vue — SSE-tailed journald viewer.
 *
 * Backend contract (Team C, see /api/logs and /api/logs/stream):
 *   GET /api/logs?unit=<u>&n=<N>&since=<ts>&level=<lvl>
 *     → { unit, lines: [..lines..], count } (best-effort; empty on
 *       hosts without journalctl)
 *   GET /api/logs/stream?unit=<u>&level=<lvl>&since=<ts>
 *     → SSE, each frame `data: "<JSON-encoded line>"`
 *
 * Controls:
 *   - Unit selector: defaults to hal0-api, plus one option per slot
 *     (hal0-slot@<name>) and a custom-unit text entry for power users.
 *   - Level filter: error | warning | info | debug (maps to journalctl
 *     --priority severities — selecting "info" includes everything info
 *     and more-severe, matching the backend's _LEVELS table).
 *   - Time range: last 5 min / 1 hr / 24 hr / custom (passes journalctl-
 *     compatible ``--since`` strings).
 *   - Freeze toggle: stops the SSE stream and keeps the viewport static
 *     so the user can scroll back through historical lines without new
 *     ones racing in.
 *
 * Line classification (line-error / line-warn / line-debug) reads the
 * line text since journalctl's `--output=cat` mode doesn't preserve
 * structured priority — best-effort, matches what `journalctl -u` shows
 * the user in a terminal.
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LemonadeJournalPanel from '../components/LemonadeJournalPanel.vue'

const system = useSystemStore()
const route = useRoute()
const router = useRouter()

// PR-14: two-tab surface — systemd journal (default, prior behaviour)
// and Lemonade daemon log. Tab state lives in ?tab=… so the URL is
// shareable + reload-stable, mirroring Agent.vue's pattern.
const TABS = ['systemd', 'lemonade']
const activeTab = computed(() => {
  const t = String(route.query.tab || 'systemd')
  return TABS.includes(t) ? t : 'systemd'
})
function selectTab(tab) {
  if (tab === activeTab.value) return
  router.replace({ query: { ...route.query, tab } })
}

const lines      = ref([])
const filter     = ref({
  unit: 'hal0-api',
  customUnit: '',
  level: '',         // '' = all
  range: '1h',       // '5m' | '1h' | '24h' | 'custom'
  customSince: '',
  text: '',
})
const connected  = ref(false)
const frozen     = ref(false)
const loading    = ref(false)
const error      = ref(null)
const logboxEl   = ref(null)
const autoScroll = ref(true)

let es = null

function lineClass(line) {
  // journalctl --output=cat strips priority, so classify on substring
  // matches the way the user would visually scan a terminal.
  if (/\bERROR\b|\bERR\b|\[ERROR\]/.test(line)) return 'line-error'
  if (/\bWARN(ING)?\b|\[WARN\]/.test(line))      return 'line-warn'
  if (/\bDEBUG\b|\[DEBUG\]/.test(line))          return 'line-debug'
  return ''
}

const filteredLines = computed(() => {
  if (!filter.value.text.trim()) return lines.value
  const q = filter.value.text.toLowerCase()
  return lines.value.filter((l) => l.toLowerCase().includes(q))
})

// All unit options exposed to the selector.
const unitOptions = computed(() => {
  const opts = [
    { value: 'hal0-api',      label: 'hal0-api' },
    { value: 'hal0-openwebui', label: 'hal0-openwebui' },
  ]
  for (const s of system.slots) {
    if (s.name) opts.push({ value: `hal0-slot@${s.name}`, label: `hal0-slot@${s.name}` })
  }
  opts.push({ value: '__custom__', label: 'Custom unit…' })
  return opts
})

const effectiveUnit = computed(() => {
  if (filter.value.unit === '__custom__') return filter.value.customUnit.trim()
  return filter.value.unit
})

// Build the `since` query param. journalctl accepts ISO timestamps and
// human strings like "5 min ago" — we pass the latter directly.
const sinceParam = computed(() => {
  const r = filter.value.range
  if (r === '5m')  return '5 minutes ago'
  if (r === '1h')  return '1 hour ago'
  if (r === '24h') return '24 hours ago'
  if (r === 'custom') return filter.value.customSince.trim() || null
  return null
})

function buildQuery(extra = {}) {
  const params = new URLSearchParams()
  params.set('unit', effectiveUnit.value)
  if (filter.value.level) params.set('level', filter.value.level)
  if (sinceParam.value)   params.set('since', sinceParam.value)
  for (const [k, v] of Object.entries(extra)) {
    if (v != null) params.set(k, String(v))
  }
  return params.toString()
}

async function loadHistorical() {
  // Don't query when there's no unit to ask about.
  if (!effectiveUnit.value) return
  loading.value = true
  error.value = null
  try {
    const data = await api(`/api/logs?${buildQuery({ n: 500 })}`)
    lines.value = Array.isArray(data?.lines) ? data.lines.slice() : []
    if (data?.hint) error.value = data.hint
  } catch (e) {
    lines.value = []
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function openStream() {
  closeStream()
  if (!effectiveUnit.value) return
  try {
    es = new EventSource(`/api/logs/stream?${buildQuery()}`)
    es.onopen = () => { connected.value = true }
    es.onmessage = (ev) => {
      if (frozen.value) return
      let line = ev.data
      // Backend wraps each line in JSON (`json.dumps(line)`) so clients
      // can JSON.parse to strip quotes / preserve embedded quotes.
      try { line = JSON.parse(line) } catch { /* leave as-is */ }
      lines.value.push(line)
      // Cap buffer so the page doesn't grow unbounded on a chatty unit.
      if (lines.value.length > 5000) lines.value = lines.value.slice(-5000)
      if (autoScroll.value) scrollToBottom()
    }
    es.onerror = () => { connected.value = false }
  } catch (e) {
    error.value = `EventSource failed: ${e.message}`
  }
}

function closeStream() {
  if (es) { es.close(); es = null }
  connected.value = false
}

async function reload() {
  closeStream()
  await loadHistorical()
  if (!frozen.value) openStream()
  scrollToBottom()
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
  autoScroll.value = scrollHeight - scrollTop - clientHeight < 40
}

function toggleFreeze() {
  frozen.value = !frozen.value
  if (frozen.value) {
    closeStream()
  } else {
    openStream()
  }
}

function clear() {
  lines.value = []
}

// Reconnect when any filter input changes. Debounce so typing a custom
// unit name doesn't fire a stream open per keystroke — wait for blur or
// 400 ms of idle.
let reloadTimer = null
function debouncedReload() {
  clearTimeout(reloadTimer)
  reloadTimer = setTimeout(reload, 400)
}

watch(
  () => [filter.value.unit, filter.value.customUnit, filter.value.level, filter.value.range, filter.value.customSince],
  () => debouncedReload(),
)

// Ensure slots are loaded so the unit selector can offer slot units.
onMounted(async () => {
  if (system.slots.length === 0) {
    await system.fetchStatus().catch(() => {})
  }
  reload()
})
onUnmounted(closeStream)
</script>

<template>
  <div class="logs-page">
    <PageHeader eyebrow="Journal" title="Logs" subtitle="Live systemd journal tail">
      <template v-if="activeTab === 'systemd'" #actions>
        <div class="status-dot-wrap" :title="connected ? 'SSE connected' : (frozen ? 'frozen' : 'disconnected')" aria-label="SSE connection status">
          <span class="dot" :class="frozen ? 'dot-frozen' : (connected ? 'dot-live' : 'dot-off')" aria-hidden="true" />
          <span class="dot-label">{{ frozen ? 'frozen' : (connected ? 'live' : 'offline') }}</span>
        </div>
        <button class="btn-secondary" type="button" @click="toggleFreeze">
          <template v-if="frozen">
            <svg width="13" height="13" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.14v14l11-7-11-7z"/></svg>
            Resume
          </template>
          <template v-else>
            <svg width="13" height="13" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>
            Freeze
          </template>
        </button>
        <button class="btn-secondary" type="button" @click="clear">Clear</button>
        <button class="btn-secondary" type="button" @click="reload">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" :class="{ spin: loading }" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
          </svg>
          Reload
        </button>
      </template>
    </PageHeader>

    <!-- Tabs: systemd journal (default) | Lemonade daemon log (PR-14). -->
    <nav class="tabs" role="tablist" aria-label="Log sources">
      <button
        v-for="t in TABS"
        :key="t"
        type="button"
        role="tab"
        :aria-selected="activeTab === t"
        :class="['tab', { active: activeTab === t }]"
        :data-testid="`logs-tab-${t}`"
        @click="selectTab(t)"
      >
        <span class="tab-label">{{ t === 'systemd' ? 'systemd' : 'Lemonade' }}</span>
      </button>
    </nav>

    <!-- systemd journal pane (existing surface) -->
    <div v-if="activeTab === 'systemd'" class="filters-bar">
      <label class="sr-only" for="log-unit-filter">Unit</label>
      <select id="log-unit-filter" v-model="filter.unit" class="filter-select">
        <option v-for="o in unitOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>
      <input
        v-if="filter.unit === '__custom__'"
        v-model="filter.customUnit"
        type="text"
        class="filter-input mono"
        placeholder="hal0-slot@foo"
        aria-label="Custom systemd unit"
      />

      <label class="sr-only" for="log-level-filter">Level</label>
      <select id="log-level-filter" v-model="filter.level" class="filter-select">
        <option value="">All levels</option>
        <option value="error">error</option>
        <option value="warning">warning+</option>
        <option value="info">info+</option>
        <option value="debug">debug+</option>
      </select>

      <label class="sr-only" for="log-range-filter">Range</label>
      <select id="log-range-filter" v-model="filter.range" class="filter-select">
        <option value="5m">Last 5 min</option>
        <option value="1h">Last 1 hr</option>
        <option value="24h">Last 24 hr</option>
        <option value="custom">Custom…</option>
      </select>
      <input
        v-if="filter.range === 'custom'"
        v-model="filter.customSince"
        type="text"
        class="filter-input mono"
        placeholder="2026-05-15 12:00 or '30 minutes ago'"
        aria-label="Custom since value"
      />

      <div class="search-wrap">
        <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" class="search-icon" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z"/>
        </svg>
        <input
          v-model="filter.text"
          class="filter-search"
          placeholder="Filter text…"
          aria-label="Filter log lines"
        />
      </div>

      <span class="line-count">{{ filteredLines.length }} / {{ lines.length }} lines</span>
    </div>

    <!-- Log box -->
    <div v-if="activeTab === 'systemd'" class="page-body">
      <div v-if="error" class="error-banner" role="alert">{{ error }}</div>

      <Card :padded="false" class="logbox-card">
        <div
          ref="logboxEl"
          class="logbox"
          @scroll="onScroll"
          role="log"
          aria-live="polite"
          aria-label="Log output"
        >
          <div v-if="loading" class="logbox-loading">Loading…</div>
          <div v-else-if="filteredLines.length === 0" class="logbox-empty">No log lines match the current filter.</div>
          <div
            v-for="(line, i) in filteredLines"
            :key="i"
            class="log-line"
            :class="lineClass(line)"
          >{{ line }}</div>
        </div>

        <div v-if="!autoScroll && !frozen" class="scroll-paused">
          <button type="button" class="scroll-resume-btn" @click="autoScroll = true; scrollToBottom()">
            ↓ Auto-scroll paused — click to resume
          </button>
        </div>
      </Card>
    </div>

    <!-- Lemonade daemon journal pane (PR-14, plan §11 + §2.2). -->
    <div v-else-if="activeTab === 'lemonade'" class="page-body lemonade-body">
      <LemonadeJournalPanel />
    </div>
  </div>
</template>

<style scoped>
.logs-page { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.page-body { padding: 0 24px 20px; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; }
.lemonade-body { padding: 16px 24px 20px; }

/* Tab bar mirrors Agent.vue so navigation feels uniform across the app. */
.tabs {
  display: flex;
  gap: 4px;
  padding: 0 24px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}
.tab {
  position: relative;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 12px 14px;
  background: transparent;
  border: none;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: -0.01em;
  cursor: pointer;
  text-transform: capitalize;
  transition: color 0.12s;
}
.tab:hover { color: var(--color-fg); }
.tab.active { color: var(--hal0-accent); }
.tab.active::after {
  content: '';
  position: absolute;
  left: 8px;
  right: 8px;
  bottom: -1px;
  height: 2px;
  background: var(--hal0-accent);
  border-radius: 2px 2px 0 0;
  box-shadow: 0 0 12px -2px var(--hal0-accent);
}
.tab:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: -2px;
}

.filters-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 24px;
  border-bottom: 1px solid var(--color-border);
  flex-wrap: wrap;
}
.filter-select,
.filter-input {
  padding: 5px 8px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  font-size: 12px;
  font-family: var(--font-mono);
  cursor: pointer;
  outline: none;
}
.filter-input { cursor: text; color: var(--color-fg); min-width: 220px; }
.filter-select:focus,
.filter-input:focus { border-color: var(--color-border-hi); }

.search-wrap { position: relative; }
.search-icon { position: absolute; left: 8px; top: 50%; transform: translateY(-50%); color: var(--color-fg-faint); pointer-events: none; }
.filter-search {
  padding: 5px 8px 5px 26px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 12px;
  font-family: var(--font-mono);
  outline: none;
  width: 180px;
  transition: border-color 0.1s;
}
.filter-search:focus { border-color: var(--color-border-hi); }
.filter-search::placeholder { color: var(--color-fg-faint); }

.line-count { font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-faint); margin-left: auto; }

.status-dot-wrap { display: flex; align-items: center; gap: 6px; }
.dot { width: 7px; height: 7px; border-radius: 50%; flex-shrink: 0; }
.dot-live   { background: var(--hal0-accent); box-shadow: 0 0 8px var(--hal0-accent); }
.dot-frozen { background: var(--color-warning); }
.dot-off    { background: var(--color-fg-faint); }
.dot-label  { font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-faint); text-transform: uppercase; letter-spacing: 0.06em; }

.error-banner { padding: 8px 14px; border-radius: var(--radius); background: color-mix(in oklch, var(--color-warning) 10%, var(--color-surface)); border: 1px solid color-mix(in oklch, var(--color-warning) 30%, transparent); color: var(--color-warning); font-size: 12px; font-family: var(--font-mono); }

.logbox-card { flex: 1; display: flex; flex-direction: column; position: relative; min-height: 0; }
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
.logbox-loading,
.logbox-empty { color: var(--color-fg-faint); padding: 8px 0; }
.log-line { color: var(--hal0-fg-dim); white-space: pre-wrap; word-break: break-all; }
.log-line.line-error { color: var(--color-danger); }
.log-line.line-warn  { color: var(--color-warning); }
.log-line.line-debug { color: var(--color-fg-faint); opacity: 0.7; }

.scroll-paused {
  position: absolute;
  bottom: 12px;
  left: 50%;
  transform: translateX(-50%);
}
.scroll-resume-btn {
  padding: 6px 14px;
  border-radius: var(--radius-lg);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border-hi);
  color: var(--color-fg-muted);
  font-size: 11.5px;
  font-family: var(--font-mono);
  cursor: pointer;
  backdrop-filter: blur(4px);
}
.scroll-resume-btn:hover { color: var(--color-fg); }

.btn-secondary { display: flex; align-items: center; gap: 6px; padding: 5px 11px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 11.5px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-secondary:hover { border-color: var(--color-border-hi); color: var(--color-fg); }
.spin { animation: spin 0.8s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }

.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0,0,0,0); white-space: nowrap; border: 0; }
</style>
