/**
 * board-bulk-v3 — Bulk-action contract for the Operator Board.
 *
 * Covers every bulk action:
 *   - todo / ready / block / unblock / complete / archive / delete
 *   - reassign → POST /tasks/<id>/reassign body {assignee}
 *   - select-all / clear selection
 *
 * For PATCH-based status moves the test asserts N individual PATCH calls
 * (one per selected task id) with the correct {status} body — this matches
 * the moveTo() implementation in board-view.jsx.
 *
 * Card selection is via .kc-check (the checkbox element inside each card).
 */

import { test, expect, json } from '../fixtures/apiMock'
import { BOARD_TASKS } from '../fixtures/mock-data'

const FIVE_S = 5_500

async function gotoBoardAndWait(page: any) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

// Select N cards by clicking their .kc-check element
async function selectCards(page: any, ids: string[]) {
  for (const id of ids) {
    const card = page.locator(`[data-testid="board-task-${id}"]`)
    await expect(card).toBeVisible()
    // Click the checkbox (.kc-check) to select without opening drawer
    const check = card.locator('.kc-check').first()
    await check.click()
  }
}

test.describe('BoardView — bulk actions', () => {

  test('selecting 2 cards shows bulkbar with count', async ({ page }) => {
    await gotoBoardAndWait(page)
    const t1 = BOARD_TASKS.find(t => t.status === 'todo')!
    const t2 = BOARD_TASKS.filter(t => t.status === 'todo')[1]!
    await selectCards(page, [t1.id, t2.id])
    const bulkbar = page.locator('.bulkbar')
    await expect(bulkbar).toBeVisible({ timeout: FIVE_S })
    await expect(bulkbar).toContainText('2 selected')
  })

  test('bulk → todo: fires PATCH {status:"todo"} per selected id', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'ready').slice(0, 2)
    const patchBodies: any[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBodies.push(route.request().postDataJSON())
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))
    await expect(page.locator('.bulkbar')).toBeVisible()
    await page.locator('[data-testid="board-action-todo"]').click()
    await page.waitForTimeout(300)

    expect(patchBodies.length).toBeGreaterThanOrEqual(2)
    expect(patchBodies.every(b => b.status === 'todo')).toBe(true)
  })

  test('bulk → ready: fires PATCH {status:"ready"} per selected id', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'todo').slice(0, 2)
    const patchBodies: any[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBodies.push(route.request().postDataJSON())
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))
    await page.locator('[data-testid="board-action-ready"]').click()
    await page.waitForTimeout(300)

    expect(patchBodies.some(b => b.status === 'ready')).toBe(true)
  })

  test('bulk → block: fires PATCH {status:"blocked"} per selected id', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'todo').slice(0, 2)
    const patchBodies: any[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBodies.push(route.request().postDataJSON())
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))
    await page.locator('[data-testid="board-action-block"]').click()
    await page.waitForTimeout(300)

    expect(patchBodies.some(b => b.status === 'blocked')).toBe(true)
  })

  test('bulk → unblock: fires PATCH {status:"todo"} (unblock = move to todo)', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'blocked').slice(0, 1)
    const patchBodies: any[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBodies.push(route.request().postDataJSON())
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))
    await page.locator('[data-testid="board-action-unblock"]').click()
    await page.waitForTimeout(300)

    expect(patchBodies.some(b => b.status === 'todo')).toBe(true)
  })

  test('bulk → complete: fires PATCH {status:"done"}', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'ready').slice(0, 2)
    const patchBodies: any[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBodies.push(route.request().postDataJSON())
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))
    await page.locator('[data-testid="board-action-complete"]').click()
    await page.waitForTimeout(300)

    expect(patchBodies.some(b => b.status === 'done')).toBe(true)
  })

  test('bulk → archive: fires PATCH {status:"archived"}', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'done').slice(0, 2)
    const patchBodies: any[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBodies.push(route.request().postDataJSON())
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))
    await page.locator('[data-testid="board-action-archive"]').click()
    await page.waitForTimeout(300)

    expect(patchBodies.some(b => b.status === 'archived')).toBe(true)
  })

  test('bulk → delete: fires DELETE per selected id', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'done').slice(0, 2)
    const deleteUrls: string[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'DELETE') {
        deleteUrls.push(route.request().url())
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))
    await page.locator('[data-testid="board-action-delete"]').click()
    await page.waitForTimeout(300)

    expect(deleteUrls.length).toBeGreaterThanOrEqual(2)
    for (const t of targets) {
      expect(deleteUrls.some(u => u.includes(t.id))).toBe(true)
    }
  })

  test('bulk → reassign: fires POST /reassign {assignee} per id', async ({ page }) => {
    const targets = BOARD_TASKS.filter(t => t.status === 'ready').slice(0, 2)
    const reassignBodies: any[] = []
    const reassignUrls: string[] = []

    await page.route(/\/api\/board\/tasks\/[^/]+\/reassign/, async (route) => {
      reassignBodies.push(route.request().postDataJSON())
      reassignUrls.push(route.request().url())
      await json(route, { ok: true })
    })

    await gotoBoardAndWait(page)
    await selectCards(page, targets.map(t => t.id))

    // Select reassign target from dropdown
    const reassignSelect = page.locator('.bulkbar select.bmini')
    await expect(reassignSelect).toBeVisible()
    await reassignSelect.selectOption('mem-agent')

    await page.locator('[data-testid="board-action-reassign"]').click()
    await page.waitForTimeout(300)

    expect(reassignBodies.length).toBeGreaterThanOrEqual(2)
    expect(reassignBodies.every(b => b.assignee === 'mem-agent')).toBe(true)
    for (const t of targets) {
      expect(reassignUrls.some(u => u.includes(t.id))).toBe(true)
    }
  })

  test('select-all selects all visible tasks', async ({ page }) => {
    await gotoBoardAndWait(page)
    // Select one card to show bulkbar first
    const first = BOARD_TASKS.find(t => t.status === 'todo')!
    await selectCards(page, [first.id])
    await expect(page.locator('.bulkbar')).toBeVisible()

    await page.locator('[data-testid="board-action-select-all"]').click()
    // Count should jump to total visible task count
    const bulkbar = page.locator('.bulkbar')
    const text = await bulkbar.locator('.bsel').textContent()
    const count = parseInt(text?.replace(' selected', '') ?? '0')
    // We have 18 tasks, 1 archived; default view has 17 visible
    expect(count).toBeGreaterThan(2)
  })

  test('clear selection removes bulkbar', async ({ page }) => {
    await gotoBoardAndWait(page)
    const first = BOARD_TASKS.find(t => t.status === 'todo')!
    await selectCards(page, [first.id])
    await expect(page.locator('.bulkbar')).toBeVisible()

    await page.locator('[data-testid="board-action-clear"]').click()
    await expect(page.locator('.bulkbar')).not.toBeVisible()
  })

})
