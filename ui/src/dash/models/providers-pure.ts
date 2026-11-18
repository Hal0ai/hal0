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
  const searching = f.q.trim() !== ''
  const off = searching ? offAll : offAll.slice(0, Math.max(0, offShown))
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
  if (state === 'not-converted') return 'routes everything'
  return `${u.enabled_count} of ${u.models_total} on`
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
