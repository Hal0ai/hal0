// Memory ▸ Knowledge, worked out from the answers the hooks hold. No React and no window.
//
// What the engine sends, and what this file makes of it:
//
//   - A knowledge page IS a mental model. The mental-models list carries both kinds, and marks a
//     page's model with `is_knowledge_page`. The tree names each page's model in
//     `mental_model_id`. A model is a page's when either says so, so nothing is listed twice.
//   - A tree node has `is_stale` and `last_refresh_failed_at` but no `last_refreshed_at`. When a
//     page was last refreshed is read from its model.
//   - `is_stale` means facts in the page's scope were saved after its last refresh. It is normal
//     on a busy bank. A failed refresh is the fault, and only while it is later than the last
//     refresh that worked (the rule Health uses, from health-pure).
//   - A model's history is a list, newest first. An entry is either the text a refresh replaced
//     (`previous_content`, replaced at `changed_at`) or the record of a refresh that failed
//     (`kind: "refresh_failed"`, with `error_message`).
//
// A figure that is not known is `null`, never `0`.

import type { Directive, KnowledgeNode, KnowledgeTree, MentalModel } from '@/api/hooks/useHindsight'
import { refreshFailed } from './health-pure'
import { formatCount, formatDuration } from './shared/format-pure'

/** A mental model as the list sends it. The hook's type names only the fields older views read. */
export type KnowledgeModel = MentalModel & {
  is_knowledge_page?: boolean | null
  trigger?: unknown
}

const text = (value: unknown): string | null => (typeof value === 'string' && value.trim() ? value : null)

const byName = (a: { name: string }, b: { name: string }) =>
  a.name.localeCompare(b.name, 'en', { numeric: true, sensitivity: 'base' })

// ── Pages and models ──────────────────────────────────────────────────────────

/** A knowledge page: its tree node, joined to the mental model it is written from. */
export interface PageRow {
  id: string
  name: string
  /** The question the page answers (the node's `description`, or its model's source query). */
  question: string | null
  tags: string[]
  /** The model that holds the page's text. A page without one cannot be refreshed. */
  modelId: string | null
  stale: boolean
  failed: boolean
  lastRefreshedAt: string | null
  lastFailedAt: string | null
  trigger: unknown
}

/** A mental model that is not a knowledge page. */
export interface ModelRow {
  id: string
  name: string
  question: string | null
  tags: string[]
  stale: boolean
  failed: boolean
  lastRefreshedAt: string | null
  lastFailedAt: string | null
  content: string | null
  trigger: unknown
}

/** The part of a row the status functions read. */
export interface StatusRow {
  stale: boolean
  failed: boolean
  lastRefreshedAt: string | null
}

function pageRow(node: KnowledgeNode, model: KnowledgeModel | undefined): PageRow {
  const lastRefreshedAt = text(model?.last_refreshed_at)
  // The node's own stamp is the newer read; the model's stands in when the node has none.
  const lastFailedAt = text(node.last_refresh_failed_at) ?? text(model?.last_refresh_failed_at)
  return {
    id: node.id,
    name: node.name,
    question: text(node.description) ?? text(model?.source_query),
    tags: node.tags.length > 0 ? node.tags : (model?.tags ?? []),
    modelId: node.mental_model_id,
    stale: node.is_stale === true || model?.is_stale === true,
    failed: refreshFailed({ last_refreshed_at: lastRefreshedAt, last_refresh_failed_at: lastFailedAt }),
    lastRefreshedAt,
    lastFailedAt,
    trigger: model?.trigger ?? null,
  }
}

function modelRow(model: KnowledgeModel): ModelRow {
  return {
    id: model.id,
    name: model.name || model.id,
    question: text(model.source_query),
    tags: Array.isArray(model.tags) ? model.tags : [],
    stale: model.is_stale === true,
    failed: refreshFailed(model),
    lastRefreshedAt: text(model.last_refreshed_at),
    lastFailedAt: text(model.last_refresh_failed_at),
    content: text(model.content),
    trigger: model.trigger ?? null,
  }
}

