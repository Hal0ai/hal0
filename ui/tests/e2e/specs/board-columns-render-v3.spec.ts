/**
 * board-columns-render-v3 — does the board render tasks from the REAL Hermes
 * {columns:[...]} payload? Reproduces "added task lands in Ready but the column
 * shows empty". Uses the exact live wire shape (assignee:null, status:'ready',
 * the {columns,tenants,assignees,latest_event_id,now} envelope).
 */

import { test, expect, json } from '../fixtures/apiMock'

const FIVE_S = 5_500

// Mirrors GET /api/board/board from the live Hermes kanban (trimmed).
const LIVE_BOARD = {
  columns: [
    { name: 'triage', tasks: [] },
    { name: 'todo', tasks: [] },
    { name: 'scheduled', tasks: [] },
    {
      name: 'ready',
      tasks: [
        { id: 't_f775e1de', title: 'New task', status: 'ready', assignee: null, priority: 0 },
        { id: 't_982099b4', title: 'New task', status: 'ready', assignee: null, priority: 0 },
      ],
    },
    { name: 'running', tasks: [] },
    { name: 'blocked', tasks: [] },
    { name: 'review', tasks: [] },
    { name: 'done', tasks: [] },
  ],
  tenants: [],
  assignees: ['default', 'operator'],
  latest_event_id: 1,
  now: 1781868603,
}

test('board renders ready-column tasks from the {columns:[...]} payload', async ({ page }) => {
  await page.route('**/api/board/board*', (route) => json(route, LIVE_BOARD))

  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })

  // Both ready tasks must render as cards.
  await expect(page.locator('[data-testid="board-task-t_f775e1de"]')).toBeVisible({ timeout: FIVE_S })
  await expect(page.locator('[data-testid="board-task-t_982099b4"]')).toBeVisible()
  await expect(page.locator('[data-testid="board-task-t_f775e1de"]')).toContainText('New task')
})
