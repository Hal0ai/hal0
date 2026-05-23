<script setup>
/**
 * Footer.vue — v2 dash chrome (slice #168).
 *
 * Two-line collapsed footer:
 *   Line 1 (chip row, ~26px) — lemond:<state> · throughput · loaded ·
 *                              NPU coresident · queued + journal toggle
 *                              + update-available pill.
 *   Line 2 (~22px) — last-3 journal entries, mono, single-line ellipsis.
 *
 * Journal expand pane (slides up from the footer when toggled open):
 *   ~30 lines mono, source filter (merged / hal0 / lemond), search input
 *   with amber inline highlight on matches, empty-state link to clear
 *   filters, and an "Open full logs →" jump to /logs. Persisted in
 *   sessionStorage as `hal0:journal-pane`.
 *
 * Data: subscribes to /api/events/stream via useEvents() — the same ring
 * the v1 Logs view uses. Maintained as a rolling 30-line buffer.
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useEvents, useEventsLifecycle } from '../composables/useEvents.js'
import { useLemonadeStore } from '../stores/lemonade.js'
import { useSystemStore } from '../stores/system.js'

const router    = useRouter()
const lemonade  = useLemonadeStore()
const system    = useSystemStore()

// Own the SSE lifecycle here (App.vue mounts <Footer/> once, so this
// stays a single connection per session).
const eventsApi = useEventsLifecycle()
const { events: rawEvents } = useEvents()

// ── sessionStorage-backed expand state ────────────────────────────
const SS_KEY = 'hal0:journal-pane'
function readExpanded() {
  try {
    return sessionStorage.getItem(SS_KEY) === '1'
  } catch { return false }
}
const expanded = ref(readExpanded())
watch(expanded, (v) => {
  try { sessionStorage.setItem(SS_KEY, v ? '1' : '0') } catch { /* sandbox */ }
})

function toggle() { expanded.value = !expanded.value }
function collapse() { expanded.value = false }

// ── Journal projection ────────────────────────────────────────────
// Rolling buffer view onto the shared events ring. Each entry is
// remapped to {ts, source, level, msg} so the line markup is dumb.
function deriveSource(evt) {
  const src = String(evt.source || '').toLowerCase()
  if (src.includes('lemond') || src.includes('lemonade')) return 'lemond'
  if (src === 'ui' || src === '') return 'hal0'
  return 'hal0'
}
function deriveLevel(evt) {
  const s = String(evt.severity || '').toLowerCase()
  if (s === 'error' || s === 'err') return 'error'
  if (s === 'warn' || s === 'warning') return 'warn'
  if (s === 'ok' || s === 'success') return 'ok'
  return 'info'
}
function fmtTs(ts) {
  if (!ts) return ''
  const d = new Date((typeof ts === 'number' ? ts : Number(ts)) * 1000)
  if (isNaN(d.getTime())) return String(ts)
  const pad = (n, w = 2) => String(n).padStart(w, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}.${pad(d.getMilliseconds(), 3)}`
}

const journal = computed(() => {
  const out = []
  const list = rawEvents.value || []
  // Take the LAST 30 entries (rolling buffer).
  const tail = list.slice(-30)
  for (const e of tail) {
    out.push({
      id: e.id,
      ts: fmtTs(e.ts),
      source: deriveSource(e),
      level: deriveLevel(e),
      msg: String(e.message || ''),
    })
  }
  return out
})

const last3 = computed(() => journal.value.slice(-3))

// ── Pane filter + search ──────────────────────────────────────────
const paneSrc = ref('merged')
const paneQ   = ref('')

const filtered = computed(() => {
  return journal.value.filter((e) => {
    if (paneSrc.value !== 'merged' && e.source !== paneSrc.value) return false
    if (paneQ.value && !e.msg.toLowerCase().includes(paneQ.value.toLowerCase())) return false
    return true
  })
})

function clearFilters() {
  paneSrc.value = 'merged'
  paneQ.value = ''
}

function highlightHTML(text, q) {
  if (!q) return text
  const i = text.toLowerCase().indexOf(q.toLowerCase())
  if (i < 0) return text
  // Build a tiny tagged-array for v-html safe rendering.
  const esc = (s) => s.replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]))
  return `${esc(text.slice(0, i))}<mark class="hl">${esc(text.slice(i, i + q.length))}</mark>${esc(text.slice(i + q.length))}`
}

// ── Update-available pill (from /api/status) ──────────────────────
const updateAvailable = computed(() => !!system.status?.update_available)

// ── Chip row data ─────────────────────────────────────────────────
const lemondState = computed(() => lemonade.health)
const throughput  = computed(() => {
  const v = lemonade.throughput
  return (typeof v === 'number' && v >= 0) ? v.toFixed(1) : null
})
const loadedCount = computed(() => lemonade.loadedModels?.length ?? 0)
const maxModels   = computed(() => lemonade.maxModels ?? '—')
const queued      = computed(() => 0)   // Lemonade has no queue endpoint yet (v0.2.x)

const npuCoresident = computed(() => {
  // Coresident == any model has a backend_url that references a
  // non-default port. Conservative heuristic: when more than one model
  // is loaded we treat it as coresident (matches the v0.3 chip's intent).
  return loadedCount.value >= 2
})

// ── Routes ───────────────────────────────────────────────────────
function openFullLogs() {
  router.push({ path: '/logs' })
}

// ── Keybinds (Alt+~ to toggle, Esc to collapse) ──────────────────
function isTextInput(el) {
  if (!el) return false
  const tag = (el.tagName || '').toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if (el.isContentEditable) return true
  return false
}
function onKey(e) {
  if (e.altKey && (e.key === '`' || e.key === '~' || e.code === 'Backquote')) {
    if (isTextInput(e.target)) return
    e.preventDefault()
    toggle()
    return
  }
  if (e.key === 'Escape' && expanded.value) {
    collapse()
  }
}

