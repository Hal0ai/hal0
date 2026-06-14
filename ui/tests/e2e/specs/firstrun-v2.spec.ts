/**
 * firstrun-v2 — quick-path install + services step (design D1/D3/D5).
 *
 * Mirrors firstrun-v3's component-mount harness: components are exposed on
 * `window` via firstrun.jsx Object.assign and mounted directly inside the
 * Hal0QueryClientProvider, so each test stays focused on one surface.
 *
 *   1. FirstRunQuick — Install button calls POST /api/install/apply with the
 *      selected tier (not a per-model pick-default).
 *   2. FirstRunServices — a down+repairable unit shows Retry; clicking it
 *      POSTs the repair endpoint.
 */
import { test, expect, json } from '../fixtures/apiMock'

const INSTALL_STATE = {
  first_run: true,
  has_models: false,
  has_default_slot: false,
  openwebui_running: false,
}

const MODEL_STORE = {
  effective: '/var/lib/hal0/models',
  suggestions: [{ path: '/var/lib/hal0/models', exists: true, files_count: 0, free_bytes: 1, size_bytes: 0 }],
  current_state: { exists: true, files_count: 0, free_bytes: 1, size_bytes: 0 },
}

const HARDWARE = { name: 'test', ram: { total: 128 }, gpu: 'Radeon', npu: { present: true, name: 'XDNA2' } }

async function mountQuick(page: any) {
  await page.evaluate(() => {
    const root = document.getElementById('root') || document.body
    root.innerHTML = '<div id="fr-test"></div>'
    const el = document.getElementById('fr-test')!
    const Comp = (window as any).FirstRunQuick
    if (!Comp) throw new Error('FirstRunQuick not on window — check Object.assign export')
    const QCP = (window as any).Hal0QueryClientProvider
    const qc = (window as any).Hal0QueryClient
    const R = (window as any).React
    ;(window as any).__frInstalled = null
    ;(window as any).ReactDOM.createRoot(el).render(
      R.createElement(QCP, { client: qc },
        R.createElement(Comp, {
          layout: 'grid',
          onSkip: () => {},
          onInstalled: (ids: string[], tier: string) => { (window as any).__frInstalled = { ids, tier } },
        }),
      ),
    )
  })
}

async function mountServices(page: any) {
  await page.evaluate(() => {
    const root = document.getElementById('root') || document.body
    root.innerHTML = '<div id="fr-test"></div>'
    const el = document.getElementById('fr-test')!
    const Comp = (window as any).FirstRunServices
    if (!Comp) throw new Error('FirstRunServices not on window — check Object.assign export')
    const QCP = (window as any).Hal0QueryClientProvider
    const qc = (window as any).Hal0QueryClient
    const R = (window as any).React
    ;(window as any).ReactDOM.createRoot(el).render(
      R.createElement(QCP, { client: qc },
        R.createElement(Comp, { onDone: () => {} }),
      ),
    )
  })
}

