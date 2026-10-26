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

// A 1x1 PNG.
const PNG_1X1 = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==',
  'base64',
)

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

/**
 * The API's mode for an arbiter block, as the server reports it in `/status` `mode` and the
 * `/arbiter` envelope (`api_mode`, src/hal0/api/routes/comfyui.py): `generation` only once image
 * mode is entered and no switch or restore is running, so `inference` while a switch runs.
 */
export function apiMode(arbiter: Body): 'generation' | 'inference' | null {
  if (arbiter.mode === 'llm') return 'inference'
  if (arbiter.mode === 'img') return arbiter.switching ? 'inference' : 'generation'
  return null
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

/**
 * The reference box: a 64 GB unified-memory machine with one chat slot (`brain`, 9.6 GB) loaded.
 * A render can use 58.2 GB once image mode has unloaded `brain`, and RAM is the system's.
 */
export function referenceBoxMemory(): Body {
  return {
    gpu: {
      kind: 'shared',
      total_gb: 62.4,
      used_gb: 11.8,
      this_box: [{ slot: 'brain', gb: 9.6, state: 'serving' }],
      image_engine_gb: 0,
      image_engine_slot: 'img',
      image_engine_estimated: false,
      other_guests_gb: 2.2,
      attribution_excess_gb: 0,
      free_gb: 50.6,
    },
    ram: { limit_gb: 62.7, used_gb: 9.8, free_gb: 52.9, source: 'system' },
    render_free_gb: 58.2,
  }
}

/**
 * The capped guest: a 48 GB container on a 116 GB shared pool whose other guests hold 43.4 GB of
 * it (the box of memory-budget-capped-guest-v3). The GPU has room; RAM is what is short.
 */
export function cappedGuestMemory(): Body {
  return {
    gpu: {
      kind: 'shared',
      total_gb: 116,
      used_gb: 43.4,
      this_box: [],
      image_engine_gb: 0,
      image_engine_slot: 'img',
      image_engine_estimated: false,
      other_guests_gb: 43.4,
      attribution_excess_gb: 0,
      free_gb: 72.6,
    },
    ram: { limit_gb: 48, used_gb: 2.2, free_gb: 45.8, source: 'container' },
    render_free_gb: 70.6,
  }
}

/** An install job (`PullJob.as_dict()`): queued, nothing downloaded, until `over` says more. */
export function imageJob(modelId: string, over: Body = {}): Body {
  return {
    id: `job-${modelId}`,
    model_id: modelId,
    state: 'queued',
    bytes_downloaded: 0,
    bytes_total: 0,
    started_at: 1_761_400_000,
    finished_at: null,
    error: null,
    error_code: null,
    sha256: null,
    path: null,
    files: [],
    ...over,
  }
}

// The catalog's own rows (src/hal0/comfyui/catalog.py), in GiB the way `_model_row` reports them.
// The id, label, licence, speed, sizes and sampler defaults are the catalog's. A row starts
// uninstalled (`state: 'none'`, every file missing); `INSTALLED` turns it into an installed one,
// and `fit` is the verdict the mock server gives on the box in question.

/** What an installed row changes: every file is on disk. */
export const INSTALLED = { state: 'complete', missing_gb: 0, missing: [] } as const

/** SDXL Lightning (8-step): the starter pick for a box with 16..96 GB. 7.6 GB decimal is 7.1 GiB. */
export function sdxlLightning(over: Body = {}): Body {
  return catalogRow('sdxl-lightning', {
    state: 'none',
    label: 'SDXL Lightning (8-step)',
    license: 'CreativeML-OpenRAIL++',
    est_seconds: 10,
    sizes: ['1024x1024', '1152x896', '896x1152', '1216x832', '832x1216', '1344x768', '768x1344'],
    notes: "SDXL base with ByteDance's 8-step Lightning LoRA and the fp16-fix VAE.",
    defaults: { steps: 8, cfg: 1 },
    approx_gb: 7.1,
    missing_gb: 7.1,
    // Every component file: `<subdir>/<relpath>` of each (catalog.py).
    missing: [
      'checkpoints/sd_xl_base_1.0.safetensors',
      'loras/sdxl_lightning_8step_lora.safetensors',
      'vae/sdxl_vae_fp16_fix.safetensors',
    ],
    ...over,
  })
}

/** Stable Diffusion 1.5: the starter pick below 16 GB. 4.3 GB decimal is 4.0 GiB. */
export function sd15(over: Body = {}): Body {
  return catalogRow('sd-1.5-pruned-emaonly', {
    state: 'none',
    label: 'Stable Diffusion 1.5',
    license: 'CreativeML-Open-RAIL-M',
    est_seconds: 5,
    sizes: ['512x512', '512x768', '768x512'],
    notes: 'Pruned EMA-only checkpoint; runs on small GPUs. Quality drops above 768.',
    defaults: { steps: 20, cfg: 7.0 },
    approx_gb: 4,
    missing_gb: 4,
    missing: ['checkpoints/v1-5-pruned-emaonly.safetensors'],
    ...over,
  })
}

/** Qwen-Image (4-step Lightning): 47.9 GiB of weights, loads through RAM (45.5 GiB measured floor). */
export function qwenLightning(over: Body = {}): Body {
  return catalogRow('qwen-image-lightning', {
    state: 'none',
    label: 'Qwen-Image (4-step Lightning)',
    license: 'Apache-2.0',
    est_seconds: 75,
    sizes: ['1328x1328', '1664x928', '928x1664', '1472x1140', '1140x1472'],
    notes: 'Qwen-Image 2512 in bf16 with the 4-step Lightning LoRA.',
    defaults: { steps: 4, cfg: 1 },
    approx_gb: 47.9,
    missing_gb: 47.9,
    missing: [
      'diffusion_models/qwen_image_2512_bf16.safetensors',
      'text_encoders/qwen_2.5_vl_7b_fp8_scaled.safetensors',
      'vae/qwen_image_vae.safetensors',
      'loras/Qwen-Image-2512-Lightning-4steps-V1.0-bf16.safetensors',
    ],
    ...over,
  })
}

/** A fit the mock server judged, in the shape of `Fit.as_dict()` (src/hal0/comfyui/fit.py). */
export function fitOf(verdict: 'fits' | 'tight' | 'wont_fit', fit: Body): Body {
  return { verdict, reason: null, ...fit }
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
  /** The verdict `GET /models/{id}/fit` gives per model id; a model not listed fits (`FITS`). */
  fits: Record<string, Body | null>
  /** The job `POST /models/{id}/install` answers 202 with; the default is a queued one. */
  install: (modelId: string) => Body
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
    fits: {},
    install: (modelId) => imageJob(modelId),
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
    const id = decodeURIComponent(url.pathname.split('/').at(-2) ?? '')
    const fit = id in mock.fits ? mock.fits[id] : FITS
    return json(route, { fit, size: url.searchParams.get('size') ?? '1024x1024' })
  })
  await page.route('**/api/comfyui/jobs', (route) => json(route, { jobs: mock.jobs }))
  await page.route('**/api/comfyui/files', (route) => json(route, { root: '/models/comfyui', files: mock.files }))
  await page.route('**/api/comfyui/workflows', (route) => json(route, { workflows: mock.workflows }))
  await page.route(/\/api\/comfyui\/logs/, (route) => json(route, { lines: mock.logs }))
  await page.route('**/api/config/urls', (route) => json(route, mock.urls))
  await page.route('**/api/stats/hardware', (route) => json(route, { memory: mock.memory }))

  await page.route(/\/api\/comfyui\/models\/[^/]+\/install$/, (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2) ?? '')
    return record(route, { job: mock.install(id) })
  })
  // A rendered image: the cache path `/v1/images/generations` answers with. One pixel is enough
  // for the tile's <img> to load.
  await page.route('**/api/images/cache/*.png', (route) =>
    route.fulfill({ status: 200, contentType: 'image/png', body: PNG_1X1 }),
  )
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
