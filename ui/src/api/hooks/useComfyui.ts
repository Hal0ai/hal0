// hal0 v3 dashboard — ComfyUI generation-engine hooks.
//
// /api/comfyui/status is a read-only aggregate (docker container state +
// systemd state of the LLM stack + ComfyUI's own /system_stats + /queue). The
// pane on the slots-page Image-Gen tab polls it; renders take minutes so a
// 4s cadence is plenty to track queue depth + memory pressure without hammering
// the cpp-httplib server behind :8188.
//
// The switchover (inference ⇄ generation) flips the single iGPU between the LLM
// stack and ComfyUI via the API's GPU arbiter (Phase D): switching to
// generation drains and STOPS the LLM slots, then starts the ComfyUI img slot;
// switching back restores the saved slots. The endpoint answers 202 and the
// arbiter runs the transition in-process; the `switchover` block on /status
// (active/target/error) is what tracks the transition to terminal — the pane's
// poll renders it, per the async-job-must-poll-to-terminal rule. Tearing down a
// non-empty queue needs `force: true` (the confirm dialog is that consent).
//
// `arbiter` is the arbiter-truth block (`ArbiterState`: mode, pin, the GPU phase, its
// countdowns and the unload set); it is null when the arbiter is unavailable (gate off /
// older backend) and every consumer fails soft to the legacy display. `GET /arbiter`
// answers the same block inside an envelope (`ArbiterEnvelope`), light enough to poll
// every second, which is what the GPU status line does.
//
// The Image Gen pane's other server state lives here too: the model catalog and its
// install jobs, the per-size fit check, and the /v1 generate call.

import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
  type UseQueryResult,
} from '@tanstack/react-query'
import { api, apiDelete, apiGet, apiPost, type Hal0Error } from '../client'
import { ENDPOINTS } from '../endpoints'
import { isImageJobActive } from './imageJobState'

const STATUS_KEY = ['comfyui', 'status'] as const

export type ComfyuiEngineState = 'stopped' | 'starting' | 'running' | 'generating' | 'error'
export type ComfyuiMode = 'generation' | 'inference'

export interface ComfyuiMemory {
  gtt_used_gb: number | null
  gtt_ceil_gb: number
  ram_used_gb: number | null
  ram_ceil_gb: number
  pressure: boolean
}

export interface ComfyuiSwitchover {
  active: boolean
  target: ComfyuiMode | null
  error: string | null
}

// GPU-arbiter truth block: `arbiter.status()` in src/hal0/slots/arbiter.py, served inside
// `/status` (`arbiter`) and `/arbiter` (`arbiter`).
//
// `mode` is the arbiter's own vocabulary ('img' | 'llm', distinct from the legacy
// 'generation' | 'inference' of the top-level `mode`): every "is image mode on" test reads
// this one. `switching` is true while the arbiter holds its switch lock (a mode change or a
// restore decision is running); `image_jobs` counts /v1/images renders in flight.
//
// `phase` is what the GPU is doing: `draining`, `unloading`, `starting_engine` and
// `reloading` while a switch or restore runs; in image mode `handoff`, `rendering` or
// `grace`; else null (chat mode, right after an API restart, and a pinned idle image mode).
// A render in ComfyUI's own queue (the editor's) is invisible here and reads as `grace`.
// `restore_at` / `handoff_until` are epoch seconds, set only in their own phase.
// `drain_in_flight` counts the chat replies a `draining` switch waits for.
// `unload_set` is what a switch would unload (chat mode) or what is unloaded and comes back
// (image mode); null means unknown (a cold cache), which is not the same as `[]`.
export interface ArbiterState {
  mode: 'llm' | 'img'
  pinned: boolean
  saved_llm_slots: string[]
  switching: boolean
  image_jobs: number
  phase: string | null
  restore_at: number | null
  handoff_until: number | null
  drain_in_flight: number
  unload_set: string[] | null
  llm_waiters: { count: number; oldest_s: number | null }
}

