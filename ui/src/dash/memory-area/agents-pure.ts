// Memory ▸ Agents: who reads and writes memory, worked out from evidence and nothing else.
//
// Three kinds of evidence reach this file:
//
//   tags      per bank, how many facts carry each tag. A fact written through hal0 carries
//             `agent:<name>` (hal0 stamps it; a caller cannot set it). `agent:unknown` is what
//             hal0 stamps when the caller sent no name.
//   installed the agents hal0 ships that are installed on this box (Hermes, Pi).
//   clients   bridge clients: names that hold a key. One with the `memory` scope may read and
//             write memory through hal0 under its own name.
//
// A row exists only when one of those names it. Its status is what the evidence supports:
// `seen writing` (tagged facts exist), `connected` (a live key with the memory scope exists for
// the name; nothing says it has been used for memory), or `not seen`.
//
// Counts are per tag and per bank, and are never added across tags: one fact may carry both an
// `agent:` and a `harness:` tag, and a sum would count it twice.

export interface TagCount {
  tag: string
  count: number
}

/** One bank's tag list. `items` is null while it loads and when it failed. */
export interface BankTagsEntry {
  bankId: string
  items: TagCount[] | null
  isLoading?: boolean
  isError?: boolean
}

export interface InstalledAgent {
  name: string
}

export interface MemoryClient {
  name: string
  scopes: string[]
  created_by?: string
  revoked_at: number | null
  last_seen_at?: number | null
}

/** Facts carrying one tag on one bank. */
export interface Sighting {
  tag: string
  bank: string
  count: number
}

export type AgentStatus = 'seen writing' | 'connected' | 'not seen'

/** Which sentences describe the row: each kind has its own, taken from what the code does. */
export type AgentKind = 'hermes' | 'hermes-profile' | 'pi' | 'coding' | 'client' | 'tag'

export interface AgentRow {
  id: string
  name: string
  kind: AgentKind
  /** Shown under the name when the name is not the id itself. */
  idNote: string | null
  sightings: Sighting[]
  status: AgentStatus
  /** The key behind a `connected` status, when there is one. */
  client: MemoryClient | null
}

const AGENT_PREFIX = 'agent:'
const HARNESS_PREFIX = 'harness:'
export const UNKNOWN_ID = 'unknown'
export const UNKNOWN_TAG = `${AGENT_PREFIX}${UNKNOWN_ID}`

/** The name brain chat writes under unless its profile was given another. */
export const BRAIN_ID = 'hermes__hal0-brain'
/** The name of the key hal0 itself holds for Open WebUI. */
export const OPENWEBUI_ID = 'openwebui'
/** Names that belong to a part of hal0, shown on the "Built into hal0" card, not as agents. */
const BUILT_IN_IDS = new Set([BRAIN_ID, OPENWEBUI_ID])

const HERMES_PROFILE_PREFIX = 'hermes__'
const CODING_BANK_PREFIX = 'coding-agent::'

const HARNESS_NAMES: Record<string, string> = {
  'claude-code': 'Claude Code',
  codex: 'Codex',
  opencode: 'OpenCode',
  pi: 'Pi',
  'cursor-cli': 'Cursor CLI',
  'copilot-cli': 'Copilot CLI',
  'qwen-code': 'Qwen Code',
}

/** A coding agent's product name, or its id when hal0 has no name for it. */
export function harnessName(id: string): string {
  return HARNESS_NAMES[id] ?? id
}

const positive = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n) && n > 0

/** Every `agent:` and `harness:` tag with at least one fact, by the name after the prefix. */
export function sightingsByName(entries: BankTagsEntry[]): Map<string, Sighting[]> {
  const out = new Map<string, Sighting[]>()
  for (const entry of entries) {
    for (const item of entry.items ?? []) {
      if (!item || typeof item.tag !== 'string' || !positive(item.count)) continue
      const prefix = item.tag.startsWith(AGENT_PREFIX)
        ? AGENT_PREFIX
        : item.tag.startsWith(HARNESS_PREFIX)
          ? HARNESS_PREFIX
          : null
      if (!prefix) continue
      const name = item.tag.slice(prefix.length)
      if (!name) continue
      const list = out.get(name) ?? []
      list.push({ tag: item.tag, bank: entry.bankId, count: item.count })
      out.set(name, list)
    }
  }
  for (const list of out.values()) {
    list.sort((a, b) => b.count - a.count || a.bank.localeCompare(b.bank) || a.tag.localeCompare(b.tag))
  }
  return out
}

