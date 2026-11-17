// Settings ▸ Memory ▸ Models: the pure half. Which keys the panel owns, which of them a provider
// needs, and how a form turns into the minimal partial a save sends.
//
// The allowed provider values mirror the validators in src/hal0/config/schema.py
// (`RERANKER_PROVIDERS`, and the `allowed` set in `_decision_provider_known`). The JSON schema
// does not carry them, because they are validators and not enums, so they are listed here.
//
// `[memory.models].embeddings_*` is deliberately absent: the embedder changes only through
// `sudo hal0 memory embedder switch <model>` (ADR-0058).

export const RERANKER_PROVIDERS = ['local', 'litellm', 'tei', 'rrf'] as const
export const DECISION_PROVIDERS = ['off', 'local', 'hindsight-cloud'] as const

export const EMBEDDER_SWITCH_COMMAND = 'sudo hal0 memory embedder switch <model>'
export const DECISION_INSTALL_COMMAND = 'sudo hal0 memory decision install'

/** The deep-link targets the health tiles use: `#settings/memory/models/<group>`. */
export const MODELS_GROUPS = ['embedder', 'reranker', 'decision'] as const
export type ModelsGroup = (typeof MODELS_GROUPS)[number]

/** The group a settings sub-path (`models/<group>`) names, or null. */
export function modelsFocus(sub: string | null | undefined): ModelsGroup | null {
  const [head, group] = String(sub || '').split('/')
  return head === 'models' && (MODELS_GROUPS as readonly string[]).includes(group) ? (group as ModelsGroup) : null
}

export const modelsHref = (group: ModelsGroup) => `#settings/memory/models/${group}`

export interface ModelKey {
  /** Dotted settings key. */
  key: string
  label: string
  hint: string
  kind: 'string' | 'integer' | 'boolean'
  /** Suffix shown after a number. */
  unit?: string
}

const RM = 'memory.models.'
const DC = 'memory.decision.'

export const PROVIDER_KEY = `${RM}reranker_provider`
export const DECISION_PROVIDER_KEY = `${DC}provider`

export const RERANKER_BASIC: ModelKey[] = [
  { key: `${RM}reranker_model`, label: 'Model', kind: 'string', hint: 'The cross-encoder the engine loads to re-order recall candidates.' },
  { key: `${RM}reranker_max_candidates`, label: 'Candidates scored', kind: 'integer', hint: 'How many recall candidates the reranker scores per recall. Lower is faster; the engine default of 300 takes tens of seconds on a CPU.' },
]

export const RERANKER_CAPS: ModelKey[] = [
  { key: `${RM}reranker_max_candidates_low`, label: 'Candidates, low budget', kind: 'integer', hint: 'Cap for low-budget recalls. Empty uses the cap above.' },
  { key: `${RM}reranker_max_candidates_mid`, label: 'Candidates, mid budget', kind: 'integer', hint: 'Cap for mid-budget recalls. Empty uses the cap above.' },
  { key: `${RM}reranker_max_candidates_high`, label: 'Candidates, high budget', kind: 'integer', hint: 'Cap for high-budget recalls. Empty uses the cap above.' },
]

export const RERANKER_LOCAL: ModelKey[] = [
  { key: `${RM}reranker_local_batch_size`, label: 'Batch size', kind: 'integer', hint: 'Pairs scored per call. Empty keeps the engine default (32).' },
  { key: `${RM}reranker_local_max_concurrent`, label: 'Concurrent reranks', kind: 'integer', hint: 'Rerank calls running at once. Empty keeps the engine default (4).' },
  { key: `${RM}reranker_local_timeout_s`, label: 'Timeout', kind: 'integer', unit: 's', hint: 'Ceiling for one recall’s rerank, in seconds; 0 turns it off. Empty keeps the engine default (300).' },
  { key: `${RM}reranker_bucket_batching`, label: 'Bucket batching', kind: 'boolean', hint: 'Group candidates of similar length into one batch.' },
]

