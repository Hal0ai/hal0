// The Generate tab's session state: the run in flight, its error and the last 12 results.
//
// It lives at module level, not in the panel, because the panel is not always mounted: switching
// to the Models tab unmounts it, and a render takes minutes. A run is not cancelled by leaving
// the tab (the render goes on in ComfyUI whatever the browser does), so the request, its outcome
// and the results all outlive the panel and the panel reads them back when it returns. The
// results are mirrored to sessionStorage so a reload of the tab keeps them too; every storage
// access is guarded, since private windows and blocked site data throw.
//
// The panel owns the form and the cancel routing (it knows the arbiter's phase); this module owns
// the request.

import { useSyncExternalStore } from 'react'
import { generateImage, type ImageGenerateResponse, type ImageGenerateVars } from '@/api/hooks/useComfyui'

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

/** Drop the error on screen (the viewer moved on to another model). */
export function dismissError(): void {
  if (state.error) update({ error: null })
}

/** Forget everything: for the tests, and nothing else. */
export function resetGenerateStore(): void {
  epoch += 1
  state.run?.controller.abort()
  state = { results: [], run: null, error: null }
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
  if (current()) update({ ...patch, run: null })
}
