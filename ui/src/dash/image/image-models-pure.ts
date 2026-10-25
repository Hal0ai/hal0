// Models → Image: how the catalog is grouped, what each card's install button does and how far
// a download has got. Pure, so it is tested without rendering
// (__tests__/image-models-pure.test.ts).
//
// The catalog comes from GET /api/comfyui/models (`useComfyuiModels`) and the install jobs from
// GET /api/comfyui/jobs (`useImageJobs`). Nothing here judges whether a model fits this box:
// that verdict is the server's (`model.fit`).

import { isImageJobActive, type ComfyuiCatalogModel, type Fit, type ImageJob } from '@/api/hooks/useComfyui'

// The catalog's task keys in reading order, with the group header each gets. A task the
// catalog grows later and this table does not know still shows, after these, under its key.
const TASKS: ReadonlyArray<{ task: string; label: string }> = [
  { task: 'txt2img', label: 'Text → Image' },
  { task: 'img2img', label: 'Image edit' },
  { task: 'txt2video', label: 'Text → Video' },
  { task: 'img2video', label: 'Image → Video' },
  { task: 'upscale', label: 'Upscale' },
]

export interface TaskGroup {
  task: string
  label: string
  models: ComfyuiCatalogModel[]
}

// The groups in reading order, each holding its models in the order the catalog lists them.
// A group with no model is left out.
export function groupByTask(models: readonly ComfyuiCatalogModel[]): TaskGroup[] {
  const groups = new Map<string, TaskGroup>(TASKS.map((t) => [t.task, { ...t, models: [] }]))
  for (const model of models) {
    let group = groups.get(model.task)
    if (!group) {
      group = { task: model.task, label: model.task, models: [] }
      groups.set(model.task, group)
    }
    group.models.push(model)
  }
  return [...groups.values()].filter((g) => g.models.length > 0)
}

export type StateActionId = 'install' | 'cancel' | 'remove'

export interface StateAction {
  id: StateActionId
  label: string
  disabled: boolean
}

// 4.2 -> "4.2", 12 -> "12": the server rounds to one decimal already.
const fmtGb = (gb: number) => String(Number(gb.toFixed(1)))

// What the card's install-lifecycle button is, from the model's state and its latest job (the
// fresher `useImageJobs` copy, not necessarily `model.job`).
//
//   - a job queued or running -> Cancel, whatever the files say: the last file can land before
//     the job closes;
//   - none -> Install;
//   - partial -> "Install · {missing_gb} GB to download". After an API restart the job is gone
//     but the files are half there (or another model holds the shared ones), so the card says
//     what is left to fetch, never "Resume" and never the full size;
//   - complete -> Remove.
//
// Install needs the engine (a host with no ROCm lane lists the catalog but cannot run it);
// cancelling a download and removing files do not.
export function stateAction(
  model: Pick<ComfyuiCatalogModel, 'state' | 'missing_gb'>,
  job: ImageJob | null | undefined,
  engineAvailable: boolean,
): StateAction {
  if (isImageJobActive(job)) return { id: 'cancel', label: 'Cancel', disabled: false }
  if (model.state === 'complete') return { id: 'remove', label: 'Remove', disabled: false }
  const label =
    model.state === 'partial' && model.missing_gb > 0
      ? `Install · ${fmtGb(model.missing_gb)} GB to download`
      : 'Install'
  return { id: 'install', label, disabled: !engineAvailable }
}

// How much of the download is done, 0..1. A job with no known total reads 0, not NaN; a
// finished one reads 1 (an install with nothing to download still completes).
export function jobProgress(job: ImageJob | null | undefined): number {
  if (!job) return 0
  if (job.state === 'completed') return 1
  if (job.bytes_total <= 0) return 0
  return Math.min(1, Math.max(0, job.bytes_downloaded / job.bytes_total))
}

// The models the Generate tab can offer: installed and rendered through the API, in catalog
// order. (User checkpoints come from the file inventory, not the catalog.)
export function renderableInstalled(models: readonly ComfyuiCatalogModel[]): ComfyuiCatalogModel[] {
  return models.filter((m) => m.api_renderable && m.state === 'complete')
}

// "1024x1024" -> {width, height}, or null. The server's wire form: two to four digits a side
// and a lower-case x.
export function parseSize(size: string): { width: number; height: number } | null {
  const m = /^(\d{2,4})x(\d{2,4})$/.exec(size)
  return m ? { width: Number(m[1]), height: Number(m[2]) } : null
}

// Download speed (MB = 10^6 bytes) and time left between two polls of the same job, `dtS`
// seconds apart. null when there is nothing honest to say: the first poll, a different job
// (the model was installed again), no time elapsed, no bytes moved, no known total, or a
// download already finished.
export function jobRate(
  prev: ImageJob | null,
  cur: ImageJob,
  dtS: number,
): { mbPerS: number; etaS: number } | null {
  if (!prev || prev.id !== cur.id || dtS <= 0) return null
  const moved = cur.bytes_downloaded - prev.bytes_downloaded
  const left = cur.bytes_total - cur.bytes_downloaded
  if (moved <= 0 || cur.bytes_total <= 0 || left <= 0) return null
  const bytesPerS = moved / dtS
  return { mbPerS: bytesPerS / 1e6, etaS: left / bytesPerS }
}

// "Fits · {spare} GB to spare": how much headroom the tighter of the two pools has, in GiB to
// one decimal and never below 0. A display subtraction of two figures the server gave (the pool
// a render can use less what it needs, the same for RAM); it never decides whether the model
// fits, which is `fit.verdict`.
export function fitSpareGb(fit: Fit): number {
  const spare = Math.min(fit.render_free_gb - fit.need.gpu_gb, fit.ram_free_gb - fit.need.ram_gb)
  return Math.max(0, Math.round(spare * 10) / 10)
}
