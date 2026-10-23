// #2919 — the pure half of the memory engine model picker.
//
// The memory engine (hindsight-api) always calls the one virtual id
// `hal0/memory`; hal0's /v1 resolves it from [memory.graph] on every call,
// either to the local extraction slot or to an upstream model the operator
// picked. The dashboard shows that as ONE <select> with two groups. Its value
// encodes the choice:
//
//   "slot:<name>"   — a local llm slot ([memory.graph].extraction_slot)
//   "model:<id>"    — an upstream model id  ([memory.graph].extraction_model)
//
// These helpers own the encoding, the option lists (keeping a configured but
// missing value pickable so the operator can see and move off it), the PUT
// body each choice turns into, and the "engine running" line that names what
// the engine really loads (#2918) — so MemoryPage.jsx and the agents memory
// tab render the same thing and the logic is unit-testable without React.

const SLOT = 'slot:'
const MODEL = 'model:'

/** `"slot:<name>"` | `"model:<id>"` for the configured target, `""` without a status. */
export function engineTargetValue(status) {
  if (!status) return ''
  if (status.extraction_model) return MODEL + status.extraction_model
  if (status.extraction_slot) return SLOT + status.extraction_slot
  return ''
}

/**
 * `{local: [{value, label}], upstream: [{value, label, owner}]}` — every enabled
 * llm slot and every upstream model the status advertises. A configured slot
 * that no longer resolves, or a configured model no upstream advertises, is
 * prepended to its group with a marker rather than silently dropped.
 */
export function engineTargetOptions(status) {
  if (!status) return { local: [], upstream: [] }
  const slots = Array.isArray(status.available_slots) ? status.available_slots : []
  const local = slots.map((s) => ({ value: SLOT + s, label: s }))
  if (status.extraction_slot && !slots.includes(status.extraction_slot)) {
    local.unshift({
      value: SLOT + status.extraction_slot,
      label: `${status.extraction_slot} (not running)`,
    })
  }
  const models = Array.isArray(status.available_models) ? status.available_models : []
  const upstream = models
    .filter((m) => m && typeof m.id === 'string' && m.id)
    .map((m) => ({ value: MODEL + m.id, label: `${m.id} · ${m.owned_by}`, owner: m.owned_by }))
  if (status.extraction_model && !models.some((m) => m && m.id === status.extraction_model)) {
    upstream.unshift({
      value: MODEL + status.extraction_model,
      label: `${status.extraction_model} (not advertised by any upstream)`,
      owner: null,
    })
  }
  return { local, upstream }
}

/**
 * The `PUT /api/memory/graph` fields for a picker value. A slot choice also
 * clears `extraction_model` (that is what makes the slot the target again); a
 * model choice leaves `extraction_slot` alone — it stays the way back.
 */
export function engineTargetPutBody(value) {
  if (typeof value !== 'string') return {}
  if (value.startsWith(SLOT) && value.length > SLOT.length) {
    return { extraction_slot: value.slice(SLOT.length), extraction_model: null }
  }
  if (value.startsWith(MODEL) && value.length > MODEL.length) {
    return { extraction_model: value.slice(MODEL.length) }
  }
  return {}
}

const UNKNOWN = 'engine LLM unknown (hal0-api could not read the hindsight-api unit)'

/**
 * `{text, warn}` describing the LLM hindsight-api really runs on. `warn` is
 * set when /etc/hal0/hindsight-llm.env overrides provider/base_url/model:
 * then hal0's picker is not in effect until those lines are removed.
 */
export function engineRunningLine(status) {
  const e = status && status.engine_llm
  if (!e || !e.model) return { text: UNKNOWN, warn: false }
  let text = `${e.model} via ${e.provider || '?'}`
  if (e.base_url) text += ` @ ${e.base_url}`
  if (e.overridden) {
    const keys = Array.isArray(e.override_keys) && e.override_keys.length ? ` (${e.override_keys.join(', ')})` : ''
    text += ` — overridden by ${e.override_file || 'hindsight-llm.env'}${keys}; the picker has no effect until those lines are removed and hindsight-api is restarted`
    return { text, warn: true }
  }
  return { text, warn: false }
}