export const RERANKER_REMOTE: ModelKey[] = [
  { key: `${RM}reranker_base_url`, label: 'Endpoint URL', kind: 'string', hint: 'Where the engine sends rerank calls. No credentials in the URL.' },
  { key: `${RM}reranker_api_model`, label: 'Model id sent', kind: 'string', hint: 'The model id sent in each rerank request. Required for litellm.' },
  { key: `${RM}reranker_timeout_s`, label: 'Request timeout', kind: 'integer', unit: 's', hint: 'HTTP timeout for one rerank call. Empty keeps the engine default.' },
]

export const DECISION_KEYS: ModelKey[] = [
  { key: `${DC}threads`, label: 'Threads', kind: 'integer', hint: 'CPU threads the local decision server uses.' },
  { key: `${DC}max_concurrent`, label: 'Concurrent requests', kind: 'integer', hint: 'Decision requests the engine sends at once.' },
  { key: `${DC}max_request_tokens`, label: 'Largest request', kind: 'integer', unit: 'tokens', hint: 'Cap on one request, as a CPU latency budget.' },
  { key: `${DC}timeout_s`, label: 'Timeout', kind: 'integer', unit: 's', hint: 'HTTP timeout for one decision call.' },
]

/** The remote-only rows a provider needs. `litellm` takes the model id too. */
export function remoteKeys(provider: string): ModelKey[] {
  if (provider === 'litellm') return RERANKER_REMOTE
  if (provider === 'tei') return RERANKER_REMOTE.filter((k) => !k.key.endsWith('reranker_api_model'))
  return []
}

export function localKeys(provider: string): ModelKey[] {
  return provider === 'local' ? RERANKER_LOCAL : []
}

export type Buffer = Record<string, string | boolean>

interface SchemaField {
  type?: string
  minimum?: number
  maximum?: number
  nullable?: boolean
  default?: unknown
}

export type Coerced = { ok: true; value: string | number | boolean | null } | { ok: false; message: string }

export function coerce(key: ModelKey, field: SchemaField | null | undefined, raw: string | boolean): Coerced {
  if (key.kind === 'boolean') return { ok: true, value: !!raw }
  const s = String(raw).trim()
  const nullable = field ? !!field.nullable && field.default == null : true
  if (key.kind === 'integer') {
    if (s === '') return nullable ? { ok: true, value: null } : { ok: false, message: 'A number is required.' }
    if (!/^-?\d+$/.test(s)) return { ok: false, message: 'Whole numbers only.' }
    const n = parseInt(s, 10)
    if (field?.minimum != null && n < field.minimum) return { ok: false, message: `At least ${field.minimum}.` }
    if (field?.maximum != null && n > field.maximum) return { ok: false, message: `At most ${field.maximum}.` }
    return { ok: true, value: n }
  }
  if (s === '') return nullable ? { ok: true, value: null } : { ok: false, message: 'A value is required.' }
  return { ok: true, value: s }
}

export const getIn = (obj: unknown, dotKey: string): unknown =>
  dotKey.split('.').reduce<unknown>((o, k) => (o == null ? undefined : (o as Record<string, unknown>)[k]), obj)

/** `{a: {b: v}}` for `a.b`, merged into `into`. */
export function setIn(into: Record<string, unknown>, dotKey: string, value: unknown): Record<string, unknown> {
  const parts = dotKey.split('.')
  let node = into
  for (const p of parts.slice(0, -1)) {
    node[p] = (node[p] as Record<string, unknown>) ?? {}
    node = node[p] as Record<string, unknown>
  }
  node[parts[parts.length - 1]] = value
  return into
}

export interface FormState {
  /** Keys whose buffered value differs from the live config. */
  dirty: string[]
  /** A message per key whose buffered value cannot be saved. */
  invalid: Record<string, string>
  /** The partial a save sends: only the dirty keys. */
  patch: Record<string, unknown>
}

/**
 * Diff the buffer against the live config. `keys` are the rows currently on screen; a buffered
 * value for a row that is hidden (the provider changed) is not sent. `field` resolves a key's
 * schema node.
 */
