/**
 * board-assignee-display-v3 — task cards must show their assignee.
 *
 * Bug of record: normaliseTask() emits the task field as `assignee`
 * (matching the BoardTask type and the task drawer), but kcard.jsx,
 * lane.jsx's by-profile grouping, and board-view's "Assignee" filter all
 * read the nonexistent `task.profile`. Result: every card rendered
 * "unassigned", every task grouped under one "unassigned" sublane, and the
 * Assignee filter matched nothing — even though the data carried a real
 * assignee. The board-view object-render crash fix (PR #905) is unrelated;
 * this is the field-name mismatch noted there as a follow-up.
 */

import { test, expect } from '../fixtures/apiMock'
import { BOARD_TASKS } from '../fixtures/mock-data'

const FIVE_S = 5_500

test.describe('BoardView — assignee display', () => {
  test('task card shows its assignee (@admin-agent), not "unassigned"', async ({ page }) => {
    await page.goto('/#board')
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })

    // BOARD_TASKS[0] carries assignee: 'admin-agent'.
    const task = BOARD_TASKS[0]
    expect(task.assignee).toBe('admin-agent')

    const card = page.locator(`[data-testid="board-task-${task.id}"]`)
    await expect(card).toBeVisible()
    const chip = card.locator('.kc-assignee')
    await expect(chip).toContainText('admin-agent')
    await expect(chip).not.toHaveClass(/unassigned/)
  })

  test('by-profile grouping uses the real assignee as the sublane header', async ({ page }) => {
    await page.goto('/#board')
    await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
    // byProfile defaults on; with real assignees the headers carry agent
    // names — not a single "unassigned" bucket for everything.
    await expect(page.locator('.sublane-h', { hasText: 'admin-agent' }).first()).toBeVisible({
      timeout: FIVE_S,
    })
  })
})