/** A client that may use memory today: a live key, the memory scope, and not one hal0 holds. */
export function memoryClients(clients: MemoryClient[] | null | undefined): MemoryClient[] {
  if (!Array.isArray(clients)) return []
  return clients.filter(
    (c) =>
      !!c &&
      typeof c.name === 'string' &&
      c.revoked_at == null &&
      Array.isArray(c.scopes) &&
      c.scopes.includes('memory') &&
      !String(c.created_by ?? '').startsWith('system:') &&
      !BUILT_IN_IDS.has(c.name),
  )
}

function kindOf(id: string, harnesses: Set<string>, sightings: Sighting[], isClient: boolean): AgentKind {
  if (id === 'hermes') return 'hermes'
  if (id === 'pi') return 'pi'
  if (id.startsWith(HERMES_PROFILE_PREFIX)) return 'hermes-profile'
  if (isClient) return 'client'
  if (harnesses.has(id) || sightings.some((s) => s.bank.startsWith(CODING_BANK_PREFIX))) return 'coding'
  return 'tag'
}

function nameOf(id: string, kind: AgentKind): string {
  if (kind === 'hermes') return 'Hermes'
  if (kind === 'pi') return 'Pi'
  if (kind === 'hermes-profile') return `Hermes profile ${id.slice(HERMES_PROFILE_PREFIX.length)}`
  if (kind === 'coding') return harnessName(id)
  return id
}

const KIND_ORDER: AgentKind[] = ['hermes', 'hermes-profile', 'pi', 'coding', 'client', 'tag']

/**
 * The Agents table. A row per name that is installed, holds a memory key, or has tagged facts.
 * `agent:unknown` is not an agent (see `unknownSummary`), and the names of hal0's own parts are
 * left to the "Built into hal0" card.
 */
export function agentRows(input: {
  tags: BankTagsEntry[]
  installed?: InstalledAgent[] | null
  clients?: MemoryClient[] | null
  harnesses?: string[] | null
}): AgentRow[] {
  const seen = sightingsByName(input.tags)
  const clients = new Map(memoryClients(input.clients).map((c) => [c.name, c]))
  const harnesses = new Set(input.harnesses ?? [])

  const ids = new Set<string>()
  for (const agent of input.installed ?? []) {
    if (agent && (agent.name === 'hermes' || agent.name === 'pi')) ids.add(agent.name)
  }
  for (const name of seen.keys()) ids.add(name)
  for (const name of clients.keys()) ids.add(name)
  ids.delete(UNKNOWN_ID)
  for (const id of BUILT_IN_IDS) ids.delete(id)

  const rows: AgentRow[] = [...ids].map((id) => {
    const sightings = seen.get(id) ?? []
    const client = clients.get(id) ?? null
    const kind = kindOf(id, harnesses, sightings, client !== null)
    const name = nameOf(id, kind)
    return {
      id,
      name,
      kind,
      idNote: name === id ? null : id,
      sightings,
      status: sightings.length > 0 ? 'seen writing' : client ? 'connected' : 'not seen',
      client,
    }
  })
  rows.sort(
    (a, b) => KIND_ORDER.indexOf(a.kind) - KIND_ORDER.indexOf(b.kind) || a.name.localeCompare(b.name),
  )
  return rows
}

/** What the card header says: how many rows, and how many in each status that occurs. */
export function rowsSummary(rows: AgentRow[]): string {
  const count = (status: AgentStatus) => rows.filter((r) => r.status === status).length
  const parts = [`${rows.length} ${rows.length === 1 ? 'agent' : 'agents'}`]
  for (const status of ['seen writing', 'connected', 'not seen'] as AgentStatus[]) {
    const n = count(status)
    if (n > 0) parts.push(`${n} ${status}`)
  }
  return parts.join(' · ')
}

