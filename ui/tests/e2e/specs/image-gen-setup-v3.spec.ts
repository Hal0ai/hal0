/**
 * image-gen-setup-v3 — the Image Gen pane's three critical paths, end to end against mocked
 * `/api/comfyui/*` and `/v1` endpoints:
 *
 *   1. Setup, install, generate. A box with nothing installed opens on Models with "Set up image
 *      generation"; the recommended install runs (POST 202, /jobs walks running 0% -> 50% ->
 *      completed); after a reload Generate is the default tab; a prompt walks the GPU through
 *      draining -> unloading -> starting_engine -> handoff -> rendering (step 1..8) -> grace
 *      while the phase strip and the status line follow; /v1 answers one url and a tile
 *      appears. `/api/comfyui/models/fetch` (the old install route) is never requested.
 *   2. Won't fit. On the capped guest (a 48 GB container) Qwen-Image Lightning is `wont_fit`:
 *      Generate is disabled, the server's RAM sentence is shown verbatim, no /v1 request is sent.
 *   3. Pinned. A pinned box says "Image mode pinned", and Unpin posts `{pinned: false}`.
 *
 * Mocks: fixtures/imageGen.ts (the pane's endpoint bodies; the catalog rows, memory budgets and
 * fits of the reference box and the capped guest) over the `apiMock` catch-all. The shapes are
 * the server's: src/hal0/api/routes/comfyui.py (`_model_row`, `/arbiter`, `/status`, `/jobs`,
 * `/install`), src/hal0/slots/arbiter.py (`status()`), src/hal0/comfyui/fit.py (`Fit`, the
 * reasons), src/hal0/api/routes/v1.py (images).
 *
 * No timers drive a mock. Every state a test needs is set by the test, at the point it has
 * seen the previous one, and the test then waits for the page to show it: the pane polls
 * /arbiter every 1 s while anything moves (5 s idle) and /status every 2 s while it is busy,
 * so those waits take a poll or two. They carry `POLL`, the longest a poll may take.
 */
import { json, test, expect, type Page } from '../fixtures/apiMock'
import { pickRichOption } from '../fixtures/richSelect'
import {
  INSTALLED,
  arbiterEnvelope,
  arbiterState,
  catalog,
  cappedGuestMemory,
  fitOf,
  gotoImageGen,
  imageJob,
  imageStatus,
  makeImageGenMock,
  qwenLightning,
  referenceBoxMemory,
  routeImageGen,
  sd15,
  sdxlLightning,
  type ImageGenMock,
} from '../fixtures/imageGen'

type Body = Record<string, any>

// A poll-bound wait: /arbiter is read every 5 s until it shows a phase, then every second.
const POLL = { timeout: 10_000 }

// ── the old install route ────────────────────────────────────────────────────

// `POST /api/comfyui/models/fetch` installed a model before the catalog's own /install route
// replaced it. Nothing may call it: a request fails the test from the route itself, and again at
// the end of the test, in case the page swallowed the failed response.
let fetchRequests: string[] = []

test.beforeEach(async ({ page }) => {
  fetchRequests = []
  await page.route(/\/api\/comfyui\/models\/fetch(\?|$)/, (route) => {
    const where = `${route.request().method()} ${route.request().url()}`
    fetchRequests.push(where)
    expect.soft(where, '/api/comfyui/models/fetch was requested').toBeUndefined()
    return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
  })
})

test.afterEach(() => {
  expect(fetchRequests, 'nothing requests /api/comfyui/models/fetch').toEqual([])
})

// ── the reference box ────────────────────────────────────────────────────────

// SDXL Lightning is the pick for a 62 GB pool. It is the model the install and the render use.
const MODEL = 'sdxl-lightning'
const MODEL_LABEL = 'SDXL Lightning (8-step)'
// Its download: 7.6 GB decimal, which the card shows as 7.1 GiB.
const BYTES = 7.6e9

