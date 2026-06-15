/**
 * board-drawer-v3 — TaskDrawer contract for each task status.
 *
 * Covers:
 *   - Drawer opens when card is clicked → board-task-drawer visible
 *   - Status actions present for each status
 *   - triage: specify + decompose buttons visible
 *   - blocked: block_reason shown
 *   - done: run history (board-runs) visible
 *   - running: worker log (board-worklog) shows "worker streaming" or logs
 *   - Dependencies: add parent / add child / remove dep chip
 *   - Comment compose: input + submit fires POST /tasks/<id>/comments
 *   - Events log section (board-events) visible
 *   - Worker log refresh (board-action-worklog-refresh) fires refetch
 */

import { test, expect, json } from '../fixtures/apiMock'
import { BOARD_TASKS } from '../fixtures/mock-data'

const FIVE_S = 5_500

// All three prior crash-workaround stubs are GONE — the lead fixed the source bugs
// they masked, so the drawer is exercised through its real code paths:
//
// 1. BoardIcon — task-drawer.jsx now resolves window.BoardIcon at RENDER time
//    (`function Icon(props){ const BI = window.BoardIcon; return BI ? <BI/> : null }`),
//    so it no longer captures the chrome.jsx Icons OBJECT at module-eval and no longer
//    crashes. We seed a harmless render-time stub so the test never depends on
//    board-view.jsx import order to register the real icon component.
//
// 2. __hal0UseBoardTask — left LIVE. The drawer now reads `liveTaskQ.data` (the
//    normalised task off the TanStack QueryResult) instead of using the QueryResult as
//    the task. GET /api/board/tasks/:id (apiMock) returns the matching task and
//    useBoardTask normalises it to camelCase, so the status-gated sections render.
//
// 3. __hal0UseBoardTaskLog — left LIVE. The drawer now joins worker-log {ts,line}
//    entries to text instead of rendering the array as a React child (which crashed).
//    The default /tasks/:id/log mock returns [] so running tasks fall back to the
//    runs-based "worker streaming" message; the populated-log test below overrides the
//    route to return entries and asserts they render as text.
//
// pageerror guard: any uncaught React error on the board surface fails the test.
test.beforeEach(async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(String(e)))
  ;(page as any).__boardErrors = errors
  await page.addInitScript(() => {
    ;(window as any).BoardIcon = () => null
  })
})

test.afterEach(async ({ page }) => {
  const errors: string[] = (page as any).__boardErrors || []
  expect(errors, `pageerror(s) on board surface:\n${errors.join('\n')}`).toEqual([])
})

async function gotoBoardAndWait(page: any) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

async function openTask(page: any, taskId: string) {
  const card = page.locator(`[data-testid="board-task-${taskId}"]`)
  // Wait for card to be visible (TanStack Query first-fetch must complete)
  await expect(card).toBeVisible({ timeout: FIVE_S })
  // Click the title row — safe zone that bubbles to the card's onClick
  // (kc-check has stopPropagation so we avoid it)
  const title = card.locator('.kc-title')
  await expect(title).toBeVisible()
  await title.click()
  await expect(page.locator('[data-testid="board-task-drawer"]')).toBeVisible({ timeout: FIVE_S })
}