export interface UnknownSummary {
  total: number
  banks: { bank: string; count: number }[]
}

/** Facts tagged `agent:unknown`, by bank, or null when there is none. */
export function unknownSummary(entries: BankTagsEntry[]): UnknownSummary | null {
  const banks: { bank: string; count: number }[] = []
  for (const entry of entries) {
    const count = (entry.items ?? [])
      .filter((item) => item && item.tag === UNKNOWN_TAG && positive(item.count))
      .reduce((sum, item) => sum + item.count, 0)
    if (count > 0) banks.push({ bank: entry.bankId, count })
  }
  if (banks.length === 0) return null
  banks.sort((a, b) => b.count - a.count || a.bank.localeCompare(b.bank))
  return { total: banks.reduce((sum, b) => sum + b.count, 0), banks }
}

/** The tagged facts of one of hal0's own parts (brain chat, Open WebUI). */
export function builtInSightings(entries: BankTagsEntry[], id: string): Sighting[] {
  return sightingsByName(entries).get(id) ?? []
}

export interface TagsState {
  loading: boolean
  /** Banks whose tags could not be read. Their facts are in no count. */
  failed: string[]
  /** Every bank failed: there is no evidence to show at all. */
  allFailed: boolean
}

export function tagsState(entries: BankTagsEntry[]): TagsState {
  const failed = entries.filter((e) => e.isError && e.items === null).map((e) => e.bankId)
  return {
    loading: entries.some((e) => e.isLoading),
    failed,
    allFailed: entries.length > 0 && failed.length === entries.length,
  }
}

/**
 * Coding-agent banks that hold no fact naming its writer. An agent connected with the install
 * command alone writes to its repository's bank without an `agent:` tag, so the bank is the
 * only trace of it.
 */
export function unattributedCodingBanks(codingBanks: string[] | null | undefined, entries: BankTagsEntry[]): string[] {
  const byBank = new Map(entries.map((e) => [e.bankId, e]))
  return (codingBanks ?? []).filter((bank) => {
    const entry = byBank.get(bank)
    if (!entry || entry.items === null) return false
    return !entry.items.some(
      (item) =>
        positive(item.count) &&
        (item.tag.startsWith(HARNESS_PREFIX) || (item.tag.startsWith(AGENT_PREFIX) && item.tag !== UNKNOWN_TAG)),
    )
  })
}

// ── The install command ─────────────────────────────────────────────────────

/** The command for one coding agent: the route's template with its `<harness>` filled in. */
export function installCommandFor(template: string | null | undefined, harness: string): string {
  if (!template || !harness) return ''
  return template.replace('<harness>', harness)
}

/** The agents route's answer, read defensively: a body of another shape is "failed", not a crash. */
export function readAgentsRoute(
  query: { data?: unknown; isError?: boolean } | null | undefined,
):
  | { kind: 'loading' }
  | { kind: 'failed' }
  | { kind: 'ready'; banks: string[]; harnesses: string[]; command: string; mintCommand: string; keyFile: string } {
  const data = query?.data as
    | { banks?: unknown; install?: { harnesses?: unknown; command?: unknown; mint_command?: unknown; key_file?: unknown } }
    | null
    | undefined
  if (data === undefined || data === null) return query?.isError ? { kind: 'failed' } : { kind: 'loading' }
  const install = data.install
  if (!install || !Array.isArray(install.harnesses) || typeof install.command !== 'string') {
    return { kind: 'failed' }
  }
  return {
    kind: 'ready',
    banks: Array.isArray(data.banks) ? data.banks.filter((b): b is string => typeof b === 'string') : [],
    harnesses: install.harnesses.filter((h): h is string => typeof h === 'string' && h !== ''),
    command: install.command,
    mintCommand: typeof install.mint_command === 'string' ? install.mint_command : '',
    keyFile: typeof install.key_file === 'string' ? install.key_file : '',
  }
}