// The latest prompt the GPU gate refused (or hal0 refused the gate's switch request): epoch
// seconds and a reason cleaned and capped by the server. It has no source field, and the
// reason is text to put in a text node, never markup.
export interface GateRefusal {
  at: number
  reason: string
}

// `GET /api/comfyui/arbiter`: the arbiter, the switchover tracker and the gate's latest
// refusal in one light payload. The top-level `mode` is the legacy vocabulary and reads
// 'inference' for the whole of a switch, so use `arbiter.mode`.
export interface ArbiterEnvelope {
  mode: ComfyuiMode | null
  arbiter: ArbiterState | null
  switchover: ComfyuiSwitchover
  gate_refusal: GateRefusal | null
}

// The GPU gate in the img container, as `/status` reports it. In order of precedence:
// `no_credential` (hal0 requires the gate to authenticate and it has no way to), `stale`
// (an older gate: restart the engine), `unknown` (not heard from since hal0-api started),
// `current`.
export interface ComfyuiGate {
  state: 'no_credential' | 'stale' | 'unknown' | 'current'
  version_seen: string | null
}

// The hal0-submitted render ComfyUI is running (/v1 and the Generate tab), or null. A render
// from the ComfyUI editor never has one. `step`/`total`/`node` are null until the first
// progress frame arrives (the checkpoint load and the queue wait come before it).
export interface ComfyuiProgress {
  model: string
  step: number | null
  total: number | null
  node: string | null
  elapsed_s: number
  prompt_id: string
}

export interface ComfyuiStatus {
  // null when the GPU arbiter cannot report the mode (unwired / unreadable)
  mode: ComfyuiMode | null
  reachable: boolean
  engine: ComfyuiEngineState
  container: { name: string; state: string }
  endpoint: string | null
  // Still served, but on its way out (the Engine tab task deletes it server-side): the
  // memory figures are the `memory` block of /api/stats/hardware.
  memory: ComfyuiMemory | null
  // Counts only: ComfyUI's queue has no per-job detail here.
  queue: { running: number; pending: number }
  util: number | null
  temp: number | null
  clock: number | null
  progress: ComfyuiProgress | null
  inference: { hermes: boolean }
  inventory: Record<string, number> | null
  switchover: ComfyuiSwitchover
  arbiter: ArbiterState | null
  gate: ComfyuiGate | null
  gate_refusal: GateRefusal | null
}

// Neutral default so the pane renders a coherent "stopped/inference" shell on
// first paint and whenever the backend is briefly unreachable — never undefined
// field access.
export const COMFYUI_FALLBACK: ComfyuiStatus = {
  mode: 'inference',
  reachable: false,
  engine: 'stopped',
  container: { name: 'comfyui', state: 'absent' },
  endpoint: null,
  memory: null,
  queue: { running: 0, pending: 0 },
  util: 0,
  temp: null,
  clock: null,
  progress: null,
  inference: { hermes: false },
  inventory: null,
  switchover: { active: false, target: null, error: null },
  arbiter: null,
  gate: null,
  gate_refusal: null,
}

// Active (Image-Gen tab open): 4s, fast enough to track queue + pressure.
// Idle (other tab): 20s — keeps the tab's live dot honest without spending a
// docker inspect + 2× systemctl + 2× HTTP probe every few seconds; per-tab
// polling can starve an embedded HTTP server, so this is the cheap guard.
// Busy (pane open and the GPU is doing something, or a Generate run is in flight): 2s, so the
// status line's step count and the queue move while a render runs. /status is the heavy route,
// which is why the fast cadence is only for as long as there is something to watch.
const POLL_ACTIVE_MS = 4_000
const POLL_IDLE_MS = 20_000
const POLL_BUSY_MS = 2_000

const queueBusy = (queue: { running: number; pending: number } | null | undefined) =>
  (queue?.running ?? 0) + (queue?.pending ?? 0) > 0

// Read off the status itself: the arbiter block rides on /status, so no second query is needed.
function statusBusy(status: ComfyuiStatus | undefined): boolean {
  if (!status) return false
  return status.arbiter?.phase != null || status.arbiter?.switching === true || queueBusy(status.queue)
}

