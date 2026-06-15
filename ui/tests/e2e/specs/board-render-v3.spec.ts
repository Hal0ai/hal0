/**
 * board-render-v3 — Operator Board render contract.
 *
 * Covers:
 *   - Entry via #board hash, ⌘B shortcut, ⌘K palette, sidebar nav, .tb-board
 *   - Populated board: 8 lanes, cards in correct lanes, card fields
 *   - Empty board: 8 lanes each with "no tasks" / empty sentinel
 *   - Show-archived toggle: board-lane-archived appears
 *   - By-profile toggle: sublane-h headers appear
 *   - Attention banner visible when blocked/review tasks exist
 */

import { test, expect, json } from '../fixtures/apiMock'
import { BOARD_TASKS, makeBoardLanesResponse } from '../fixtures/mock-data'

const FIVE_S = 5_500

// console/pageerror guard: board-view.jsx must mount with no uncaught React error.
// Fails the test if the board surface throws (e.g. an Icon object-as-component crash
// or a QueryResult misuse regresses).
test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(`console.error: ${m.text()}`)
  })
  ;(page as any).__boardErrors = errors
})

test.afterEach(async ({ page }) => {
  const errors: string[] = (page as any).__boardErrors || []
  expect(errors, `console/pageerror on board mount:\n${errors.join('\n')}`).toEqual([])
})

async function gotoBoardAndWait(page: any) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

