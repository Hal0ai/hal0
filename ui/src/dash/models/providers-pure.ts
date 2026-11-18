// Providers tab (Models → Providers): every piece of logic that is not DOM.
// The list, the detail panel and the Add provider modal import from here so the
// rules (what state a provider is in, how many off rows to draw, how a failed
// switch reads, what the create body holds) live in one tested place.
import { modelPath, type ModelType } from '@/lib/providerModel'
import type {
  CatalogEntry,
  ProbeModelRow,
  ProbeResult,
  UpstreamCreateBody,
  UpstreamEntry,
  UpstreamListing,
  UpstreamModelRow,
} from '@/api/hooks/useUpstreams'

export { modelPath }
export type { ModelType }
export type ProviderState = 'per-model' | 'not-converted' | 'managed'

const RESERVED_NAME = 'hal0'
const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/

/** Agent-managed, slot-backed, slot-kind or the reserved name: the API refuses writes to these. */
function isReadOnlyEntry(u: UpstreamEntry): boolean {
  return !!u.managed_by || !!u.slot_name || u.kind === 'slot' || u.name === RESERVED_NAME
}

export function providerState(u: UpstreamEntry, listing?: UpstreamListing): ProviderState {
  if (isReadOnlyEntry(u) || listing?.protected) return 'managed'
  return u.curated ? 'per-model' : 'not-converted'
}

export function keyState(u: UpstreamEntry): 'set' | 'missing' | 'not-needed' | 'managed' {
  if (isReadOnlyEntry(u)) return 'managed'
  if (u.auth_style === 'none') return 'not-needed'
  return u.auth_key_present ? 'set' : 'missing'
}

export interface ModelFilter {
  q: string
  show: 'all' | 'on' | 'off'
  type: ModelType | 'any'
}

export function filterModels(rows: UpstreamModelRow[], f: ModelFilter): UpstreamModelRow[] {
  const needle = f.q.trim().toLowerCase()
  return rows.filter((m) => {
    if (f.show === 'on' && !m.enabled) return false
    if (f.show === 'off' && m.enabled) return false
    if (f.type !== 'any' && m.type !== f.type) return false
    return !needle || m.id.toLowerCase().includes(needle)
  })
}

/** How many off rows a provider section draws before "Show more". */
export const OFF_PAGE = 25

const byId = (a: UpstreamModelRow, b: UpstreamModelRow) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0)

export function sectionRows(
  rows: UpstreamModelRow[],
  f: ModelFilter,
  offShown: number,
): { on: UpstreamModelRow[]; off: UpstreamModelRow[]; offTotal: number; offHidden: number } {
  const kept = filterModels(rows, f)
  const on = kept.filter((m) => m.enabled).sort(byId)
  const offAll = kept
    .filter((m) => !m.enabled)
    .sort((a, b) => Number(b.recommended) - Number(a.recommended) || byId(a, b))
  const off = offAll.slice(0, Math.max(0, offShown))
  return { on, off, offTotal: offAll.length, offHidden: offAll.length - off.length }
}

export function rowFlag(m: UpstreamModelRow): 'gone' | 'recommended' | null {
  if (!m.discovered) return 'gone'
  if (m.recommended && !m.enabled) return 'recommended'
  return null
}

export function countLabel(u: UpstreamEntry): string {
  const state = providerState(u)
  if (state === 'managed') return 'read-only'
  if (u.enabled === false) return 'switched off'
  if (state === 'not-converted') return 'routes everything'
  return `${u.enabled_count} of ${u.models_total} on`
}

/** Model types in display order, with the words the dashboard shows for each. The API value is `id`. */
export const MODEL_TYPE_CHOICES: ReadonlyArray<{ id: ModelType; label: string }> = [
  { id: 'chat', label: 'chat' },
  { id: 'embed', label: 'embed' },
  { id: 'tts', label: 'speech' },
  { id: 'stt', label: 'transcribe' },
  { id: 'rerank', label: 'rerank' },
  { id: 'image', label: 'image' },
]

export function modelTypeLabel(t: string): string {
  return MODEL_TYPE_CHOICES.find((c) => c.id === t)?.label ?? t
}

/** Providers listed in the tab: operator remotes by name, then managed ones. Slot-backed upstreams
 * are local models, not providers, so they never appear. */
export function visibleProviders(list: UpstreamEntry[] | null | undefined): UpstreamEntry[] {
  const remotes = (Array.isArray(list) ? list : []).filter((u) => u.kind !== 'slot' && !u.slot_name)
  const byName = (a: UpstreamEntry, b: UpstreamEntry) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
  const operator = remotes.filter((u) => providerState(u) !== 'managed').sort(byName)
  const managed = remotes.filter((u) => providerState(u) === 'managed').sort(byName)
  return [...operator, ...managed]
}