/** The `/status` poll interval. `active`: the Image Gen pane is mounted. `busy`: something the
 *  status cannot see is in flight (a Generate run). */
export function comfyuiStatusPollMs(
  status: ComfyuiStatus | undefined,
  opts: { active?: boolean; busy?: boolean },
): number {
  if (!opts.active) return POLL_IDLE_MS
  return opts.busy || statusBusy(status) ? POLL_BUSY_MS : POLL_ACTIVE_MS
}

export function useComfyui(opts: { active?: boolean; busy?: boolean } = {}): UseQueryResult<ComfyuiStatus> {
  return useQuery({
    queryKey: STATUS_KEY,
    queryFn: () => apiGet<ComfyuiStatus>(ENDPOINTS.comfyuiStatus),
    refetchInterval: (query) => comfyuiStatusPollMs(query.state.data, opts),
  })
}

// ── GPU arbiter (the status line) ────────────────────────────────────────────

const ARBITER_FAST_MS = 1_000
const ARBITER_SLOW_MS = 5_000
// The queue is only on /status. Its cached answer counts for as long as it is fresh: a bit
// longer than the idle poll, so a pane nobody polls any more cannot hold /arbiter at 1 s.
const STATUS_FRESH_MS = 30_000

/** The `/arbiter` poll interval: 1 s while a phase runs, a switch holds the lock or the queue
 *  is busy; 5 s otherwise. */
export function arbiterPollMs(env: ArbiterEnvelope | undefined, queueIsBusy: boolean): number {
  const arbiter = env?.arbiter
  return arbiter?.phase != null || arbiter?.switching === true || queueIsBusy
    ? ARBITER_FAST_MS
    : ARBITER_SLOW_MS
}

function cachedQueueBusy(qc: QueryClient): boolean {
  const state = qc.getQueryState<ComfyuiStatus>(STATUS_KEY)
  if (!state?.data || Date.now() - state.dataUpdatedAt > STATUS_FRESH_MS) return false
  return queueBusy(state.data.queue)
}

/** `GET /api/comfyui/arbiter`: the whole envelope (`data.arbiter` is the `ArbiterState`). */
export function useArbiter(): UseQueryResult<ArbiterEnvelope> {
  const qc = useQueryClient()
  return useQuery({
    queryKey: ['comfyui', 'arbiter'],
    queryFn: () => apiGet<ArbiterEnvelope>(ENDPOINTS.comfyuiArbiter),
    refetchInterval: (query) => arbiterPollMs(query.state.data, cachedQueueBusy(qc)),
  })
}

export interface SwitchoverBody {
  mode: ComfyuiMode
  // Required to tear down a non-empty render queue (jobs are dropped). The
  // confirm dialog's warning is the consent that sets this.
  force?: boolean
  // Optional: pin image mode as part of the switch (hold image mode until
  // unpinned).
  pin?: boolean
}

// raw:true so the dev mockFetch GET-shim can't mask 503 arbiter failures — we
// want the real status code to drive the toast copy.
export function useComfyuiSwitchover() {
  return useMutation({
    mutationFn: (body: SwitchoverBody) =>
      api<unknown>(ENDPOINTS.comfyuiSwitchover, { method: 'POST', body: body as any, raw: true }),
  })
}

// Pin / unpin image mode (hold it until unpinned / let it end after the last render).
// Synchronous 200 {"pinned":bool} — the caller refetches /status to reflect
// the new arbiter state. raw:true for the same failure handling as switchover.
export function useComfyuiPin() {
  return useMutation({
    mutationFn: (body: { pinned: boolean }) =>
      api<{ pinned: boolean }>(ENDPOINTS.comfyuiPin, {
        method: 'POST',
        body: body as any,
        raw: true,
      }),
  })
}

