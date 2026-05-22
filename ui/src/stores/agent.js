/**
 * stores/agent.js — Pinia store backing Phase 8's bundled-agent UX.
 *
 * One subscription, two surfaces. The /agent inbox tab and the header
 * bell modal both render off this store, so a single SSE EventSource is
 * shared. Per ADR-0004 §5 the bell is canonical and the inbox tab is
 * the convenience surface — both pulling from one place keeps "pending
 * count" honest across the dashboard.
 *
 * State
 * -----
 *   installed   — list of {name, installed_at, status, data_dir, config_path}
 *                 from GET /api/agents. v0.2 single-pick means 0 or 1.
 *   status      — derived rollup: 'none' | 'installed' | 'broken'.
 *   pending     — ApprovalEntry rows from GET /api/agent/approvals (and
 *                 SSE-driven mutations from /api/agent/approvals/events).
 *   activity    — recent MCP audit rows from GET /api/agents/{name}/activity.
 *
 * SSE wiring
 * ----------
 * connectSse() opens a single EventSource against
 * /api/agent/approvals/events. Frames carry {kind, entry}:
 *   - snapshot   — backfill on connect; replaces local pending list
 *   - enqueued   — append entry
 *   - approved   — update entry state
 *   - denied     — update entry state
 *   - executed   — remove from pending (entry settled OK)
 *   - failed     — remove from pending (entry settled with error)
 *
 * Reconnect is exponential backoff capped at 30s. Backend's keep-alive
 * comment frames (": keepalive\n\n") are silently swallowed by the
 * EventSource API — no app-side handling needed.
 */
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { api } from '../composables/useApi.js'

const SSE_URL = '/api/agent/approvals/events'
const SSE_BACKOFF_BASE_MS = 1000
const SSE_BACKOFF_MAX_MS = 30_000