test.describe('FirstRun v2 — quick install', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/install/state', (r) => json(r, INSTALL_STATE))
    await page.route('**/api/install/curated-models', (r) => json(r, { models: [], custom_allowed: true }))
    await page.route('**/api/settings/models/store', (r) => json(r, MODEL_STORE))
    await page.route('**/api/hardware', (r) => json(r, HARDWARE))
    await page.route('**/api/profiles', (r) =>
      json(r, [
        { name: 'rocm-mtp', backend: 'rocm', device_class: 'gpu', intent: 'Dense chat + MTP' },
        { name: 'vulkan', backend: 'vulkan', device_class: 'gpu', intent: 'Vulkan std' },
      ]),
    )
    await page.route('**/api/install/complete', (r) => json(r, { first_run: false }))
  })

  test('Install button posts the selected tier to /api/install/apply', async ({ page }) => {
    let capturedBody: { tier?: string; storage_dir?: string } | null = null
    await page.route('**/api/install/apply', async (r) => {
      capturedBody = await r.request().postDataJSON()
      return json(r, {
        tier: capturedBody?.tier ?? 'hal0-Default',
        model_ids: ['qwen3.5-9b'],
        slots: [{ slot: 'chat', model_id: 'qwen3.5-9b', created: true, device: 'gpu-rocm', profile: 'rocm-mtp' }],
        next: '',
      })
    })

    await page.goto('/#firstrun', { waitUntil: 'domcontentloaded' })
    await mountQuick(page)

    const installBtn = page.locator('button', { hasText: /Install/ })
    await expect(installBtn).toBeVisible()
    await expect(installBtn).not.toBeDisabled()

    const [response] = await Promise.all([
      page.waitForResponse('**/api/install/apply', { timeout: 10_000 }),
      installBtn.click(),
    ])
    expect(response.status()).toBe(200)
    expect(typeof capturedBody?.tier).toBe('string')
    expect(capturedBody?.tier).not.toBe('')
    expect(capturedBody?.storage_dir).toBe('/var/lib/hal0/models')

    // onInstalled fired with the returned model_ids → progress would mount.
    const installed = await page.evaluate(() => (window as any).__frInstalled)
    expect(installed.ids).toContain('qwen3.5-9b')
  })

  test('Advanced drawer per-slot overrides are sent (model + profile + coherent device)', async ({ page }) => {
    let capturedBody: any = null
    await page.route('**/api/install/apply', async (r) => {
      capturedBody = await r.request().postDataJSON()
      return json(r, { tier: 'hal0-Default', model_ids: ['my-chat'], slots: [], next: '' })
    })

    await page.goto('/#firstrun', { waitUntil: 'domcontentloaded' })
    await mountQuick(page)

    // Open the Advanced drawer.
    await page.locator('summary', { hasText: /Advanced/ }).click()
    await expect(page.locator('[data-testid="fr-overrides"]')).toBeVisible()

    // Override the chat slot: a custom model id + the rocm-mtp profile.
    await page.getByLabel('chat model override').fill('my-chat')
    await page.getByLabel('chat profile override').selectOption('rocm-mtp')

    await Promise.all([
      page.waitForResponse('**/api/install/apply', { timeout: 10_000 }),
      page.locator('button', { hasText: /Install/ }).click(),
    ])

    expect(capturedBody?.overrides?.chat).toEqual({
      model_id: 'my-chat',
      profile: 'rocm-mtp',
      device: 'gpu-rocm', // derived from the rocm profile → #807-coherent
    })
    // Slots left on "auto" are omitted entirely.
    expect(capturedBody?.overrides?.embed).toBeUndefined()
  })
})

test.describe('FirstRun v2 — services step', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/install/state', (r) => json(r, INSTALL_STATE))
    await page.route('**/api/install/complete', (r) => json(r, { first_run: false }))
  })

  test('down+repairable unit shows Retry and posts the repair endpoint', async ({ page }) => {
    await page.route('**/api/install/services', (r) =>
      json(r, {
        services: [
          { unit: 'hal0-openwebui.service', label: 'OpenWebUI', active: false, repairable: true },
          { unit: 'hal0-agent@hermes.service', label: 'Hermes agent', active: true, repairable: true },
        ],
      }),
    )
    let repairHit = false
    await page.route('**/api/install/services/**/repair', (r) => {
      repairHit = true
      return json(r, { unit: 'hal0-openwebui.service', active: true })
    })

    await page.goto('/#firstrun', { waitUntil: 'domcontentloaded' })
    await mountServices(page)

    // ComfyUI card always present.
    await expect(page.locator('.fr-confirm-row', { hasText: 'ComfyUI' })).toBeVisible()
    // The down OpenWebUI row exposes a Retry button; Hermes (active) does not.
    const retry = page.locator('.fr-confirm-row', { hasText: 'OpenWebUI' }).locator('button', { hasText: 'Retry' })
    await expect(retry).toBeVisible({ timeout: 5_000 })

    await Promise.all([
      page.waitForResponse('**/api/install/services/**/repair', { timeout: 10_000 }),
      retry.click(),
    ])
    expect(repairHit).toBe(true)
  })
})
