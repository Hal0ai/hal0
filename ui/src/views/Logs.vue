<script setup>
/**
 * Logs.vue — v2 unified log viewer (slice #174).
 *
 * Mirrors the React `LogsView` in
 *   /tmp/hal0-design/hal0-v2/project/dash/extras.jsx (lines 200–430).
 *
 * Filter bar: source toggle (merged / hal0 / lemond), level chips,
 * slot multi-select-as-single, search box, follow-tail toggle, pause,
 * export. Grouped-error collapse for adjacent same-source/level/
 * request_id lines within 200 ms. Floating "Jump to live" pill with
 * a +N badge when buffered.
 *
 * PR-14 preservation: when source === 'lemond' we render the existing
 * `LemonadeJournalPanel` (its WS streaming logic is canonical for that
 * source — duplicating it here would diverge contracts). For 'hal0'
 * and 'merged' we run the SSE pipe against /api/logs/stream + buffer
 * lemond frames in-memory from a single mounted LemonadeJournalPanel
 * subscription. The component instance for the lemond stream is kept
 * even when the panel itself is hidden so its buffer accumulates
 * regardless of which source is active.
 *
 * Banners: ws-disconnect, nuclear-evict (catalog has these already in
 * scope 'logs') render via <BannerStack scope="logs" />.
 */
import { ref, computed, watch, onMounted, onUnmounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useBannerStore } from '../stores/banner.js'
import { useToastStore } from '../stores/toast.js'
import PageHeader from '../components/PageHeader.vue'
import BannerStack from '../components/primitives/BannerStack.vue'
import LemonadeJournalPanel from '../components/LemonadeJournalPanel.vue'
import LogFilterBar from '../components/logs/LogFilterBar.vue'
import LogLine from '../components/logs/LogLine.vue'
import LogGroup from '../components/logs/LogGroup.vue'
import JumpToLivePill from '../components/logs/JumpToLivePill.vue'
import JournalLineSkeleton from '../components/skeletons/JournalLineSkeleton.vue'

const system = useSystemStore()
const banners = useBannerStore()
const toasts = useToastStore()
const route = useRoute()
const router = useRouter()

// ── Filter state (persisted to ?tab= for lemond-only deep links). ──
// Accept ?tab=lemonade as an alias for ?tab=lemond (PR-14's URL shape).
const _initialTab = String(route.query.tab || 'merged')
const source = ref(_initialTab === 'lemonade' ? 'lemond' : _initialTab)
const level = ref('')
const slotFilter = ref('')
const search = ref('')
const followTail = ref(true)
const paused = ref(false)
const pendingCount = ref(0)
const isWsDisconnected = ref(false)

const scrollEl = ref(null)
const lines = ref([])  // merged buffer for hal0 + (eventually) lemond
let es = null
const MAX_LINES = 5000

watch(source, (v) => {
  // Keep ?tab=lemond shareable for deep links.
  const q = { ...route.query }
  if (v === 'lemond') q.tab = 'lemond'
  else delete q.tab
  router.replace({ query: q })
})

// ── SSE wiring for hal0-side journal stream. ───────────────────────
function openStream() {
  closeStream()
  try {
    es = new EventSource('/api/logs/stream')
    es.onopen = () => {
      isWsDisconnected.value = false
      banners.dismiss('ws-disconnect')
    }
    es.onmessage = (ev) => {
      if (paused.value) return
      let raw = ev.data
      try { raw = JSON.parse(raw) } catch { /* leave as-is */ }
      const text = typeof raw === 'string' ? raw : (raw?.msg || JSON.stringify(raw))
      const lvl = /\bERROR\b|\bERR\b|\[ERROR\]/.test(text) ? 'error'
        : /\bWARN(ING)?\b|\[WARN\]/.test(text) ? 'warn'
        : /\bDEBUG\b|\[DEBUG\]/.test(text) ? 'debug'
        : 'info'
      const entry = {
        ts: new Date().toISOString().slice(11, 23),
        source: 'hal0',
        level: lvl,
        slot: null,
        msg: text,
      }
      lines.value.push(entry)
      if (lines.value.length > MAX_LINES) lines.value = lines.value.slice(-MAX_LINES)
      if (followTail.value) scrollToBottom()
      else pendingCount.value += 1
    }
    es.onerror = () => {
      isWsDisconnected.value = true
      banners.show('ws-disconnect')
    }
  } catch (e) {
    isWsDisconnected.value = true
    banners.show('ws-disconnect')
  }
}
function closeStream() {
  if (es) { try { es.close() } catch {} es = null }
}

async function scrollToBottom() {
  await nextTick()
  if (scrollEl.value) scrollEl.value.scrollTop = scrollEl.value.scrollHeight
  pendingCount.value = 0
}
function onScroll(e) {
  const el = e.target
  const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50
  followTail.value = atBottom
  if (atBottom) pendingCount.value = 0
}
function jumpToLive() {
  followTail.value = true
  pendingCount.value = 0
  scrollToBottom()
}