export function formState(
  keys: ModelKey[],
  buf: Buffer,
  live: unknown,
  field: (key: string) => SchemaField | null | undefined,
): FormState {
  const dirty: string[] = []
  const invalid: Record<string, string> = {}
  const patch: Record<string, unknown> = {}
  for (const k of keys) {
    if (!(k.key in buf)) continue
    const r = coerce(k, field(k.key), buf[k.key])
    if (!r.ok) {
      dirty.push(k.key)
      invalid[k.key] = r.message
      continue
    }
    const cur = getIn(live, k.key)
    const curNorm = cur === undefined ? (field(k.key)?.default ?? null) : cur
    if (r.value === curNorm) continue
    dirty.push(k.key)
    setIn(patch, k.key, r.value)
  }
  return { dirty, invalid, patch }
}

/** The value the form shows for a string key: the buffered choice, else the saved one. */
export function effective(buf: Buffer, live: unknown, key: string, fallback: string): string {
  const b = buf[key]
  if (typeof b === 'string') return b
  const l = getIn(live, key)
  return typeof l === 'string' && l ? l : fallback
}

/**
 * What a remote reranker still needs before a save can work: the endpoint, and for litellm the
 * model id. Read from the buffered value, else the saved one.
 */
export function remoteMissing(provider: string, buf: Buffer, live: unknown): Record<string, string> {
  const out: Record<string, string> = {}
  if (provider !== 'litellm' && provider !== 'tei') return out
  const has = (key: string) => {
    const v = key in buf ? buf[key] : getIn(live, key)
    return typeof v === 'string' && v.trim() !== ''
  }
  if (!has(`${RM}reranker_base_url`)) out[`${RM}reranker_base_url`] = 'The endpoint is required for this provider.'
  if (provider === 'litellm' && !has(`${RM}reranker_api_model`)) {
    out[`${RM}reranker_api_model`] = 'The model id is required for litellm.'
  }
  return out
}

/**
 * Per-field messages from a rejected save (`code: "config.invalid"`, details `{field_path:
 * message}`), keyed by the dotted keys the panel owns. A path the panel does not own is
 * returned under `other`.
 */
export function saveErrors(
  error: unknown,
  owned: string[],
): { fields: Record<string, string>; other: string[]; message: string } {
  const e = error as { code?: string; message?: string; details?: Record<string, unknown> } | null
  const fields: Record<string, string> = {}
  const other: string[] = []
  if (e?.code === 'config.invalid' && e.details) {
    for (const [path, msg] of Object.entries(e.details)) {
      const hit = owned.find((k) => path === k || path.endsWith(`.${k}`) || k.endsWith(`.${path}`))
      if (hit) fields[hit] = String(msg)
      else other.push(`${path}: ${String(msg)}`)
    }
  }
  return { fields, other, message: e?.message || 'see logs' }
}

export interface DecisionStatus {
  /** What the models read says about the saved provider. */
  provider: string | null
  reachable: boolean | null
  installed: boolean | null
  unitActive: boolean | null
}

const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null)

/** The decision lane as the models read reports it. `installed` and `unit_active` may be absent. */
export function decisionStatus(decision: unknown): DecisionStatus | null {
  if (!decision || typeof decision !== 'object') return null
  const d = decision as Record<string, unknown>
  return {
    provider: typeof d.provider === 'string' ? d.provider : null,
    reachable: bool(d.reachable),
    installed: bool(d.installed),
    unitActive: bool(d.unit_active),
  }
}

/**
 * The one line of status for the decision server, or null when it needs no words (provider not
 * local, or no answer to read). Worded to stay true whether or not the read carries `installed`
 * and `unit_active`.
 */
export function decisionServerNote(s: DecisionStatus | null): { tone: 'ok' | 'warn'; text: string } | null {
  if (!s || s.provider !== 'local') return null
  if (s.reachable === true) return { tone: 'ok', text: 'The decision server answers.' }
  if (s.reachable === null) return null
  const why =
    s.installed === false
      ? 'The decision server is not installed.'
      : s.installed === true && s.unitActive === false
        ? 'The decision server is installed but not running.'
        : 'The decision server is not installed or not running.'
  return {
    tone: 'warn',
    text: `${why} Until it answers, the extraction model makes these judgments, so memory keeps working. To install and start it:`,
  }
}
