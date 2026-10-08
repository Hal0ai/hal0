// Settings ▸ Data ▸ Memory — extraction limits (#1834), the pure part.
//
// The five [memory.graph].extraction_* knobs cap how hard Hindsight may drive
// the shared extraction slot. This module holds what the panel needs that is
// not React: the field table (status key ↔ PUT key, bounds, copy) and the
// validation / diff logic, so both are unit-tested without a DOM. Bounds
// mirror hal0.config.schema.MemoryGraphConfig exactly; the server is the
// authority and rejects anything outside them with a 400.

export const EXTRACTION_LIMIT_FIELDS = [
  {
    key: 'max_concurrent',
    putKey: 'extraction_max_concurrent',
    label: 'Concurrent calls',
    min: 1,
    max: 8,
    fallback: 1,
    sub: 'Extraction LLM calls in flight at once against the slot. 1 serialises them — right for a single llama-server; raise only for a slot that serves parallel requests',
  },
  {
    key: 'max_tokens',
    putKey: 'extraction_max_tokens',
    label: 'Max tokens per call',
    min: 3072,
    max: 32768,
    fallback: 4096,
    sub: 'Completion-token cap per extraction call (3072–32768). Bounds how long one call can run; must stay above Hindsight\'s 3000-char chunk',
  },
  {
    key: 'llm_retries',
    putKey: 'extraction_llm_retries',
    label: 'LLM retries',
    min: 0,
    max: 5,
    fallback: 1,
    sub: 'Retries of one extraction call inside a retain attempt (0–5). Each retry can wait the full LLM timeout',
  },
  {
    key: 'task_retries',
    putKey: 'extraction_task_retries',
    label: 'Task retries',
    min: 0,
    max: 10,
    fallback: 2,
    sub: 'Requeues of a failed queued memory task before it is marked failed (0–10) and shows under "Retry failed". Worker-wide: consolidation and mental-model refresh share it',
  },
  {
    key: 'retry_backoff_s',
    putKey: 'extraction_retry_backoff_s',
    label: 'Retry backoff',
    min: 10,
    max: 3600,
    fallback: 120,
    sub: 'Seconds a failed queued task waits before it is requeued (10–3600), so a retry does not land on a slot still busy with the attempt that timed out. Worker-wide',
  },
]

/**
 * Does this status payload carry the limits at all? An API that predates
 * them echoes nothing; the panel must then show them as unsupported rather
 * than present the defaults as if they were in force (that older
 * MemoryGraphConfig also silently ignores the keys on a PUT).
 * @param {{extraction_limits?: unknown} | null | undefined} status
 */
export function limitsSupported(status) {
  return !!(status && status.extraction_limits && typeof status.extraction_limits === 'object')
}

/**
 * Initial editable strings from a status payload (fallbacks for an old API).
 * @param {{extraction_limits?: Record<string, number>} | null | undefined} status
 * @returns {Record<string, string>}
 */
export function limitsFormFromStatus(status) {
  const src = (status && status.extraction_limits) || {}
  /** @type {Record<string, string>} */
  const out = {}
  for (const f of EXTRACTION_LIMIT_FIELDS) {
    const v = src[f.key]
    out[f.key] = String(typeof v === 'number' ? v : f.fallback)
  }
  return out
}

/** Per-field validity: an integer within the field's bounds. */
/** @param {Record<string, string>} form @returns {Record<string, boolean>} */
export function limitsFormValidity(form) {
  /** @type {Record<string, boolean>} */
  const out = {}
  for (const f of EXTRACTION_LIMIT_FIELDS) {
    const raw = String(form[f.key] ?? '').trim()
    const n = Number(raw)
    out[f.key] = /^\d+$/.test(raw) && n >= f.min && n <= f.max
  }
  return out
}

export function limitsFormAllValid(form) {
  return Object.values(limitsFormValidity(form)).every(Boolean)
}

/**
 * The PUT body fragment for every limit that differs from the status payload
 * (PUT keys). Unchanged fields are left out so a save that only touches the
 * slot never re-sends limits an older server might not know.
 */
/**
 * @param {Record<string, string>} form
 * @param {{extraction_limits?: Record<string, number>} | null | undefined} status
 * @returns {Record<string, number>}
 */
export function limitsPutBody(form, status) {
  /** @type {Record<string, number>} */
  const body = {}
  // Unsupported upstream: never send keys the server would silently drop.
  if (!limitsSupported(status)) return body
  const current = status.extraction_limits
  for (const f of EXTRACTION_LIMIT_FIELDS) {
    const raw = String(form[f.key] ?? '').trim()
    if (!/^\d+$/.test(raw)) continue // Number('') is 0 — never a change
    const n = Number(raw)
    const was = typeof current[f.key] === 'number' ? current[f.key] : f.fallback
    if (was !== n) body[f.putKey] = n
  }
  return body
}

export function limitsFormDirty(form, status) {
  return Object.keys(limitsPutBody(form, status)).length > 0
}