onMounted(async () => {
  await eventsApi.start()
  window.addEventListener('keydown', onKey)
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
})

defineExpose({ toggle, collapse })
</script>

<template>
  <div
    class="footer"
    :class="{ expanded }"
    role="contentinfo"
    aria-label="Runtime footer"
  >
    <!-- Expanded journal pane: slides up from the footer line. -->
    <transition name="foot-pane">
      <div v-if="expanded" class="foot-pane" data-testid="foot-pane">
        <div class="foot-pane-h mono">
          <span>Live journal</span>
          <span class="ct">· {{ filtered.length }} / {{ journal.length }}</span>
          <div class="foot-pane-filter mono" role="tablist" aria-label="Journal source filter">
            <button
              v-for="opt in ['merged', 'hal0', 'lemond']"
              :key="opt"
              type="button"
              role="tab"
              :aria-selected="paneSrc === opt"
              class="foot-pane-chip"
              :class="{ on: paneSrc === opt }"
              :data-testid="`foot-pane-filter-${opt}`"
              @click="paneSrc = opt"
            >{{ opt }}</button>
          </div>
          <input
            v-model="paneQ"
            class="foot-pane-search mono"
            placeholder="search…"
            aria-label="Search journal"
            data-testid="foot-pane-search"
          />
          <span class="foot-pane-meta">
            <a href="#" class="foot-pane-link" data-testid="foot-pane-open-logs" @click.prevent="openFullLogs">Open full logs →</a>
          </span>
        </div>
        <div class="foot-pane-body" data-testid="foot-pane-body">
          <div
            v-if="filtered.length === 0"
            class="foot-pane-empty"
            data-testid="foot-pane-empty"
          >
            No journal entries match.
            <span class="foot-pane-clear" @click="clearFilters">Clear filters</span>
          </div>
          <div
            v-for="(e, i) in filtered"
            v-else
            :key="`${e.id}-${i}`"
            class="foot-line"
            :class="e.level"
          >
            <span class="ts">{{ e.ts }}</span>
            <span class="sl" :class="e.source">[{{ e.source }}]</span>
            <span class="lvl">{{ e.level }}</span>
            <span class="msg" v-html="highlightHTML(e.msg, paneQ)" />
          </div>
        </div>
      </div>
    </transition>

    <!-- Chip row (always visible) -->
    <div class="foot-chips" data-testid="foot-chips">
      <div class="foot-chip up" :class="{ up: lemondState === 'up', warn: lemondState === 'degraded', err: lemondState === 'down' }">
        <span class="dot" />
        <span class="k">lemond:</span>
        <span class="v" data-testid="foot-chip-lemond">{{ lemondState }}</span>
      </div>
      <div v-if="throughput" class="foot-chip">
        <span class="k">throughput</span>
        <span class="v num">{{ throughput }} MB/s</span>
      </div>
      <div class="foot-chip">
        <span class="k">loaded</span>
        <span class="v num" data-testid="foot-chip-loaded">{{ loadedCount }}/{{ maxModels }}</span>
      </div>
      <div v-if="npuCoresident" class="foot-chip npu">
        <span class="dot" />
        <span class="k">npu</span>
        <span class="v">coresident</span>
      </div>
      <div v-if="queued > 0" class="foot-chip">
        <span class="k">queued</span>
        <span class="v num">{{ queued }}</span>
      </div>
      <div v-if="updateAvailable" class="foot-chip accent" data-testid="foot-chip-update">
        <span class="k">●</span>
        <span class="v">update available</span>
      </div>
      <button
        class="foot-toggle"
        type="button"
        :aria-expanded="expanded"
        aria-label="Toggle journal pane"
        data-testid="foot-toggle"
        @click="toggle"
      >
        <span class="caret" :class="{ rot: expanded }">⌃</span>
        <span>journal</span>
      </button>
    </div>

    <!-- Last-3 journal row (collapsed peek) -->
    <div class="foot-journal mono" data-testid="foot-journal-peek">
      <span
        v-for="(e, i) in last3"
        :key="`${e.id}-${i}`"
        class="ent"
        :class="e.level"
      >
        <span class="ts">{{ e.ts }}</span>
        <span class="sl">[{{ e.source }}]</span>
        <span class="ar">·</span>
        <span class="msg">{{ e.msg }}</span>
      </span>
      <span v-if="last3.length === 0" class="ent empty">no recent events</span>
    </div>
  </div>
