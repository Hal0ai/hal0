import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import { useEvents } from '../composables/useEvents.js'
import { useSystemStore } from './system.js'

/**
 * footer store — UI state for the expandable bottom dock.
 *
 * Owns:
 *   - expanded/height/active-tab persistence (localStorage)
 *   - derived rollups (slot tally, health dot, in-flight jobs ring)
 *   - hooks into the shared useEvents() ring for activity / jobs
 *
 * Tabs: activity | slots | logs | jobs
 * Logs sub-tabs: api | primary | embed | stt | tts | all (+ custom slots)
 */

const LS = {
  expanded: 'hal0:footer:expanded',
  height: 'hal0:footer:height',
  tab: 'hal0:footer:tab',
  logsSubtab: 'hal0:footer:logs:subtab',
  logsLevel: 'hal0:footer:logs:level',
}

const DEFAULT_HEIGHT = 320
const MIN_HEIGHT = 180
const MAX_VH = 0.7

const ACTIVE_SLOT_STATES = new Set(['running', 'ready', 'serving', 'idle'])
const HEALTHY_SLOT_STATES = new Set(['running', 'ready', 'serving', 'idle', 'unloaded', 'stopped'])

function lsGet(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    if (raw == null) return fallback
    if (typeof fallback === 'boolean') return raw === 'true'
    if (typeof fallback === 'number') {
      const n = Number(raw)
      return Number.isFinite(n) ? n : fallback
    }
    return raw
  } catch { return fallback }
}
function lsSet(key, val) {
  try { localStorage.setItem(key, String(val)) } catch {}
}

