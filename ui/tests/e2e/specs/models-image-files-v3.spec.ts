/**
 * models-image-files-v3 — Models → Image / ComfyUI → Files is the weight-file inventory
 * (GET /api/comfyui/files), not a slice of /api/models: ComfyUI image models are
 * not registry rows. The tab opens on its Models view (the image catalog, covered by
 * models-image-tab-v3); the Files switch shows what is on disk, including files no
 * catalog model owns.
 *
 * The page asks for the image catalog on every visit, only to put a count on the
 * Image tab, so a catalog that fails or comes back malformed must not take the
 * Inference tab (and its Add model / Edit options controls) down with it. The failure
 * that prompted this guard: the default `/api/` catch-all answered `{}`, the count
 * read a `.length` off it, and the whole Models page blanked with
 * "Cannot read properties of undefined (reading 'length')".
 */
import { test, expect, json } from '../fixtures/apiMock'

const FILES = {
  root: '/models/comfyui',
  files: [
    { kind: 'checkpoint', subdir: 'checkpoints', relpath: 'flux-dev.safetensors', size_bytes: 23_800_000_000, models: ['flux-dev'], symlink: false },
    { kind: 'checkpoint', subdir: 'checkpoints', relpath: 'mine/sdxl-custom.safetensors', size_bytes: 6_900_000_000, models: [], symlink: false, arch: 'sdxl', api_id: 'ckpt/mine/sdxl-custom' },
    { kind: 'lora', subdir: 'loras', relpath: 'style.safetensors', size_bytes: 150_000_000, models: [], symlink: false },
  ],
}

const CATALOG = {
  models: [{ id: 'flux-dev', label: 'FLUX.1 dev', task: 'txt2img', state: 'complete', api_renderable: true, license: 'FLUX.1-dev NC', approx_gb: 23.8, missing_gb: 0, missing: [], est_seconds: 30, sizes: ['1024x1024'], notes: '', workflow: 'flux-dev.json', job: null, fit: null, recommended: false }],
  engine: { available: true, reason: null },
}

const imageTabCount = (page: import('@playwright/test').Page) =>
  page.locator('.slot-tab[role="tab"]:has-text("Image / ComfyUI") .slot-tab-ct')

const openFiles = async (page: import('@playwright/test').Page) => {
  await page.getByTestId('img-view-files').click()
  await expect(page.getByTestId('imgf-view')).toBeVisible()
}

test.describe('Models → Image / ComfyUI file inventory', () => {
  test('lists the files on disk by kind and names the catalog model that loads each', async ({ page }) => {
    await page.route('**/api/comfyui/files', (route) => json(route, FILES))
    await page.route('**/api/comfyui/models', (route) => json(route, CATALOG))
    await page.goto('/#models')
    // The tab counts the catalog's models, like every other tab counts its rows.
    await expect(imageTabCount(page)).toHaveText('1')

    await page.locator('.slot-tab[role="tab"]:has-text("Image / ComfyUI")').click()
    await expect(page).toHaveURL(/#models\/image/)
    await openFiles(page)
    await expect(page.locator('.mdl-list-h .right')).toContainText('3 files')
    await expect(page.getByTestId('imgf-group-checkpoint').locator('.mdl-row')).toHaveCount(2)
    await expect(page.getByTestId('imgf-group-lora').locator('.mdl-row')).toHaveCount(1)
    await expect(page.getByTestId('imgf-row-checkpoints/flux-dev.safetensors')).toContainText('Used by FLUX.1 dev')
    await expect(page.getByTestId('imgf-row-checkpoints/mine/sdxl-custom.safetensors')).toContainText('Unmanaged')

    // The detail opens on the first row; a catalog-owned file is removed by removing its model.
    await expect(page.getByTestId('imgf-owned-note')).toContainText('hal0 comfyui models remove flux-dev')
    // A file nobody owns can be deleted from here.
    await page.getByTestId('imgf-row-checkpoints/mine/sdxl-custom.safetensors').click()
    await expect(page.getByTestId('imgf-delete')).toBeVisible()

    // The shared toolbar narrows the inventory to what nobody owns.
    await page.getByTestId('mdl-filter-unmanaged').click()
    await expect(page.locator('.mdl-row')).toHaveCount(2)
    await expect(page.getByTestId('imgf-row-checkpoints/flux-dev.safetensors')).toHaveCount(0)
  })

  test('with nothing on disk the Files view says so and how to install a model', async ({ page }) => {
    // The default fixture: an engine with nothing installed.
    await page.goto('/#models/image')
    await expect(imageTabCount(page)).toHaveText('0')
    await openFiles(page)
    await expect(page.getByTestId('imgf-empty-hint')).toContainText('hal0 comfyui models install')
  })

  for (const [label, fulfil] of [
    ['an empty object', (route: import('@playwright/test').Route) => json(route, {})],
    ['a server error', (route: import('@playwright/test').Route) => json(route, { detail: 'boom' }, 500)],
  ] as const) {
    test(`an image catalog that is ${label} leaves the Inference tab working and counts 0 on the Image tab`, async ({ page }) => {
      await page.route('**/api/comfyui/models', fulfil)
      await page.goto('/#models')
      await expect(page.getByTestId('mdl-add')).toBeVisible()
      await expect(page.locator('.models-layout')).toBeVisible()
      await expect(imageTabCount(page)).toHaveText('0')
    })
  }
})
