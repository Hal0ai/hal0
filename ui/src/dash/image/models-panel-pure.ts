// Models tab (ImageModelsPanel): the pure parts. Which cards a filter keeps, the badges a card
// wears, the sentence that says why a model was recommended, what a removal deletes and keeps,
// and the first-run picks. Tested without rendering (__tests__/models-panel-pure.test.ts).
//
// Nothing here judges whether a model fits this box: that verdict is the server's (`model.fit`).
// The helpers only read it, to filter on it or to pick a model whose verdict is not "won't fit".

import type { ComfyuiCatalogModel, ComfyuiFile, ComfyuiRecommendation } from '@/api/hooks/useComfyui'

export type ModelFilter = 'all' | 'fits' | 'installed'

// The full-precision video variants (`ltx2-t2v-bf16`, `wan22-t2v-fp16`): the heavy builds of a
// model that also has a lighter fp8 one, kept behind a "More" toggle in their group.
export const isFullPrecision = (id: string): boolean => /-(fp16|bf16)$/.test(id)

// `NC` as a part of the license id (`SAI-NC-Research-Community`, `CC-BY-NC-SA-4.0`), matched
// case-sensitively and not inside a word: "Tencent" has an "nc" and is not non-commercial.
export const isNonCommercial = (license: string): boolean => /(^|[^A-Za-z])NC([^A-Za-z]|$)/.test(license)

/** What a render produces, for "~{est_seconds} s per image/clip". */
export const perUnit = (task: string): 'image' | 'clip' =>
  task === 'txt2video' || task === 'img2video' ? 'clip' : 'image'

// A model at or above this many GiB wears "Large".
const LARGE_GB = 30

// A card's badges, in the order they are drawn. `defaultId` is the model the img capability
// has selected, or null.
export function modelBadges(model: ComfyuiCatalogModel, defaultId: string | null | undefined): string[] {
  const badges: string[] = []
  if (model.recommended) badges.push('Recommended')
  if (defaultId && model.id === defaultId) badges.push('Default')
  if (!model.api_renderable) badges.push('Editor only')
  if (model.approx_gb >= LARGE_GB) badges.push('Large')
  if (isNonCommercial(model.license)) badges.push('Non-commercial')
  return badges
}

// Whether a card stays under a filter. "Fits this box" drops only the server's "won't fit": a
// model with no verdict (`fit` null) stays, since an unknown fit is not a refusal. When the
// engine is unavailable the verdicts are not shown, so they cannot filter either.
export function passesFilter(model: ComfyuiCatalogModel, filter: ModelFilter, engineAvailable: boolean): boolean {
  if (filter === 'fits') return !engineAvailable || model.fit?.verdict !== 'wont_fit'
  if (filter === 'installed') return model.state === 'complete'
  return true
}

// "chosen from {pool_gb} GB of RAM (container limit|system)" or "chosen from {pool_gb} GB of GPU
// memory": the figure the server's picker really used (`recommendation`, not a second reading),
// and for RAM where the limit comes from (`memory.ram.source`). null without a recommendation
// (a box that was never probed has no figure to cite).
export function recommendationReason(
  recommendation: ComfyuiRecommendation | null | undefined,
  ramSource: 'container' | 'system' | null | undefined,
): string | null {
  if (!recommendation) return null
  const { pool_gb: gb, limited_by: limitedBy } = recommendation
  if (limitedBy === 'gpu') return `chosen from ${gb} GB of GPU memory`
  const suffix = ramSource === 'container' ? ' (container limit)' : ramSource === 'system' ? ' (system)' : ''
  return `chosen from ${gb} GB of RAM${suffix}`
}

export interface FirstRunPicks {
  recommended: ComfyuiCatalogModel
  others: ComfyuiCatalogModel[]
}

// The first-run block: the recommended renderable model, and the two smallest other renderable
// ones as lighter alternatives. null when the catalog names no recommended renderable model.
export function firstRunPicks(models: readonly ComfyuiCatalogModel[]): FirstRunPicks | null {
  const renderable = models.filter((m) => m.api_renderable)
  const recommended = renderable.find((m) => m.recommended)
  if (!recommended) return null
  const others = renderable
    .filter((m) => m.id !== recommended.id)
    .sort((a, b) => a.approx_gb - b.approx_gb)
    .slice(0, 2)
  return { recommended, others }
}