export function providerLabel(u: UpstreamEntry, catalog?: Record<string, CatalogEntry> | null): string {
  return (u.catalog_id && catalog?.[u.catalog_id]?.name) || u.name
}

/** Who keeps a managed provider up to date, in plain words. */
export function ownerLabel(u: UpstreamEntry): string {
  if (!u.managed_by) return 'hal0'
  const m = /^agent:([A-Za-z0-9_-]+)$/.exec(u.managed_by)
  if (!m) return 'an agent'
  return `the ${m[1][0].toUpperCase()}${m[1].slice(1)} agent`
}

export function hostOf(url: string | null | undefined): string {
  try {
    return new URL(url as string).host
  } catch {
    return url || ''
  }
}

// The variable names the credentials route refuses for a key, mirrored from src/hal0/api/_env_store.py
// (is_process_control_env: PROCESS_CONTROL_ENV_NAMES, PROCESS_CONTROL_ENV_PREFIXES, the _PROXY suffix,
// the HF Hub redirect names and prefixes; the TLS trust names) and the HAL0_ service namespace
// (routes/secrets.py is_protected_secret). They are tested against the FINAL variable name.
const RESERVED_ENV_NAMES = new Set([
  'PATH', 'HOME', 'SHELL', 'IFS', 'ENV', 'BASH_ENV', 'NODE_OPTIONS', 'PERL5OPT', 'RUBYOPT', 'GCONV_PATH',
  'PERL5LIB', 'NODE_PATH', 'SHELLOPTS', 'BASHOPTS', 'PS4', 'HERMES_HOME', 'SSLKEYLOGFILE',
  'NODE_TLS_REJECT_UNAUTHORIZED',
  'HF_ENDPOINT', 'HF_INFERENCE_ENDPOINT', 'HUGGINGFACE_CO_STAGING', 'HF_TOKEN_PATH', 'HF_ASSETS_CACHE',
  'HUGGINGFACE_ASSETS_CACHE',
  'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS',
])
const RESERVED_ENV_PREFIXES = [
  'LD_', 'DYLD_', 'PYTHON', 'UV_', 'PIP_', 'NPM_CONFIG_', 'GIT_', 'OPENSSL_', 'XDG_',
  'HF_HUB_', 'HUGGINGFACE_HUB_', 'HF_XET_',
  'HAL0_',
]
export function isReservedEnv(env: string): boolean {
  const n = env.toUpperCase()
  return RESERVED_ENV_NAMES.has(n) || n.endsWith('_PROXY') || RESERVED_ENV_PREFIXES.some((p) => n.startsWith(p))
}

/** The variable a provider's key is stored under, derived from its NAME so that two providers made
 * from one preset never share a key: `minimax` -> MINIMAX_API_KEY, `minimax-2` -> MINIMAX_2_API_KEY.
 * A reserved or digit-leading name gets a PROVIDER_ prefix; a variable another provider already
 * uses gets a numeric suffix. */
export function keyEnvFor(name: string, used: string[] = []): string {
  let stem = name.toUpperCase().replace(/[^A-Z0-9]/g, '_')
  if (/^[0-9]/.test(stem) || isReservedEnv(`${stem}_API_KEY`)) stem = `PROVIDER_${stem}`
  let env = `${stem}_API_KEY`
  for (let n = 2; used.includes(env); n += 1) env = `${stem}_${n}_API_KEY`
  return env
}

// Same rule as the server (upstreams/registry.py normalise_key): printable ASCII, no whitespace,
// after one optional leading "Bearer " (exactly one space, then the token).
export const KEY_MESSAGE = 'The key must be printable ASCII without spaces.'
export function keyProblem(raw: string): string | null {
  const t = raw.trim()
  if (!t) return null
  if (/^bearer\s*$/i.test(t)) return KEY_MESSAGE
  let bare = t
  if (/^bearer\s/i.test(t)) {
    if (t[6] !== ' ' || /^\s/.test(t.slice(7))) return KEY_MESSAGE
    bare = t.slice(7)
  }
  return /^[\x21-\x7e]+$/.test(bare) ? null : KEY_MESSAGE
}

/** A refresh answer is clean only when the provider was reached: not stale and no warning. */
export function refreshOutcome(res: Pick<UpstreamListing, 'stale' | 'warning'> | null | undefined): 'clean' | 'unreachable' {
  return res?.stale || res?.warning ? 'unreachable' : 'clean'
}

