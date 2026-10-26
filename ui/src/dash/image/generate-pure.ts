// The Generate tab's pure parts: the form's starting values, the request it becomes, the number
// rules of its fields and the phase strip of a run. Tested without rendering
// (__tests__/generate-pure.test.ts).
//
// Nothing here judges whether a render fits this box (that is the server's verdict, `/fit` and
// the `/v1` preflight) or what the GPU is doing (that is the arbiter's `phase`): the strip only
// lays the arbiter's phase and ComfyUI's progress out as steps.

import type {
  ArbiterState,
  ComfyuiCatalogModel,
  ComfyuiFile,
  ComfyuiProgress,
  ImageGenerateVars,
} from '@/api/hooks/useComfyui'
import { parseSize, renderableInstalled } from './image-models-pure'

// ── the fields' ranges ───────────────────────────────────────────────────────

export const N_RANGE = { min: 1, max: 4 } as const
export const STEPS_RANGE = { min: 1, max: 100 } as const
export const CFG_RANGE = { min: 0, max: 20, step: 0.5 } as const
// What the server renders: each side clamped to 64..2048 and floored to a multiple of 8
// (`_parse_size` in providers/comfyui_workflows.py), which is also what `/fit` judges.
export const SIDE_RANGE = { min: 64, max: 2048, multiple: 8 } as const

/** Images per request, a whole number from 1 to 4 (the server takes up to 8; the panel stops at 4). */
export function clampN(n: number): number {
  if (!Number.isFinite(n)) return N_RANGE.min
  return Math.min(N_RANGE.max, Math.max(N_RANGE.min, Math.round(n)))
}

/** Sampler steps, a whole number from 1 to 100; null for a value that is not a number. */
export function clampSteps(steps: number): number | null {
  if (!Number.isFinite(steps)) return null
  return Math.min(STEPS_RANGE.max, Math.max(STEPS_RANGE.min, Math.round(steps)))
}

/** Guidance scale, 0 to 20 in steps of 0.5; null for a value that is not a number. */
export function clampCfg(cfg: number): number | null {
  if (!Number.isFinite(cfg)) return null
  const stepped = Math.round(cfg / CFG_RANGE.step) * CFG_RANGE.step
  return Math.min(CFG_RANGE.max, Math.max(CFG_RANGE.min, stepped))
}

/** One side of a custom size: within 64..2048 and floored to a multiple of 8, the way the server
 *  floors it, so the size on screen is the size that is judged and rendered. */
export function snapDim(side: number): number {
  const within = Math.min(SIDE_RANGE.max, Math.max(SIDE_RANGE.min, Number.isFinite(side) ? side : 0))
  return Math.max(SIDE_RANGE.min, Math.floor(within / SIDE_RANGE.multiple) * SIDE_RANGE.multiple)
}

/** `1030x30` -> `1024x64`; the input back when it is not a size. */
export function snapSize(size: string): string {
  const parsed = parseSize(size)
  return parsed ? `${snapDim(parsed.width)}x${snapDim(parsed.height)}` : size
}

/** A seed typed or pasted, as a whole number a browser holds exactly (0 to 2**53 - 1); null for
 *  something that is not a number. */
export function clampSeed(seed: number): number | null {
  if (!Number.isFinite(seed)) return null
  return Math.min(Number.MAX_SAFE_INTEGER, Math.max(0, Math.floor(seed)))
}

/** A fresh seed for the dice and for a "Random" run. The panel draws its own rather than leaving
 *  it to the server: the server draws up to 2**63, and `_hal0.meta.seed` would reach the browser
 *  rounded (see `isReusableSeed`), so "Reuse seed" could never repeat that image. */
export function rollSeed(): number {
  return Math.floor(Math.random() * 2 ** 32)
}

/** Whether a seed can go back to the server as the same number. The server draws seeds up to
 *  2**63 and reports the one it used in `_hal0.meta.seed`; `JSON.parse` keeps 53 bits of a
 *  number, so a larger seed arrives rounded and would render a different image. */
export function isReusableSeed(seed: number | null | undefined): seed is number {
  return typeof seed === 'number' && Number.isSafeInteger(seed) && seed >= 0
}

// ── the model select ─────────────────────────────────────────────────────────

// The native size of a user checkpoint, by family (`_USER_CKPT_SIZES` in comfyui/resolve.py):
// SDXL is trained at 1024, SD 1.5 at 512. A catalog row says its own; a checkpoint has no row.
const CHECKPOINT_SIZE: Record<string, string> = { sdxl: '1024x1024', sd15: '512x512' }