// What the mock server judges for each model on the reference box (fit.py: the worse of the GPU
// and RAM verdicts decides; a need above 0.9 of what is free is `tight`).
const SDXL_FITS = fitOf('fits', {
  need: { gpu_gb: 15.9, ram_gb: 7.1, gpu_estimated: false, ram_estimated: true },
  render_free_gb: 58.2,
  ram_free_gb: 52.9,
})
const SD15_FITS = fitOf('fits', {
  need: { gpu_gb: 11.6, ram_gb: 4, gpu_estimated: false, ram_estimated: true },
  render_free_gb: 58.2,
  ram_free_gb: 52.9,
})
const QWEN_TIGHT = fitOf('tight', {
  reason: 'needs ~47.9 GB of RAM to load, 52.9 GB free (62.7 GB system)',
  need: { gpu_gb: 47.9, ram_gb: 47.9, gpu_estimated: true, ram_estimated: false },
  render_free_gb: 58.2,
  ram_free_gb: 52.9,
})

/** The reference box's catalog: nothing installed, or SDXL Lightning installed. */
function referenceCatalog(installed: boolean): Body {
  return catalog(
    [
      sdxlLightning({ recommended: true, fit: SDXL_FITS, ...(installed ? INSTALLED : {}) }),
      sd15({ fit: SD15_FITS }),
      qwenLightning({ fit: QWEN_TIGHT }),
    ],
    // The picker's pool is the box's unified GPU memory here (hal0.comfyui.picker._image_pool).
    { recommendation: { id: MODEL, pool_gb: 62.4, limited_by: 'gpu' } },
  )
}

/** A freshly installed reference box: chat mode, one chat slot (`brain`) that image mode pauses. */
function referenceBox(over: Partial<ImageGenMock> = {}): ImageGenMock {
  return makeImageGenMock({
    memory: referenceBoxMemory(),
    models: referenceCatalog(false),
    fits: { [MODEL]: SDXL_FITS, 'sd-1.5-pruned-emaonly': SD15_FITS, 'qwen-image-lightning': QWEN_TIGHT },
    ...over,
  })
}

// ── the GPU walking through a render ─────────────────────────────────────────

// Image mode with `brain` paused: what the arbiter reports from the first beat of the switch.
// (`st["mode"] = "img"` is persisted before the drain starts; arbiter.py `ensure_img`.)
const IMAGE_MODE = { mode: 'img', saved_llm_slots: ['brain'], unload_set: ['brain'] } as const

const nowS = () => Date.now() / 1000

/** Put the box in one state: the arbiter's block as `/arbiter` and `/status` both carry it. */
function setBox(mock: ImageGenMock, arbiter: Body, status: Body = {}) {
  mock.arbiter = arbiterEnvelope(arbiter, { mode: arbiter.switching ? 'inference' : 'generation' })
  mock.status = imageStatus({ mode: 'generation', arbiter: arbiterState(arbiter), ...status })
}

/** ComfyUI is rendering `step` of 8 of the hal0 prompt (`step` null: still loading the model). */
function rendering(step: number | null): Body {
  return {
    engine: 'generating',
    util: 71,
    queue: { running: 1, pending: 0 },
    progress: {
      model: MODEL,
      step,
      total: step == null ? null : 8,
      node: step == null ? null : '3',
      elapsed_s: step ?? 0,
      prompt_id: 'p1',
    },
  }
}

// ── page helpers ─────────────────────────────────────────────────────────────

const head = (page: Page) => page.getByTestId('image-status-head')
const detail = (page: Page) => page.getByTestId('image-status-detail')
/** The phase strip's current step, on the Generate tab while a run is in flight. */
const stripNow = (page: Page) => page.locator('[data-testid="gen-step"][data-state="now"]')
/** The status line's switch chip that is running now. */
const chipNow = (page: Page) => page.locator('[data-testid="image-status-step"][data-state="active"]')