// Cancel current + queued renders (best-effort, 202).
export function useComfyuiRenderCancel() {
  return useMutation({
    mutationFn: () => api<{ status: string }>(ENDPOINTS.comfyuiRenderCancel, { method: 'POST' }),
  })
}

// Restart ComfyUI container (202, fire-and-forget).
export function useComfyuiRestart() {
  return useMutation({
    mutationFn: () => api<{ status: string }>(ENDPOINTS.comfyuiRestart, { method: 'POST' }),
  })
}

// Fetch container logs (tail=N, default 60).
export function useComfyuiLogs(tail = 60) {
  return useQuery({
    queryKey: ['comfyui', 'logs', tail],
    queryFn: () => apiGet<{ lines: string[] }>(`${ENDPOINTS.comfyuiLogs}?tail=${tail}`),
    enabled: false, // on-demand only
  })
}

// List launchable workflows discovered on disk. Fail-soft: 404 / network
// error (comfyui not installed, no dir yet) yields an empty list so the
// curated strip renders unchanged. Polled lazily — the file set changes
// rarely, so a long staleTime keeps this cheap.
export interface ComfyuiWorkflow {
  name: string
  source: 'primary' | 'user'
}

export function useComfyuiWorkflows() {
  return useQuery({
    queryKey: ['comfyui', 'workflows'],
    queryFn: async (): Promise<ComfyuiWorkflow[]> => {
      try {
        const res = await apiGet<{ workflows?: ComfyuiWorkflow[] }>(ENDPOINTS.comfyuiWorkflows)
        return Array.isArray(res?.workflows) ? res.workflows : []
      } catch {
        return []
      }
    },
    staleTime: 60_000,
  })
}

// ── Model files on disk (Models → Image) ─────────────────────────────────────
//
// GET /api/comfyui/files is the one scan of the ComfyUI models tree
// (comfyui/inventory.py list_files): every weight file, with the catalog models
// that load it. `kind` is the component kind for a catalog directory and the
// directory's own name for one operators fill by hand (controlnet, embeddings…).
// `relpath` is under `subdir` and may be nested. `arch` + `api_id` only appear
// on an unowned checkpoint whose header sniffs as a known family. `symlink` is
// true when the listed path is a link, which DELETE refuses.

export interface ComfyuiFile {
  kind: string
  subdir: string
  relpath: string
  size_bytes: number
  // Catalog model ids that load this file; empty = nobody owns it.
  models: string[]
  arch?: string | null
  api_id?: string | null
  symlink?: boolean
}

export interface ComfyuiFilesResponse {
  root: string
  files: ComfyuiFile[]
}

// The tree changes when an install lands or a file is deleted — both invalidate
// this key — so a short staleTime only covers files an operator adds by hand.
// The scan walks the whole models tree and sniffs checkpoint headers, so a caller that needs it
// only now and then passes `enabled: false` until it does (the Models tab, for its remove
// confirmation).
export function useComfyuiFiles(opts: { enabled?: boolean } = {}) {
  return useQuery({
    queryKey: ['comfyui', 'files'],
    queryFn: () => apiGet<ComfyuiFilesResponse>(ENDPOINTS.comfyuiFiles),
    staleTime: 10_000,
    enabled: opts.enabled !== false,
  })
}

// DELETE /api/comfyui/files/{subdir}/{relpath}. Refused with 409
// `comfyui.file_catalog_owned` for a file a catalog model loads and 400
// `comfyui.file_symlink` for a link — the error message says why. /status
// carries the per-category counts, so it refreshes with the listing.
export function useComfyuiFileDelete() {
  const qc = useQueryClient()
  return useMutation<{ deleted: string }, Hal0Error, { subdir: string; relpath: string }>({
    mutationFn: ({ subdir, relpath }) =>
      apiDelete<{ deleted: string }>(ENDPOINTS.comfyuiFile(subdir, relpath)),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['comfyui', 'files'] })
      qc.invalidateQueries({ queryKey: ['comfyui', 'status'] })
    },
  })
}