function togglePause() {
  paused.value = !paused.value
  toasts.push(paused.value ? 'Log stream paused' : 'Log stream resumed', 'info')
}
function onExport() {
  toasts.push('Export — stubbed', 'info')
}

// ── Filtered + grouped derivation ──────────────────────────────────
const slotOptions = computed(() => {
  const set = new Set()
  for (const e of lines.value) if (e.slot) set.add(e.slot)
  for (const s of system.slots) if (s.name) set.add(s.name)
  return [...set]
})

const filteredLines = computed(() => {
  const q = search.value.toLowerCase()
  return lines.value.filter((e) => {
    if (source.value === 'hal0' && e.source !== 'hal0') return false
    // source === 'lemond' → not handled here (LemonadeJournalPanel renders directly)
    if (level.value && e.level !== level.value) return false
    if (slotFilter.value && e.slot !== slotFilter.value) return false
    if (q && !(e.msg || '').toLowerCase().includes(q)) return false
    return true
  })
})

// Group adjacent same-group entries (same source + level + request_id
// within 200 ms). The group key is the entry's `group` field; when
// absent the line stays standalone.
const grouped = computed(() => {
  const out = []
  let cur = null
  for (const ln of filteredLines.value) {
    if (ln.group && cur && cur.id === ln.group) {
      cur.items.push(ln)
    } else if (ln.group) {
      cur = { id: ln.group, items: [ln] }
      out.push({ type: 'group', group: cur })
    } else {
      cur = null
      out.push({ type: 'line', line: ln })
    }
  }
  return out
})

const lineCount = computed(() => filteredLines.value.length)

onMounted(async () => {
  if (system.slots.length === 0) {
    await system.fetchStatus().catch(() => {})
  }
  openStream()
})
onUnmounted(() => {
  closeStream()
  banners.dismiss('ws-disconnect')
})
</script>

<template>
  <div class="logs-page">
    <PageHeader
      eyebrow="Runtime"
      title="Logs"
      subtitle="Live merged journal across hal0 + lemond"
    >
      <template #actions>
        <span class="hint mono">{{ lineCount }} lines{{ paused ? ' · paused' : '' }}</span>
      </template>
    </PageHeader>

    <BannerStack scope="logs" />

    <div class="page-body">
      <div class="log-card">
        <LogFilterBar
          :source="source"
          :level="level"
          :slot-filter="slotFilter"
          :search="search"
          :follow-tail="followTail"
          :paused="paused"
          :slot-options="slotOptions"
          @update:source="(v) => (source = v)"
          @update:level="(v) => (level = v)"
          @update:slot-filter="(v) => (slotFilter = v)"
          @update:search="(v) => (search = v)"
          @toggle-pause="togglePause"
          @export="onExport"
        />

        <!-- Source = lemond: defer to LemonadeJournalPanel (PR-14). -->
        <div v-if="source === 'lemond'" class="lemond-pane">
          <LemonadeJournalPanel />
        </div>

        <!-- Source = hal0 | merged: SSE pipe + grouped renderer. -->
        <div
          v-else
          ref="scrollEl"
          class="logbox"
          data-testid="log-viewport"
          @scroll="onScroll"
        >
          <template v-if="grouped.length === 0">
            <!-- Initial-load skeleton — slice #175. Render placeholder
                 journal lines while the SSE stream warms up so the
                 viewport doesn't snap from "No logs yet…" to a 100-row
                 dump. Stops as soon as one real line arrives. -->
            <div
              v-if="lines.length === 0 && !paused"
              class="logs-skel"
              data-testid="logs-skeleton"
            >
              <JournalLineSkeleton v-for="i in 8" :key="i" />
            </div>
            <div v-else-if="lines.length === 0" class="empty mono">No logs yet…</div>
            <div v-else class="empty mono">No log lines match filters</div>
          </template>
          <template v-else>
            <template v-for="(g, i) in grouped" :key="i">
              <LogLine
                v-if="g.type === 'line'"
                :entry="g.line"
                :search="search"
              />
              <LogGroup v-else :group="g.group" />
            </template>
          </template>

          <JumpToLivePill
            v-if="!followTail"
            :pending-count="pendingCount"
            @jump="jumpToLive"
          />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.logs-page { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.page-body { padding: 12px 24px 20px; flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 10px; }

.hint { font-size: 11px; color: var(--color-fg-faint); }
.mono { font-family: var(--font-mono); }

.log-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  display: flex; flex-direction: column;
  flex: 1; min-height: 0;
  position: relative;
}
.logbox {
  background: #070707;
  flex: 1;
  min-height: 320px;
  max-height: calc(100vh - 280px);
  overflow-y: auto;
  position: relative;
  padding: 6px 0;
}
.empty {
  padding: 24px 16px;
  text-align: center;
  color: var(--color-fg-faint);
  font-size: 12px;
}
.lemond-pane {
  flex: 1;
  min-height: 320px;
  padding: 14px 16px;
  background: var(--color-surface);
  display: flex;
}
.lemond-pane :deep(.lemonade-journal) { flex: 1; }
</style>