function walkPages(nodes: KnowledgeNode[], visit: (node: KnowledgeNode) => void) {
  for (const node of sortSiblings(nodes)) {
    if (node.kind === 'page') visit(node)
    walkPages(node.children, visit)
  }
}

/** Folders first, then pages, each by name. The order a file tree is read in. */
export function sortSiblings(nodes: KnowledgeNode[]): KnowledgeNode[] {
  return [...nodes].sort(
    (a, b) => Number(a.kind !== 'folder') - Number(b.kind !== 'folder') || byName(a, b),
  )
}

/**
 * Every page, in the order the tree shows them, and every mental model that is not a page, by
 * name. A model is left out of `models` when the list marks it as a page's, or when a page in
 * the tree names it.
 */
export function splitKnowledge(
  tree: KnowledgeTree | null | undefined,
  models: KnowledgeModel[] | null | undefined,
): { pages: PageRow[]; models: ModelRow[] } {
  const list = Array.isArray(models) ? models : []
  const byId = new Map(list.map((model) => [model.id, model]))
  const pages: PageRow[] = []
  walkPages(tree?.roots ?? [], (node) => {
    pages.push(pageRow(node, node.mental_model_id ? byId.get(node.mental_model_id) : undefined))
  })
  const backing = new Set(pages.map((page) => page.modelId).filter((id): id is string => !!id))
  return {
    pages,
    models: list
      .filter((model) => model.is_knowledge_page !== true && !backing.has(model.id))
      .map(modelRow)
      .sort(byName),
  }
}

// ── Counts ────────────────────────────────────────────────────────────────────

export interface StatusCounts {
  total: number
  stale: number
  failed: number
}

export function statusCounts(rows: StatusRow[]): StatusCounts {
  return {
    total: rows.length,
    stale: rows.filter((row) => row.stale).length,
    failed: rows.filter((row) => row.failed).length,
  }
}

/** The most recent refresh that worked, across pages and models. `null` when none has. */
export function lastSuccessAt(rows: StatusRow[]): string | null {
  let best: string | null = null
  let bestTime = -Infinity
  for (const row of rows) {
    const time = row.lastRefreshedAt ? Date.parse(row.lastRefreshedAt) : NaN
    if (Number.isFinite(time) && time > bestTime) {
      bestTime = time
      best = row.lastRefreshedAt
    }
  }
  return best
}

export interface StatusChipSpec {
  tone: 'ok' | 'warn' | 'err'
  word: 'fresh' | 'stale' | 'failing'
}

/** One chip for a group: failing when any refresh failed, else stale, else fresh. None when empty. */
export function groupChip(counts: StatusCounts): StatusChipSpec | null {
  if (counts.total === 0) return null
  if (counts.failed > 0) return { tone: 'err', word: 'failing' }
  if (counts.stale > 0) return { tone: 'warn', word: 'stale' }
  return { tone: 'ok', word: 'fresh' }
}

const count = (value: number) => formatCount(value) ?? String(value)

/** `all fresh`, `3 of 29 stale`, `3 of 29 stale · 1 failed its last refresh`. */
export function groupHelper(counts: StatusCounts): string | null {
  if (counts.total === 0) return null
  const parts: string[] = []
  if (counts.stale > 0) parts.push(`${count(counts.stale)} of ${count(counts.total)} stale`)
  if (counts.failed > 0) {
    parts.push(`${count(counts.failed)} failed ${counts.failed === 1 ? 'its' : 'their'} last refresh`)
  }
  return parts.length > 0 ? parts.join(' · ') : counts.total === 1 ? 'fresh' : 'all fresh'
}

/**
 * The line in a list's header, and which chips its rows carry. A status every row shares is
 * said once, in the header, and left off the rows; a row carries a chip only where it differs.
 */
