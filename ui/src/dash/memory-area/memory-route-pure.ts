// The Memory area's address, read from and written to the hash:
//
//   #memory/<tab>[/<flow>]?bank=<id>&project=<id>&fact=<id>&page=<id>
//
// The tab is one of MEMORY_TABS; anything else reads as Health. `flow` is a sub-page of a tab
// (none exists yet; the slot is here so a later flow does not change the address shape). The
// query holds what the page is scoped to and what is open in it: the bank and the project (the
// scope bar), the open fact (Facts) and the open page (Knowledge). Nothing else keeps that
// state, so a reload, a shared link and Back all land on the same view.
//
// Bank ids are written as they are. They contain `::`, `:` and `.` (`coding-agent::hal0-mono`,
// `coding-agent::ultrareview-1.4`); a colon is legal in a fragment, so it is left unescaped and
// the address stays readable. Every other reserved character is percent-encoded, and reading
// decodes either spelling.
//
// No React and no window: every function takes the hash as a string.

export const MEMORY_TABS = ['health', 'facts', 'knowledge', 'operations', 'agents'] as const
export type MemoryTab = (typeof MEMORY_TABS)[number]

/** The tab an address with no tab, or an unknown one, opens. */
export const DEFAULT_MEMORY_TAB: MemoryTab = 'health'

export interface MemoryRoute {
  tab: MemoryTab
  /** A sub-page of the tab, or null on the tab itself. */
  flow: string | null
  bank: string | null
  project: string | null
  /** The fact open in the Facts inspector. */
  fact: string | null
  /** The page open in the Knowledge reader. */
  page: string | null
}

const QUERY_KEYS = ['bank', 'project', 'fact', 'page'] as const
type QueryKey = (typeof QUERY_KEYS)[number]

const isTab = (value: string): value is MemoryTab =>
  (MEMORY_TABS as readonly string[]).includes(value)

function decode(value: string): string {
  try {
    return decodeURIComponent(value)
  } catch {
    // A stray `%` that is not an escape: keep what was typed.
    return value
  }
}

/** `coding-agent::hal0-mono` stays as it is; `a b&c` becomes `a%20b%26c`. */
function encode(value: string): string {
  return encodeURIComponent(value).replace(/%3A/gi, ':')
}

/** `#memory/facts?bank=x` → `['memory/facts', 'bank=x']`. The leading `#` is optional. */
function splitHash(hash: string): [path: string, query: string] {
  const raw = hash.replace(/^#/, '')
  const at = raw.indexOf('?')
  return at === -1 ? [raw, ''] : [raw.slice(0, at), raw.slice(at + 1)]
}

function parseQuery(query: string): Record<QueryKey, string | null> {
  const out: Record<QueryKey, string | null> = { bank: null, project: null, fact: null, page: null }
  for (const pair of query.split('&')) {
    if (!pair) continue
    const at = pair.indexOf('=')
    const key = decode(at === -1 ? pair : pair.slice(0, at))
    const value = at === -1 ? '' : decode(pair.slice(at + 1))
    if ((QUERY_KEYS as readonly string[]).includes(key) && value) out[key as QueryKey] = value
  }
  return out
}

/**
 * Read a Memory address. A hash that is not a Memory address at all reads as Health with
 * nothing chosen, so a caller never has to handle "no route".
 */
export function parseMemoryRoute(hash: string): MemoryRoute {
  const [path, query] = splitHash(hash)
  const parts = path.split('/')
  const inArea = parts[0] === 'memory'
  const named = inArea ? (parts[1] ?? '') : ''
  const tab = isTab(named) ? named : DEFAULT_MEMORY_TAB
  // A flow belongs to the tab that was named. An unknown tab has none.
  const flow = isTab(named) && parts[2] ? decode(parts.slice(2).join('/')) : null
  return { tab, flow, ...(inArea ? parseQuery(query) : parseQuery('')) }
}

/**
 * Write a Memory address. Only the tab is required; a missing or empty field is left out of
 * the address. The query is always in the same order, so one view has one address.
 */
export function serializeMemoryRoute(route: Partial<MemoryRoute> & { tab: MemoryTab }): string {
  const path = `#memory/${route.tab}${route.flow ? `/${route.flow.split('/').map(encode).join('/')}` : ''}`
  const pairs: string[] = []
  for (const key of QUERY_KEYS) {
    const value = route[key]
    if (value) pairs.push(`${key}=${encode(value)}`)
  }
  return pairs.length > 0 ? `${path}?${pairs.join('&')}` : path
}

/**
 * The address of another tab, reached from `from`. The scope (bank and project) travels with
 * the operator; what was open in the tab they are leaving (a fact, a page, a flow) does not.
 */
export function memoryTabHref(tab: MemoryTab, from?: Pick<MemoryRoute, 'bank' | 'project'> | null): string {
  return serializeMemoryRoute({ tab, bank: from?.bank ?? null, project: from?.project ?? null })
}

/**
 * Where an address from before the Memory area now lives, or null when `hash` needs no
 * redirect. The query is kept as it was written.
 *
 *   #memory                    → #memory/health
 *   #memory/bank?…             → #memory/facts?…     (the bank workspace)
 *   #agent/memory[/…][?…]      → #memory/facts?…     (the Agents ▸ Memory tab)
 *
 * `#memworkspace/…` is not redirected: that page still exists.
 *
 * Remove these with the next minor release: by then no saved link uses the old addresses.
 */
export function legacyMemoryRedirect(hash: string): string | null {
  const [path, query] = splitHash(hash)
  const parts = path.split('/')
  const tail = query ? `?${query}` : ''
  if (parts[0] === 'agent' && parts[1] === 'memory') return `#memory/facts${tail}`
  if (parts[0] !== 'memory') return null
  const sub = parts[1] ?? ''
  if (sub === '') return `#memory/${DEFAULT_MEMORY_TAB}${tail}`
  if (sub === 'bank') return `#memory/facts${tail}`
  return null
}