export interface GenerateChoice {
  /** The id `/v1` takes: a catalog id, or `ckpt/<path>` for a user checkpoint. */
  id: string
  label: string
  kind: 'catalog' | 'checkpoint'
  /** A second line for the select: licence, size and speed (catalog), or the file size. */
  detail: string
  sizes: string[]
  defaults: Record<string, unknown>
}

const gib = (bytes: number) => String(Number((bytes / 1024 ** 3).toFixed(1)))

/** What the Generate tab can render, in the order the select lists it: the img selection first,
 *  then the installed catalog models that the API renders, in catalog order, then the user's own
 *  checkpoints (the files scan rows that sniffed to a family, which have an `api_id`). */
export function generateChoices(
  models: readonly ComfyuiCatalogModel[] | null | undefined,
  files: readonly ComfyuiFile[] | null | undefined,
  defaultId?: string | null,
): GenerateChoice[] {
  const catalog: GenerateChoice[] = renderableInstalled(models ?? []).map((m) => ({
    id: m.id,
    label: m.label,
    kind: 'catalog',
    detail: `${m.license} · ${m.approx_gb} GB · ~${m.est_seconds} s per image`,
    sizes: m.sizes,
    defaults: m.defaults,
  }))
  const checkpoints: GenerateChoice[] = (files ?? [])
    .filter((f): f is ComfyuiFile & { api_id: string } => !!f.api_id)
    .map((f) => {
      const arch = f.arch ?? 'unknown'
      return {
        id: f.api_id,
        label: `${f.relpath} (your checkpoint, ${arch})`,
        kind: 'checkpoint',
        detail: `${gib(f.size_bytes)} GB`,
        sizes: CHECKPOINT_SIZE[arch] ? [CHECKPOINT_SIZE[arch]] : [],
        defaults: {},
      }
    })
  const all = [...catalog, ...checkpoints]
  const first = defaultId ? all.find((c) => c.id === defaultId) : undefined
  return first ? [first, ...all.filter((c) => c !== first)] : all
}

/** The id the select starts on: what the viewer picked, else what the Models tab asked for, else
 *  the img selection, else the first model there is. Only an id that is on offer counts. */
export function pickChoice(
  choices: readonly GenerateChoice[],
  ...wanted: Array<string | null | undefined>
): string | null {
  for (const id of wanted) if (id && choices.some((c) => c.id === id)) return id
  return choices[0]?.id ?? null
}

// ── the form ─────────────────────────────────────────────────────────────────

/** The img slot's `[image]` table (`GET /api/slots/{name}/config` -> `.image`): the defaults the
 *  server applies to a request that carries none. Both are optional and loosely typed because
 *  they come off a config file; an empty size and a zero step count mean "the model's own". */
export interface SlotImageDefaults {
  default_size?: unknown
  default_steps?: unknown
}

/** What `initialForm` needs of a model: a catalog row, or the stand-in for a user checkpoint. */
export interface GenerateModel {
  id: string
  // `WxH`, native size first.
  sizes?: readonly string[]
  // The workflow template's sampler defaults (steps, cfg, ...); empty for a user checkpoint.
  defaults?: Record<string, unknown>
}

export interface GenerateForm {
  model: string
  prompt: string
  negative_prompt: string
  // `WxH`; '' sends none and leaves the choice to the server.
  size: string
  // null/'' fields are left out of the request, and the server uses the template's own.
  steps: number | null
  cfg: number | null
  // null = random: the server draws one.
  seed: number | null
  n: number
}

const finiteNumber = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null)

/** The form for a model: its size, steps and cfg start from the img slot's defaults, then the
 *  model's own (`defaults` for steps and cfg, the native size for the size). The slot has no
 *  default cfg, so cfg is the model's alone. */
export function initialForm(
  model: GenerateModel,
  slot?: SlotImageDefaults | null,
): GenerateForm {
  const slotSize = typeof slot?.default_size === 'string' ? slot.default_size.trim().toLowerCase() : ''
  const slotSteps = finiteNumber(slot?.default_steps)
  const modelSteps = finiteNumber(model.defaults?.steps)
  const modelCfg = finiteNumber(model.defaults?.cfg)
  return {
    model: model.id,
    prompt: '',
    negative_prompt: '',
    size: parseSize(slotSize) ? snapSize(slotSize) : (model.sizes?.[0] ?? ''),
    steps: slotSteps != null && slotSteps >= 1 ? clampSteps(slotSteps) : modelSteps != null ? clampSteps(modelSteps) : null,
    cfg: modelCfg != null ? clampCfg(modelCfg) : null,
    seed: null,
    n: N_RANGE.min,
  }
}

