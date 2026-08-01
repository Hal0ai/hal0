/**
 * board-bulk-delete-guard-v3 — #1535: bulk delete cannot destroy N cards
 * (and their cascaded history) on one unacknowledged click.
 *
 * The Board's bulk toolbar had a `delete` button wired straight to
 * `delTasks([...sel])` → `DELETE /api/board/tasks/{id}` per id →
 * `store.delete_task()`, a hard SQL delete that cascades comments, links, runs
 * and events. `grep -rn confirm ui/src/dash/board/` returned nothing.
 *
 * The failure is a slip, not a mistake: `archive` sits immediately to the left
 * of the red `delete` in the same button row, and archive is the reversible
 * option (recoverable via "Show archived"). An operator shift-selecting a
 * range to archive who lands one button right destroys the lot, with nothing
 * written anywhere recoverable and no undo affordance.
 *
 * This was the only unguarded destructive multi-record action left in the app.
 * The guard matches the established precedent rather than inventing one —
 * `ConfirmDialog` with `destructive` + `typeToConfirm` + an explicit
 * blast-radius list, exactly as `DeleteSlotDialog` does (which states what is
 * destroyed AND what survives), and in the same family as the secrets removal
 * (#1450) and the memory bank-wipe echo guard (#1024 / #1457).
 *
 * Every assertion below is on the WIRE — zero DELETEs — rather than on the
 * dialog rendering, because a dialog that renders but doesn't actually gate
 * the mutation is precisely the bug being fixed.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { BOARD_TASKS } from '../fixtures/mock-data'

const FIVE_S = 5_500

/** Capture every per-id DELETE the board can fire. */
async function captureDeletes(page: any): Promise<string[]> {
  const urls: string[] = []
  await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route: any) => {
    if (route.request().method() === 'DELETE') {
      urls.push(route.request().url())
      await json(route, { ok: true })
    } else {
      await route.fallback()
    }
  })
  return urls
}

async function gotoBoardAndWait(page: any) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

async function selectCards(page: any, ids: string[]) {
  for (const id of ids) {
    const card = page.locator(`[data-testid="board-task-${id}"]`)
    await card.scrollIntoViewIfNeeded()
    await card.locator('.kc-check').click()
  }
}

const deleteBtn = (page: any) => page.locator('[data-testid="board-action-delete"]')
const dialog = (page: any) => page.locator('[data-testid="board-delete-blast"]')
const confirmBtn = (page: any) =>
  page.locator('[role="dialog"] button', { hasText: /^Delete \d+ card/ })

/** Two cards that exist in the mock board. */
const targets = () => BOARD_TASKS.filter((t: any) => t.status === 'done').slice(0, 2)

test.describe('Board bulk delete — confirmation guard (#1535)', () => {
  test('G1 — clicking delete fires ZERO DELETEs and raises a confirm naming the count', async ({
    page,
  }) => {
    const t = targets()
    const deletes = await captureDeletes(page)

    await gotoBoardAndWait(page)
    await selectCards(page, t.map((x: any) => x.id))
    await deleteBtn(page).click()

    // The whole point: the click alone must not reach the wire.
    await page.waitForTimeout(400)
    expect(deletes).toEqual([])

    await expect(dialog(page)).toBeVisible()
    await expect(dialog(page)).toContainText(String(t.length))
  })

  test('G2 — the confirm states the cascade and names archive as the reversible option', async ({
    page,
  }) => {
    const t = targets()
    await captureDeletes(page)

    await gotoBoardAndWait(page)
    await selectCards(page, t.map((x: any) => x.id))
    await deleteBtn(page).click()

    const blast = dialog(page)
    await expect(blast).toBeVisible()
    // What is destroyed — the cascade is the part an operator cannot see.
    await expect(blast).toContainText(/comment/i)
    await expect(blast).toContainText(/run/i)
    await expect(blast).toContainText(/event/i)
    // …and the recoverable alternative sitting next to the button they hit.
    await expect(blast).toContainText(/archive/i)
  })

  test('G3 — Cancel fires nothing and keeps the selection intact', async ({ page }) => {
    const t = targets()
    const deletes = await captureDeletes(page)

    await gotoBoardAndWait(page)
    await selectCards(page, t.map((x: any) => x.id))
    await deleteBtn(page).click()
    await expect(dialog(page)).toBeVisible()

    await page.locator('[role="dialog"] button', { hasText: /^Cancel$/ }).click()
    await expect(dialog(page)).toHaveCount(0)
    await page.waitForTimeout(300)
    expect(deletes).toEqual([])

    // The selection survives, so a mis-click costs nothing — the operator can
    // still hit archive, which is what they usually meant.
    await expect(deleteBtn(page)).toBeVisible()
  })

  test('G4 — Confirm stays disabled until the echo text matches', async ({ page }) => {
    const t = targets()
    const deletes = await captureDeletes(page)

    await gotoBoardAndWait(page)
    await selectCards(page, t.map((x: any) => x.id))
    await deleteBtn(page).click()
    await expect(dialog(page)).toBeVisible()

    await expect(confirmBtn(page)).toBeDisabled()
    await page.locator('[role="dialog"] input').fill('delet')
    await expect(confirmBtn(page)).toBeDisabled()
    // A forced click on the disabled control must still reach nothing.
    await confirmBtn(page).click({ force: true })
    await page.waitForTimeout(250)
    expect(deletes).toEqual([])

    await page.locator('[role="dialog"] input').fill('delete')
    await expect(confirmBtn(page)).toBeEnabled()
  })

  test('G5 — confirming fires exactly one DELETE per selected id', async ({ page }) => {
    const t = targets()
    const deletes = await captureDeletes(page)

    await gotoBoardAndWait(page)
    await selectCards(page, t.map((x: any) => x.id))
    await deleteBtn(page).click()
    await page.locator('[role="dialog"] input').fill('delete')
    await confirmBtn(page).click()

    await expect.poll(() => deletes.length).toBe(t.length)
    for (const x of t) {
      expect(deletes.some((u) => u.includes(x.id))).toBe(true)
    }
    await expect(dialog(page)).toHaveCount(0)
  })

  test('G6 (guard) — archive is NOT gated; only the terminal action is', async ({
    page,
  }) => {
    // Over-gating guard. `archive` is the reversible slot and is used
    // constantly; putting a type-to-confirm in front of it would train
    // operators to type through the dialog, which is how a guard stops
    // working.
    const t = targets()
    const bulk: any[] = []
    await page.route(/\/api\/board\/tasks\/bulk/, async (route: any) => {
      if (route.request().method() === 'POST') {
        bulk.push(route.request().postDataJSON())
        await json(route, { updated: t.length })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await selectCards(page, t.map((x: any) => x.id))
    await page.locator('[data-testid="board-action-archive"]').click()

    await expect.poll(() => bulk.length).toBeGreaterThan(0)
    expect(bulk.some((b) => b.update?.status === 'archived')).toBe(true)
    await expect(dialog(page)).toHaveCount(0)
  })
})
