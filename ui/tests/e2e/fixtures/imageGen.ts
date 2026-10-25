/**
 * Image Gen pane fixtures: the bodies the pane's endpoints answer, and the routes that serve them.
 *
 * Same pattern as the other specs (`page.route` over the `apiMock` catch-all, which answers `{}`):
 * a spec builds a mock with `makeImageGenMock({...})`, installs it with `routeImageGen(page, mock)`
 * and reads `mock.posts` for the writes the pane made. The routes read the mock when a request
 * arrives, so a test can change `mock.status` (or any other body) after navigating and the next
 * poll answers it.
 *
 * Every body is the shape the backend sends (src/hal0/api/routes/comfyui.py), reduced to what the
 * pane reads. `/api/comfyui/status` carries no `memory`: the memory figures are the `memory` block
 * of `/api/stats/hardware`.
 */
import type { Page, Route } from '@playwright/test'
import { json } from './apiMock'

type Body = Record<string, any>

/** A fit that fits, as `GET /api/comfyui/models` and `/fit` carry it. */
export const FITS = {
  verdict: 'fits',
  reason: null,
  need: { gpu_gb: 7, ram_gb: 12, gpu_estimated: false, ram_estimated: false },
  render_free_gb: 90,
  ram_free_gb: 80,
}

/** One catalog row (`GET /api/comfyui/models`). Installed and renderable unless said otherwise. */
export function catalogRow(id: string, over: Body = {}): Body {
  return {
    id,
    label: id,
    task: 'txt2img',
    state: 'complete',
    api_renderable: true,
    recommended: false,
    license: 'apache-2.0',
    est_seconds: 8,
    sizes: ['1024x1024'],
    notes: '',
    defaults: { steps: 8, cfg: 1 },
    approx_gb: 6.9,
    missing_gb: 0,
    missing: [],
    workflow: `${id}.json`,
    job: null,
    fit: FITS,
    ...over,
  }
}

export function catalog(models: Body[], over: Body = {}): Body {
  return {
    models,
    engine: { available: true, reason: null },
    store: { free_gb: 200, path: '/models/comfyui' },
    recommendation: null,
    ...over,
  }
}

/** The arbiter's own block (`ArbiterState`): chat mode, nothing happening. */
export function arbiterState(over: Body = {}): Body {
  return {
    mode: 'llm',
    pinned: false,
    saved_llm_slots: [],
    switching: false,
    image_jobs: 0,
    phase: null,
    restore_at: null,
    handoff_until: null,
    drain_in_flight: 0,
    unload_set: ['brain'],
    llm_waiters: { count: 0, oldest_s: null },
    ...over,
  }
}

/** `GET /api/comfyui/arbiter`. */
export function arbiterEnvelope(arbiter: Body = {}, over: Body = {}): Body {
  return {
    mode: null,
    arbiter: arbiterState(arbiter),
    switchover: { active: false, target: null, error: null },
    gate_refusal: null,
    ...over,
  }
}

/** `GET /api/comfyui/status` with the engine up and idle. */
export function imageStatus(over: Body = {}): Body {
  return {
    mode: 'inference',
    reachable: true,
    engine: 'running',
    container: { name: 'hal0-slot-img', state: 'running' },
    endpoint: ':8188',
    queue: { running: 0, pending: 0 },
    util: 0,
    temp: 68.5,
    clock: 2.7,
    progress: null,
    inference: { hermes: false },
    inventory: null,
    switchover: { active: false, target: null, error: null },
    arbiter: arbiterState(),
    gate: { state: 'current', version_seen: '3' },
    gate_refusal: null,
    ...over,
  }
}

/** The `memory` block of `GET /api/stats/hardware` (ADR-0039): a box with a shared GPU pool. */
export function memoryBudget(over: Body = {}): Body {
  return {
    gpu: {
      kind: 'shared',
      total_gb: 116,
      used_gb: 30,
      this_box: [{ slot: 'brain', gb: 24, state: 'serving' }],
      image_engine_gb: 4.4,
      image_engine_slot: 'img',
      image_engine_estimated: false,
      other_guests_gb: 0,
      attribution_excess_gb: 0,
      free_gb: 86,
    },
    ram: { limit_gb: 105.5, used_gb: 16.1, free_gb: 89.4, source: 'container' },
    render_free_gb: 80,
    ...over,
  }
}