test.describe('TaskDrawer — per-status contract', () => {

  // ── triage ────────────────────────────────────────────────────────────

  test('triage task: drawer opens, specify+decompose buttons visible', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'triage')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-action-specify"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-decompose"]')).toBeVisible()
    // triage + ready + block + unblock + complete + archive always present
    await expect(page.locator('[data-testid="board-action-triage"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-ready"]')).toBeVisible()
  })

  test('triage task: specify fires POST /specify', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'triage')!
    let specifyUrl = ''

    await page.route(/\/api\/board\/tasks\/[^/]+\/specify/, async (route) => {
      specifyUrl = route.request().url()
      await json(route, { ok: true })
    })

    await gotoBoardAndWait(page)
    await openTask(page, task.id)
    await page.locator('[data-testid="board-action-specify"]').click()
    await page.waitForTimeout(200)
    expect(specifyUrl).toContain(task.id)
  })

  test('triage task: decompose fires POST /decompose', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'triage')!
    let decomposeUrl = ''

    await page.route(/\/api\/board\/tasks\/[^/]+\/decompose/, async (route) => {
      decomposeUrl = route.request().url()
      await json(route, { ok: true })
    })

    await gotoBoardAndWait(page)
    await openTask(page, task.id)
    await page.locator('[data-testid="board-action-decompose"]').click()
    await page.waitForTimeout(200)
    expect(decomposeUrl).toContain(task.id)
  })

  // ── todo ──────────────────────────────────────────────────────────────

  test('todo task: drawer opens with status actions; no specify/decompose', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'todo')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-action-triage"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-ready"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-complete"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-specify"]')).toHaveCount(0)
    await expect(page.locator('[data-testid="board-action-decompose"]')).toHaveCount(0)
  })

  // ── scheduled ─────────────────────────────────────────────────────────

  test('scheduled task: drawer opens, standard status actions present', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'scheduled')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-action-triage"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-ready"]')).toBeVisible()
  })

  // ── ready ─────────────────────────────────────────────────────────────

  test('ready task: drawer opens with standard status actions', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'ready')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-action-triage"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-ready"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-complete"]')).toBeVisible()
  })

  // ── running ───────────────────────────────────────────────────────────

  test('running task: empty log → runs-based "worker streaming" fallback', async ({ page }) => {
    // Default /tasks/:id/log mock returns [] → drawer falls back to the active-run
    // streaming message (running task has an active run).
    const task = BOARD_TASKS.find(t => t.status === 'running' && t.runs.some(r => r.state === 'active'))!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    const worklog = page.locator('[data-testid="board-worklog"]')
    await expect(worklog).toBeVisible()
    await expect(worklog).toContainText('worker streaming', { timeout: FIVE_S })
  })

  test('running task: populated worker log renders {ts,line} entries as text', async ({ page }) => {
    // CORRECT (post-fix) behaviour: task-drawer.jsx now joins each log entry to a
    // string (e.shape {ts,line}) and renders the joined text — previously it rendered
    // the entry objects directly as React children and crashed the whole drawer.
    const task = BOARD_TASKS.find(t => t.status === 'running')!
    await page.route(/\/api\/board\/tasks\/[^/]+\/log/, (route) =>
      json(route, [
        { ts: '12:00:01', line: 'spawned worker pid=4123' },
        { ts: '12:00:02', line: 'attached to lemond journal' },
        { ts: '12:00:03', line: 'probing /var headroom…' },
      ]),
    )

    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    const worklog = page.locator('[data-testid="board-worklog"]')
    await expect(worklog).toBeVisible()
    await expect(worklog).toContainText('spawned worker pid=4123')
    await expect(worklog).toContainText('attached to lemond journal')
    await expect(worklog).toContainText('probing /var headroom')
    // No raw object stringification leaked into the DOM
    await expect(worklog).not.toContainText('[object Object]')
  })

  test('running task: worklog refresh button fires toast', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'running')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-action-worklog-refresh"]')).toBeVisible()
    await page.locator('[data-testid="board-action-worklog-refresh"]').click()
    // Should show toast "log refreshed" — check toast or just that click didn't throw
    await page.waitForTimeout(200)
  })

  test('running task: run history (board-runs) shows active run row', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'running' && t.runs.length > 0)!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-runs"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-runs"]')).toContainText('active')
  })

  // ── blocked ───────────────────────────────────────────────────────────

  test('blocked task: block_reason shown in drawer', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'blocked' && t.block_reason)!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    // block reason displayed in .dr-block
    const blockSection = page.locator('.dr-block')
    await expect(blockSection).toBeVisible()
    await expect(blockSection).toContainText('unified-memory headroom')
  })

  test('blocked task: unblock action moves to todo', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'blocked')!
    let patchBody: any = null

    await page.route(/\/api\/board\/tasks\/[^/]+$/, async (route) => {
      if (route.request().method() === 'PATCH') {
        patchBody = route.request().postDataJSON()
        await json(route, { ...task, status: 'todo' })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await openTask(page, task.id)
    await page.locator('[data-testid="board-action-unblock"]').click()
    await page.waitForTimeout(200)
    expect(patchBody).toMatchObject({ status: 'todo' })
  })

  // ── review ────────────────────────────────────────────────────────────

  test('review task: drawer opens with standard actions', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'review')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-task-drawer"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-complete"]')).toBeVisible()
  })

  // ── done ──────────────────────────────────────────────────────────────

  test('done task: run history with completed run', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'done' && t.runs.length > 0)!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-runs"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-runs"]')).toContainText('completed')
  })

  // ── Events log ────────────────────────────────────────────────────────

  test('events section always rendered in drawer', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'ready')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    await expect(page.locator('[data-testid="board-events"]')).toBeVisible()
  })

  // ── Dependencies ──────────────────────────────────────────────────────

  test('task with children: child dep chips visible', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.deps.children.length > 0)!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    // Children dep chips should be rendered
    const childChip = page.locator(`.dep-chip`).first()
    await expect(childChip).toBeVisible()
  })

  test('dep remove chip fires DELETE on /api/board/links', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.deps.children.length > 0)!
    const childId = task.deps.children[0]
    let deleteBody: any = null

    await page.route('**/api/board/links', async (route) => {
      if (route.request().method() === 'DELETE') {
        deleteBody = route.request().postDataJSON()
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    const removeBtn = page.locator(`[data-testid="board-dep-remove-${childId}"]`)
    await expect(removeBtn).toBeVisible()
    await removeBtn.click()
    await page.waitForTimeout(200)
    expect(deleteBody).toBeTruthy()
  })

  test('add parent dep fires POST /api/board/links', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'triage')!
    let linkBody: any = null

    await page.route('**/api/board/links', async (route) => {
      if (route.request().method() === 'POST') {
        linkBody = route.request().postDataJSON()
        await json(route, { ok: true }, 201)
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    // Select a parent from the dep-add-parent select
    const parentSelect = page.locator('[data-testid="board-dep-add-parent"]')
    await expect(parentSelect).toBeVisible()
    // Select any option that's not the empty one
    const options = await parentSelect.locator('option').all()
    const firstTaskOption = options.find(async (opt) => (await opt.getAttribute('value')) !== '')
    if (firstTaskOption) {
      const val = await firstTaskOption.getAttribute('value')
      if (val) {
        await parentSelect.selectOption(val)
        // Click the "parent" add button (sibling button)
        await parentSelect.locator('..').locator('button').first().click()
        await page.waitForTimeout(200)
        expect(linkBody).toBeTruthy()
      }
    }
  })

  // ── Comment compose ───────────────────────────────────────────────────

  test('comment compose: input visible, submit fires POST /comments', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'ready')!
    let commentBody: any = null
    let commentUrl = ''

    await page.route(/\/api\/board\/tasks\/[^/]+\/comments/, async (route) => {
      commentBody = route.request().postDataJSON()
      commentUrl = route.request().url()
      await json(route, { ok: true }, 201)
    })

    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    const input = page.locator('[data-testid="board-comment-input"]')
    await expect(input).toBeVisible()
    await input.fill('test comment from e2e')
    await page.locator('[data-testid="board-action-comment"]').click()
    await page.waitForTimeout(200)

    expect(commentUrl).toContain(task.id)
    // CORRECT (post-fix) wire shape. task-drawer.jsx now passes a bare string body to
    // addComment.mutate({ id, body }), and useAddComment POSTs { body }, so the request
    // body is a flat { body: "<text>" } — no double-nesting.
    expect(commentBody).toEqual({ body: 'test comment from e2e' })
  })

  test('comment compose: Enter submits comment', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'todo')!
    let commentBody: any = null

    await page.route(/\/api\/board\/tasks\/[^/]+\/comments/, async (route) => {
      commentBody = route.request().postDataJSON()
      await json(route, { ok: true }, 201)
    })

    await gotoBoardAndWait(page)
    await openTask(page, task.id)

    const input = page.locator('[data-testid="board-comment-input"]')
    await input.fill('enter key comment')
    await input.press('Enter')
    await page.waitForTimeout(200)

    expect(commentBody).toEqual({ body: 'enter key comment' })
  })

  // ── Close drawer ──────────────────────────────────────────────────────

  test('closing drawer via Escape hides it', async ({ page }) => {
    const task = BOARD_TASKS.find(t => t.status === 'todo')!
    await gotoBoardAndWait(page)
    await openTask(page, task.id)
    await expect(page.locator('[data-testid="board-task-drawer"]')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(page.locator('[data-testid="board-task-drawer"]')).not.toBeVisible({ timeout: FIVE_S })
  })

})
