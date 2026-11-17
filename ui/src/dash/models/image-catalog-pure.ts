// Pure parts of Models → Image: a ComfyUI catalog model and a weight file as the shared shell's
// state, and the toolbar's filters and sorts for both views. Nothing here judges a fit: the
// verdict is the server's (`model.fit`).
import { isImageJobActive } from '@/api/hooks/imageJobState'
import type { ComfyuiCatalogModel, ComfyuiFile, ImageJob } from '@/api/hooks/useComfyui'
import { jobProgress, taskLabel } from '@/dash/image/image-models-pure'
import { matchesStateFilter, type CatalogState } from './catalog-pure'

/** What the img capability's selected model is "in use" by. */
export const IMAGE_DEFAULT_USER = 'img'

export function imageState(
  model: Pick<ComfyuiCatalogModel, 'id' | 'state' | 'fit'>,
  job: ImageJob | null | undefined,
  defaultId: string | null | undefined,
  engineAvailable: boolean,
): CatalogState {
  const running = isImageJobActive(job)
  return {
    installed: model.state === 'complete',
    inUseBy: model.state === 'complete' && defaultId === model.id ? [IMAGE_DEFAULT_USER] : [],
    // A job with no known total has no honest percentage.
    download: running && job ? { pct: job.bytes_total > 0 ? jobProgress(job) * 100 : null } : null,
    // With no engine the verdicts are not shown, so they do not mark a row either.
    wontFit: engineAvailable && model.fit?.verdict === 'wont_fit',
  }
}

export const IMAGE_STATE_FILTERS = [
  { id: 'installed', label: 'Installed' },
  { id: 'available', label: 'Not installed' },
] as const
export const IMAGE_FIT_FILTERS = [{ id: 'fits', label: 'Fits this box' }] as const
export const IMAGE_SORT_FIELDS = [
  { id: 'catalog', label: 'catalog order' },
  { id: 'name', label: 'name' },
  { id: 'size', label: 'size' },
]

/** One Task chip per task the catalog holds, in the order the tasks first appear. */
export function imageTaskFilters(models: ReadonlyArray<Pick<ComfyuiCatalogModel, 'task'>>): Array<{ id: string; label: string }> {
  const seen: string[] = []
  for (const m of models) if (!seen.includes(m.task)) seen.push(m.task)
  return seen.map((task) => ({ id: task, label: taskLabel(task) }))
}

export interface ImageFilter {
  q: string
  states: ReadonlyArray<string>
  tasks: ReadonlyArray<string>
  fits: boolean
}

export function filterImageModels(
  models: ReadonlyArray<ComfyuiCatalogModel>,
  f: ImageFilter,
  stateOf: (m: ComfyuiCatalogModel) => CatalogState,
): ComfyuiCatalogModel[] {
  const needle = f.q.trim().toLowerCase()
  return models.filter((m) => {
    if (needle && !`${m.label} ${m.id} ${taskLabel(m.task)}`.toLowerCase().includes(needle)) return false
    const state = stateOf(m)
    if (!matchesStateFilter(f.states, state)) return false
    if (f.tasks.length && !f.tasks.includes(m.task)) return false
    // "Fits this box" drops only the server's "won't fit"; a model with no verdict stays.
    if (f.fits && state.wontFit) return false
    return true
  })
}

/** Sort a copy; "catalog" keeps the order the server lists the models in. */
export function sortImageModels(rows: ReadonlyArray<ComfyuiCatalogModel>, field: string, dir: 'asc' | 'desc'): ComfyuiCatalogModel[] {
  if (field === 'catalog') return dir === 'desc' ? [...rows].reverse() : [...rows]
  const sign = dir === 'desc' ? -1 : 1
  return [...rows].sort((a, b) =>
    field === 'size'
      ? sign * (a.approx_gb - b.approx_gb) || a.label.localeCompare(b.label)
      : sign * a.label.localeCompare(b.label),
  )
}

// ── the Files view ───────────────────────────────────────────────────────────────

export const fileKey = (f: Pick<ComfyuiFile, 'subdir' | 'relpath'>): string => `${f.subdir}/${f.relpath}`
export const fileName = (relpath: string): string => relpath.split('/').pop() ?? relpath
/** Where the file sits, as the listing reports it: `loras/styles` for `styles/a.safetensors`. */
export const fileFolder = (f: Pick<ComfyuiFile, 'subdir' | 'relpath'>): string =>
  [f.subdir, ...f.relpath.split('/').slice(0, -1)].join('/')

export const FILE_OWNER_FILTERS = [
  { id: 'owned', label: 'Used by a model' },
  { id: 'unmanaged', label: 'Unmanaged' },
] as const

/** A weight file is always on disk; the catalog models that load it are what uses it. */
export function fileState(f: Pick<ComfyuiFile, 'models'>, labelOf: (id: string) => string): CatalogState {
  return { installed: true, inUseBy: f.models.map(labelOf) }
}

export function filterFiles<T extends Pick<ComfyuiFile, 'subdir' | 'relpath' | 'models'>>(
  files: ReadonlyArray<T>,
  f: { q: string; owners: ReadonlyArray<string> },
): T[] {
  const needle = f.q.trim().toLowerCase()
  return files.filter((file) => {
    if (needle && !fileKey(file).toLowerCase().includes(needle)) return false
    if (f.owners.length === 0) return true
    return f.owners.includes(file.models.length > 0 ? 'owned' : 'unmanaged')
  })
}

export function sortFiles<T extends Pick<ComfyuiFile, 'relpath' | 'size_bytes'>>(files: ReadonlyArray<T>, field: string, dir: 'asc' | 'desc'): T[] {
  const sign = dir === 'desc' ? -1 : 1
  const byName = (a: T, b: T) => fileName(a.relpath).localeCompare(fileName(b.relpath))
  return [...files].sort((a, b) =>
    field === 'size' ? sign * ((a.size_bytes || 0) - (b.size_bytes || 0)) || byName(a, b) : sign * byName(a, b),
  )
}
