/**
 * comfyui-arbiter-v3 — the Image Gen pane's live wiring: what `/api/comfyui/status` and
 * `/api/comfyui/arbiter` put on the dock's GPU segment and tab and on the Engine tab, and which
 * POSTs their buttons send. (The pane's own status line, its device card and the Engine tab's Stop
 * moved to the dock; Stop image mode is the GPU tab's `gpu-status-action-stop`.)
 *
 * Replaces the specs of the old "render hero" pane (its GTT and RAM gauges, the fake `it/s` and
 * frame counters, the queue drawer and its Restart, the hard-coded workflow strip and the model
 * inventory pills were deleted with it; a spec for a deleted control is not kept). What it
 * pinned that is still true is here against the new pane: the render and queue bind from the
 * status, Cancel render, Restart and Stop post to their endpoints, a stopped or unreachable
 * engine says so, an idle queue is in-flow and clickable, and a failing poll shows no
 * fabricated engine.
 *
 * Convention (as before): VITE_MOCK_HAL0=1 build, `/api/comfyui/*` is not in MOCK_ALLOWLIST, so
 * `page.route` drives it; fixtures/imageGen.ts builds the bodies and records the writes.
 */
import type { Page } from '@playwright/test'
import { test, expect } from '../fixtures/apiMock'
import {
  arbiterEnvelope,
  arbiterState,
  catalog,
  catalogRow,
  gotoImageGen,
  imageStatus,
  makeImageGenMock,
  memoryBudget,
  routeImageGen,
} from '../fixtures/imageGen'

// Image mode on, a hal0 render at step 3 of 8, and two prompts waiting behind it.
const RENDER = { model: 'sdxl-lightning', step: 3, total: 8, node: '3', elapsed_s: 6.2, prompt_id: 'p1' }
const rendering = () =>
  makeImageGenMock({
    status: imageStatus({
      mode: 'generation',
      engine: 'generating',
      queue: { running: 1, pending: 2 },
      util: 63,
      progress: RENDER,
      arbiter: arbiterState({ mode: 'img', phase: 'rendering', image_jobs: 1 }),
    }),
    arbiter: arbiterEnvelope({ mode: 'img', phase: 'rendering', image_jobs: 1 }, { mode: 'generation' }),
  })
/** Expand the dock onto its GPU tab, and return the pane (the segment keeps the same testids). */
async function openGpuTab(page: Page) {
  await page.getByTestId('dock-toggle').click()
  await page.getByTestId('dock-tab-gpu').click()
  const pane = page.getByTestId('dock-gpu')
  await expect(pane).toBeVisible()
  return pane
}

test.describe('Image Gen live wiring: dock GPU status', () => {
  test('a render in progress shows its model and step', async ({ page }) => {
    await routeImageGen(page, rendering())
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('gpu-status-head')).toHaveText('Rendering · sdxl-lightning · step 3/8')
  })

  test('Cancel render posts /render/cancel', async ({ page }) => {
    const mock = rendering()
    await routeImageGen(page, mock)
    await gotoImageGen(page, 'engine')

    const pane = await openGpuTab(page)
    await pane.getByTestId('gpu-status-action-cancel_render').click()
    await expect.poll(() => mock.posts.map(([path]) => path)).toContain('/api/comfyui/render/cancel')
  })

  test('with no GPU segment (no GPU budget) the pane still renders', async ({ page }) => {
    // The segment is hidden while the memory budget has no GPU pool; nothing else depends on it.
    await routeImageGen(page, makeImageGenMock({ memory: memoryBudget({ gpu: null, render_free_gb: null }) }))
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('dock-gpu-segment')).toHaveCount(0)
    await expect(page.getByTestId('engine-card')).toBeVisible()
  })
})