/** The toast after the header "Refresh model lists" has asked every eligible provider. */
export function refreshSummary(clean: number, unreachable: number): { text: string; kind: 'info' | 'warn' } {
  if (clean === 0 && unreachable === 0) return { text: 'No provider to refresh.', kind: 'info' }
  const base = `Refreshed ${clean} model list${clean === 1 ? '' : 's'}`
  if (unreachable === 0) return { text: base, kind: 'info' }
  return { text: `${base} · ${unreachable} could not be reached`, kind: 'warn' }
}

const DASH = '—'

function fmtMtok(v: number | null): string {
  if (v === null) return DASH
  const s = v.toFixed(2)
  return v > 0 && Number(s) === 0 ? String(v) : s
}

type Priced = Pick<UpstreamModelRow, 'price_in' | 'price_out' | 'price_unit'>

/** Works on a stored row and on a probe row. Per Mtok: "in / out". Any other unit:
 * "in / unit", "out N / unit" when only the output side is set, "in / out / unit" for both. */
export function formatPrice(m: Priced): string {
  if (m.price_in === null && m.price_out === null) return DASH
  if (m.price_unit === 'Mtok') return `${fmtMtok(m.price_in)} / ${fmtMtok(m.price_out)}`
  if (m.price_in === null) return `out ${m.price_out} / ${m.price_unit}`
  if (m.price_out === null) return `${m.price_in} / ${m.price_unit}`
  return `${m.price_in} / ${m.price_out} / ${m.price_unit}`
}

export function formatContext(n: number | null): string {
  if (n === null) return DASH
  if (n >= 999_500) return `${Math.round((n / 1_000_000) * 10) / 10}M`
  if (n >= 1000) return `${Math.round(n / 1000)}k`
  return String(n)
}

export function defaultProviderName(preset: CatalogEntry, taken: string[]): string {
  if (preset.id === 'custom') return ''
  const base = preset.id.replace(/_/g, '-')
  if (!taken.includes(base)) return base
  let n = 2
  while (taken.includes(`${base}-${n}`)) n += 1
  return `${base}-${n}`
}

export function validateProviderName(name: string, taken: string[]): string | null {
  if (!name) return 'Enter a name for this provider.'
  if (!NAME_PATTERN.test(name)) {
    return 'Use lowercase letters, digits, "-" and "_", starting with a letter or digit, up to 64 characters.'
  }
  if (name === RESERVED_NAME) return '"hal0" is reserved. Pick another name.'
  if (taken.includes(name)) return 'A provider with this name already exists.'
  return null
}

export function buildCreateBody(args: {
  name: string
  catalogId: string
  url: string
  authStyle: string
  authHeader: string
  picked: Set<string>
  probed: ProbeModelRow[]
  types: Record<string, ModelType>
}): UpstreamCreateBody {
  const body: UpstreamCreateBody = {
    name: args.name,
    url: args.url,
    auth_style: args.authStyle,
    models: args.probed.map((m) => ({
      id: m.id,
      enabled: args.picked.has(m.id),
      type: args.types[m.id] ?? m.type,
      context: m.context,
      price_in: m.price_in,
      price_out: m.price_out,
      price_unit: m.price_unit,
    })),
  }
  if (args.catalogId) body.catalog_id = args.catalogId
  if (args.authHeader) body.auth_header = args.authHeader
  return body
}

export function initialPicks(probed: ProbeModelRow[]): Set<string> {
  return new Set(probed.filter((m) => m.recommended).map((m) => m.id))
}

export function toggleErrorMessage(err: { status?: number; code?: string }): string {
  if (err.status === 409 && err.code === 'upstream.not_curated') return 'Choose models for this provider first.'
  if (err.status === 409 && err.code === 'upstream.protected') {
    return 'This provider is managed by an agent and cannot be changed here.'
  }
  if (err.status === 404) return 'This provider no longer exists. Reload the list.'
  return 'Could not change the model. Try again.'
}

export function probeErrorMessage(r: ProbeResult): string {
  if (r.status === 401 || r.status === 403) {
    return 'The provider rejected the key. Check that it was copied whole and is active.'
  }
  if (r.status === null) return 'Could not reach the provider. Check the address.'
  return r.error ?? 'The provider answered with an error.'
}

/**
 * Where an address from before the Providers tab now lives (a route without the
 * leading `#`), or null when it needs no redirect. The Models `upstream` tab and
 * the Slots → Endpoints upstream panel both moved to Models → Providers.
 *
 * HAL0-SUNSET: v1.9.0 — remove with the next minor release: by then no saved link uses
 * the old addresses.
 */
export function legacyModelsRedirect(
  route: string,
  param: string | undefined,
  query: URLSearchParams,
): string | null {
  if (route === 'models' && param === 'upstream') return 'models/providers'
  if (route === 'slots' && param === 'endpoints' && query.get('focus') === 'upstream') return 'models/providers'
  return null
}