export interface ImageGenMock {
  status: Body | { __status: number }
  arbiter: Body
  models: Body
  jobs: Body[]
  workflows: Body[]
  files: Body[]
  logs: string[]
  urls: Body
  memory: Body
  /** What `POST /v1/images/generations` does: answer, or hold the request for the test. */
  generate: (route: Route) => Promise<void> | void
  /** Every POST/DELETE the pane sent: `[path, body]`. */
  posts: [string, any][]
}

export function makeImageGenMock(over: Partial<ImageGenMock> = {}): ImageGenMock {
  return {
    status: imageStatus(),
    arbiter: arbiterEnvelope(),
    models: catalog([catalogRow('sdxl-lightning')]),
    jobs: [],
    workflows: [],
    files: [],
    logs: ['2026-10-25T10:00:00+0000 comfy start', '2026-10-25T10:00:01+0000 comfy ready'],
    // The page is served from localhost in the e2e run, so the editor link is on the box.
    urls: {
      api: 'http://localhost:8080',
      openwebui: 'http://localhost:3001',
      openwebui_enabled: false,
      hermes: '',
      hermes_enabled: false,
      comfyui: 'http://localhost:8188',
      comfyui_public: false,
    },
    memory: memoryBudget(),
    generate: (route) => json(route, { created: 0, data: [] }),
    posts: [],
    ...over,
  }
}

/**
 * Serve `mock` for every endpoint the Image Gen pane reads, and record the writes. Install it
 * after the `apiMock` fixture's catch-all (a spec's `page.route` always is).
 */
export async function routeImageGen(page: Page, mock: ImageGenMock) {
  const record = (route: Route, body: Body, status = 202) => {
    const req = route.request()
    let sent: any = null
    try {
      sent = req.postDataJSON()
    } catch {
      sent = req.postData()
    }
    mock.posts.push([new URL(req.url()).pathname, sent])
    return json(route, body, status)
  }

  await page.route('**/api/comfyui/status', (route) => {
    const s = mock.status as Body
    if (typeof s.__status === 'number') return route.fulfill({ status: s.__status, contentType: 'application/json', body: '{}' })
    return json(route, s)
  })
  await page.route('**/api/comfyui/arbiter', (route) => json(route, mock.arbiter))
  await page.route('**/api/comfyui/models', (route) => json(route, mock.models))
  await page.route(/\/api\/comfyui\/models\/[^/]+\/fit/, (route) => {
    const url = new URL(route.request().url())
    return json(route, { fit: FITS, size: url.searchParams.get('size') ?? '1024x1024' })
  })
  await page.route('**/api/comfyui/jobs', (route) => json(route, { jobs: mock.jobs }))
  await page.route('**/api/comfyui/files', (route) => json(route, { root: '/models/comfyui', files: mock.files }))
  await page.route('**/api/comfyui/workflows', (route) => json(route, { workflows: mock.workflows }))
  await page.route(/\/api\/comfyui\/logs/, (route) => json(route, { lines: mock.logs }))
  await page.route('**/api/config/urls', (route) => json(route, mock.urls))
  await page.route('**/api/stats/hardware', (route) => json(route, { memory: mock.memory }))

  await page.route('**/api/comfyui/restart', (route) => record(route, { status: 'restart_requested' }))
  await page.route('**/api/comfyui/render/cancel', (route) => record(route, { status: 'cancel_requested' }))
  await page.route('**/api/comfyui/switchover', (route) => record(route, { status: 'accepted' }))
  await page.route('**/api/comfyui/pin', (route) => {
    const pinned = route.request().postDataJSON()?.pinned ?? false
    return record(route, { pinned }, 200)
  })
  await page.route('**/v1/images/generations', (route) => mock.generate(route))
}

/** Open `#slots/image[/sub]` and wait for the pane. */
export async function gotoImageGen(page: Page, sub?: 'generate' | 'models' | 'engine') {
  await page.goto(sub ? `/#slots/image/${sub}` : '/#slots/image')
  await page.getByTestId('image-gen-pane').waitFor({ timeout: 10_000 })
}