test.describe('Image Gen live wiring: Engine tab', () => {
  test('the queue card binds the running and waiting counts and the render in progress', async ({ page }) => {
    await routeImageGen(page, rendering())
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('queue-summary')).toHaveText('1 running · 2 waiting')
    const progress = page.getByTestId('queue-progress')
    await expect(progress).toContainText('sdxl-lightning')
    await expect(progress).toContainText('step 3/8')
    await expect(page.getByTestId('queue-empty')).toHaveCount(0)
  })

  test('an idle queue says nothing is queued, in-flow, and leaves the editor button clickable', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'engine')

    const empty = page.getByTestId('queue-empty')
    await expect(empty).toBeVisible()
    await expect(empty).toHaveText(
      'Nothing queued. Renders from Generate, Open WebUI, the API and the editor all show here.',
    )
    // The PR #845 lockup: an absolutely positioned empty state ate every click below it.
    const position = await empty.evaluate((el: Element) => getComputedStyle(el).position)
    expect(position).toBe('static')
    const open = page.getByTestId('editor-open')
    await expect(open).toBeEnabled()
    await open.click({ trial: true })
  })

  test('the engine card states the engine, its Quadlet container, loopback and the editor route', async ({
    page,
  }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('engine-pill')).toHaveAttribute('data-state', 'running')
    await expect(page.getByTestId('engine-row-container')).toContainText('Quadlet')
    await expect(page.getByTestId('engine-row-container')).toContainText('hal0-slot-img')
    await expect(page.getByTestId('engine-row-listen')).toContainText('listens on loopback')
    await expect(page.getByTestId('engine-row-editor')).toContainText('127.0.0.1:8188')
    await expect(page.getByTestId('engine-row-gate')).toHaveAttribute('data-gate-state', 'current')
  })

  test('Restart engine posts /restart', async ({ page }) => {
    const mock = makeImageGenMock()
    await routeImageGen(page, mock)
    await gotoImageGen(page, 'engine')

    await page.getByTestId('engine-restart').click()
    await expect.poll(() => mock.posts.map(([path]) => path)).toContain('/api/comfyui/restart')
  })

  test('Logs open the container log in a modal, and Esc closes it', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'engine')

    await page.getByTestId('engine-logs').click()
    await expect(page.getByTestId('engine-logs-pre')).toContainText('comfy ready')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('engine-logs-pre')).toHaveCount(0)
  })

  // Stop image mode lives on the dock's GPU tab, and the status line offers it only while image
  // mode is pinned (status-line-pure.ts): an unpinned image mode ends by itself after its grace.
  // The Engine tab has no Stop button.
  test('Stop image mode (pinned) unpins, then asks the arbiter to go back to inference', async ({ page }) => {
    const mock = makeImageGenMock({
      status: imageStatus({ mode: 'generation', arbiter: arbiterState({ mode: 'img', phase: 'idle', pinned: true }) }),
      arbiter: arbiterEnvelope({ mode: 'img', phase: 'idle', pinned: true }, { mode: 'generation' }),
    })
    await routeImageGen(page, mock)
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('engine-stop')).toHaveCount(0)
    const pane = await openGpuTab(page)
    await pane.getByTestId('gpu-status-action-stop').click()
    await expect.poll(() => mock.posts.length).toBeGreaterThan(1)
    expect(mock.posts).toEqual([
      ['/api/comfyui/pin', { pinned: false }],
      ['/api/comfyui/switchover', { mode: 'inference' }],
    ])
  })

  test('Stop image mode while a render runs asks first, and forces only after a yes', async ({ page }) => {
    // Pinned, with a prompt in ComfyUI's queue that the arbiter does not see (the editor's).
    const mock = makeImageGenMock({
      status: imageStatus({
        mode: 'generation',
        queue: { running: 1, pending: 0 },
        arbiter: arbiterState({ mode: 'img', phase: 'idle', pinned: true }),
      }),
      arbiter: arbiterEnvelope({ mode: 'img', phase: 'idle', pinned: true }, { mode: 'generation' }),
    })
    await routeImageGen(page, mock)
    await gotoImageGen(page, 'engine')

    const pane = await openGpuTab(page)
    await pane.getByTestId('gpu-status-action-stop').click()
    await page.getByRole('button', { name: 'Stop and cancel render' }).waitFor()
    expect(mock.posts).toEqual([])
    await page.getByRole('button', { name: 'Stop and cancel render' }).click()
    await expect.poll(() => mock.posts.length).toBeGreaterThan(1)
    expect(mock.posts).toEqual([
      ['/api/comfyui/pin', { pinned: false }],
      ['/api/comfyui/switchover', { mode: 'inference', force: true }],
    ])
  })

  test('a stopped engine says Generate still works, and offers no Stop', async ({ page }) => {
    await routeImageGen(
      page,
      makeImageGenMock({
        status: imageStatus({
          engine: 'stopped',
          reachable: false,
          container: { name: 'hal0-slot-img', state: 'absent' },
        }),
      }),
    )
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('engine-pill')).toHaveAttribute('data-state', 'stopped')
    await expect(page.getByTestId('engine-stopped')).toContainText('ComfyUI is stopped')
    await expect(page.getByTestId('engine-stopped')).toContainText('Generate still works')
    await expect(page.getByTestId('engine-stop')).toHaveCount(0)
  })

  test("a container that is up but not answering says so and shows the last log lines", async ({ page }) => {
    await routeImageGen(
      page,
      makeImageGenMock({
        status: imageStatus({ engine: 'starting', reachable: false }),
        logs: ['loading checkpoint', 'importing custom nodes', 'bind failed'],
      }),
    )
    await gotoImageGen(page, 'engine')

    const notice = page.getByTestId('engine-unreachable')
    await expect(notice).toContainText("ComfyUI isn't answering")
    await expect(page.getByTestId('engine-unreachable-log')).toContainText('bind failed')
  })
})