// ── Image-model catalog, install jobs, fit and generate ──────────────────────
//
// GET /api/comfyui/models is the image-model catalog (`_model_row` in
// src/hal0/api/routes/comfyui.py), in catalog order. GB figures are GiB to one decimal.

/** One file of an install job (`PullFile.as_dict()`). */
export interface ImageJobFile {
  hf_filename: string
  kind: string
  dest: string | null
  bytes_total: number
  bytes_done: number
  sha256: string | null
  expected_sha256: string | null
}

/** An install job (`PullJob.as_dict()`). Jobs live in memory, the latest per model, and are
 *  lost on an API restart. Byte counts are raw bytes (not GiB). */
export interface ImageJob {
  id: string
  model_id: string
  state: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  bytes_downloaded: number
  bytes_total: number
  started_at: number
  finished_at: number | null
  error: string | null
  error_code: string | null
  sha256: string | null
  path: string | null
  files: ImageJobFile[]
}

export { isImageJobActive }

export type FitVerdict = 'fits' | 'tight' | 'wont_fit'

/** What one render needs, in GiB; `*_estimated` is false only for a figure the catalog measured. */
export interface FitNeed {
  gpu_gb: number
  ram_gb: number
  gpu_estimated: boolean
  ram_estimated: boolean
}

/** The server's verdict on one render against this box's memory (`Fit.as_dict()`). `reason` is
 *  null exactly when the verdict is `fits`. The two `*_free_gb` figures are what the need was
 *  judged against. The UI shows the verdict as given and never judges a fit itself. */
export interface Fit {
  verdict: FitVerdict
  reason: string | null
  need: FitNeed
  render_free_gb: number
  ram_free_gb: number
}

export interface ComfyuiCatalogModel {
  id: string
  label: string
  // txt2img | img2img | txt2video | img2video | upscale
  task: string
  state: 'none' | 'partial' | 'complete'
  // False for the models only the ComfyUI editor runs (video, edit, upscale).
  api_renderable: boolean
  // Exactly one row: the starter pick for this box.
  recommended: boolean
  license: string
  est_seconds: number
  // `WxH`, native size first; empty for editor-only models.
  sizes: string[]
  notes: string
  // The workflow template's own sampler defaults (steps, cfg, sampler_name, ...); {} for
  // editor-only models.
  defaults: Record<string, unknown>
  approx_gb: number
  missing_gb: number
  // Each absent weight file as `<subdir>/<relpath>`.
  missing: string[]
  // The workflow file provisioned for the model: `<id>.json`.
  workflow: string
  // The model's latest install job, if any (`useImageJobs` is the fresher copy).
  job: ImageJob | null
  // The verdict at the model's native size; null when the budget is unknown.
  fit: Fit | null
}

/** What the starter pick was chosen from (`recommendation()` in `hal0.comfyui.picker`):
 *  `pool_gb` is the figure the picker used (GiB, one decimal), the smaller of the GPU pool and
 *  this box's RAM limit, and `limited_by` says which of the two set it. */
export interface ComfyuiRecommendation {
  id: string
  pool_gb: number
  limited_by: 'ram' | 'gpu'
}

export interface ComfyuiModelsResponse {
  models: ComfyuiCatalogModel[]
  // `available` is false on a host with no ROCm lane; the catalog lists either way.
  engine: { available: boolean; reason: string | null }
  // The models tree and the free space (GiB) of the filesystem under it; null when unmeasured.
  store: { free_gb: number | null; path: string }
  // The model marked `recommended` and the figure it was chosen from; null when the box's pool
  // is unknown (never probed), in which case there is no figure to cite.
  recommendation: ComfyuiRecommendation | null
}

export function useComfyuiModels() {
  return useQuery({
    queryKey: ['comfyui', 'models'],
    queryFn: () => apiGet<ComfyuiModelsResponse>(ENDPOINTS.comfyuiModels),
    staleTime: 10_000,
  })
}

const JOBS_KEY = ['comfyui', 'jobs'] as const

