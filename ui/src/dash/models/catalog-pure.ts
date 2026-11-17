// Pure parts of the Models page's shared catalog shell: the one status vocabulary every tab's
// rows use, the State filter, sectioning with pagination, and "runs on" for a registry row.
// The React parts are catalog-shell.jsx; each tab feeds both from its own data.
import type { XdnaFit, XdnaFitRow } from '@/api/hooks/useXdnaFit'

/** What a row's status is derived from. Every model type fills in what it knows. */
export interface CatalogState {
  /** The model's files are on this host. */
  installed: boolean
  /** Served by an upstream: nothing is stored here and nothing can be downloaded. */
  remote?: boolean
  /** Names of what uses it (slots, lanes, a default selection). Empty or absent = free. */
  inUseBy?: ReadonlyArray<string>
  /** A newer file is available at the source. */
  update?: boolean
  /** A download is running; `pct` is null while its size is unknown or it is queued. */
  download?: { pct: number | null } | null
  /** The server says it cannot run on this box. */
  wontFit?: boolean
}

export type StatusKind = 'downloading' | 'update' | 'inuse' | 'installed' | 'remote' | 'wontfit' | 'available'
export interface CatalogStatus {
  kind: StatusKind
  label: string
  /** The `.chip` modifier: '' is the plain chip. */
  tone: '' | 'ok' | 'amber' | 'info' | 'err'
  title?: string
}

/** The status chip for a row. One model shows one status; the order is what the operator most
 *  needs to know first: a running download, then a pending update, then who is using it. */
export function catalogStatus(s: CatalogState): CatalogStatus {
  if (s.download) {
    const pct = s.download.pct
    return { kind: 'downloading', tone: 'amber', label: pct == null ? 'downloading' : `downloading ${Math.round(pct)}%` }
  }
  const users = s.inUseBy ?? []
  if (s.remote) {
    // An upstream row always says where it comes from; who uses it is in the tooltip and the section.
    const note = 'Served by an upstream. Not stored on this host.'
    return { kind: 'remote', tone: 'info', label: 'upstream', title: users.length ? `${note} In use by ${users.join(', ')}.` : note }
  }
  if (!s.installed) {
    return s.wontFit
      ? { kind: 'wontfit', tone: 'err', label: "won't fit", title: 'Too large for this box.' }
      : { kind: 'available', tone: '', label: 'not installed' }
  }
  if (s.update) return { kind: 'update', tone: 'amber', label: 'update ↑', title: 'A newer version of this file is available.' }
  if (users.length) return { kind: 'inuse', tone: 'ok', label: `in use · ${users[0]}`, title: `In use by ${users.join(', ')}` }
  return { kind: 'installed', tone: 'ok', label: 'installed' }
}

export type DotKind = 'busy' | 'remote' | 'inuse' | 'installed' | 'empty'
/** The leading state dot: the same facts as the chip, without the update. */
export function catalogDot(s: CatalogState): DotKind {
  if (s.download) return 'busy'
  if (s.remote) return 'remote'
  if (!s.installed) return 'empty'
  return (s.inUseBy ?? []).length ? 'inuse' : 'installed'
}

export const STATE_FILTERS = [
  { id: 'installed', label: 'Installed' },
  { id: 'inuse', label: 'In use' },
  { id: 'available', label: 'Not installed' },
  { id: 'update', label: 'Update' },
] as const
export type StateFilterId = (typeof STATE_FILTERS)[number]['id']

/** Multi-select OR, like the page's other chips: nothing selected shows everything. */
export function matchesStateFilter(sel: ReadonlyArray<string>, s: CatalogState): boolean {
  if (sel.length === 0) return true
  return sel.some((id) => {
    if (id === 'installed') return s.installed && !s.remote
    if (id === 'inuse') return (s.inUseBy ?? []).length > 0
    if (id === 'available') return !s.installed && !s.remote
    if (id === 'update') return !!s.update
    return false
  })
}

