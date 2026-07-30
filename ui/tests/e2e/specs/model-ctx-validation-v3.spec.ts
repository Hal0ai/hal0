/**
 * model-ctx-validation-v3 — the model drawer's Context size field must reject
 * malformed input instead of silently corrupting or deleting the stored value
 * (#1378).
 *
 * PUT /api/models/{id} merges `defaults` WHOLESALE (registry/store.py
 * merge_update), so an absent `context_size` key is a deletion, not a no-op.
 * A lenient parseInt therefore had two data-loss modes, both ending in a green
 * "Updated" toast: "32k" → 32 (a 1000x context collapse) and "abc" → key
 * dropped (stored 32768 destroyed). Both must now block Save inline.
 */
import { test, expect } from '../fixtures/apiMock'

const MODEL_ID = 'qwen3.6-27b-mtp'
const SEEDED_CTX = 32768

async function seedStoredCtx(page: import('@playwright/test').Page) {
  await page.addInitScript(({ id, ctx }) => {
    window.addEventListener('DOMContentLoaded', () => {
      const target = (window as any).HAL0_DATA?.models?.find((row: any) => row.id === id)
      if (target) {
        target.defaults = { ...(target.defaults || {}), context_size: ctx }
      }
    })
  }, { id: MODEL_ID, ctx: SEEDED_CTX })
}

async function openDrawer(page: import('@playwright/test').Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').first().click()
  await expect(page.getByTestId('model-ctx-input')).toBeVisible()
}

/** Capture every PUT body so "no write fired" is provable, not inferred. */
async function capturePuts(page: import('@playwright/test').Page) {
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

test.describe('Model drawer — Context size validation (#1378)', () => {
  test('a numeric-prefixed value ("32k") is rejected inline and never truncates the stored size', async ({ page }) => {
    await seedStoredCtx(page)
    const puts = await capturePuts(page)
    await openDrawer(page)

    await expect(page.getByTestId('model-ctx-input')).toHaveValue(String(SEEDED_CTX))
    await page.getByTestId('model-ctx-input').fill('32k')

    await expect(page.getByTestId('model-ctx-error')).toBeVisible()
    await expect(page.getByTestId('model-save')).toBeDisabled()

    await page.getByTestId('model-save').click({ force: true })
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
    // Save was blocked, so the drawer stays open with the bad value visible.
    await expect(page.locator('.drawer.open')).toHaveCount(1)
  })

  test('a non-numeric value ("abc") is rejected inline and never drops the stored key', async ({ page }) => {
    await seedStoredCtx(page)
    const puts = await capturePuts(page)
    await openDrawer(page)

    await page.getByTestId('model-ctx-input').fill('abc')

    await expect(page.getByTestId('model-ctx-error')).toBeVisible()
    await expect(page.getByTestId('model-save')).toBeDisabled()

    await page.getByTestId('model-save').click({ force: true })
    await page.waitForTimeout(250)
    // The wholesale defaults merge means a PUT here would DELETE context_size.
    expect(puts).toEqual([])
    await expect(page.locator('.drawer.open')).toHaveCount(1)
  })

  test('a fractional value ("8.9") is rejected rather than floored', async ({ page }) => {
    await seedStoredCtx(page)
    const puts = await capturePuts(page)
    await openDrawer(page)

    await page.getByTestId('model-ctx-input').fill('8.9')
    await expect(page.getByTestId('model-ctx-error')).toBeVisible()
    await expect(page.getByTestId('model-save')).toBeDisabled()
    await page.getByTestId('model-save').click({ force: true })
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
  })

  test('a below-floor value ("64") is rejected, matching the slot drawer floor', async ({ page }) => {
    await seedStoredCtx(page)
    const puts = await capturePuts(page)
    await openDrawer(page)

    await page.getByTestId('model-ctx-input').fill('64')
    await expect(page.getByTestId('model-ctx-error')).toBeVisible()
    await expect(page.getByTestId('model-save')).toBeDisabled()
    await page.getByTestId('model-save').click({ force: true })
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
  })

  test('a clean integer saves, and correcting a rejected value clears the error', async ({ page }) => {
    await seedStoredCtx(page)
    const puts = await capturePuts(page)
    await openDrawer(page)

    await page.getByTestId('model-ctx-input').fill('32k')
    await expect(page.getByTestId('model-ctx-error')).toBeVisible()

    // Correcting the field must release the gate — the error is derived, not sticky.
    await page.getByTestId('model-ctx-input').fill('16384')
    await expect(page.getByTestId('model-ctx-error')).toHaveCount(0)
    await expect(page.getByTestId('model-save')).toBeEnabled()

    await page.getByTestId('model-save').click()
    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].defaults.context_size).toBe(16384)
    await expect(page.locator('.hal0-toast')).toContainText('Updated')
  })

  test('clearing the field still deletes the override (empty stays an explicit clear)', async ({ page }) => {
    await seedStoredCtx(page)
    const puts = await capturePuts(page)
    await openDrawer(page)

    await page.getByTestId('model-ctx-input').fill('')
    await expect(page.getByTestId('model-ctx-error')).toHaveCount(0)
    await expect(page.getByTestId('model-save')).toBeEnabled()

    await page.getByTestId('model-save').click()
    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].defaults).not.toHaveProperty('context_size')
  })
})
