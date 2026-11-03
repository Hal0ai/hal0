// Settings ▸ Data ▸ Memory — extraction throughput (#2353 / #2798), the pure part.
//
// hal0-api measures whether the extraction slot can finish one extraction
// inside [memory.graph].llm_timeout_s and reports it two ways:
//   * PUT /api/memory/graph  -> `throughput` plus, on a too_slow verdict only,
//     `warnings: [throughput.message]` (routes/memory.py, update_graph)
//   * GET /api/memory/graph/status -> the cached `throughput`, or null until
//     something has measured the current slot (routes/memory.py, graph_status)
// `throughput` is ExtractionThroughput.as_dict() (memory/extraction_env.py):
// `verdict` is "ok" | "too_slow" | "unknown" and `message` is the one
// operator-facing sentence. These helpers read those two shapes without any
// React so they are unit-tested without a DOM.

/**
 * The warning strings of a PUT /api/memory/graph response, ready to render.
 * Anything that is not a non-blank string is dropped, so a server that sends
 * no `warnings` (or a malformed one) yields no notice rather than a broken one.
 */
export function saveWarnings(resp) {
  const raw = resp?.warnings
  if (!Array.isArray(raw)) return []
  return raw.filter((w) => typeof w === 'string' && w.trim() !== '')
}

const TONE_BY_VERDICT = { too_slow: 'warn', ok: 'ok' }

/**
 * The cached throughput message for the status payload, or null when there is
 * nothing to say: no status yet, an API that predates the field, no
 * measurement of the current slot, or an engine running on an upstream model
 * (then the slot is not in play and a slot measurement would mislead).
 * Returns `{ tone: 'warn' | 'ok' | 'muted', verdict, text }`.
 */
export function throughputNote(status) {
  const tp = status?.throughput
  if (!tp || typeof tp.message !== 'string' || tp.message.trim() === '') return null
  if (status.target?.kind === 'upstream' || status.extraction_model) return null
  return {
    tone: TONE_BY_VERDICT[tp.verdict] || 'muted',
    verdict: tp.verdict || 'unknown',
    text: tp.message,
  }
}

/**
 * What the page does with a PUT /api/memory/graph response: the warnings to
 * keep on screen and the toast to raise. A failed hindsight-api restart stays
 * an error toast (it is the louder fact); otherwise any warning softens the
 * "saved" toast to warn so the toast and the notice below it agree.
 * Returns `{ warnings: string[], toast: { text, kind: 'ok' | 'warn' | 'err' } }`.
 */
export function graphSaveOutcome(resp) {
  const warnings = saveWarnings(resp)
  const perr = resp?.propagation?.error
  if (perr) {
    return { warnings, toast: { text: `Saved, but hindsight-api restart failed — ${perr}`, kind: 'err' } }
  }
  if (warnings.length > 0) {
    return { warnings, toast: { text: 'Memory graph settings saved — extraction warning below', kind: 'warn' } }
  }
  return { warnings, toast: { text: 'Memory graph settings saved', kind: 'ok' } }
}

/**
 * Per-bank failure notices from GET /api/memory/graph/status (#3241).
 * `failed_by_bank` is `{ [bank_id]: failed_count }`, only banks with failures.
 * Returns one sentence per bank with a positive count, largest first; anything
 * malformed (or an API that predates the field) yields none.
 */
export function bankFailureNotes(status) {
  const raw = status?.failed_by_bank
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return []
  return Object.entries(raw)
    .filter(([, n]) => Number.isFinite(n) && n > 0)
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([bank, n]) => `Bank "${bank}" has ${n} failed operation${n === 1 ? '' : 's'}.`)
}
