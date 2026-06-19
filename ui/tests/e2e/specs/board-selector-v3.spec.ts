/**
 * board-selector-v3 — Board selector, new-board modal, and orchestration popover.
 *
 * Covers:
 *   - Selector lists 3 boards (default/models/memory)
 *   - Switching board → POST /api/board/boards/<slug>/switch
 *   - New-board modal opens via board-action-new-board
 *   - New-board modal: fill slug/name/desc/icon, create → POST /api/board/boards
 *   - New-board modal: cancel closes modal
 *   - Orchestration popover: opens from orch-pill, shows 4 editable knobs + 4 RO knobs
 *   - Orch mode toggle auto/manual
 *   - Orch save → PUT /api/board/orchestration with body
 *   - Tweaks panel: board-tweaks opens via fab, shows density/accent/titlefont/meta
 */

import { test, expect, json } from '../fixtures/apiMock'
import { BOARD_BOARDS } from '../fixtures/mock-data'

const FIVE_S = 5_500

// BoardIcon stub: orchestration-popover.jsx now resolves window.BoardIcon at RENDER
// time (function Icon(props) reads window.BoardIcon each call), so a stub is not
// strictly required for the popover to mount. We seed a harmless render-time stub so
// the test never depends on board-view.jsx's import order to register the icon.
//
// The orch hooks (__hal0UseBoardProfiles / __hal0UseBoardAssignees / __hal0UseBoardConfig
// / __hal0UseBoardOrchestration) are LEFT LIVE — orchestration-popover.jsx now correctly
// unwraps `.data` off each TanStack QueryResult, so the dropdowns populate from the
// apiMock /api/board/profiles + /assignees responses and the read-only knobs show the
// real /api/board/config values (5/3/4/600). No hook hijack needed; the tests below
// assert the CORRECT (post-fix) behaviour.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    ;(window as any).BoardIcon = () => null
  })
})

async function gotoBoardAndWait(page: any) {
  await page.goto('/#board')
  await expect(page.locator('[data-testid="board-view"]')).toBeVisible({ timeout: FIVE_S })
}

