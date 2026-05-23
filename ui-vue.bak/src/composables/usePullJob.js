import { ref, computed, watch, onUnmounted } from 'vue'
import { api, Hal0Error } from './useApi.js'

/**
 * usePullJob — drive a model-pull lifecycle (start → stream → cancel)
 * from the dashboard. Shared between FirstRun.vue and Models.vue so
 * both surfaces show identical inline progress + cancel + reattach
 * behaviour (Team I gap #3).
 *
 * Backend contract (target — see report; backend wiring is Team B's
 * scope). The composable assumes:
 *
 *   POST   /api/models/{id}/pull
 *     body: {hf_url?: str, slot?: str}        # hf_url optional override
 *     200:  {job_id: str, model_id: str}
 *
 *   GET    /api/models/{id}/pull/status
 *     200:  {state: "queued"|"running"|"completed"|"failed"|"cancelled",
 *            downloaded: int, total: int, speed_bps: float, eta_s: float,
 *            error?: {code, message, details}}
 *
 *   GET    /api/models/{id}/pull/stream            (text/event-stream)
 *     event: progress
 *     data: {state, downloaded, total, speed_bps, eta_s}
 *
 *     event: completed | failed | cancelled
 *     data: {state, error?: {code, message, details}}
 *
 *   POST   /api/models/{id}/pull/cancel
 *     204
 *
 * Until the backend lands, every call surfaces the structured
 * `system.not_implemented` envelope and the composable's `error` ref
 * is set so the UI can render an inline failure rather than swallowing.
 */

const TERMINAL_STATES = new Set(['completed', 'failed', 'cancelled'])