test.describe('Image Gen live wiring: editor card', () => {
  test('the workflow chips are the installed models plus the operator’s own files', async ({ page }) => {
    await routeImageGen(
      page,
      makeImageGenMock({
        models: catalog([
          catalogRow('sdxl-lightning'),
          catalogRow('wan22-t2v', { task: 'txt2video', api_renderable: false }),
          catalogRow('qwen-image', { state: 'none' }),
        ]),
        // `GET /workflows` names are the file stems. `sdxl-lightning` belongs to an installed
        // model, `qwen-image` to one that is not installed (a half install left its file), and
        // `my-upscale` is the operator's own.
        workflows: [
          { name: 'sdxl-lightning', source: 'primary' },
          { name: 'qwen-image', source: 'primary' },
          { name: 'my-upscale', source: 'user' },
        ],
      }),
    )
    await gotoImageGen(page, 'engine')

    const chips = page.getByTestId('editor-chip')
    await expect(chips).toHaveCount(3)
    const order = await chips.evaluateAll((els: Element[]) => els.map((e) => e.getAttribute('data-workflow')))
    expect(order).toEqual(['sdxl-lightning', 'wan22-t2v', 'my-upscale'])
    await expect(page.locator('[data-workflow="qwen-image"]')).toHaveCount(0)
    await expect(page.locator('[data-workflow="my-upscale"]')).toHaveAttribute('data-custom', 'true')
  })

  test('on the box, a chip and the button are links that open the editor in a new tab', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'engine')

    const chip = page.getByTestId('editor-chip').first()
    expect((await chip.evaluate((el: Element) => el.tagName)).toLowerCase()).toBe('a')
    await expect(chip).toHaveAttribute('target', '_blank')
    await expect(chip).toHaveAttribute('href', 'http://localhost:8188')
    await expect(page.getByTestId('editor-open')).toHaveAttribute('href', 'http://localhost:8188')
    await expect(page.getByTestId('editor-offbox')).toHaveCount(0)
  })

  test('a workflow link does not pretend to open a workflow', async ({ page }) => {
    // ComfyUI cannot open a workflow from a link (comfyanonymous/ComfyUI#9858): every chip opens the
    // editor, and the card says where the workflow is.
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('editor-workflow-hint')).toContainText('Workflows')
    const href = await page.getByTestId('editor-chip').first().getAttribute('href')
    expect(href).not.toContain('workflow=')
  })
})

test.describe('Image Gen live wiring: no placeholder values', () => {
  test('a status body with most fields missing still renders the tab', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock({ status: { engine: 'running', reachable: true } }))
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('engine-card')).toBeVisible()
    await expect(page.getByTestId('engine-pill')).toHaveAttribute('data-state', 'running')
  })

  test('a failing status poll shows a loading line, not a made-up engine', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock({ status: { __status: 500 } }))
    await gotoImageGen(page, 'engine')

    await expect(page.getByTestId('engine-loading')).toBeVisible()
    await expect(page.getByTestId('engine-pill')).toHaveCount(0)
    await expect(page.getByTestId('engine-row-state')).toHaveCount(0)
    // Nor does it claim what is installed, or what runs.
    await expect(page.getByTestId('image-gen-pane')).not.toContainText('checkpoints')
  })

  test('there are no memory gauges, even if an older hal0 still sends a memory block', async ({ page }) => {
    await routeImageGen(
      page,
      makeImageGenMock({
        status: imageStatus({
          memory: { gtt_used_gb: 54, gtt_ceil_gb: 80, ram_used_gb: 61, ram_ceil_gb: 96, pressure: false },
        }),
      }),
    )
    await gotoImageGen(page, 'engine')

    const tab = page.getByTestId('engine-tab')
    await expect(tab).toBeVisible()
    await expect(tab).not.toContainText(/gtt|system ram/i)
    await expect(tab.locator('.gauge, [role="meter"]')).toHaveCount(0)
  })
})

test.describe('Services page ComfyUI row', () => {
  // The old Services queue drawer fetched ComfyUI's /queue and /history from the browser at :8188,
  // which an off-box page cannot reach. The row keeps its status and its link, and nothing else.
  test('has no queue drawer and never calls ComfyUI from the page', async ({ page }) => {
    const direct: string[] = []
    page.on('request', (req) => {
      if (/:8188\//.test(req.url())) direct.push(req.url())
    })
    await routeImageGen(page, makeImageGenMock())
    await page.route('**/api/services', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          services: [
            {
              id: 'comfyui',
              name: 'ComfyUI',
              description: 'Image generation engine (img slot container, loopback :8188).',
              managed: true,
              unit: 'hal0-slot@img.service',
              unit_state: { active_state: 'active', sub_state: 'running', unit_file_state: 'enabled', since: null },
              up: true,
              detail: 'running — 0 job(s) active',
              stat: null,
              url: 'http://localhost:8188',
              mdns_url: null,
              loopback_port: null,
              actions: ['restart'],
              mdns_capable: false,
              hints: [],
            },
          ],
          mdns: { available: false, hostname: 'hal0.local', base_advertised: false, advertised: [] },
        }),
      }),
    )
    await page.goto('/#services')

    const card = page.getByTestId('svcp-card-comfyui')
    await expect(card).toBeVisible()
    // On the box (the e2e page is served from localhost) the link works.
    await expect(page.getByTestId('svcp-open-comfyui')).toBeEnabled()
    await expect(page.getByTestId('svcp-queue-comfyui')).toHaveCount(0)
    await expect(card).not.toContainText('Queue')
    // The drawer asked ComfyUI for its queue once the card was up: let the network go idle, so a
    // request that followed the render has been made, then read the list.
    await page.waitForLoadState('networkidle')
    expect(direct).toEqual([])
  })
})