test.describe('Image Gen setup: first run, install, generate', () => {
  // The render walk waits on the pane's own polls (5 s for the first, then 1-2 s each) across
  // twenty states.
  test.setTimeout(120_000)

  test('a new box opens on Models, installs the recommended model, then generates', async ({ page }) => {
    let finishRender: () => void = () => {}
    const rendered = new Promise<void>((resolve) => (finishRender = resolve))
    let generated: Body | null = null

    const mock = referenceBox({
      // The install starts a download: the answer is the running job, and /jobs reports it.
      install: (id) => {
        const job = imageJob(id, { state: 'running', bytes_total: BYTES })
        mock.jobs = [job]
        return job
      },
      // The /v1 request is answered when the render is over, as the server does.
      generate: async (route) => {
        generated = route.request().postDataJSON()
        await rendered
        await json(route, {
          created: 1_761_400_100,
          data: [{ url: '/api/images/cache/lighthouse.png' }],
          _hal0: {
            meta: { seed: 1234567, steps: 8, cfg: 1, width: 1024, height: 1024, batch_size: 1 },
            prompt_id: 'p1',
            upstream: null,
            model: MODEL,
            template: 'SDXL-Lightning-8step',
          },
        })
      },
    })
    await routeImageGen(page, mock)

    await test.step('Models is the default tab, and it offers the recommended model', async () => {
      await gotoImageGen(page)
      await expect(page.getByTestId('igt-tab-models')).toHaveAttribute('aria-selected', 'true')
      await expect(page.getByTestId('igt-tab-generate')).toHaveAttribute('aria-selected', 'false')

      const firstRun = page.getByTestId('imp-first-run')
      await expect(firstRun.getByRole('heading', { name: 'Set up image generation' })).toBeVisible()
      const cta = page.getByRole('button', { name: `Install recommended — ${MODEL_LABEL} (~7.1 GB)` })
      await expect(cta).toBeVisible()
      // The reason cites the figure the picker used, and the meter is the server's verdict.
      await expect(page.getByTestId('imp-first-run-recommended')).toContainText('chosen from 62.4 GB of GPU memory')
      await expect(page.getByTestId(`fit-chip-fr-${MODEL}`)).toHaveText('Fits · 42.3 GB to spare')
    })

    await test.step('the install answers 202 and its progress shows, 0% then 50%', async () => {
      await page.getByRole('button', { name: `Install recommended — ${MODEL_LABEL} (~7.1 GB)` }).click()
      await expect.poll(() => mock.posts.map(([path]) => path)).toContain(`/api/comfyui/models/${MODEL}/install`)
      expect(mock.posts.filter(([path]) => path.endsWith('/install'))).toHaveLength(1)

      // The block that offered the install is replaced by the model's card and its download.
      await expect(page.getByTestId('imp-first-run')).toHaveCount(0)
      const card = page.getByTestId(`imp-card-${MODEL}`)
      const bar = card.getByRole('progressbar', { name: 'Download progress' })
      await expect(bar).toHaveAttribute('aria-valuenow', '0')
      await expect(page.getByTestId('igt-tab-models')).toHaveText('Models · 1 downloading')

      mock.jobs = [imageJob(MODEL, { state: 'running', bytes_total: BYTES, bytes_downloaded: BYTES / 2 })]
      await expect(bar).toHaveAttribute('aria-valuenow', '50', POLL)
      await expect(page.getByTestId(`imp-progress-${MODEL}`)).toContainText('3.5 / 7.1 GB')
    })

    await test.step('the install completes and the model is installed', async () => {
      // The catalog answers `complete` by the time the job is reported done.
      mock.models = referenceCatalog(true)
      mock.jobs = [
        imageJob(MODEL, { state: 'completed', bytes_total: BYTES, bytes_downloaded: BYTES, finished_at: 1_761_400_090 }),
      ]
      const card = page.getByTestId(`imp-card-${MODEL}`)
      await expect(card).toHaveAttribute('data-complete', 'true', POLL)
      await expect(card.getByRole('button', { name: 'Generate' })).toBeVisible()
      await expect(page.getByTestId(`imp-progress-${MODEL}`)).toHaveCount(0)
      await expect(page.getByTestId('igt-tab-models')).toHaveText('Models')
    })

    await test.step('after a reload Generate is the default tab', async () => {
      await page.reload()
      await page.getByTestId('image-gen-pane').waitFor()
      await expect(page.getByTestId('igt-tab-generate')).toHaveAttribute('aria-selected', 'true')
      await expect(page.getByTestId('gen-panel')).toBeVisible()
      await expect(page.getByTestId('gen-model')).toContainText(MODEL_LABEL)
      await expect(page.getByTestId('fit-chip-gen')).toHaveText('Fits · 42.3 GB to spare')

      // Chat mode: the status line and the button's cost line say what an image costs chat.
      await expect(head(page)).toHaveText('GPU · chat mode')
      const cost = 'Pauses brain (9.6 GB). They reload about 10 s after the last image.'
      await expect(page.getByTestId('gen-cost')).toHaveText(cost)
      await expect(detail(page)).toHaveText(cost)
    })

    await test.step('Generate starts the run', async () => {
      await page.getByTestId('gen-prompt').fill('a lighthouse at dusk, oil on canvas')
      await page.getByTestId('gen-submit').click()
      await expect(page.getByTestId('gen-run')).toBeVisible()
      await expect(page.getByTestId('gen-submit')).toHaveCount(0)
      // The prompt has not reached the arbiter yet: the first step is the one waiting.
      await expect(stripNow(page)).toHaveText('Chat reply finishing')
      await expect.poll(() => generated).not.toBeNull()
      expect(generated).toMatchObject({
        model: MODEL,
        prompt: 'a lighthouse at dusk, oil on canvas',
        n: 1,
        size: '1024x1024',
        response_format: 'url',
      })
      expect(generated!.extra_body).toMatchObject({ steps: 8, cfg: 1 })
      expect(Number.isInteger(generated!.extra_body.seed)).toBe(true)
    })

    await test.step('draining: one chat reply finishes first', async () => {
      setBox(mock, { ...IMAGE_MODE, switching: true, phase: 'draining', drain_in_flight: 1 }, { engine: 'stopped' })
      await expect(head(page)).toHaveText('Switching to image mode', POLL)
      await expect(detail(page)).toHaveText('Waiting for 1 chat reply to finish.')
      await expect(chipNow(page)).toHaveText('Chat replies')
      await expect(stripNow(page)).toHaveText('Chat reply finishing')
    })

    await test.step('unloading: the chat slot is released', async () => {
      setBox(mock, { ...IMAGE_MODE, switching: true, phase: 'unloading' }, { engine: 'stopped' })
      await expect(detail(page)).toHaveText('Unloading brain to free the GPU.', POLL)
      await expect(chipNow(page)).toHaveText('Unload slots')
      await expect(stripNow(page)).toHaveText('Unload 1 GPU slot')
    })

    await test.step('starting_engine: the image engine comes up', async () => {
      setBox(mock, { ...IMAGE_MODE, switching: true, phase: 'starting_engine' }, { engine: 'starting' })
      await expect(detail(page)).toHaveText('Starting the image engine.', POLL)
      await expect(chipNow(page)).toHaveText('Start engine')
      await expect(stripNow(page)).toHaveText('Engine ready')
    })

    await test.step('handoff: the prompt is going to ComfyUI', async () => {
      setBox(mock, { ...IMAGE_MODE, phase: 'handoff', handoff_until: nowS() + 30 }, { engine: 'running' })
      await expect(head(page)).toHaveText('Image mode · hand-off', POLL)
      await expect(stripNow(page)).toHaveText('Load model')
    })

    await test.step('rendering: the model loads, then steps 1 to 8', async () => {
      const busy = { ...IMAGE_MODE, phase: 'rendering', image_jobs: 1 }
      setBox(mock, busy, rendering(null))
      await expect(head(page)).toHaveText(`Rendering · ${MODEL}`, POLL)
      await expect(stripNow(page)).toHaveText('Load model')
      await expect(page.getByTestId('gen-progress')).toHaveAttribute('data-indeterminate', 'true')
      await expect(page.getByTestId('image-status-action-cancel_render')).toBeVisible()

      // 1/8 .. 8/8 as percentages, rounded the way the bar rounds them.
      const pct = [13, 25, 38, 50, 63, 75, 88, 100]
      for (let step = 1; step <= 8; step++) {
        setBox(mock, busy, rendering(step))
        await expect(head(page)).toHaveText(`Rendering · ${MODEL} · step ${step}/8`, POLL)
        await expect(stripNow(page)).toHaveText(`Render ${step}/8`)
        await expect(page.getByTestId('gen-progress')).toHaveAttribute('aria-valuenow', String(pct[step - 1]))
        await expect(page.getByTestId('gen-progress')).toHaveAttribute('data-indeterminate', 'false')
      }
    })

    await test.step('grace: the render is over and the files are written', async () => {
      setBox(mock, { ...IMAGE_MODE, phase: 'grace', restore_at: nowS() + 10, image_jobs: 0 }, { engine: 'running' })
      await expect(head(page)).toHaveText(/^Image mode ends in \d+ s$/, POLL)
      await expect(stripNow(page)).toHaveText('Save')
      // The run is still open until /v1 answers.
      await expect(page.getByTestId('gen-run')).toBeVisible()
      await expect(page.getByTestId('gen-tile')).toHaveCount(0)
    })

    await test.step('/v1 returns one url and a tile appears', async () => {
      finishRender()
      const tile = page.getByTestId('gen-tile')
      await expect(tile).toHaveCount(1)
      await expect(tile.locator('img')).toHaveJSProperty('naturalWidth', 1)
      await expect(tile.locator('img')).toHaveAttribute('src', '/api/images/cache/lighthouse.png')
      await expect(tile.locator('figcaption')).toContainText(`${MODEL} · 1024x1024 · seed 1234567`)

      // The run is over: the strip is gone and the button is back, now reading "again" because
      // image mode is still on, and the cost line says the next image starts at once.
      await expect(page.getByTestId('gen-run')).toHaveCount(0)
      await expect(page.getByTestId('gen-submit')).toHaveText('Generate again')
      await expect(page.getByTestId('gen-cost')).toHaveText('Image mode is still on, so this one starts at once.')
      await expect(page.getByTestId('gen-error')).toHaveCount(0)
    })
  })
})