</template>

<style scoped>
.footer {
  grid-column: 1 / -1;
  grid-row: 3;
  border-top: 1px solid var(--color-border);
  background: var(--color-bg);
  display: flex;
  flex-direction: column;
  font-family: var(--font-mono);
  font-size: 11px;
  position: relative;
  z-index: 25;
}

/* ── Chip row ──────────────────────────────────────────────────── */
.foot-chips {
  display: flex;
  align-items: center;
  height: 26px;
  padding: 0 14px;
  border-bottom: 1px solid var(--color-border);
  overflow-x: auto;
  white-space: nowrap;
}
.foot-chip {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  padding: 0 14px;
  height: 26px;
  border-right: 1px solid var(--color-border);
  color: var(--color-fg-muted);
}
.foot-chip:first-child { padding-left: 0; }
.foot-chip:last-child { border-right: none; }
.foot-chip .k { color: var(--color-fg-faint); }
.foot-chip .v { color: var(--color-fg); }
.foot-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentColor;
  box-shadow: 0 0 6px currentColor;
}
.foot-chip.up { color: var(--color-success); }
.foot-chip.warn { color: var(--color-warn, #e8b94e); }
.foot-chip.err { color: var(--color-danger); }
.foot-chip.npu { color: var(--dev-npu, #c084fc); }
.foot-chip.accent {
  color: var(--hal0-accent);
  border-left: 1px solid color-mix(in srgb, var(--hal0-accent) 35%, transparent);
}

.foot-toggle {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--color-fg-muted);
  padding: 0 12px;
  height: 26px;
  cursor: pointer;
  background: transparent;
  border: none;
  border-left: 1px solid var(--color-border);
}
.foot-toggle:hover { color: var(--hal0-accent); }
.foot-toggle .caret {
  display: inline-block;
  transition: transform 0.18s ease;
}
.foot-toggle .caret.rot { transform: rotate(180deg); }

/* ── Last-3 peek row ──────────────────────────────────────────── */
.foot-journal {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 0 14px;
  height: 22px;
  overflow-x: hidden;
  font-size: 10.5px;
  color: var(--color-fg-faint);
  white-space: nowrap;
}
.foot-journal .ent {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.foot-journal .ent.empty { color: var(--color-fg-faint); font-style: italic; }
.foot-journal .ent .ts { color: var(--color-fg-faint); }
.foot-journal .ent .sl { color: var(--color-fg-muted); }
.foot-journal .ent.ok .sl { color: var(--color-success); }
.foot-journal .ent.warn .sl { color: var(--color-warn, #e8b94e); }
.foot-journal .ent.error .sl { color: var(--color-danger); }
.foot-journal .ent .ar { color: var(--color-fg-faint); }
.foot-journal .ent .msg {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 60ch;
}

/* ── Expanded pane (slides up) ────────────────────────────────── */
.foot-pane {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 100%;
  height: 320px;
  background: var(--color-surface, #0f0f0f);
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
  box-shadow: 0 -12px 32px -8px rgba(0, 0, 0, 0.6);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  z-index: 50;
}
.foot-pane-enter-active, .foot-pane-leave-active {
  transition: transform 0.22s cubic-bezier(0.22, 1, 0.36, 1), opacity 0.18s ease;
}
.foot-pane-enter-from, .foot-pane-leave-to {
  transform: translateY(20%);
  opacity: 0;
}

.foot-pane-h {
  padding: 8px 16px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg);
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--color-fg-muted);
  flex-shrink: 0;
}
.foot-pane-h .ct { color: var(--color-fg-faint); }
.foot-pane-meta { margin-left: auto; }
.foot-pane-link {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--hal0-accent);
  text-transform: none;
  letter-spacing: 0;
  cursor: pointer;
  text-decoration: none;
}
.foot-pane-link:hover { text-decoration: underline; }

.foot-pane-filter {
  display: inline-flex;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  overflow: hidden;
  margin-left: 6px;
}
.foot-pane-chip {
  padding: 3px 9px;
  background: transparent;
  border: none;
  border-right: 1px solid var(--color-border);
  color: var(--color-fg-faint);
  font-family: var(--font-mono);
  font-size: 10.5px;
  cursor: pointer;
  text-transform: lowercase;
  letter-spacing: 0;
}
.foot-pane-chip:last-child { border-right: none; }
.foot-pane-chip.on {
  color: var(--hal0-accent);
  background: color-mix(in srgb, var(--hal0-accent) 12%, transparent);
}
.foot-pane-chip:hover { color: var(--color-fg); }

.foot-pane-search {
  background: var(--color-surface, #1a1a1a);
  border: 1px solid var(--color-border);
  color: var(--color-fg);
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 3px 8px;
  border-radius: 3px;
  margin-left: 6px;
  width: 140px;
  outline: none;
  text-transform: lowercase;
  letter-spacing: 0;
}
.foot-pane-search:focus { border-color: var(--hal0-accent); }

.foot-pane-body {
  flex: 1;
  overflow-y: auto;
  padding: 6px 0;
  font-family: var(--font-mono);
  font-size: 11px;
  line-height: 1.55;
}
.foot-pane-empty {
  padding: 24px;
  text-align: center;
  color: var(--color-fg-faint);
  font-family: var(--font-mono);
  font-size: 11.5px;
}
.foot-pane-clear {
  color: var(--hal0-accent);
  cursor: pointer;
  margin-left: 6px;
}
.foot-pane-clear:hover { text-decoration: underline; }

.foot-line {
  padding: 1px 16px;
  display: grid;
  grid-template-columns: 96px 80px 50px 1fr;
  gap: 10px;
  border-left: 2px solid transparent;
}
.foot-line:hover { background: rgba(255, 255, 255, 0.02); }
.foot-line.warn  { border-left-color: var(--color-warn, #e8b94e); }
.foot-line.error { border-left-color: var(--color-danger); }
.foot-line .ts { color: var(--color-fg-faint); }
.foot-line .sl.lemond { color: var(--dev-vulkan, #7fb8ff); }
.foot-line .sl.hal0 { color: var(--hal0-accent); }
.foot-line .lvl { color: var(--color-fg-muted); }
.foot-line.ok    .lvl { color: var(--color-success); }
.foot-line.warn  .lvl { color: var(--color-warn, #e8b94e); }
.foot-line.error .lvl { color: var(--color-danger); }
.foot-line .msg {
  color: var(--color-fg-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.foot-line .msg :deep(mark.hl) {
  background: color-mix(in srgb, var(--hal0-accent) 22%, transparent);
  color: var(--hal0-accent);
  padding: 0 2px;
  border-radius: 2px;
}

/* ── Mobile <720 hides whole footer (BottomTabs replaces) ─────── */
@media (max-width: 719px) {
  .footer { display: none; }
}
</style>