/** Which of the three standard sections a row belongs in. */
export function sectionKey(s: CatalogState): 'inuse' | 'installed' | 'available' {
  if (s.installed || s.remote) return (s.inUseBy ?? []).length ? 'inuse' : 'installed'
  return 'available'
}

export interface CatalogSection<T> {
  key: string
  /** The heading without its count; the count of the whole section is appended on render. */
  label: string
  rows: T[]
}
export interface PagedSections<T> {
  /** The sections with only the rows on this page; a section with none on the page is dropped. */
  sections: Array<CatalogSection<T> & { total: number }>
  page: number
  totalPages: number
  total: number
}

/** Cut one page out of the sections, read in order. `perPage` "all" shows everything. A page past
 *  the end is clamped to the last one, so narrowing a filter never strands the list on an empty page. */
export function pageSections<T>(
  sections: ReadonlyArray<CatalogSection<T>>,
  page: number,
  perPage: number | 'all',
): PagedSections<T> {
  const total = sections.reduce((n, s) => n + s.rows.length, 0)
  const size = perPage === 'all' ? Math.max(total, 1) : perPage
  const totalPages = Math.max(1, Math.ceil(total / size))
  const safe = Math.min(Math.max(1, page), totalPages)
  const from = (safe - 1) * size
  const to = from + size
  const out: PagedSections<T>['sections'] = []
  let at = 0
  for (const s of sections) {
    const lo = Math.max(from - at, 0)
    const hi = Math.min(to - at, s.rows.length)
    if (hi > lo) out.push({ ...s, rows: s.rows.slice(lo, hi), total: s.rows.length })
    at += s.rows.length
  }
  return { sections: out, page: safe, totalPages, total }
}

// ── registry rows: where a model can run ───────────────────────────────────────

export type RunsOn = 'gpu' | 'cpu' | 'npu'
export const RUNS_ON_FILTERS: ReadonlyArray<{ id: RunsOn; label: string }> = [
  { id: 'gpu', label: 'GPU' },
  { id: 'cpu', label: 'CPU' },
  { id: 'npu', label: 'NPU' },
]

/** The ggml-xdna fit for a registry row, or null when the NPU cannot or should not run it
 *  (no fit row, no path, or the server marked it impractical). */
export function npuFitOf(modelId: string, fit: XdnaFit | null | undefined): XdnaFitRow | null {
  const row = fit?.[modelId]
  if (!row || !row.xdna_path || row.npu_ok === false) return null
  return row
}

/** Devices a registry row can run on: its backends, plus the NPU when ggml-xdna fits it.
 *  `rocm`, `vulkan` and any other accelerator backend all read as GPU. */
export function runsOn(
  model: { id: string; backends?: ReadonlyArray<string> | null; device?: string | null },
  fit?: XdnaFit | null,
): RunsOn[] {
  const names = [...(model.backends ?? []), model.device ?? ''].map((b) => String(b).toLowerCase()).filter(Boolean)
  const out = new Set<RunsOn>()
  for (const b of names) {
    if (b === 'cpu') out.add('cpu')
    else if (b === 'npu' || b === 'xdna' || b === 'flm') out.add('npu')
    else out.add('gpu')
  }
  if (npuFitOf(model.id, fit)) out.add('npu')
  return RUNS_ON_FILTERS.map((f) => f.id).filter((id) => out.has(id))
}

/** Slots that reference a registry model, by the same rule the detail pane's "Used by" uses. */
export function slotsUsing(
  slots: ReadonlyArray<{ name?: string; model_id?: string | null; model?: unknown }> | null | undefined,
  modelId: string,
): string[] {
  const out: string[] = []
  for (const s of slots ?? []) {
    if (!s?.name || out.includes(s.name)) continue
    if (s.model_id === modelId || s.model === modelId) out.push(s.name)
  }
  return out
}
