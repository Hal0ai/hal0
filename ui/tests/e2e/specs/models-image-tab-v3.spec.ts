/**
 * models-image-tab-v3 — Models → Image / ComfyUI on the shared catalog shell: the image
 * catalog as the tab's Models view (sections, status, filters, the server's fit verdict),
 * install and remove from the detail card, the header's Check updates / Install image model,
 * and an install showing in the shared Downloads pane.
 */
import { test, expect, json } from '../fixtures/apiMock'

const fit = (verdict: string, reason: string | null) => ({
  verdict,
  reason,
  need: { gpu_gb: 14, ram_gb: 8, gpu_estimated: true, ram_estimated: true },
  render_free_gb: 38,
  ram_free_gb: 40,
})
const model = (id: string, label: string, extra: Record<string, unknown> = {}) => ({
  id,
  label,
  task: 'txt2img',
  state: 'none',
  api_renderable: true,
  recommended: false,
  license: 'Apache-2.0',
  est_seconds: 9,
  sizes: ['1024x1024'],
  notes: '',
  defaults: {},
  approx_gb: 17.2,
  missing_gb: 17.2,
  missing: [`unet/${id}.safetensors`],
  workflow: `${id}.json`,
  job: null,
  fit: fit('fits', null),
  ...extra,
})
const CATALOG = {
  engine: { available: true, reason: null },
  store: { free_gb: 212.5, path: '/models/comfyui' },
  recommendation: null,
  models: [
    model('flux-schnell', 'FLUX.1 schnell', { state: 'complete', missing_gb: 0, missing: [], recommended: true }),
    model('qwen-edit', 'Qwen Image Edit', { task: 'img2img', approx_gb: 21.4, missing_gb: 21.4 }),
    model('wan-t2v', 'Wan 2.2 T2V', {
      task: 'txt2video',
      api_renderable: false,
      approx_gb: 32,
      missing_gb: 32,
      fit: fit('wont_fit', 'Needs 52 GB of GPU memory; 38 GB can be freed.'),
    }),
  ],
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/comfyui/models', (route) => json(route, CATALOG))
})

test.describe('Models → Image / ComfyUI', () => {
  test('opens on the catalog, in the same list and detail layout as the other tabs', async ({ page }) => {
    await page.goto('/#models/image')
    const view = page.getByTestId('img-catalog-view')
    await expect(view).toHaveClass(/models-layout/)
    await expect(view.locator('.mdl-toolbar .mdl-search')).toBeVisible()
    await expect(view.locator('.mdl-row')).toHaveCount(3)
    await expect(view.getByTestId('mdl-section-installed').getByTestId('img-state-flux-schnell')).toHaveText('installed')
    await expect(view.getByTestId('mdl-section-available').getByTestId('img-state-qwen-edit')).toHaveText('not installed')
    // The server's verdict, not one the page worked out.
    await expect(view.getByTestId('img-state-wan-t2v')).toHaveText("won't fit")
    await expect(view.locator('.mdl-list-h .right')).toContainText('3 in the catalog · 1 installed · 212.5 GB free')
    // The detail card opens on the first installed model, with the Downloads pane under it.
    await expect(view.getByTestId('img-detail').locator('.mdl-detail-h .nm')).toHaveText('FLUX.1 schnell')
    await expect(view.getByTestId('img-open-gen-flux-schnell')).toHaveAttribute('href', '#slots/image/generate')
    await expect(view.getByTestId('mdl-downloads')).toBeVisible()
  })

  test('the filters narrow the catalog by state, task and fit', async ({ page }) => {
    await page.goto('/#models/image')
    await page.getByTestId('mdl-filter-available').click()
    await expect(page.locator('.mdl-row')).toHaveCount(2)
    await page.getByTestId('mdl-filter-fits').click()
    await expect(page.locator('.mdl-row')).toHaveCount(1)
    await expect(page.locator('.mdl-row')).toContainText('Qwen Image Edit')
    await page.locator('.mdl-clear').click()
    await page.getByTestId('mdl-filter-txt2video').click()
    await expect(page.locator('.mdl-row')).toHaveCount(1)
    await expect(page.locator('.mdl-row')).toContainText('Wan 2.2 T2V')
  })

  test('the header carries Check updates and Install image model', async ({ page }) => {
    await page.goto('/#models/image')
    await expect(page.getByTestId('mdl-check-image')).toBeVisible()
    // From the Files view, the add action comes back to the catalog, filtered to what is not installed.
    await page.getByTestId('img-view-files').click()
    await expect(page.getByTestId('imgf-view')).toBeVisible()
    await page.getByTestId('mdl-add').click()
    await expect(page.getByTestId('img-catalog-view')).toBeVisible()
    await expect(page.getByTestId('mdl-filter-available')).toHaveAttribute('aria-pressed', 'true')
    await expect(page.locator('.mdl-row')).toHaveCount(2)
  })

  test('Install starts the install, which shows on the row and in Downloads', async ({ page }) => {
    let installed: string | null = null
    const job = { id: 'job-1', model_id: 'qwen-edit', state: 'running', bytes_downloaded: 5_000_000_000, bytes_total: 20_000_000_000, started_at: 1, finished_at: null, error: null, error_code: null, sha256: null, path: null, files: [] }
    await page.route('**/api/comfyui/jobs', (route) => json(route, { jobs: installed ? [job] : [] }))
    await page.route('**/api/comfyui/models/*/install', (route) => {
      installed = decodeURIComponent(new URL(route.request().url()).pathname.split('/').slice(-2)[0])
      return json(route, { job })
    })
    await page.goto('/#models/image')
    await page.getByTestId('img-model-qwen-edit').click()
    const action = page.getByTestId('img-action-qwen-edit')
    await expect(action).toHaveText('Install')
    await action.click()
    await expect.poll(() => installed).toBe('qwen-edit')
    await expect(page.getByTestId('img-state-qwen-edit')).toHaveText('downloading 25%')
    await expect(action).toHaveText('Cancel')
    await expect(page.getByTestId('mdl-dl-image-qwen-edit')).toContainText('25%')
  })

  test('Remove shows what it will delete before removing', async ({ page }) => {
    await page.route('**/api/comfyui/files', (route) =>
      json(route, {
        root: '/models/comfyui',
        files: [{ kind: 'unet', subdir: 'unet', relpath: 'flux-schnell.safetensors', size_bytes: 17_000_000_000, models: ['flux-schnell'], symlink: false }],
      }),
    )
    let removed = false
    await page.route('**/api/comfyui/models/flux-schnell', (route) => {
      if (route.request().method() !== 'DELETE') return route.fallback()
      removed = true
      return json(route, { removed: true })
    })
    await page.goto('/#models/image')
    const action = page.getByTestId('img-action-flux-schnell')
    await expect(action).toHaveText('Remove')
    await action.click()
    await expect(page.getByTestId('imp-remove-delete')).toContainText('unet/flux-schnell.safetensors')
    expect(removed).toBe(false)
    await page.getByRole('button', { name: 'Remove model' }).click()
    await expect.poll(() => removed).toBe(true)
  })

  test('a model that will not fit says why, and cannot be mistaken for installable', async ({ page }) => {
    await page.goto('/#models/image')
    await page.getByTestId('img-model-wan-t2v').click()
    await expect(page.getByTestId('img-detail-fit')).toContainText('Needs 52 GB of GPU memory')
    await expect(page.getByTestId('img-detail-missing')).toContainText('unet/wan-t2v.safetensors')
  })
})
