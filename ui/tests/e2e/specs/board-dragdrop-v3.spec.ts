/**
 * board-dragdrop-v3 — Drag-and-drop contract for the Operator Board.
 *
 * Covers:
 *   - Drag card between lanes → assert PATCH /api/board/tasks/<id> body {status}
 *   - Drop card to delete zone → assert DELETE /api/board/tasks/<id>
 *
 * HTML5 DnD note: Playwright dragTo() fires dragstart+dragover+drop too fast
 * for React's synthetic event to commit dragId state. We use the split approach:
 *   1. Dispatch dragstart on card via page.evaluate with a DataTransfer
 *   2. Wait ~100ms for React state to settle
 *   3. Dispatch dragover+drop on target lane/zone with same dataTransfer
 * Then assert the intercepted network request body.
 */

import { test, expect, json } from '../fixtures/apiMock'
import { BOARD_TASKS } from '../fixtures/mock-data'

const FIVE_S = 5_500

async function gotoBoardAndWait(page: any) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

// Perform HTML5 drag from source element to target element via page.evaluate.
// Returns true if drag completed.
async function doDrag(page: any, sourceTestId: string, targetTestId: string) {
  // Step 1: dispatch dragstart on source
  await page.evaluate((src: string) => {
    const el = document.querySelector(`[data-testid="${src}"]`) as HTMLElement | null
    if (!el) throw new Error(`drag source not found: ${src}`)
    const dt = new DataTransfer()
    dt.effectAllowed = 'move'
    const ev = new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt })
    ;(window as any).__e2eDt = dt
    el.dispatchEvent(ev)
  }, sourceTestId)

  // Step 2: wait for React dragId state to settle
  await page.waitForTimeout(120)

  // Step 3: dispatch dragover + drop on target
  await page.evaluate((tgt: string) => {
    const el = document.querySelector(`[data-testid="${tgt}"]`) as HTMLElement | null
    if (!el) throw new Error(`drag target not found: ${tgt}`)
    const dt = (window as any).__e2eDt || new DataTransfer()
    el.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }))
    el.dispatchEvent(new DragEvent('drop',     { bubbles: true, cancelable: true, dataTransfer: dt }))
    // Clean up dragend on document
    document.dispatchEvent(new DragEvent('dragend', { bubbles: true }))
  }, targetTestId)
}

test.describe('BoardView — drag-and-drop', () => {

  test('drag card to another lane → PATCH body {status: "<lane>"}', async ({ page }) => {
    // Pick a todo task to drag to "ready"
    const todoTask = BOARD_TASKS.find(t => t.status === 'todo')!
    let patchBody: any = null
    let patchUrl = ''

    // Intercept PATCH for this specific task
    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchUrl = route.request().url()
        patchBody = route.request().postDataJSON()
        await json(route, { ...todoTask, status: 'ready' })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)

    const cardTestId = `board-task-${todoTask.id}`
    await expect(page.locator(`[data-testid="${cardTestId}"]`)).toBeVisible()

    await doDrag(page, cardTestId, 'board-lane-ready')

    // Wait for PATCH to fire
    await page.waitForTimeout(300)
    expect(patchUrl).toContain(todoTask.id)
    expect(patchBody).toMatchObject({ status: 'ready' })
  })

  test('drag card from ready to running lane → PATCH body {status: "running"}', async ({ page }) => {
    const readyTask = BOARD_TASKS.find(t => t.status === 'ready')!
    let patchBody: any = null

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBody = route.request().postDataJSON()
        await json(route, { ...readyTask, status: 'running' })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)

    const cardTestId = `board-task-${readyTask.id}`
    await expect(page.locator(`[data-testid="${cardTestId}"]`)).toBeVisible()

    await doDrag(page, cardTestId, 'board-lane-running')

    await page.waitForTimeout(300)
    expect(patchBody).toMatchObject({ status: 'running' })
  })

  test('drag card to done lane → PATCH body {status: "done"}', async ({ page }) => {
    const runningTask = BOARD_TASKS.find(t => t.status === 'running')!
    let patchBody: any = null

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBody = route.request().postDataJSON()
        await json(route, { ...runningTask, status: 'done' })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)

    const cardTestId = `board-task-${runningTask.id}`
    await expect(page.locator(`[data-testid="${cardTestId}"]`)).toBeVisible()

    await doDrag(page, cardTestId, 'board-lane-done')

    await page.waitForTimeout(300)
    expect(patchBody).toMatchObject({ status: 'done' })
  })

  test('drop card on delete zone → DELETE /api/board/tasks/<id>', async ({ page }) => {
    const anyTask = BOARD_TASKS.find(t => t.status === 'ready')!
    let deleteUrl = ''

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'DELETE') {
        deleteUrl = route.request().url()
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)

    const cardTestId = `board-task-${anyTask.id}`
    await expect(page.locator(`[data-testid="${cardTestId}"]`)).toBeVisible()

    await doDrag(page, cardTestId, 'board-drop-delete')

    await page.waitForTimeout(300)
    expect(deleteUrl).toContain(anyTask.id)
  })

  test('drag blocked task to triage lane → PATCH body {status: "triage"}', async ({ page }) => {
    const blockedTask = BOARD_TASKS.find(t => t.status === 'blocked')!
    let patchBody: any = null

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBody = route.request().postDataJSON()
        await json(route, { ...blockedTask, status: 'triage' })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)

    const cardTestId = `board-task-${blockedTask.id}`
    await expect(page.locator(`[data-testid="${cardTestId}"]`)).toBeVisible()

    await doDrag(page, cardTestId, 'board-lane-triage')

    await page.waitForTimeout(300)
    expect(patchBody).toMatchObject({ status: 'triage' })
  })

  test('dragging a card over the board background arms the danger veil', async ({ page }) => {
    const anyTask = BOARD_TASKS.find(t => t.status === 'ready')!
    await gotoBoardAndWait(page)
    const cardTestId = `board-task-${anyTask.id}`
    await expect(page.locator(`[data-testid="${cardTestId}"]`)).toBeVisible()

    // Veil is hidden until a drag is in progress over the background.
    await expect(page.locator('[data-testid="board-danger-veil"]')).toHaveCount(0)

    // dragstart on the card, then dragover the lanes-scroll background.
    await page.evaluate((src: string) => {
      const el = document.querySelector(`[data-testid="${src}"]`) as HTMLElement
      const dt = new DataTransfer()
      dt.effectAllowed = 'move'
      ;(window as any).__e2eDt = dt
      el.dispatchEvent(new DragEvent('dragstart', { bubbles: true, cancelable: true, dataTransfer: dt }))
    }, cardTestId)
    await page.waitForTimeout(120)
    await page.evaluate(() => {
      const el = document.querySelector('[data-testid="board-drop-delete"]') as HTMLElement
      const dt = (window as any).__e2eDt || new DataTransfer()
      el.dispatchEvent(new DragEvent('dragover', { bubbles: true, cancelable: true, dataTransfer: dt }))
    })

    await expect(page.locator('[data-testid="board-danger-veil"]')).toBeVisible({ timeout: FIVE_S })
    await expect(page.locator('[data-testid="board-danger-veil"]')).toContainText('Release to delete')
  })

})