test.describe('BoardView — render', () => {

  // ── Entry points ──────────────────────────────────────────────────────

  test('entry via #board hash renders board-view', async ({ page }) => {
    await gotoBoardAndWait(page)
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible()
  })

  test('entry via ⌘B (Ctrl+b) shows board-view', async ({ page }) => {
    await page.goto('/')
    // Wait for React to mount (topbar is the reliable mount marker)
    await expect(page.locator('.topbar')).toBeVisible({ timeout: FIVE_S })
    await page.keyboard.press('Control+b')
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('entry via ⌘K palette → Operator Board', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.topbar')).toBeVisible({ timeout: FIVE_S })
    // Click the command palette button in topbar
    await page.locator('.tb-cmdk').click()
    const palette = page.locator('.cp-shell')
    await expect(palette).toBeVisible({ timeout: FIVE_S })
    // Type to find board
    await page.keyboard.type('Operator Board')
    // Click the board result
    await page.locator('.cp-item').filter({ hasText: 'Operator Board' }).first().click()
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('entry via sidebar nav-board link', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    await page.locator('[data-testid="nav-board"]').click()
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('entry via .tb-board topbar button', async ({ page }) => {
    await page.goto('/')
    await page.waitForLoadState('domcontentloaded')
    await page.locator('[data-testid="tb-board"]').click()
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
  })

  // ── Populated board ───────────────────────────────────────────────────

  test('8 standard lanes render with correct ids', async ({ page }) => {
    await gotoBoardAndWait(page)
    for (const id of ['triage', 'todo', 'scheduled', 'ready', 'running', 'blocked', 'review', 'done']) {
      await expect(page.locator(`[data-testid="board-lane-${id}"]`)).toBeVisible()
    }
  })

  test('tasks appear in their correct lane', async ({ page }) => {
    await gotoBoardAndWait(page)
    // triage task
    const triageTask = BOARD_TASKS.find(t => t.status === 'triage')!
    await expect(
      page.locator(`[data-testid="board-lane-triage"] [data-testid="board-task-${triageTask.id}"]`)
    ).toBeVisible()
    // blocked task
    const blockedTask = BOARD_TASKS.find(t => t.status === 'blocked')!
    await expect(
      page.locator(`[data-testid="board-lane-blocked"] [data-testid="board-task-${blockedTask.id}"]`)
    ).toBeVisible()
    // done task
    const doneTask = BOARD_TASKS.find(t => t.status === 'done')!
    await expect(
      page.locator(`[data-testid="board-lane-done"] [data-testid="board-task-${doneTask.id}"]`)
    ).toBeVisible()
  })

  test('board-selector shows current board name', async ({ page }) => {
    await gotoBoardAndWait(page)
    await expect(page.locator('[data-testid="board-selector"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-selector"]')).toContainText('strix-halo-01 ops')
  })

  test('board-search input is present', async ({ page }) => {
    await gotoBoardAndWait(page)
    await expect(page.locator('[data-testid="board-search"]')).toBeVisible()
  })

  test('attention banner visible when blocked/review tasks exist', async ({ page }) => {
    await gotoBoardAndWait(page)
    // Mock data has blocked + review tasks → attn banner
    await expect(page.locator('[data-testid="board-attn"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-attn"]')).toContainText('need attention')
  })

  test('action buttons present: nudge, refresh, chat, new-board', async ({ page }) => {
    await gotoBoardAndWait(page)
    for (const id of ['board-action-nudge', 'board-action-refresh', 'board-action-chat', 'board-action-new-board']) {
      await expect(page.locator(`[data-testid="${id}"]`)).toBeVisible()
    }
  })

  // ── Empty board ───────────────────────────────────────────────────────

  test('empty board: 8 lanes each show "no tasks"', async ({ page }) => {
    // Override board endpoint to return empty lanes
    await page.route('**/api/board/board', (route) =>
      json(route, { lanes: { triage: [], todo: [], scheduled: [], ready: [], running: [], blocked: [], review: [], done: [] } })
    )
    await gotoBoardAndWait(page)
    // No cards
    const cards = page.locator('[data-testid^="board-task-"]')
    await expect(cards).toHaveCount(0)
    // All 8 lanes still render
    for (const id of ['triage', 'todo', 'scheduled', 'ready', 'running', 'blocked', 'review', 'done']) {
      await expect(page.locator(`[data-testid="board-lane-${id}"]`)).toBeVisible()
    }
    // At least one "no tasks" sentinel per lane
    const empties = page.locator('.lane-empty')
    await expect(empties).toHaveCount(8)
    await expect(empties.first()).toContainText('no tasks')
  })

  // ── Show-archived toggle ──────────────────────────────────────────────

  test('show-archived toggle is visible and clickable', async ({ page }) => {
    // The BOARD_LANES in board-view.jsx has 8 lanes; no archived lane is added
    // by the local toggle — it affects visibleIds (select-all includes archived)
    // and triggers a refetch with include_archived=true via the hook.
    await gotoBoardAndWait(page)
    const toggleLabel = page.locator('[data-testid="board-toggle-archived"]')
    await expect(toggleLabel).toBeVisible()
    // Toggle on
    await toggleLabel.click()
    // The check element gets the "on" class when active
    const check = toggleLabel.locator('..').locator('.kcheck').first()
    await expect(check).toHaveClass(/on/, { timeout: FIVE_S })
    // Toggle off again
    await toggleLabel.click()
    await expect(check).not.toHaveClass(/on/)
  })

  // ── By-profile toggle ─────────────────────────────────────────────────

  test('by-profile toggle: sublane-h headers visible when on', async ({ page }) => {
    await gotoBoardAndWait(page)
    // Default: byProfile = true, sublane-h headers should appear for lanes with tasks
    const sublaneHeaders = page.locator('.sublane-h')
    // There should be sublane headers (admin-agent, operator, mem-agent, unassigned…)
    await expect(sublaneHeaders.first()).toBeVisible()
    // Turn off by-profile
    const toggle = page.locator('[data-testid="board-toggle-byprofile"]')
    await toggle.click()
    // After turning off, sublane-h should be gone (cards flat in lane)
    await expect(sublaneHeaders).toHaveCount(0)
  })

  // ── Drop-to-delete zone ───────────────────────────────────────────────

  test('drop-to-delete zone is visible', async ({ page }) => {
    await gotoBoardAndWait(page)
    await expect(page.locator('[data-testid="board-drop-delete"]')).toBeVisible()
  })

})