// ── the capped guest ─────────────────────────────────────────────────────────

test.describe("Image Gen setup: a model that won't fit", () => {
  // fit.py `_ram_reason`: the sentence for the tighter pool, here RAM: Qwen-Image Lightning loads
  // its 47.9 GiB through the guest's memory, and the container has 48 GB with 45.8 GB free.
  const REASON = 'needs ~47.9 GB of RAM to load, 45.8 GB free (48.0 GB container limit)'
  const QWEN = 'qwen-image-lightning'
  const SDXL_FITS_CAPPED = fitOf('fits', {
    need: { gpu_gb: 15.9, ram_gb: 7.1, gpu_estimated: false, ram_estimated: true },
    render_free_gb: 70.6,
    ram_free_gb: 45.8,
  })
  const QWEN_WONT_FIT = fitOf('wont_fit', {
    reason: REASON,
    need: { gpu_gb: 47.9, ram_gb: 47.9, gpu_estimated: true, ram_estimated: false },
    render_free_gb: 70.6,
    ram_free_gb: 45.8,
  })

  test("Qwen-Image Lightning is 'Won't fit': Generate is disabled and nothing is sent to /v1", async ({ page }) => {
    // Both models are installed; the other guests hold 43.4 GB of the pool and the container's
    // 48 GB RAM is the limit.
    const mock = makeImageGenMock({
      memory: cappedGuestMemory(),
      models: catalog(
        [
          sdxlLightning({ ...INSTALLED, recommended: true, fit: SDXL_FITS_CAPPED }),
          qwenLightning({ ...INSTALLED, fit: QWEN_WONT_FIT }),
        ],
        { recommendation: { id: MODEL, pool_gb: 48, limited_by: 'ram' } },
      ),
      fits: { [MODEL]: SDXL_FITS_CAPPED, [QWEN]: QWEN_WONT_FIT },
      generate: (route) => {
        expect.soft(route.request().url(), 'a /v1 request reached the server').toBeUndefined()
        return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
      },
    })
    await routeImageGen(page, mock)

    // Every request to /v1, counted at the network and not at a route handler.
    let v1Requests = 0
    page.on('request', (req) => {
      if (new URL(req.url()).pathname.startsWith('/v1/')) v1Requests++
    })

    await gotoImageGen(page, 'generate')
    await page.getByTestId('gen-prompt').fill('a lighthouse at dusk')

    // The first model fits, so the button is live (the prompt is not what disables it later).
    await expect(page.getByTestId('fit-chip-gen')).toHaveText(/^Fits · /)
    await expect(page.getByTestId('gen-submit')).toBeEnabled()

    await pickRichOption(page.getByTestId('gen-model'), QWEN)
    await expect(page.getByTestId('gen-model')).toContainText('Qwen-Image (4-step Lightning)')

    await expect(page.getByTestId('fit-chip-gen')).toHaveText("Won't fit")
    await expect(page.getByTestId('fit-meter-gen')).toHaveAttribute('data-verdict', 'wont_fit')
    // The server's sentence, as given: in the meter and beside the button.
    await expect(page.getByTestId('fit-reason-gen')).toHaveText(REASON)
    await expect(page.getByTestId('gen-blocked')).toHaveText(REASON)
    await expect(page.getByTestId('gen-submit')).toBeDisabled()

    // The keyboard shortcut is the other way to generate; it is refused too. The round trip to
    // the page after it is the barrier a request would have to cross.
    await page.getByTestId('gen-prompt').press('Control+Enter')
    await page.evaluate(() => new Promise<void>((done) => setTimeout(done, 0)))
    await expect(page.getByTestId('gen-run')).toHaveCount(0)
    await expect(page.getByTestId('gen-error')).toHaveCount(0)
    expect(v1Requests, 'no /v1 request was sent').toBe(0)

    // Back on a model that fits, the button works again: the block belonged to Qwen.
    await pickRichOption(page.getByTestId('gen-model'), MODEL)
    await expect(page.getByTestId('fit-chip-gen')).toHaveText(/^Fits · /)
    await expect(page.getByTestId('gen-blocked')).toHaveCount(0)
    await expect(page.getByTestId('gen-submit')).toBeEnabled()
    expect(v1Requests, 'choosing a model sends nothing to /v1').toBe(0)
  })
})

