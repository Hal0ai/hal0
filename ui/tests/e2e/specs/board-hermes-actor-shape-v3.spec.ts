/**
 * board-hermes-actor-shape-v3 — regression for the React #31 black-screen.
 *
 * Bug of record: /api/board/{profiles,assignees} are proxied straight to
 * Hermes, which returns actors shaped {name, on_disk, counts} — NOT the
 * {id,label} the UI assumed. board-view's `p.id ?? p` / `p.label ?? p.id ?? p`
 * fallbacks bottomed out at the raw object, so opening the Assignee filter
 * (or the bulk reassign dropdown) rendered an object as a React child →
 * error #31 → the whole board black-screened.
 *
 * The default fixtures (BOARD_PROFILES/BOARD_ASSIGNEES) use the idealised
 * {id,label} shape, which is exactly why the existing board specs never
 * caught this. Here we feed the REAL Hermes shape and assert the board both
 * mounts and renders the actor's name as a label (never "[object Object]",
 * never the view-level error boundary).
 */

import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

// Real-world Hermes actor shape: `name`, plus `on_disk`/`counts` — no id/label.
const HERMES_ACTORS = [
  { name: 'scout', on_disk: true, counts: { ready: 1 } },
  { name: 'builder', on_disk: false, counts: { todo: 2 } },
]

test.beforeEach(async ({ page }) => {
  // Override the board actor endpoints with the malformed-for-the-UI shape.
  await page.route('**/api/board/profiles', (route) => json(route, HERMES_ACTORS))
  await page.route('**/api/board/assignees', (route) => json(route, HERMES_ACTORS))
})

test.describe('BoardView — Hermes actor shape', () => {
  test('mounts and renders actor name (no #31 black-screen) with {name,on_disk,counts}', async ({
    page,
  }) => {
    await page.goto('/#board')
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })

    // The view-level error boundary must NOT have engaged.
    await expect(page.getByText('This view hit an error')).toHaveCount(0)
    // A raw object must never have leaked into the DOM.
    await expect(page.locator('body')).not.toContainText('[object Object]')

    // Open the Assignee filter; its options come from the (normalised) profiles.
    await page.locator('.flt', { hasText: 'Assignee' }).locator('button.sel-btn').click()
    // The actor's `name` must surface as a readable label.
    await expect(page.locator('.sel-menu')).toContainText('scout')

    // Board still alive after interacting with the actor-backed dropdown.
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible()
  })
})