/** The latest job per model, with `job` replacing the one the model already had. */
function upsertJob(jobs: ImageJob[] | undefined, job: ImageJob): ImageJob[] {
  if (!jobs) return [job]
  return jobs.some((j) => j.model_id === job.model_id)
    ? jobs.map((j) => (j.model_id === job.model_id ? job : j))
    : [...jobs, job]
}

/** True when a job active in `prev` is no longer the same active job in `next`. */
function aJobEnded(prev: ImageJob[] | undefined, next: ImageJob[]): boolean {
  return (prev ?? []).some((p) => {
    if (!isImageJobActive(p)) return false
    const now = next.find((j) => j.model_id === p.model_id)
    return !now || now.id !== p.id || !isImageJobActive(now)
  })
}

/** The `/jobs` poll interval: 1 s while any job is queued or running, otherwise off. */
export function imageJobsPollMs(jobs: ImageJob[] | undefined): number | false {
  return jobs?.some(isImageJobActive) ? 1_000 : false
}

/** `GET /api/comfyui/jobs`, unwrapped to the list. Polls while an install runs, and when one
 *  ends refreshes what the install changed (the catalog's states, the file listing and the
 *  status inventory). `active: false` (the tab is not showing) turns the query off. */
export function useImageJobs(opts: { active?: boolean } = {}) {
  const qc = useQueryClient()
  return useQuery<ImageJob[]>({
    queryKey: JOBS_KEY,
    queryFn: async () => {
      const prev = qc.getQueryData<ImageJob[]>(JOBS_KEY)
      const res = await apiGet<{ jobs?: ImageJob[] }>(ENDPOINTS.comfyuiJobs)
      const jobs = Array.isArray(res?.jobs) ? res.jobs : []
      if (aJobEnded(prev, jobs)) {
        qc.invalidateQueries({ queryKey: ['comfyui', 'models'] })
        qc.invalidateQueries({ queryKey: ['comfyui', 'files'] })
        qc.invalidateQueries({ queryKey: STATUS_KEY })
      }
      return jobs
    },
    enabled: opts.active !== false,
    refetchInterval: (query) => imageJobsPollMs(query.state.data),
  })
}

/** `POST /models/{id}/install`: 202 with the new job. 409 `comfyui.install_running`,
 *  `comfyui.model_installed`, `comfyui.staging_cross_device`, `comfyui.models_dir_not_writable`;
 *  507 `comfyui.insufficient_disk`. The job goes into the jobs list at once so polling starts. */
export function useImageModelInstall() {
  const qc = useQueryClient()
  return useMutation<{ job: ImageJob }, Hal0Error, string>({
    mutationFn: (modelId) => apiPost<{ job: ImageJob }>(ENDPOINTS.comfyuiModelInstall(modelId)),
    onSuccess: ({ job }) => {
      qc.setQueryData<ImageJob[]>(JOBS_KEY, (prev) => upsertJob(prev, job))
      qc.invalidateQueries({ queryKey: JOBS_KEY })
      qc.invalidateQueries({ queryKey: ['comfyui', 'models'] })
    },
  })
}

/** What `DELETE /models/{id}` did: the paths deleted, each weight file left because another
 *  complete model loads it (with those models), and the other models it broke. */
export interface ImageModelRemoveResult {
  removed: string[]
  kept: { relpath: string; models: string[] }[]
  affected: string[]
}

/** `DELETE /models/{id}`. There is no dry run: the confirmation lists what goes and what stays
 *  from the `/files` rows. `includeShared` also deletes the files other models load. 409
 *  `comfyui.install_running`, `comfyui.remove_failed`, `comfyui.models_dir_not_writable`. */
export function useImageModelRemove() {
  const qc = useQueryClient()
  return useMutation<ImageModelRemoveResult, Hal0Error, { id: string; includeShared?: boolean }>({
    mutationFn: ({ id, includeShared }) =>
      apiDelete<ImageModelRemoveResult>(
        `${ENDPOINTS.comfyuiModel(id)}${includeShared ? '?include_shared=true' : ''}`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['comfyui', 'models'] })
      qc.invalidateQueries({ queryKey: ['comfyui', 'files'] })
      qc.invalidateQueries({ queryKey: STATUS_KEY })
    },
  })
}

