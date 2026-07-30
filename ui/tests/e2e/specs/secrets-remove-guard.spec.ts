/**
 * #1450 — Settings ▸ Secrets is not a one-click shredder for api.env.
 *
 * Before this, `Remove` called `delSecret.mutate(name)` straight from
 * `onClick` with no dialog anywhere in the page, and the list rendered every
 * uncommented `KEY=` line in api.env — including `HAL0_ADMIN_KEY` (removing it
 * locks every new session out) and `HAL0_PORT` / `HAL0_UI_DIST` (removing them
 * break the service on next restart) — as ordinary removable rows.
 *
 * Two behaviours pinned here: reserved rows render locked with no mutating
 * control at all, and removing a real secret costs a typed confirmation.
 *
 * The list itself is served by the in-app forced-mock layer
 * (`src/api/mockFixtures.ts` → `buildSecrets`), not by `page.route` — the
 * client short-circuits GETs before they reach the network. Mutations do go
 * out, so the DELETE is observed on the wire.
 */
import { test, expect } from '../fixtures/apiMock'

async function openSecrets(page: any, onDelete?: (name: string) => void) {
  await page.route('**/api/secrets/*', async (r: any) => {
    if (r.request().method() === 'DELETE') {
      onDelete?.(decodeURIComponent(new URL(r.request().url()).pathname.split('/').pop()!))
    }
    return r.fulfill({ status: 204, body: '' })
  })
  await page.goto('/#settings', { waitUntil: 'domcontentloaded' })
  await page.locator('.settings-nav .nav-item', { hasText: 'Secrets' }).click()
  await expect(page.locator('.s-row', { hasText: 'HF_TOKEN' }).first()).toBeVisible()
}

test.describe('Settings — Secrets remove guard', () => {
  test('reserved HAL0_* rows are locked, not removable', async ({ page }) => {
    const deleted: string[] = []
    await openSecrets(page, (n) => deleted.push(n))

    // The row is still listed — the operator can see what the service carries.
    const row = page.locator('.s-row', { hasText: 'HAL0_ADMIN_KEY' })
    await expect(row).toBeVisible()
    await expect(page.getByTestId('secret-locked-HAL0_ADMIN_KEY')).toBeVisible()
    // ...but carries no Remove/Update button to click.
    await expect(row.locator('button', { hasText: /Remove|Update|Add/ })).toHaveCount(0)
    expect(deleted).toEqual([])
  })

  test('removing a real secret requires typing its name', async ({ page }) => {
    const deleted: string[] = []
    await openSecrets(page, (n) => deleted.push(n))

    await page.locator('.s-row', { hasText: 'HF_TOKEN' })
      .locator('button', { hasText: 'Remove' }).first().click()

    // The dialog is up and the confirm button is inert until the name is typed.
    const confirm = page.locator('button', { hasText: 'Remove secret' })
    await expect(confirm).toBeVisible()
    await expect(confirm).toBeDisabled()
    expect(deleted).toEqual([])

    await page.locator('input.mono[placeholder="HF_TOKEN"]').fill('HF_TOKEN')
    await expect(confirm).toBeEnabled()

    await Promise.all([
      page.waitForResponse(
        (r: any) => r.url().includes('/api/secrets/HF_TOKEN') && r.request().method() === 'DELETE',
      ),
      confirm.click(),
    ])
    expect(deleted).toEqual(['HF_TOKEN'])
  })

  test('a wrong name in the confirm box keeps the button dead', async ({ page }) => {
    const deleted: string[] = []
    await openSecrets(page, (n) => deleted.push(n))

    await page.locator('.s-row', { hasText: 'HF_TOKEN' })
      .locator('button', { hasText: 'Remove' }).first().click()
    await page.locator('input.mono[placeholder="HF_TOKEN"]').fill('HF_TOKE')

    await expect(page.locator('button', { hasText: 'Remove secret' })).toBeDisabled()
    expect(deleted).toEqual([])
  })

  test('cancelling the dialog deletes nothing', async ({ page }) => {
    const deleted: string[] = []
    await openSecrets(page, (n) => deleted.push(n))

    await page.locator('.s-row', { hasText: 'HF_TOKEN' })
      .locator('button', { hasText: 'Remove' }).first().click()
    await page.locator('button', { hasText: 'Cancel' }).first().click()

    await expect(page.locator('button', { hasText: 'Remove secret' })).toHaveCount(0)
    expect(deleted).toEqual([])
  })
})