export function listStatus(
  counts: StatusCounts,
  one: string,
  many: string,
): { meta: string; rowStale: boolean; rowFailed: boolean } {
  const { total, stale, failed } = counts
  const noun = `${count(total)} ${total === 1 ? one : many}`
  if (total === 0) return { meta: noun, rowStale: false, rowFailed: false }
  const all = (word: string) => (total === 1 ? word : `all ${word}`)
  const parts: string[] = []
  if (stale === total) parts.push(all('stale'))
  else if (stale > 0) parts.push(`${count(stale)} stale`)
  if (failed === total) parts.push(total === 1 ? 'last refresh failed' : 'all failed their last refresh')
  else if (failed > 0) parts.push(`${count(failed)} failed`)
  if (parts.length === 0) parts.push(all('fresh'))
  return {
    meta: `${noun}, ${parts.join(', ')}`,
    rowStale: stale > 0 && stale < total,
    rowFailed: failed > 0 && failed < total,
  }
}

// ── The tree ──────────────────────────────────────────────────────────────────

/** One visible row of the tree, with what an assistive tech needs to place it. */
export interface TreeRow {
  id: string
  kind: 'folder' | 'page'
  name: string
  /** 1 for a root. */
  level: number
  parentId: string | null
  /** Folders only. */
  expanded: boolean
  /** Folders only: how many pages are anywhere under it. */
  pageCount: number
  posInSet: number
  setSize: number
}

const pagesUnder = (node: KnowledgeNode): number =>
  node.children.reduce((sum, child) => sum + (child.kind === 'page' ? 1 : pagesUnder(child)), 0)

/** The rows the tree shows: every node whose ancestors are all open. */
export function treeRows(tree: KnowledgeTree | null | undefined, collapsed: ReadonlySet<string>): TreeRow[] {
  const out: TreeRow[] = []
  const walk = (nodes: KnowledgeNode[], level: number, parentId: string | null) => {
    const sorted = sortSiblings(nodes)
    sorted.forEach((node, at) => {
      const folder = node.kind !== 'page'
      const expanded = folder && !collapsed.has(node.id)
      out.push({
        id: node.id,
        kind: folder ? 'folder' : 'page',
        name: node.name,
        level,
        parentId,
        expanded,
        pageCount: folder ? pagesUnder(node) : 0,
        posInSet: at + 1,
        setSize: sorted.length,
      })
      if (expanded) walk(node.children, level + 1, node.id)
    })
  }
  walk(tree?.roots ?? [], 1, null)
  return out
}

export type TreeKeyAction =
  | { type: 'focus'; id: string }
  | { type: 'toggle'; id: string }
  | { type: 'open'; id: string }

/**
 * What a key does on the row that has focus, as in any tree: Up and Down move through the
 * visible rows, Home and End jump to the ends, Right opens a closed folder or steps into an
 * open one, Left closes an open folder or steps out to the parent, Enter and Space open a page
 * or toggle a folder. `null` for a key that is not the tree's, or with nowhere to go.
 */
export function treeKeyAction(rows: TreeRow[], currentId: string | null, key: string): TreeKeyAction | null {
  if (rows.length === 0) return null
  const at = rows.findIndex((row) => row.id === currentId)
  const row = at === -1 ? null : rows[at]
  const focus = (target: TreeRow | undefined): TreeKeyAction | null =>
    target && target.id !== currentId ? { type: 'focus', id: target.id } : null
  switch (key) {
    case 'ArrowDown':
      return focus(rows[at === -1 ? 0 : at + 1])
    case 'ArrowUp':
      return focus(rows[at === -1 ? rows.length - 1 : at - 1])
    case 'Home':
      return focus(rows[0])
    case 'End':
      return focus(rows[rows.length - 1])
    case 'ArrowRight':
      if (!row || row.kind !== 'folder') return null
      if (!row.expanded) return { type: 'toggle', id: row.id }
      return rows[at + 1]?.parentId === row.id ? { type: 'focus', id: rows[at + 1].id } : null
    case 'ArrowLeft':
      if (!row) return null
      if (row.kind === 'folder' && row.expanded) return { type: 'toggle', id: row.id }
      return row.parentId ? { type: 'focus', id: row.parentId } : null
    case 'Enter':
    case ' ':
      if (!row) return null
      return row.kind === 'folder' ? { type: 'toggle', id: row.id } : { type: 'open', id: row.id }
    default:
      return null
  }
}