/** `POST /jobs/{model_id}/cancel`: keyed by the MODEL id, 202 with the job. Files that landed
 *  stay, so a later install carries on from there. 404 `comfyui.job_not_found`. */
export function useImageJobCancel() {
  const qc = useQueryClient()
  return useMutation<{ job: ImageJob }, Hal0Error, string>({
    mutationFn: (modelId) => apiPost<{ job: ImageJob }>(ENDPOINTS.comfyuiJobCancel(modelId)),
    onSuccess: ({ job }) => {
      qc.setQueryData<ImageJob[]>(JOBS_KEY, (prev) => upsertJob(prev, job))
      qc.invalidateQueries({ queryKey: JOBS_KEY })
    },
  })
}

/** `GET /models/{id}/fit?size=`. `size` is the size that was judged: the requested one clamped
 *  to 64..2048 and floored to a multiple of 8, so it can differ from the request (show
 *  "judged at {size}" then). `fit` is null when the budget is unknown. */
export interface ModelFitResponse {
  fit: Fit | null
  size: string | null
}

// A user checkpoint (`ckpt/<path>`): the /fit route 404s for it, and the /v1 preflight judges
// it by file size when the render is requested.
const isUserCheckpoint = (id: string) => id.startsWith('ckpt/')

// The verdict depends on what the box has free right now, so it is re-read now and then.
const FIT_POLL_MS = 10_000

export function useModelFit(id: string | null | undefined, size: string | null | undefined) {
  const enabled = !!id && !isUserCheckpoint(id)
  return useQuery<ModelFitResponse>({
    queryKey: ['comfyui', 'fit', id ?? null, size ?? null],
    queryFn: () => apiGet<ModelFitResponse>(ENDPOINTS.comfyuiModelFit(id as string, size)),
    enabled,
    staleTime: 5_000,
    refetchInterval: enabled ? FIT_POLL_MS : false,
  })
}

export interface ImageGenerateVars {
  // An installed catalog id, or `ckpt/<path>` for a user checkpoint.
  model: string
  prompt: string
  // Images, 1-4 from the panel (the server accepts 1-8).
  n?: number
  // `WxH`; omitted = the img slot's default size, else the model's native one.
  size?: string
  // `extra_body`: left out when null/undefined so the server applies the template's own.
  seed?: number | null
  steps?: number | null
  cfg?: number | null
  negative_prompt?: string | null
  // The Generate panel's own AbortController. Aborting before the prompt is queued ends the
  // request; the server answers an empty 499 if it notices the disconnect.
  signal?: AbortSignal
}

/** `POST /v1/images/generations`: the OpenAI shape plus hal0's `_hal0` block. */
export interface ImageGenerateResponse {
  created: number
  data: { url?: string; b64_json?: string }[]
  _hal0: {
    // What the translator rendered with. The seed used is `_hal0.meta.seed`.
    meta: {
      seed?: number
      steps?: number
      cfg?: number
      width?: number
      height?: number
      batch_size?: number
      negative_prompt?: string
    }
    prompt_id: string
    upstream: string | null
    model: string
    template: string
  }
}

/** The generate call itself, for callers that hold their own state. A render can run for
 *  minutes (Qwen-Image at 20 steps is about six), and client.ts has no default timeout, so none
 *  is set here: only `signal`, the caller's own abort, ends the request early. Errors: 409
 *  `image.model_not_installed` (`details.model`), 422 (`image.wont_fit`, ...), 502, 503
 *  `image.gate_refused`; a 499 or an abort is the caller cancelling. */
export function generateImage({
  signal,
  seed,
  steps,
  cfg,
  negative_prompt,
  ...request
}: ImageGenerateVars): Promise<ImageGenerateResponse> {
  const extras: Record<string, unknown> = { seed, steps, cfg, negative_prompt }
  const extra_body = Object.fromEntries(Object.entries(extras).filter(([, v]) => v != null))
  return api<ImageGenerateResponse>(ENDPOINTS.v1ImagesGenerations, {
    method: 'POST',
    body: { ...request, response_format: 'url', extra_body },
    signal,
  })
}