// ── pinned ───────────────────────────────────────────────────────────────────

test.describe('Image Gen setup: a pinned box', () => {
  test('says "Image mode pinned", and Unpin posts {pinned: false}', async ({ page }) => {
    const pinned = {
      ...IMAGE_MODE,
      pinned: true,
      // Two chat requests are waiting on the paused slot: why anyone would unpin.
      llm_waiters: { count: 2, oldest_s: 37 },
    }
    const mock = makeImageGenMock({ memory: referenceBoxMemory() })
    setBox(mock, pinned)
    await routeImageGen(page, mock)
    // The pin route of the fixture records the POST and answers it; this one first moves the box
    // as the server does, an unpinned image mode that starts its grace.
    await page.route('**/api/comfyui/pin', (route) => {
      if (route.request().postDataJSON()?.pinned === false) {
        setBox(mock, { ...pinned, pinned: false, phase: 'grace', restore_at: nowS() + 10, llm_waiters: { count: 0, oldest_s: null } })
      }
      return route.fallback()
    })

    await gotoImageGen(page)

    await expect(head(page)).toHaveText('Image mode pinned')
    await expect(detail(page)).toHaveText(
      'Held until you unpin it. The paused GPU slots stay paused. 2 chat requests waiting, longest 0:37 of 2:00.',
    )
    await expect(page.getByTestId('image-status-line')).toHaveAttribute('data-tone', 'pin')
    await expect(page.getByTestId('image-status-action-stop')).toHaveText('Stop image mode')
    const unpin = page.getByTestId('image-status-action-unpin')
    await expect(unpin).toHaveText('Unpin')
    expect(mock.posts, 'nothing was sent before Unpin was pressed').toEqual([])

    await unpin.click()

    // Exactly one write, to /pin, with exactly this body.
    await expect.poll(() => mock.posts).toEqual([['/api/comfyui/pin', { pinned: false }]])
    // The line leaves "pinned" at once (the click asks for the new state) and counts the grace.
    await expect(head(page)).toHaveText(/^Image mode ends in \d+ s$/)
    await expect(page.getByTestId('image-status-action-unpin')).toHaveCount(0)
    await expect(page.getByTestId('image-status-action-keep')).toBeVisible()
  })
})
