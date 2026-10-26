// The Generate tab's session state: the run in flight, its error and the last 12 results.
//
// It lives at module level, not in the panel, because the panel does not always stay mounted: the
// tabs only hide it, but leaving the Slots page unmounts the whole pane, and a render takes
// minutes. A run is not cancelled by leaving (the render goes on in ComfyUI whatever the browser
// does), so the request, its outcome and the results all outlive the panel and the panel reads
// them back when it returns. The
// results are mirrored to sessionStorage so a reload of the tab keeps them too; every storage
// access is guarded, since private windows and blocked site data throw.
//
// The panel owns the form; this module owns the request and its Cancel (`cancelRun`), and reports
// the run to `useImageRunStore` so the status dock can show it and cancel it with the panel gone.

import { useSyncExternalStore } from 'react'
import {
  generateImage,
  postRenderCancel,
  renderIsQueued,
  type ImageGenerateResponse,
  type ImageGenerateVars,
} from '@/api/hooks/useComfyui'
import { queryClient } from '@/lib/queryClient'
import { useImageRunStore } from '@/stores/useImageRunStore'

/** One finished image. `seed` is `_hal0.meta.seed`, the seed the render used. */
export interface GenerateResult {
  /** Same-origin path of the cached PNG (`/api/images/cache/<stem>.png`). */
  url: string
  seed: number | null
  prompt: string
  model: string
  /** `WxH` as rendered. */
  size: string
  /** Epoch ms. */
  at: number
}

export interface GenerateRun {
  /** Aborting it ends the request; the server drops the render if it is not queued yet. */
  controller: AbortController
  /** Epoch ms. */
  startedAt: number
  /** The arbiter already read `grace` when the run began (see `stripArbiter`). */
  graceAtStart: boolean
}

/** The server's answer to a refused or failed request. */
export interface GenerateError {
  message: string
  code: string | null
  status: number | null
  details: Record<string, unknown>
}

export interface GenerateState {
  /** Newest first. */
  results: GenerateResult[]
  run: GenerateRun | null
  error: GenerateError | null
}

export const MAX_RESULTS = 12
const STORAGE_KEY = 'hal0.imageGen.results'

// ── results <-> storage ──────────────────────────────────────────────────────

// A same-origin path only: the url ends up in `src` and `href`, and a stored value is as
// untrusted as any other string read back.
const isSafeUrl = (url: unknown): url is string => typeof url === 'string' && /^\/(?!\/)[^\\\s]*$/.test(url)

function isResult(v: unknown): v is GenerateResult {
  if (!v || typeof v !== 'object') return false
  const r = v as Record<string, unknown>
  return (
    isSafeUrl(r.url) &&
    (r.seed === null || typeof r.seed === 'number') &&
    typeof r.prompt === 'string' &&
    typeof r.model === 'string' &&
    typeof r.size === 'string' &&
    typeof r.at === 'number'
  )
}

function readStored(): GenerateResult[] {
  try {
    const parsed: unknown = JSON.parse(window.sessionStorage.getItem(STORAGE_KEY) ?? 'null')
    return Array.isArray(parsed) ? parsed.filter(isResult).slice(0, MAX_RESULTS) : []
  } catch {
    return []
  }
}

function writeStored(results: GenerateResult[]): void {
  try {
    window.sessionStorage.setItem(STORAGE_KEY, JSON.stringify(results))
  } catch {
    /* storage blocked or full: the results stay for as long as the page does */
  }
}

// ── the store ────────────────────────────────────────────────────────────────

let state: GenerateState = { results: readStored(), run: null, error: null }
// Bumped by `resetGenerateStore`: a run that began before it must not write into what follows.
let epoch = 0
const listeners = new Set<() => void>()

function update(patch: Partial<GenerateState>): void {
  state = { ...state, ...patch }
  if (patch.results) writeStored(state.results)
  listeners.forEach((notify) => notify())
}

const subscribe = (notify: () => void) => {
  listeners.add(notify)
  return () => {
    listeners.delete(notify)
  }
}
const snapshot = () => state

/** The session state, live. */
export function useGenerateState(): GenerateState {
  return useSyncExternalStore(subscribe, snapshot, snapshot)
}

/** Abort the request in flight (a no-op without one). Whether the render in ComfyUI is cancelled
 *  too is the caller's to decide: it depends on whether the prompt is queued yet. */
export function abortRun(): void {
  state.run?.controller.abort()
}