export function usePullJob() {
  const jobId = ref(null)
  const modelId = ref(null)
  const state = ref('idle')              // 'idle' | queued | running | completed | failed | cancelled
  const downloaded = ref(0)
  const total = ref(0)
  const speedBps = ref(0)
  const etaS = ref(0)
  const error = ref(null)                // {code, message, details} | null
  let es = null

  const pct = computed(() => {
    if (!total.value) return null
    return Math.min(100, Math.round((downloaded.value / total.value) * 100))
  })

  const inFlight = computed(() => state.value === 'queued' || state.value === 'running')
  const terminal = computed(() => TERMINAL_STATES.has(state.value))

  function reset() {
    closeStream()
    jobId.value = null
    modelId.value = null
    state.value = 'idle'
    downloaded.value = 0
    total.value = 0
    speedBps.value = 0
    etaS.value = 0
    error.value = null
  }

  function closeStream() {
    if (es) {
      es.close()
      es = null
    }
  }

  function applyPayload(payload) {
    if (!payload || typeof payload !== 'object') return
    if (typeof payload.state === 'string') state.value = payload.state
    // Backend's PullJob.as_dict (src/hal0/registry/pull.py) emits
    // bytes_downloaded / bytes_total. The earlier shape (downloaded /
    // total) was a draft spec that never shipped — keep both readers
    // so the composable doesn't break if either side rolls forward.
    const dlField = payload.bytes_downloaded ?? payload.downloaded
    const totField = payload.bytes_total ?? payload.total
    if (typeof dlField === 'number') downloaded.value = dlField
    if (typeof totField === 'number') total.value = totField
    if (typeof payload.speed_bps === 'number') speedBps.value = payload.speed_bps
    if (typeof payload.eta_s === 'number') etaS.value = payload.eta_s
    if (payload.error) {
      error.value = typeof payload.error === 'string'
        ? { code: payload.error_code || 'pull.failed', message: payload.error, details: {} }
        : payload.error
    }
    if (TERMINAL_STATES.has(state.value)) closeStream()
  }

  function attachStream(id) {
    closeStream()
    try {
      es = new EventSource(`/api/models/${encodeURIComponent(id)}/pull/stream`)
    } catch (e) {
      error.value = { code: 'system.unknown', message: e?.message ?? 'EventSource failed', details: {} }
      return
    }
    const onMsg = (evt) => {
      try { applyPayload(JSON.parse(evt.data)) }
      catch { /* skip malformed frame */ }
    }
    es.addEventListener('progress', onMsg)
    es.addEventListener('completed', (e) => { applyPayload({ state: 'completed' }); onMsg(e) })
    es.addEventListener('failed', (e) => { applyPayload({ state: 'failed' }); onMsg(e) })
    es.addEventListener('cancelled', (e) => { applyPayload({ state: 'cancelled' }); onMsg(e) })
    es.onmessage = onMsg
    es.onerror = () => {
      // EventSource auto-reconnects; the backend's terminal-event close
      // is what tears the stream down on our side. Don't null the
      // reference here — that breaks reconnect.
    }
  }

  /**
   * Kick off a pull. `id` is the curated model id or HF repo path; if
   * `body` is supplied it's POSTed as-is (e.g. {hf_url, slot}).
   * Returns the started job_id; throws Hal0Error on failure (which the
   * UI's `useApi` wrapper toasts).
   */
  async function start(id, body = null) {
    reset()
    modelId.value = id
    state.value = 'queued'
    try {
      const res = await api(`/api/models/${encodeURIComponent(id)}/pull`, {
        method: 'POST',
        body: body ? JSON.stringify(body) : undefined,
      })
      // Backend's POST /pull returns { id, model_id, state, ... }. The
      // older draft spec used `job_id`; accept both.
      jobId.value = res?.id ?? res?.job_id ?? null
      attachStream(id)
      return res
    } catch (e) {
      state.value = 'failed'
      if (e instanceof Hal0Error) {
        error.value = { code: e.code, message: e.message, details: e.details }
      } else {
        error.value = { code: 'system.unknown', message: String(e?.message ?? e), details: {} }
      }
      throw e
    }
  }

  /**
   * Start a pull and return a Promise that resolves on `completed`
   * (and rejects on `failed`/`cancelled` or a POST failure). Wraps
   * `start()` so callers that need to wait for the SSE terminal frame
   * can `await pullAndWait(id)` instead of subscribing to state.value
   * by hand. The composable's existing reactive state is still updated
   * along the way so any inline progress UI keeps working.
   */
  function pullAndWait(id, body = null) {
    return new Promise((resolve, reject) => {
      const stop = watch(state, (s) => {
        if (s === 'completed') {
          stop()
          resolve()
        } else if (s === 'failed' || s === 'cancelled') {
          stop()
          reject(new Error(error.value?.message || s))
        }
      })
      start(id, body).catch((e) => {
        stop()
        reject(e)
      })
    })
  }

  /**
   * Reattach to an in-flight job on mount. Polls /pull/status; if the
   * backend reports `queued` or `running`, opens the SSE stream so the
   * caller can resume showing live progress without re-issuing the pull.
   * Safe to call when no job exists — returns silently.
   */
  async function reattach(id) {
    if (!id) return
    try {
      const status = await api(`/api/models/${encodeURIComponent(id)}/pull/status`)
      if (!status || typeof status !== 'object') return
      modelId.value = id
      applyPayload(status)
      if (status.state === 'queued' || status.state === 'running') {
        attachStream(id)
      }
    } catch (e) {
      // 404 = no in-flight job for this id; silently bail. Anything else
      // surfaces on the next user-driven start().
      if (!(e instanceof Hal0Error) || e.status !== 404) {
        // Don't toast — reattach is a background best-effort.
      }
    }
  }

  /**
   * Cancel an in-flight pull. No-op if nothing is in flight.
   */
  async function cancel() {
    if (!modelId.value || !inFlight.value) return
    try {
      await api(`/api/models/${encodeURIComponent(modelId.value)}/pull/cancel`, { method: 'POST' })
      // The backend will emit a `cancelled` SSE frame; we update state
      // optimistically so the button disables immediately.
      state.value = 'cancelled'
      closeStream()
    } catch (e) {
      if (e instanceof Hal0Error) {
        error.value = { code: e.code, message: e.message, details: e.details }
      } else {
        error.value = { code: 'system.unknown', message: String(e?.message ?? e), details: {} }
      }
      throw e
    }
  }

  onUnmounted(closeStream)

  return {
    // state
    jobId, modelId, state, downloaded, total, speedBps, etaS, error,
    // derived
    pct, inFlight, terminal,
    // actions
    start, pullAndWait, cancel, reattach, reset,
  }
}

/**
 * Format a byte count as a human-readable string (e.g. "412 MB").
 */
export function fmtBytes(bytes) {
  if (!bytes || bytes < 0) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

/**
 * Format a transfer rate (bytes/sec) as a human-readable string.
 */
export function fmtSpeed(bps) {
  if (!bps || bps <= 0) return '—'
  return `${fmtBytes(bps)}/s`
}

/**
 * Format an ETA (seconds) as a short string ("3m 14s", "47s").
 */
export function fmtEta(s) {
  if (!s || s <= 0 || !isFinite(s)) return '—'
  if (s < 60) return `${Math.ceil(s)}s`
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  if (m < 60) return `${m}m ${sec}s`
  const h = Math.floor(m / 60)
  return `${h}h ${m % 60}m`
}