// What a card whose verdict is "won't fit" offers instead: the recommended model, or the
// largest renderable model that fits, whichever is a different model with a verdict that is not
// "won't fit". Only for a renderable card (a video model that does not fit has no image-model
// substitute). null when there is nothing better to offer.
export function suggestAlternative(
  model: ComfyuiCatalogModel,
  models: readonly ComfyuiCatalogModel[],
): ComfyuiCatalogModel | null {
  if (!model.api_renderable) return null
  const fits = (m: ComfyuiCatalogModel) => m.fit != null && m.fit.verdict !== 'wont_fit'
  const others = models.filter((m) => m.api_renderable && m.id !== model.id)
  const recommended = others.find((m) => m.recommended)
  if (recommended && fits(recommended)) return recommended
  return others.filter(fits).sort((a, b) => b.approx_gb - a.approx_gb)[0] ?? null
}

export interface PlannedFile {
  path: string
  size: number
}
export interface KeptFile extends PlannedFile {
  // The labels of the other complete models that load it.
  usedBy: string[]
}
export interface RemovalPlan {
  deleted: PlannedFile[]
  kept: KeptFile[]
  // The workflow file the removal deletes with the weights (`<id>.json`).
  workflow: string
}

// What removing `model` does, from the `/files` listing: `DELETE /models/{id}` has no dry run.
// A weight file the model loads goes, unless another COMPLETE model loads it too (a model only
// partly installed cannot run without its missing files, so it does not hold a file back);
// that file stays. This mirrors the server's own rule (`remove_model`), which has the last word.
export function removalPlan(
  model: Pick<ComfyuiCatalogModel, 'id' | 'workflow'>,
  files: readonly ComfyuiFile[],
  models: readonly Pick<ComfyuiCatalogModel, 'id' | 'label' | 'state'>[],
): RemovalPlan {
  const byId = new Map(models.map((m) => [m.id, m]))
  const plan: RemovalPlan = { deleted: [], kept: [], workflow: model.workflow }
  for (const f of files) {
    if (!f.models.includes(model.id)) continue
    const path = `${f.subdir}/${f.relpath}`
    const holders = f.models.filter((id) => id !== model.id && byId.get(id)?.state === 'complete')
    if (holders.length > 0) {
      plan.kept.push({ path, size: f.size_bytes, usedBy: holders.map((id) => byId.get(id)?.label ?? id) })
    } else {
      plan.deleted.push({ path, size: f.size_bytes })
    }
  }
  return plan
}

// A path as one shell word: bare when it has only safe characters, else in single quotes.
export function shellQuote(path: string): string {
  return /^[\w@%+=:,./-]+$/.test(path) ? path : `'${path.replace(/'/g, `'\\''`)}'`
}

// The two commands that give the hal0 service user the models folder (the 409
// `comfyui.models_dir_not_writable` fix). `<hal0 group>` is the group the hal0 service runs as.
export function fixCommands(path: string): [string, string] {
  const p = shellQuote(path)
  return [`sudo chgrp -R <hal0 group> ${p}`, `sudo chmod -R 2775 ${p}`]
}

// Time left, to the second under a minute, then minutes and seconds, then hours and minutes.
export function fmtEta(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  if (s < 3600) {
    const m = Math.floor(s / 60)
    const rest = s % 60
    return rest ? `${m} min ${rest} s` : `${m} min`
  }
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return m ? `${h} h ${m} min` : `${h} h`
}

// GiB to one decimal, the unit of every GB figure the API reports (ADR-0039); `bytes` are raw.
export const gibOf = (bytes: number): string => String(Number((bytes / 1024 ** 3).toFixed(1)))

// A GB figure the server already rounded to one decimal, without a trailing ".0": 6.9, 12.
export const gbLabel = (gb: number): string => String(Number(gb.toFixed(1)))