// ── What is open ──────────────────────────────────────────────────────────────

export type Selection =
  | { kind: 'page'; page: PageRow }
  | { kind: 'model'; model: ModelRow }
  | { kind: 'missing'; id: string }
  | null

/**
 * What the reader shows for the address's `page=`. The id is a page's or, failing that, a
 * mental model's, so both can be linked to. With no id the first page is shown (the first
 * model in a bank with no pages); an id that matches nothing is `missing`, and is never
 * swapped for another page.
 */
export function resolveSelection(id: string | null, pages: PageRow[], models: ModelRow[]): Selection {
  if (!id) {
    if (pages.length > 0) return { kind: 'page', page: pages[0] }
    return models.length > 0 ? { kind: 'model', model: models[0] } : null
  }
  const page = pages.find((row) => row.id === id)
  if (page) return { kind: 'page', page }
  const model = models.find((row) => row.id === id)
  return model ? { kind: 'model', model } : { kind: 'missing', id }
}

// ── A page's text ─────────────────────────────────────────────────────────────

/** `---\nid: …\n---\n\n## Body` → `## Body`. Text with no front matter is returned as it is. */
export function stripFrontMatter(markdown: string): string {
  const match = /^---\r?\n[\s\S]*?\r?\n---[ \t]*(\r?\n|$)/.exec(markdown)
  return match ? markdown.slice(match[0].length).replace(/^\s*\n/, '') : markdown
}

/**
 * The text of a page, from `GET .../knowledge-base/pages/{id}`. A 1.x engine sends it as
 * `body` (and again, under front matter, as `markdown`); `content` is accepted too. `null` for
 * a page no refresh has written yet.
 */
export function pageBody(page: unknown): string | null {
  if (!page || typeof page !== 'object') return null
  const fields = page as Record<string, unknown>
  const body = text(fields.body) ?? text(fields.content)
  if (body) return body
  // `markdown` says "No content yet." in words for an empty page; an empty `body` beside it
  // means there is nothing to show.
  if ('body' in fields) return null
  const markdown = text(fields.markdown)
  return markdown ? text(stripFrontMatter(markdown)) : null
}

// ── Errors ────────────────────────────────────────────────────────────────────

const UNEXPLAINED = 'The engine did not say why.'

function messageIn(value: unknown, depth = 0): string | null {
  if (typeof value === 'string') return value.trim() || null
  if (!value || typeof value !== 'object' || depth > 3) return null
  const fields = value as Record<string, unknown>
  for (const key of ['message', 'detail', 'error', 'error_message', 'reason']) {
    const found = messageIn(fields[key], depth + 1)
    if (found) return found
  }
  return null
}

/**
 * An error as one short sentence. An `Error` gives its message; a JSON body gives the message
 * inside it; anything longer than one sentence is cut to its first, and to `max` characters.
 * Never JSON, never empty.
 */