export const useFooterStore = defineStore('footer', () => {
  // ── Persisted UI state ──────────────────────────────────────────
  const expanded = ref(lsGet(LS.expanded, false))
  const height = ref(lsGet(LS.height, DEFAULT_HEIGHT))
  const tab = ref(lsGet(LS.tab, 'activity'))           // 'activity'|'slots'|'logs'|'jobs'
  const logsSubtab = ref(lsGet(LS.logsSubtab, 'api'))  // 'api'|'primary'|...|'all'
  const logsLevel = ref(lsGet(LS.logsLevel, ''))        // ''|'info'|'warn'|'error'

  watch(expanded, (v) => lsSet(LS.expanded, !!v))
  watch(height, (v) => lsSet(LS.height, Math.round(v)))
  watch(tab, (v) => lsSet(LS.tab, v))
  watch(logsSubtab, (v) => lsSet(LS.logsSubtab, v))
  watch(logsLevel, (v) => lsSet(LS.logsLevel, v))

  // ── Shared events ring ──────────────────────────────────────────
  const eventsApi = useEvents()
  const { events } = eventsApi

  // ── In-flight jobs derived from pull.* events ───────────────────
  // jobs keyed by job_id (or model id) so progress events update in place.
  const inFlightJobs = computed(() => {
    const map = new Map()
    for (const e of events.value) {
      if (!e.type || !e.type.startsWith('pull.')) continue
      const id = e.data?.job_id || e.data?.model_id || e.data?.model || e.id
      if (!id) continue
      const prev = map.get(id) || { id, model: e.data?.model_id || e.data?.model || id, state: 'queued', pct: 0, severity: 'info', message: '' }
      const next = { ...prev }
      next.message = e.message || prev.message
      if (e.type === 'pull.queued')      next.state = 'queued'
      if (e.type === 'pull.progress')    next.state = 'running'
      if (e.type === 'pull.completed')   { next.state = 'completed'; next.completedAt = e.ts }
      if (e.type === 'pull.failed')      { next.state = 'failed';    next.completedAt = e.ts; next.severity = 'error' }
      if (e.type === 'pull.cancelled')   { next.state = 'cancelled'; next.completedAt = e.ts }
      if (typeof e.data?.percent === 'number')   next.pct = e.data.percent
      if (typeof e.data?.downloaded === 'number') next.downloaded = e.data.downloaded
      if (typeof e.data?.total === 'number')      next.total = e.data.total
      map.set(id, next)
    }
    return [...map.values()]
  })

  const activeJobs = computed(() =>
    inFlightJobs.value.filter((j) => j.state === 'queued' || j.state === 'running'),
  )
  const recentFinishedJobs = computed(() => {
    return inFlightJobs.value
      .filter((j) => j.state === 'completed' || j.state === 'failed' || j.state === 'cancelled')
      .slice(-20)
      .reverse()
  })

  // ── Slot rollup (from systemStore) ──────────────────────────────
  const system = useSystemStore()

  const slotTally = computed(() => {
    const slots = system.slots || []
    const running = slots.filter((s) => ACTIVE_SLOT_STATES.has(s.status)).length
    return { running, total: slots.length }
  })

  /**
   * worstSlotDot — green when all healthy, amber when any non-healthy /
   * non-error, red when any error, grey when no data yet.
   */
  const worstSlotDot = computed(() => {
    const slots = system.slots || []
    if (slots.length === 0) return 'idle'
    let worst = 'ok'
    for (const s of slots) {
      const st = s.status || s.state
      if (st === 'error' || st === 'failed') return 'error'
      if (!HEALTHY_SLOT_STATES.has(st)) worst = 'warn'
    }
    return worst
  })

  /**
   * healthDot — overall: looks at API health (system.error), any failed
   * slots, and any failed-recent pulls.
   */
  const healthDot = computed(() => {
    if (system.error) return 'error'
    if (worstSlotDot.value === 'error') return 'error'
    // recent failed event in last 30s = amber
    const cutoff = Date.now() / 1000 - 30
    const hasRecentFail = events.value.some(
      (e) => e.severity === 'error' && (e.ts || 0) > cutoff,
    )
    if (hasRecentFail) return 'warn'
    if (worstSlotDot.value === 'warn') return 'warn'
    return 'ok'
  })

  /**
   * lastMeaningfulEvent — for the bar ticker. Filters out heartbeat-y
   * noise. Returns the most recent event with a non-empty message.
   */
  const lastMeaningfulEvent = computed(() => {
    for (let i = events.value.length - 1; i >= 0; i--) {
      const e = events.value[i]
      if (e?.message) return e
    }
    return null
  })

  // ── Actions ─────────────────────────────────────────────────────
  function toggleExpanded() { expanded.value = !expanded.value }
  function expand() { expanded.value = true }
  function collapse() { expanded.value = false }

  function setTab(name) {
    if (!['activity', 'slots', 'logs', 'jobs'].includes(name)) return
    tab.value = name
    if (!expanded.value) expanded.value = true
  }

  function cycleTab(dir = 1) {
    const tabs = ['activity', 'slots', 'logs', 'jobs']
    const idx = tabs.indexOf(tab.value)
    const next = tabs[(idx + dir + tabs.length) % tabs.length]
    tab.value = next
  }

  function setHeight(px) {
    const vhCap = Math.floor(window.innerHeight * MAX_VH)
    height.value = Math.max(MIN_HEIGHT, Math.min(vhCap, Math.round(px)))
  }

  function setLogsSubtab(name) { logsSubtab.value = name }
  function setLogsLevel(level) { logsLevel.value = level }

  return {
    // state
    expanded, height, tab, logsSubtab, logsLevel,
    // events
    events, eventsApi,
    // derived
    slotTally, worstSlotDot, healthDot, lastMeaningfulEvent,
    inFlightJobs, activeJobs, recentFinishedJobs,
    // actions
    toggleExpanded, expand, collapse, setTab, cycleTab, setHeight,
    setLogsSubtab, setLogsLevel,
    // constants exposed for handle clamping
    MIN_HEIGHT, MAX_VH, DEFAULT_HEIGHT,
  }
})