/** The `/v1/images/generations` request a form makes, without the abort signal. What is empty is
 *  left out (a null seed, steps or cfg, a blank negative prompt, no size) so the server applies
 *  its own; what is set is clamped to its range. */
export function toRequest(form: GenerateForm): Omit<ImageGenerateVars, 'signal'> {
  const request: Omit<ImageGenerateVars, 'signal'> = {
    model: form.model,
    prompt: form.prompt.trim(),
    n: clampN(form.n),
  }
  if (form.size) request.size = form.size
  if (form.seed != null) request.seed = form.seed
  const steps = form.steps != null ? clampSteps(form.steps) : null
  if (steps != null) request.steps = steps
  const cfg = form.cfg != null ? clampCfg(form.cfg) : null
  if (cfg != null) request.cfg = cfg
  const negative = form.negative_prompt.trim()
  if (negative) request.negative_prompt = negative
  return request
}

// ── the phase strip ──────────────────────────────────────────────────────────

export type StripState = 'done' | 'now' | 'todo'

export interface StripStep {
  label: string
  state: StripState
}

// The step each arbiter phase is in. A hal0 render holds the arbiter's `rendering` phase from the
// moment the request enters image work, so before ComfyUI reports a step that phase is the model
// loading; `grace` while the run is still going is hal0 writing the files out.
const PHASE_STEP: Record<string, number> = {
  draining: 0,
  unloading: 1,
  starting_engine: 2,
  handoff: 3,
  rendering: 3,
  grace: 5,
}
const STEP_LOAD = 3
const STEP_RENDER = 4

function currentStep(arbiter: ArbiterState | null | undefined, progress: ComfyuiProgress | null | undefined): number {
  const phase = arbiter?.phase
  // The image job is closed while the request is still open: the render is over and the files
  // are being written. It outranks a step count that is one poll old.
  if (phase === 'grace') return PHASE_STEP.grace
  // ComfyUI's step count is the freshest word otherwise: /status and /arbiter are two polls, and
  // a step can land before the arbiter's phase moves.
  if (progress?.step != null) return STEP_RENDER
  if (phase && phase in PHASE_STEP) return PHASE_STEP[phase]
  // No phase: the request is on its way in. With image mode already on (a pinned, idle box) there
  // is nothing to switch, and the model is the first thing to load; anywhere else the switch is
  // still to come.
  if (arbiter?.mode === 'img' && phase !== 'reloading') return STEP_LOAD
  return 0
}

/** A run laid out as steps: Chat reply finishing, Unload {n} GPU slots, Engine ready, Load model,
 *  Render {n}/{N} and Save. The steps before the current one are done, the current one is now.
 *  When nothing is running every step is still to do. */
export function phaseStrip(
  arbiter: ArbiterState | null | undefined,
  progress: ComfyuiProgress | null | undefined,
  run: 'idle' | 'running',
): StripStep[] {
  const slots = arbiter?.unload_set?.length ?? 0
  const labels = [
    'Chat reply finishing',
    slots > 0 ? `Unload ${slots} GPU ${slots === 1 ? 'slot' : 'slots'}` : 'Unload GPU slots',
    'Engine ready',
    'Load model',
    progress?.step != null && progress.total != null ? `Render ${progress.step}/${progress.total}` : 'Render',
    'Save',
  ]
  const at = run === 'running' ? currentStep(arbiter, progress) : -1
  return labels.map((label, i) => ({
    label,
    state: at < 0 ? 'todo' : i < at ? 'done' : i === at ? 'now' : 'todo',
  }))
}

// How long after a run starts the arbiter's `grace` may still be the previous run's. The poll
// that reads it is up to a second old and the request needs a moment to open its image job.
export const STALE_GRACE_MS = 3_000

/** The arbiter as the strip should read it. "Generate again" starts a run inside the grace of the
 *  last one, and the arbiter keeps saying `grace` until the new request opens its image job; read
 *  as it stands the strip would open on Save. A `grace` that was already there when the run
 *  started is not the run's own for its first few seconds, so it reads as no phase. */
export function stripArbiter(
  arbiter: ArbiterState | null | undefined,
  run: { graceAtStart: boolean; elapsedMs: number },
): ArbiterState | null | undefined {
  if (arbiter?.phase === 'grace' && run.graceAtStart && run.elapsedMs < STALE_GRACE_MS) {
    return { ...arbiter, phase: null }
  }
  return arbiter
}
