/**
 * model-drawer-validation-gates — the drawer's inline errors must actually gate
 * the PUT, and an emptied Display name must clear the stored name.
 *
 * #1380: toggling the `vision` capability with no mmproj sidecar renders a red
 *        inline error but used to let the save through, so the registry row
 *        advertised `vision` with no projector for the launch path to load.
 * #1381: emptying Display name dropped the `name` key from the PUT entirely —
 *        the old name survived while the drawer closed with a success toast.
 *        `Model.name` is `str` with `default=""`, and normalizeApiModel falls
 *        back to `model.id` for display, so an explicit empty is a valid write.
 */
import { test, expect } from '../fixtures/apiMock'

const MODEL_ID = 'qwen3.6-27b-mtp'

const BASE_MODEL = {
  name: 'Original name',
  tags: ['curated'],
  capabilities: ['chat'],
  backends: ['rocm'],
  mmproj: null,
  hf_repo: 'org/original-repo',
  hf_filename: 'original.gguf',
  defaults: {},
}

async function seedModel(
  page: import('@playwright/test').Page,
  overrides: Record<string, unknown> = {},
) {
  await page.addInitScript(({ id, model }) => {
    window.addEventListener('DOMContentLoaded', () => {
      const target = (window as any).HAL0_DATA?.models?.find((row: any) => row.id === id)
      if (target) {
        Object.assign(target, model)
        // Drop the legacy display aliases so normalizeApiModel derives the
        // display name from this production registry shape alone.
        delete target.longName
        delete target.repo
        delete target.labels
      }
    })
  }, { id: MODEL_ID, model: { ...BASE_MODEL, ...overrides } })
}

/** Capture PUTs to the model row; returns a getter for the last body seen. */
async function capturePut(page: import('@playwright/test').Page) {
  const puts: any[] = []
  await page.route(`**/api/models/${MODEL_ID}`, async (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON()
      puts.push(body)
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: MODEL_ID, ...body }),
      })
    }
    return route.fallback()
  })
  return puts
}

async function openDrawer(page: import('@playwright/test').Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').first().click()
  await expect(page.getByTestId('model-flags-input')).toBeVisible()
}

test.describe('Model drawer — inline errors gate the save (#1380, #1381)', () => {
  test('vision capability without an mmproj sidecar blocks the PUT until a path is supplied', async ({ page }) => {
    await seedModel(page)
    const puts = await capturePut(page)
    await openDrawer(page)

    await expect(page.getByTestId('model-mmproj-error')).toHaveCount(0)
    await page.getByTestId('cap-toggle-vision').click()

    // The invariant is violated: vision is on, mmproj is empty.
    const err = page.getByTestId('model-mmproj-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText('vision capability requires an mmproj sidecar path')

    // The error must gate Save the way flagsError does — not decorate it.
    const save = page.getByTestId('model-save')
    await expect(save).toBeDisabled()
    await save.click({ force: true })
    await page.waitForTimeout(300)
    expect(puts).toEqual([])
    await expect(page.locator('.drawer.open')).toHaveCount(1)

    // Supplying the projector clears the error and re-arms the save.
    await page.getByTestId('model-mmproj-input').fill('/models/mmproj-Q8.gguf')
    await expect(page.getByTestId('model-mmproj-error')).toHaveCount(0)
    await expect(save).toBeEnabled()
    await save.click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].capabilities).toEqual(['chat', 'vision'])
    expect(puts[0].mmproj).toBe('/models/mmproj-Q8.gguf')
  })

  test('an emptied Display name sends name:"" so the stored name is cleared', async ({ page }) => {
    await seedModel(page)
    const puts = await capturePut(page)
    await openDrawer(page)

    const nameInput = page.getByTestId('model-name-input')
    await expect(nameInput).toHaveValue('Original name')
    await nameInput.fill('')
    await page.getByTestId('model-save').click()

    await expect.poll(() => puts.length).toBe(1)
    // Not "absent" — an explicit empty string, matching the mmproj/hf_repo
    // convention the sibling text fields already follow.
    expect(puts[0]).toHaveProperty('name', '')

    await expect(page.locator('.drawer.open')).toHaveCount(0)
  })

  test('a stored empty name displays as the model id (the semantics the help text advertises)', async ({ page }) => {
    await seedModel(page, { name: '' })
    await openDrawer(page)

    // normalizeApiModel: longName = m.longName || m.name || m.id.
    await expect(page.locator('.drawer.open h2')).toHaveText(MODEL_ID)
    await expect(page.getByTestId('model-name-input')).toHaveValue('')
  })
})