test.describe('BoardView — selector, new board, orchestration, tweaks', () => {

  // ── Board selector ─────────────────────────────────────────────────────

  test('board selector button visible with default board name', async ({ page }) => {
    await gotoBoardAndWait(page)
    const selector = page.locator('[data-testid="board-selector"]')
    await expect(selector).toBeVisible()
    await expect(selector).toContainText('strix-halo-01 ops')
  })

  test('clicking selector shows all 3 boards in dropdown', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-selector"]').click()

    // Board dropdown menu should show all 3 boards
    const menu = page.locator('.board-menu')
    await expect(menu).toBeVisible({ timeout: FIVE_S })
    for (const board of BOARD_BOARDS) {
      await expect(menu).toContainText(board.name)
    }
  })

  test('switching to "models" board fires POST /boards/models/switch', async ({ page }) => {
    let switchUrl = ''
    await page.route(/\/api\/board\/boards\/[^/]+\/switch/, async (route) => {
      switchUrl = route.request().url()
      await json(route, { ok: true })
    })

    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-selector"]').click()
    await expect(page.locator('.board-menu')).toBeVisible()
    await page.locator('.board-menu .bm-row').filter({ hasText: 'model catalogue' }).click()
    await page.waitForTimeout(300)

    expect(switchUrl).toContain('/models/switch')
  })

  test('switching to "memory" board fires POST /boards/memory/switch', async ({ page }) => {
    let switchUrl = ''
    await page.route(/\/api\/board\/boards\/[^/]+\/switch/, async (route) => {
      switchUrl = route.request().url()
      await json(route, { ok: true })
    })

    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-selector"]').click()
    await expect(page.locator('.board-menu')).toBeVisible()
    await page.locator('.board-menu .bm-row').filter({ hasText: 'agent memory' }).click()
    await page.waitForTimeout(300)

    expect(switchUrl).toContain('/memory/switch')
  })

  // ── New board modal ────────────────────────────────────────────────────

  test('new-board modal opens via board-action-new-board', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-action-new-board"]').click()
    await expect(page.locator('[data-testid="board-new-modal"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('new-board modal has all input fields', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-action-new-board"]').click()
    await expect(page.locator('[data-testid="board-new-modal"]')).toBeVisible()

    await expect(page.locator('[data-testid="board-new-slug"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-new-name"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-new-desc"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-new-icon"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-new-switch"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-create-board"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-action-cancel-board"]')).toBeVisible()
  })

  test('creating a board: fires POST /api/board/boards then switches', async ({ page }) => {
    // CORRECT (post-fix) behaviour: NewBoardModal.onCreate now calls useCreateBoard()
    // → POST /api/board/boards with the form body, and on success (switchTo default
    // true) fires the board switch. Previously the modal only switched without ever
    // POSTing a board.
    let postBody: any = null
    let switchUrl = ''
    await page.route('**/api/board/boards', async (route) => {
      if (route.request().method() === 'POST') {
        postBody = route.request().postDataJSON()
        await json(route, { ...postBody, slug: postBody.slug }, 201)
      } else {
        await route.fallback()
      }
    })
    await page.route(/\/api\/board\/boards\/[^/]+\/switch/, async (route) => {
      switchUrl = route.request().url()
      await json(route, { ok: true })
    })

    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-action-new-board"]').click()
    await expect(page.locator('[data-testid="board-new-modal"]')).toBeVisible()

    await page.locator('[data-testid="board-new-slug"]').fill('my-new-board')
    await page.locator('[data-testid="board-new-name"]').fill('My New Board')
    await page.locator('[data-testid="board-new-desc"]').fill('a fresh board')
    await page.locator('[data-testid="board-action-create-board"]').click()
    await page.waitForTimeout(400)

    // Modal closes after create
    await expect(page.locator('[data-testid="board-new-modal"]')).not.toBeVisible()
    // POST /api/board/boards fired with the form body
    expect(postBody).toMatchObject({
      slug: 'my-new-board',
      name: 'My New Board',
      desc: 'a fresh board',
    })
    // switchTo default-on → board switch followed the create
    expect(switchUrl).toContain('/my-new-board/switch')
  })

  test('cancel in new-board modal closes it', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-action-new-board"]').click()
    await expect(page.locator('[data-testid="board-new-modal"]')).toBeVisible()
    await page.locator('[data-testid="board-action-cancel-board"]').click()
    await expect(page.locator('[data-testid="board-new-modal"]')).not.toBeVisible()
  })

  // ── New task modal ─────────────────────────────────────────────────────
  // CORRECT (post-fix) behaviour: a lane's "+" opens an explicit NewTaskModal
  // and POSTs NOTHING until the operator fills a title and hits Create. The
  // old behaviour POSTed a blank `{ title: "New task" }` on click, which the
  // dispatcher then auto-advanced out of the lane.

  test('lane "+" opens the new-task modal WITHOUT creating a task', async ({ page }) => {
    let posted = false
    await page.route('**/api/board/tasks', async (route) => {
      if (route.request().method() === 'POST') {
        posted = true
        await json(route, { id: 'should-not-happen' }, 201)
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-lane-triage"] .ladd').click()

    // Modal appears, and crucially nothing has been POSTed yet.
    await expect(page.locator('[data-testid="board-new-task-modal"]')).toBeVisible({ timeout: FIVE_S })
    await page.waitForTimeout(300)
    expect(posted).toBe(false)
  })

  test('new-task modal: title + Create → POST /api/board/tasks with title + lane status', async ({ page }) => {
    let postBody: any = null
    await page.route('**/api/board/tasks', async (route) => {
      if (route.request().method() === 'POST') {
        postBody = route.request().postDataJSON()
        await json(route, { ...postBody, id: 'new-1' }, 201)
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-lane-todo"] .ladd').click()
    await expect(page.locator('[data-testid="board-new-task-modal"]')).toBeVisible()

    // Create is disabled until a title is present.
    const createBtn = page.locator('[data-testid="board-action-create-task"]')
    await expect(createBtn).toBeDisabled()

    await page.locator('[data-testid="board-new-task-title"]').fill('Wire the SSE protocol')
    await page.locator('[data-testid="board-new-task-body"]').fill('match frontend to board_chat.py')
    await expect(createBtn).toBeEnabled()
    await createBtn.click()
    await page.waitForTimeout(400)

    // Modal closed and a single POST carried the title + the clicked lane's status.
    await expect(page.locator('[data-testid="board-new-task-modal"]')).not.toBeVisible()
    expect(postBody).toMatchObject({
      title: 'Wire the SSE protocol',
      body: 'match frontend to board_chat.py',
      status: 'todo',
    })
  })

  test('cancel in new-task modal closes it without POSTing', async ({ page }) => {
    let posted = false
    await page.route('**/api/board/tasks', async (route) => {
      if (route.request().method() === 'POST') {
        posted = true
        await json(route, { id: 'x' }, 201)
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await page.locator('[data-testid="board-lane-triage"] .ladd').click()
    await expect(page.locator('[data-testid="board-new-task-modal"]')).toBeVisible()
    await page.locator('[data-testid="board-action-cancel-task"]').click()
    await expect(page.locator('[data-testid="board-new-task-modal"]')).not.toBeVisible()
    expect(posted).toBe(false)
  })

  // ── Orchestration popover ──────────────────────────────────────────────

  test('orchestration popover opens from orch-pill', async ({ page }) => {
    await gotoBoardAndWait(page)
    // Click the orch-pill (contains "Orchestration" text and a mode display)
    await page.locator('.orch-pill').click()
    await expect(page.locator('[data-testid="board-orch-popover"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('orch popover has 4 editable knobs', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('.orch-pill').click()
    await expect(page.locator('[data-testid="board-orch-popover"]')).toBeVisible()

    await expect(page.locator('[data-testid="board-orch-mode-auto"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-orch-mode-manual"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-orch-profile"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-orch-assignee"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-orch-autodecompose"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-orch-autopromote"]')).toBeVisible()

    // Dropdowns populate from /api/board/profiles + /assignees (orchestration-popover
    // now unwraps .data off the QueryResult). Each select has the 3 BOARD_PROFILES /
    // BOARD_ASSIGNEES rows plus the leading "— none —" option = 4 options each.
    const profileOpts = page.locator('[data-testid="board-orch-profile"] option')
    const assigneeOpts = page.locator('[data-testid="board-orch-assignee"] option')
    await expect(profileOpts).toHaveCount(4)
    await expect(assigneeOpts).toHaveCount(4)
    await expect(page.locator('[data-testid="board-orch-profile"]')).toContainText('admin-agent')
    await expect(page.locator('[data-testid="board-orch-assignee"]')).toContainText('mem-agent')
  })

  test('orch popover has 4 read-only config knobs populated from /api/board/config', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('.orch-pill').click()
    await expect(page.locator('[data-testid="board-orch-popover"]')).toBeVisible()

    // CORRECT (post-fix) behaviour: orchestration-popover.jsx now unwraps `.data` off
    // useBoardConfig(), so the 4 read-only knobs render the real BOARD_CONFIG values
    // (tick_interval 5, failure_limit 3, max_in_flight 4, claim_ttl 600). The numeric
    // tick/ttl knobs append an "s" suffix in the component.
    await expect(page.locator('[data-testid="board-orch-ro-tick"]')).toHaveText('5s')
    await expect(page.locator('[data-testid="board-orch-ro-failure"]')).toHaveText('3')
    await expect(page.locator('[data-testid="board-orch-ro-inflight"]')).toHaveText('4')
    await expect(page.locator('[data-testid="board-orch-ro-ttl"]')).toHaveText('600s')
  })

  test('toggle orch mode to manual then auto', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('.orch-pill').click()
    await expect(page.locator('[data-testid="board-orch-popover"]')).toBeVisible()

    // Click manual
    await page.locator('[data-testid="board-orch-mode-manual"]').click()
    await expect(page.locator('[data-testid="board-orch-mode-manual"]')).toHaveClass(/on/)

    // Click auto
    await page.locator('[data-testid="board-orch-mode-auto"]').click()
    await expect(page.locator('[data-testid="board-orch-mode-auto"]')).toHaveClass(/on/)
  })

  test('orch save fires PUT /api/board/orchestration', async ({ page }) => {
    let putBody: any = null
    await page.route('**/api/board/orchestration', async (route) => {
      if (route.request().method() === 'PUT') {
        putBody = route.request().postDataJSON()
        await json(route, { ok: true })
      } else {
        await route.fallback()
      }
    })

    await gotoBoardAndWait(page)
    await page.locator('.orch-pill').click()
    await expect(page.locator('[data-testid="board-orch-popover"]')).toBeVisible()

    // Toggle mode to manual
    await page.locator('[data-testid="board-orch-mode-manual"]').click()
    // Save
    await page.locator('[data-testid="board-action-orch-save"]').click()
    await page.waitForTimeout(300)

    // PUT body carries the toggled mode plus the 4 editable knobs, seeded from the
    // live /api/board/orchestration response (BOARD_ORCH_DEFAULT, unwrapped via .data).
    expect(putBody).toBeTruthy()
    expect(putBody).toMatchObject({
      mode: 'manual',
      orchestrator_profile: 'admin-agent',
      default_assignee: 'admin-agent',
      auto_decompose: true,
      auto_promote_children: true,
    })
  })

  test('orch popover closes on Escape', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('.orch-pill').click()
    await expect(page.locator('[data-testid="board-orch-popover"]')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(page.locator('[data-testid="board-orch-popover"]')).not.toBeVisible({ timeout: FIVE_S })
  })

  // ── Tweaks panel ──────────────────────────────────────────────────────

  test('tweaks fab opens board-tweaks panel', async ({ page }) => {
    await gotoBoardAndWait(page)
    // Click the tweaks FAB
    await page.locator('.tweak-fab').click()
    await expect(page.locator('[data-testid="board-tweaks"]')).toBeVisible({ timeout: FIVE_S })
  })

  test('tweaks panel has density, accent, titlefont, meta controls', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('.tweak-fab').click()
    await expect(page.locator('[data-testid="board-tweaks"]')).toBeVisible()

    await expect(page.locator('[data-testid="board-tweak-density"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-tweak-accent"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-tweak-titlefont"]')).toBeVisible()
    await expect(page.locator('[data-testid="board-tweak-meta"]')).toBeVisible()
  })

  test('switching density to compact changes data-density attr', async ({ page }) => {
    await gotoBoardAndWait(page)
    await page.locator('.tweak-fab').click()
    await expect(page.locator('[data-testid="board-tweaks"]')).toBeVisible()

    await page.locator('[data-testid="board-tweak-density"] button').filter({ hasText: 'compact' }).click()
    await page.waitForTimeout(200)

    const board = page.locator('[data-testid="board-view"]')
    await expect(board).toHaveAttribute('data-density', 'compact')
  })

})