/**
 * Cancel the run of this browser. Which call stops it depends on whether the prompt is in
 * ComfyUI's queue yet:
 *  - Not yet (the switch phases, or nothing): abort the /v1 request. The server drops the render
 *    after the switch, answers an empty 499, and the GPU restores as usual.
 *  - Queued (hand-off, rendering, or hal0's render has progress): ComfyUI is running it, and
 *    hanging up would not stop it: /render/cancel does, and the request is aborted as well.
 * The phase and progress are read when it is called, off the query cache, so it is right whichever
 * surface calls it and whether or not the panel is mounted.
 */
export function cancelRun(): void {
  if (!state.run) return
  if (renderIsQueued(queryClient)) {
    // Through the mutation cache, so a refused (401) cancel opens the same auth challenge as any
    // other mutation.
    queryClient
      .getMutationCache()
      .build(queryClient, { mutationFn: postRenderCancel })
      .execute(undefined)
      .catch((e: { message?: string } | null) => {
        const toast = (window as unknown as { __hal0Toast?: (msg: string, kind: string) => void }).__hal0Toast
        toast?.(`Could not cancel the render — ${e?.message || 'see logs'}`, 'err')
      })
  }
  abortRun()
}

/** Drop the error on screen (the viewer moved on to another model). */
export function dismissError(): void {
  if (state.error) update({ error: null })
}

/** Forget everything: for the tests, and nothing else. */
export function resetGenerateStore(): void {
  epoch += 1
  state.run?.controller.abort()
  state = { results: [], run: null, error: null }
  useImageRunStore.getState().setRun(null)
  try {
    window.sessionStorage.removeItem(STORAGE_KEY)
  } catch {
    /* nothing stored to remove */
  }
  listeners.forEach((notify) => notify())
}

// ── a run ────────────────────────────────────────────────────────────────────

// An abort (Cancel) or a 499 (the server noticed the client had gone before the prompt was
// queued) is the viewer's own doing, not a failure.
function isCancellation(e: unknown, controller: AbortController): boolean {
  const err = e as { name?: unknown; status?: unknown } | null
  return controller.signal.aborted || err?.name === 'AbortError' || err?.status === 499
}

function toError(e: unknown): GenerateError {
  const err = (e && typeof e === 'object' ? e : {}) as Record<string, unknown>
  const message =
    typeof err.message === 'string' && err.message
      ? err.message
      : typeof e === 'string' && e
        ? e
        : 'The image request failed.'
  const details = err.details && typeof err.details === 'object' ? (err.details as Record<string, unknown>) : {}
  return {
    message,
    code: typeof err.code === 'string' ? err.code : null,
    status: typeof err.status === 'number' ? err.status : null,
    details,
  }
}

// A 200 that carried no usable image (an empty `data`, or only urls that are not ours to show):
// the run did not fail, but there is nothing to look at, and saying nothing would read as a hang.
const NO_IMAGE: GenerateError = { message: 'No image came back.', code: null, status: null, details: {} }

function resultsFrom(res: ImageGenerateResponse | null, vars: Omit<ImageGenerateVars, 'signal'>): GenerateResult[] {
  const meta = res?._hal0?.meta
  const seed = typeof meta?.seed === 'number' ? meta.seed : null
  const size = meta?.width && meta?.height ? `${meta.width}x${meta.height}` : (vars.size ?? '')
  const at = Date.now()
  return (res?.data ?? [])
    .map((d) => d?.url)
    .filter(isSafeUrl)
    .map((url) => ({ url, seed, prompt: vars.prompt, model: res?._hal0?.model ?? vars.model, size, at }))
}

/**
 * Make the request. It resolves when the run is over, whichever way; its outcome is in the
 * state, not in a return value, because the panel that started it may be gone by then. One run
 * at a time: a call while one is in flight does nothing.
 */
export async function startRun(
  vars: Omit<ImageGenerateVars, 'signal'>,
  opts: { graceAtStart?: boolean } = {},
): Promise<void> {
  if (state.run) return
  const controller = new AbortController()
  const mine = epoch
  const current = () => mine === epoch
  update({
    run: { controller, startedAt: Date.now(), graceAtStart: opts.graceAtStart === true },
    error: null,
  })
  // The dock reads the run from here, so it outlives the panel.
  useImageRunStore.getState().setRun({ running: true, cancel: cancelRun })
  let patch: Partial<GenerateState> = {}
  try {
    // No timeout of ours: a render can take minutes (Qwen-Image at 20 steps, about six). Only
    // the signal ends the request early.
    const res = await generateImage({ ...vars, signal: controller.signal })
    const made = resultsFrom(res, vars)
    patch = made.length > 0 ? { results: [...made, ...state.results].slice(0, MAX_RESULTS) } : { error: NO_IMAGE }
  } catch (e) {
    if (!isCancellation(e, controller)) patch = { error: toError(e) }
  }
  if (current()) {
    update({ ...patch, run: null })
    useImageRunStore.getState().setRun(null)
  }
}