export function shortError(input: unknown, max = 160): string {
  let message = input instanceof Error ? input.message : messageIn(input)
  if (!message) return UNEXPLAINED
  const trimmed = message.trim()
  if (/^[[{]/.test(trimmed)) {
    try {
      message = messageIn(JSON.parse(trimmed))
    } catch {
      message = null
    }
    if (!message) return UNEXPLAINED
  }
  const flat = message.replace(/\s+/g, ' ').trim()
  const sentence = /^.*?[.!?](?=\s|$)/.exec(flat)?.[0] ?? flat
  if (sentence.length <= max) return sentence
  return `${sentence.slice(0, max - 1).trimEnd()}…`
}

// ── History ───────────────────────────────────────────────────────────────────

/**
 * One entry of a model's history.
 *   `version`  the text a refresh replaced, and when it was replaced
 *   `first`    the first refresh: there was no text before it
 *   `failure`  a refresh that failed, and why
 */
export interface HistoryEntry {
  key: string
  kind: 'version' | 'first' | 'failure'
  at: string | null
  content: string | null
  reason: string | null
}

/** A history list as the engine sends it, read into entries, newest first. */
export function historyEntries(items: unknown[] | null | undefined): HistoryEntry[] {
  const entries = (Array.isArray(items) ? items : [])
    .filter((item): item is Record<string, unknown> => !!item && typeof item === 'object' && !Array.isArray(item))
    .map((item, order) => {
      const when = text(item.changed_at) ?? text(item.updated_at) ?? text(item.created_at)
      const failed = typeof item.kind === 'string' && item.kind.includes('fail')
      const content = failed ? null : (text(item.previous_content) ?? text(item.content))
      const entry: HistoryEntry = {
        key: `${when ?? 'undated'}-${order}`,
        kind: failed ? 'failure' : content ? 'version' : 'first',
        at: when,
        content,
        reason: failed ? shortError(item.error_message ?? item.failure_reason ?? item.outcome) : null,
      }
      return { entry, order, time: when ? Date.parse(when) : NaN }
    })
  return entries
    .sort((a, b) => {
      // An undated entry keeps the place the engine gave it.
      if (!Number.isFinite(a.time) || !Number.isFinite(b.time) || a.time === b.time) return a.order - b.order
      return b.time - a.time
    })
    .map(({ entry }) => entry)
}

/** Why the most recent refresh failed, when the newest entry of the history is that failure. */
export function latestFailureReason(entries: HistoryEntry[]): string | null {
  return entries[0]?.kind === 'failure' ? entries[0].reason : null
}

// ── Refresh settings, in words ────────────────────────────────────────────────

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const two = (value: number) => String(value).padStart(2, '0')
const whole = (value: string, max: number): number | null =>
  /^\d{1,2}$/.test(value) && Number(value) <= max ? Number(value) : null

/**
 * A five-field cron schedule in words, for the shapes the engine's pages use: `every 4 h`,
 * `every day at 03:00 UTC`, `every Sunday at 03:00 UTC`. `null` for any other shape; the
 * caller then shows the schedule itself.
 */
export function cronWords(expression: string): string | null {
  const fields = expression.trim().split(/\s+/)
  if (fields.length !== 5) return null
  const [minute, hour, dayOfMonth, month, dayOfWeek] = fields
  if (dayOfMonth !== '*' || month !== '*') return null
  const m = whole(minute, 59)
  const everyHours = /^\*\/(\d{1,2})$/.exec(hour)
  if (everyHours && m !== null && dayOfWeek === '*') {
    const n = Number(everyHours[1])
    if (n < 1 || n > 23) return null
    return m === 0 ? `every ${n} h` : `every ${n} h, at ${m} min past the hour`
  }
  if (hour === '*' && dayOfWeek === '*' && m !== null) {
    return m === 0 ? 'every hour' : `every hour, at ${m} min past`
  }
  const h = whole(hour, 23)
  if (h === null || m === null) return null
  const at = `${two(h)}:${two(m)} UTC`
  if (dayOfWeek === '*') return `every day at ${at}`
  const day = whole(dayOfWeek, 7)
  return day === null ? null : `every ${DAYS[day % 7]} at ${at}`
}

const schedule = (expression: string) => cronWords(expression) ?? `on the schedule ${expression} (UTC)`

/**
 * A page's or model's refresh settings as sentences. Only what was set is said; an unset
 * setting follows the bank and is left out.
 */
export function triggerWords(trigger: unknown): string[] {
  if (!trigger || typeof trigger !== 'object') return []
  const t = trigger as Record<string, unknown>
  const out: string[] = []
  const cron = text(t.refresh_cron)
  if (cron) out.push(`Refreshes ${schedule(cron)}, when facts in its scope have changed.`)
  else if (t.refresh_after_consolidation === true) out.push('Refreshes each time new facts are consolidated.')
  else out.push('Refreshes only when one is started by hand.')
  const fullCron = text(t.full_refresh_cron)
  if (fullCron) out.push(`Rewritten from scratch ${schedule(fullCron)}.`)
  if (t.mode === 'delta') out.push('A refresh edits the existing text and leaves unchanged sections as they are.')
  else if (t.mode === 'full') out.push('A refresh rewrites the whole text.')
  const floor = t.min_refresh_interval_seconds
  if (typeof floor === 'number' && Number.isFinite(floor)) {
    out.push(
      floor > 0
        ? `At most one automatic refresh every ${formatDuration(floor)}.`
        : 'No minimum time between automatic refreshes.',
    )
  }
  const types = Array.isArray(t.fact_types) ? t.fact_types.filter((v): v is string => typeof v === 'string') : []
  if (types.length > 0 && types.length < 3) out.push(`Written from ${types.join(' and ')} facts only.`)
  return out
}

// ── The bank's refresh interval ───────────────────────────────────────────────

/** The choices the selector offers, in seconds. `0` is no wait at all. */
export const INTERVAL_SECONDS = [0, 3600, 14400, 43200, 86400] as const

/**
 * The bank's minimum time between automatic refreshes, from its config. `0` when the config
 * loaded and does not set one (the engine's default); `null` while the config is not known.
 */
export function intervalSeconds(config: unknown): number | null {
  if (!config || typeof config !== 'object') return null
  const raw = (config as Record<string, unknown>).mental_model_min_refresh_interval_seconds
  if (raw === null || raw === undefined) return 0
  const value = Number(raw)
  return Number.isFinite(value) && value >= 0 ? value : 0
}

/**
 * `14400` → `4 h`; `0` → `time facts are consolidated` (no wait at all). Each completes the
 * sentence "Refresh at most every …".
 */
export function intervalLabel(seconds: number): string {
  return seconds > 0 ? (formatDuration(seconds) ?? `${seconds} s`) : 'time facts are consolidated'
}

/** The selector's options; a value the bank holds that is not a standard choice is added. */
export function intervalOptions(current: number | null): { seconds: number; label: string }[] {
  const values: number[] = [...INTERVAL_SECONDS]
  if (current !== null && !values.includes(current)) values.push(current)
  return values.sort((a, b) => a - b).map((seconds) => ({ seconds, label: intervalLabel(seconds) }))
}

// ── Directives ────────────────────────────────────────────────────────────────

/** Highest priority first (the order agents are given them in), then by name. */
export function orderDirectives(items: Directive[] | null | undefined): Directive[] {
  return [...(Array.isArray(items) ? items : [])].sort(
    (a, b) => (b.priority ?? 0) - (a.priority ?? 0) || byName(a, b),
  )
}

export interface DirectiveDraft {
  name: string
  content: string
  priority: string
}

export const EMPTY_DIRECTIVE: DirectiveDraft = { name: '', content: '', priority: '0' }

export function directiveDraft(directive: Directive): DirectiveDraft {
  return { name: directive.name, content: directive.content, priority: String(directive.priority ?? 0) }
}

const INT32 = 2 ** 31

/**
 * A draft checked into the three fields the engine takes, or the reason it cannot be saved.
 * The priority is a whole number; an empty priority is 0.
 */
export function directiveBody(
  draft: DirectiveDraft,
): { ok: true; body: { name: string; content: string; priority: number } } | { ok: false; reason: string } {
  const name = draft.name.trim()
  const content = draft.content.trim()
  if (!name) return { ok: false, reason: 'Give the directive a name.' }
  if (!content) return { ok: false, reason: 'Write the text agents are given.' }
  const raw = draft.priority.trim()
  if (raw !== '' && !/^-?\d+$/.test(raw)) return { ok: false, reason: 'Priority is a whole number.' }
  const priority = raw === '' ? 0 : Number(raw)
  if (priority < -INT32 || priority >= INT32) return { ok: false, reason: 'Priority is out of range.' }
  return { ok: true, body: { name, content, priority } }
}

/** `a, b ,, c` → `['a', 'b', 'c']`, in order, each once. */
export function parseTags(input: string): string[] {
  const seen = new Set<string>()
  for (const part of input.split(',')) {
    const tag = part.trim()
    if (tag) seen.add(tag)
  }
  return [...seen]
}

// ── Refresh all stale ─────────────────────────────────────────────────────────

export interface RefreshTarget {
  /** The mental model to refresh (a page is refreshed through its model). */
  modelId: string
  name: string
  kind: 'page' | 'model'
}

/** What "Refresh all stale" refreshes: every page and model that is stale or failed its last refresh. */
export function staleTargets(pages: PageRow[], models: ModelRow[]): RefreshTarget[] {
  const out: RefreshTarget[] = []
  for (const page of pages) {
    if ((page.stale || page.failed) && page.modelId) out.push({ modelId: page.modelId, name: page.name, kind: 'page' })
  }
  for (const model of models) {
    if (model.stale || model.failed) out.push({ modelId: model.id, name: model.name, kind: 'model' })
  }
  return out
}

export interface RefreshAllResult {
  /** How many refreshes were started. */
  started: number
  total: number
  /** The one that did not start, and why. The loop stops there. */
  failed: { target: RefreshTarget; reason: string } | null
}

/**
 * Start a refresh for each target, one at a time, in order. There is no batch route, so this
 * is a loop over the single-item one. It stops at the first that does not start, so a broken
 * engine is asked once and not once per page.
 */
export async function refreshAll(
  targets: RefreshTarget[],
  start: (target: RefreshTarget) => Promise<unknown>,
  onProgress: (progress: { at: number; total: number; target: RefreshTarget }) => void = () => {},
): Promise<RefreshAllResult> {
  let started = 0
  for (const target of targets) {
    onProgress({ at: started + 1, total: targets.length, target })
    try {
      await start(target)
    } catch (error) {
      return { started, total: targets.length, failed: { target, reason: shortError(error) } }
    }
    started += 1
  }
  return { started, total: targets.length, failed: null }
}

// ── A refresh that was started ────────────────────────────────────────────────

/** A model's refresh stamps when its refresh was started, to tell when that refresh ends. */
export interface RefreshWatch {
  refreshedAt: string | null
  failedAt: string | null
  since: number
}

/** A refresh takes minutes at most. Past this the page stops waiting and points at Operations. */
export const WATCH_LIMIT_MS = 10 * 60 * 1000

/**
 * What became of a started refresh, read from the model's stamps now: still `running`, `done`
 * (a refresh completed since), `failed` (one failed since), or `overdue` (nothing changed for
 * longer than the limit).
 */
export function watchOutcome(
  watch: RefreshWatch,
  now: { lastRefreshedAt: string | null; lastFailedAt: string | null } | null | undefined,
  nowMs: number,
): 'running' | 'done' | 'failed' | 'overdue' {
  if (now) {
    if (now.lastFailedAt && now.lastFailedAt !== watch.failedAt) return 'failed'
    if (now.lastRefreshedAt && now.lastRefreshedAt !== watch.refreshedAt) return 'done'
  }
  return nowMs - watch.since > WATCH_LIMIT_MS ? 'overdue' : 'running'
}