export function useImageGenerate() {
  return useMutation<ImageGenerateResponse, Hal0Error, ImageGenerateVars>({
    mutationFn: generateImage,
  })
}

// ── V2 pane data transform ───────────────────────────────────────────────────
//
// The server /status payload has queue counts, memory, and device telemetry.
// Per-job detail (name, kind, node, progress) is not fully surfaced yet; it/s,
// ETA, and step are explicit nulls until a ComfyUI websocket subscription lands.
// The component renders placeholders for absent fields — never crashes on
// undefined.
//
// active_render and queue_jobs are optional extensions that may be injected
// by the window.__comfyuiV2MockOverride seam or a future richer backend.

export interface ComfyuiV2Run {
  name: string
  kind: string
  pct: number
  eta: string
  node: string
  step: number
  total: number
  its: number
  loaded: string
}

export interface ComfyuiV2QueueJob {
  name: string
  kind: string
}

export interface ComfyuiV2PaneData {
  engine: {
    name: string
    endpoint: string
    image: string
    restart: string
  }
  run: ComfyuiV2Run | null
  queue: ComfyuiV2QueueJob[]
  gtt: { used: number; ceil: number }
  ram: { used: number; ceil: number }
  stats: { util: number; temp: number; clk: number; its: number }
  // Per-category weight-file counts on the model share (comfyui/inventory.py
  // status_counts — VERIFIED, never faked). null when the share root is
  // absent (dev box / fresh install) — the pane must hide the models block
  // rather than render zeros it can't stand behind.
  inventory: Record<string, number> | null
}

// Transform a live /status payload (+ optional V2 extension fields) into the
// component's data shape. All absent numeric fields get safe defaults so the
// pane renders coherently even when ComfyUI isn't running.
export function transformComfyuiStatus(
  status: ComfyuiStatus & {
    active_render?: ComfyuiV2Run | null
    queue_jobs?: ComfyuiV2QueueJob[]
  },
): ComfyuiV2PaneData {
  const mem = status.memory
  const hasRun = (status.queue?.running ?? 0) > 0

  // active_render is the rich per-job detail block — only present in the mock
  // or a future richer backend. Fall back to a placeholder skeleton when the
  // render count says something is running but the detail is absent.
  let run: ComfyuiV2Run | null = null
  if (status.active_render !== undefined) {
    run = status.active_render ?? null
  } else if (hasRun) {
    run = {
      name: 'rendering…',
      kind: '—',
      pct: 0,
      eta: '—',
      node: '—',
      step: 1,
      total: 1,
      its: 0,
      loaded: '—',
    }
  }

  // queue_jobs mirrors pending job list detail. Falls back to placeholder rows
  // equal to the pending count when detail is absent.
  let queue: ComfyuiV2QueueJob[]
  if (status.queue_jobs !== undefined) {
    queue = status.queue_jobs
  } else {
    const pending = status.queue?.pending ?? 0
    queue = Array.from({ length: pending }, (_, i) => ({ name: `job ${i + 1}`, kind: '—' }))
  }

  return {
    engine: {
      name: 'ComfyUI',
      endpoint: status.endpoint ?? ':8188',
      image: status.container?.name ?? 'comfyui',
      restart: 'no',
    },
    run,
    queue,
    gtt: {
      used: mem?.gtt_used_gb ?? 0,
      ceil: mem?.gtt_ceil_gb ?? 80,
    },
    ram: {
      used: mem?.ram_used_gb ?? 0,
      ceil: mem?.ram_ceil_gb ?? 96,
    },
    stats: {
      util: status.util ?? 0,
      temp: status.temp ?? 0,
      clk: status.clock ?? 0,
      its: 0,
    },
    inventory: status.inventory ?? null,
  }
}