export const useAgentStore = defineStore('agent', () => {
  // ── State ────────────────────────────────────────────────────────
  const installed = ref([])           // list of AgentRecord projections
  const pending = ref([])             // list of ApprovalEntry projections
  const activity = ref([])            // recent audit rows
  const loading = ref(false)
  const error = ref(null)

  // Pulse flag — flipped true briefly when a new approval lands via SSE
  // so the header bell can play an attention animation without us having
  // to re-render the whole list.
  const newApprovalPulse = ref(0)

  // SSE handle + backoff bookkeeping. eventSource is null when no socket
  // is open (either pre-connect or after dispose).
  let eventSource = null
  let reconnectTimer = null
  let backoffMs = SSE_BACKOFF_BASE_MS
  let disposed = false

  // ── Derived ──────────────────────────────────────────────────────
  // v0.2 is single-pick — there's at most one bundled agent. The store
  // still models `installed` as a list because the backend returns it
  // that way and the Phase 9 multi-agent expansion will trade the list
  // unchanged.
  const currentAgent = computed(() => installed.value[0] ?? null)

  /**
   * pendingForResource(kind, target) — find pending approval entries
   * whose tool + args matches a given dashboard row.
   *
   *   kind   — 'model' | 'slot' | 'capability'
   *   target — the row's identifier (model id / slot name / capability key)
   *
   * Returns an array of matching ApprovalEntry projections. Used by
   * Models.vue / Slots.vue / Capabilities.vue to render the "pending:
   * <op>" inline chip per ADR-0004 §5 (inline indicators, bell is
   * canonical).
   */
  function pendingForResource(kind, target) {
    if (!target) return []
    return pending.value.filter((entry) => {
      const tool = entry.tool || ''
      const args = entry.args || {}
      if (kind === 'model') {
        // model_pull / model_delete carry the id in args.model_id or
        // args.id depending on which route the MCP server translated to.
        if (!tool.startsWith('model_')) return false
        return args.model_id === target || args.id === target || args.name === target
      }
      if (kind === 'slot') {
        if (!tool.startsWith('slot_') && tool !== 'capability_set') return false
        return args.slot === target || args.name === target
      }
      if (kind === 'capability') {
        if (tool !== 'capability_set' && !tool.startsWith('capability_')) return false
        // target encoding "slot/child" matches the orchestrator path.
        const slot = args.slot
        const child = args.child
        if (!slot) return false
        const key = child ? `${slot}/${child}` : slot
        return key === target
      }
      return false
    })
  }

  const shape = computed(() => {
    const a = currentAgent.value
    if (!a) return null
    // pi-coder is CLI, hermes is service. Future bundled agents land via
    // ADR-0004 §6 (shim-first) so the lookup table grows here.
    if (a.name === 'pi-coder') return 'cli'
    if (a.name === 'hermes') return 'service'
    return null
  })

  const status = computed(() => {
    const a = currentAgent.value
    if (!a) return 'none'
    return a.status || 'unknown'
  })

  const pendingCount = computed(() => pending.value.length)

  // ── Actions: installed/status ────────────────────────────────────
  async function fetchInstalled() {
    loading.value = true
    error.value = null
    try {
      const r = await api('/api/agents')
      installed.value = Array.isArray(r?.agents) ? r.agents : []
    } catch (e) {
      error.value = e?.message || String(e)
      installed.value = []
    } finally {
      loading.value = false
    }
  }

  async function install(name, { switchAgent = false } = {}) {
    const body = { name }
    if (switchAgent) body.switch = true
    const rec = await api('/api/agents/install', {
      method: 'POST',
      body: JSON.stringify(body),
    })
    // /api/agents/install returns the new record directly (manager's
    // as_dict). Re-fetch the list so derived rollups settle.
    await fetchInstalled()
    return rec
  }

  async function uninstall(name) {
    await api(`/api/agents/${encodeURIComponent(name)}`, { method: 'DELETE' })
    await fetchInstalled()
  }

  async function switchAgent(name) {
    return install(name, { switchAgent: true })
  }

  // ── Actions: approvals (inbox + bell) ────────────────────────────
  async function fetchPending() {
    try {
      const r = await api('/api/agent/approvals')
      pending.value = Array.isArray(r?.approvals) ? r.approvals : []
    } catch (e) {
      // Don't blow up the store — 503 here just means the approval queue
      // hasn't been initialised yet (lifespan timing). UI shows empty.
      pending.value = []
    }
  }

  async function approve(approvalId) {
    const r = await api(`/api/agent/approvals/${encodeURIComponent(approvalId)}/approve`, {
      method: 'POST',
    })
    // SSE's executed/failed frame removes the entry; nudge the local
    // copy in case the socket is mid-reconnect.
    pending.value = pending.value.filter((e) => e.id !== approvalId)
    return r
  }

  async function deny(approvalId) {
    const r = await api(`/api/agent/approvals/${encodeURIComponent(approvalId)}/deny`, {
      method: 'POST',
    })
    pending.value = pending.value.filter((e) => e.id !== approvalId)
    return r
  }

  async function clearAll() {
    // "Clear all" is a UX convenience — we just deny each pending entry.
    // No bulk endpoint per ADR-0004 (audit row per decision is the rule).
    const ids = pending.value.map((e) => e.id)
    await Promise.all(ids.map((id) => deny(id).catch(() => null)))
  }

  // ── Actions: activity ────────────────────────────────────────────
  async function fetchActivity({ name = null, limit = 50 } = {}) {
    const target = name || currentAgent.value?.name
    if (!target) {
      activity.value = []
      return
    }
    try {
      const r = await api(
        `/api/agents/${encodeURIComponent(target)}/activity?limit=${limit}`,
      )
      activity.value = Array.isArray(r?.events) ? r.events : []
    } catch (e) {
      activity.value = []
    }
  }

  // ── SSE wiring ───────────────────────────────────────────────────
  function _applyFrame(frame) {
    if (!frame || typeof frame !== 'object') return
    const { kind, entry } = frame
    if (!entry || !entry.id) return

    if (kind === 'snapshot') {
      // Backfill on a fresh subscribe — append unique only so a snapshot
      // arriving mid-stream doesn't drop an enqueue we already received.
      const seen = new Set(pending.value.map((e) => e.id))
      if (!seen.has(entry.id)) pending.value.push(entry)
      return
    }
    if (kind === 'enqueued') {
      const idx = pending.value.findIndex((e) => e.id === entry.id)
      if (idx === -1) {
        pending.value.push(entry)
        newApprovalPulse.value += 1  // bell pulse trigger
      } else {
        pending.value[idx] = entry
      }
      return
    }
    if (kind === 'approved' || kind === 'denied') {
      const idx = pending.value.findIndex((e) => e.id === entry.id)
      if (idx !== -1) pending.value[idx] = entry
      return
    }
    if (kind === 'executed' || kind === 'failed') {
      pending.value = pending.value.filter((e) => e.id !== entry.id)
      return
    }
  }

  function connectSse() {
    if (disposed) return
    if (eventSource) return  // already connected
    try {
      const es = new EventSource(SSE_URL)
      eventSource = es
      es.onmessage = (evt) => {
        try {
          _applyFrame(JSON.parse(evt.data))
          // Reset backoff after the first successful frame — proves the
          // socket actually carries data, not just an HTTP-200 then
          // immediate close.
          backoffMs = SSE_BACKOFF_BASE_MS
        } catch {
          // ignore malformed frames
        }
      }
      es.onerror = () => {
        try { es.close() } catch { /* ignore */ }
        eventSource = null
        if (disposed) return
        _scheduleReconnect()
      }
    } catch (e) {
      // EventSource constructor itself threw (e.g. unsupported in test
      // environment) — schedule a retry so a polyfill can land later.
      _scheduleReconnect()
    }
  }

  function _scheduleReconnect() {
    if (reconnectTimer || disposed) return
    const delay = Math.min(backoffMs, SSE_BACKOFF_MAX_MS)
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      // Exponential back-off; jitter is unnecessary on a single client.
      backoffMs = Math.min(backoffMs * 2, SSE_BACKOFF_MAX_MS)
      connectSse()
    }, delay)
  }

  function disposeSse() {
    disposed = true
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null }
    if (eventSource) { try { eventSource.close() } catch { /* ignore */ } eventSource = null }
  }

  // ── Bootstrap helper ─────────────────────────────────────────────
  // The TopBar bell mounts on every page, so it's the natural caller
  // for the one-shot bootstrap. Idempotent: re-entries are cheap and
  // refresh the lists without re-opening the SSE socket.
  let bootstrapped = false
  async function ensureBootstrapped() {
    if (bootstrapped) return
    bootstrapped = true
    await Promise.all([fetchInstalled(), fetchPending()])
    connectSse()
  }

  return {
    // state
    installed, pending, activity, loading, error, newApprovalPulse,
    // derived
    currentAgent, shape, status, pendingCount,
    pendingForResource,
    // actions
    fetchInstalled, install, uninstall, switchAgent,
    fetchPending, approve, deny, clearAll,
    fetchActivity,
    connectSse, disposeSse, ensureBootstrapped,
  }
})
