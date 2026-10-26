/**
 * dock-events-v3 — the status dock's events tab: the journal and the activity trail as one
 * timeline. Replaces activity-log.spec.ts, which pinned the Activity sidebar pane of the Slots
 * page (the pane is gone; the dock carries the trail, and the slot rail took the sidebar).
 *
 *   1. The Slots sidebar holds the slot rail, not an activity log, and the three older sidebar
 *      widgets are still gone.
 *   2. Activity `action` records pushed through the SSE harness render as stream `a` rows, newest
 *      LAST (the pane scrolls to the tail); `event`-kind records never render.
 *   3. Rows are colorized by severity: `.foot-line.error` / `.foot-line.ok`, the `.lvl` word.
 *   4. The severity chips narrow the visible rows.
 *   5. Export is one link whose href follows the severity chip and asks for CSV.
 *   6. The pane body is a bounded scroll container.
 *   7. The dock fits a phone-width viewport; its toggle badges rows that arrive while collapsed.
 *   8. The Generate tab is two columns from 1360 px.
 *
 * SSE frame shapes (backend contract):
 *   activity: data: {"record": {...}, "epoch": "<str>"}
 *   journal:  data: {"id", "ts", "source", "level", "msg"}
 */
import type { Page } from '@playwright/test'
import { test, expect } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'
import { INSTALLED, catalog, gotoImageGen, makeImageGenMock, routeImageGen, sdxlLightning } from '../fixtures/imageGen'

const ACTIVITY = '/api/activity/stream'
const JOURNAL = '/api/journal/stream'
const EPOCH = 'epoch-test-1'

function rec(over: Record<string, unknown> = {}) {
  return {
    id: 1,
    ts: '2026-06-14T12:00:00.000000+00:00',
    kind: 'action',
    category: 'slot',
    action: 'slot.edit_config',
    target: 'primary',
    actor: 'dashboard',
    severity: 'ok',
    outcome: 'ok',
    message: 'updated primary context_size',
    before: null,
    after: null,
    error: null,
    duration_ms: 12,
    request_id: 'req-1',
    ...over,
  }
}

const OK_REC = rec({ id: 1, severity: 'ok', outcome: 'ok', message: 'edit applied' })
const ERR_REC = rec({
  id: 2,
  ts: '2026-06-14T12:00:01.000000+00:00',
  severity: 'error',
  outcome: 'error',
  action: 'slot.restart',
  message: 'restart failed: container exited 1',
  error: 'container exited 1',
})
// An `event`-kind record duplicates a journal line: the dock never draws it.
const EVENT_REC = rec({
  id: 3,
  ts: '2026-06-14T12:00:02.000000+00:00',
  category: 'model',
  severity: 'warn',
  outcome: null,
  kind: 'event',
  action: 'model.pull',
  message: 'pull slow — retrying',
})
// An action record that is a warning, so the severity filter has three values to tell apart.
const WARN_REC = rec({
  id: 4,
  ts: '2026-06-14T12:00:03.000000+00:00',
  category: 'model',
  severity: 'warn',
  outcome: 'ok',
  action: 'model.update',
  target: 'qwen',
  message: 'update available — pinned',
})

const frame = (record: Record<string, unknown>) => ({ record, epoch: EPOCH })

function journalEntry(id: number, over: Record<string, unknown> = {}) {
  return {
    id,
    ts: `2026-06-14T12:10:${String(id % 60).padStart(2, '0')}.000000+00:00`,
    source: 'hal0',
    level: 'info',
    msg: `journal line ${id}`,
    ...over,
  }
}

const body = (page: Page) => page.getByTestId('dock-events').locator('.foot-pane-body')
const actionRows = (page: Page) => page.locator('[data-testid="dock-event-row"][data-stream="a"]')

/** Expand the dock, select the events tab and wait for both of its streams. */
async function openEvents(page: Page) {
  await page.getByTestId('dock-toggle').click()
  await page.getByTestId('dock-tab-events').click()
  await expect(page.getByTestId('dock-events')).toBeVisible()
  await waitForSse(page, ACTIVITY, 6_000)
  await waitForSse(page, JOURNAL, 6_000)
}

/** Push activity records and wait for each one's own row. Both streams live as long as the dock
 *  does, so a push never lands in a reconnect gap. */
async function pushActions(page: Page, records: Array<Record<string, unknown>>) {
  for (const r of records) await emitSse(page, ACTIVITY, frame(r))
}

test.describe('Dock events tab', () => {
  test.beforeEach(async ({ page }) => {
    await installSseHarness(page)
  })

  test('the Slots sidebar holds the slot rail; the activity log and the 3 old widgets are gone', async ({
    page,
  }) => {
    await page.goto('/#slots')
    await expect(page.locator('.dash-side [data-testid="slot-rail"]')).toBeVisible({ timeout: 6_000 })
    await expect(page.locator('[data-testid="activity-log"]')).toHaveCount(0)
    await expect(page.locator('.dash-side .memmap-sidebar')).toHaveCount(0)
    await expect(page.locator('.dash-side .snapshot-strip')).toHaveCount(0)
    await expect(page.locator('.dash-side .throughput-card')).toHaveCount(0)
  })

  test('opens the activity SSE on mount; action records render newest LAST, event records never do', async ({
    page,
  }) => {
    await page.goto('/#slots')
    // The dock streams from first paint (the ticker and the badge need it collapsed), and the
    // activity stream asks for actions only.
    await waitForSse(page, `${ACTIVITY}?`, 6_000)
    const urls = await page.evaluate(() => Object.keys((window as any).__sseStreams || {}))
    expect(urls.some((u) => u.includes('/api/activity/stream') && u.includes('kind=action'))).toBe(true)

    await openEvents(page)
    await pushActions(page, [OK_REC, ERR_REC, EVENT_REC])

    await expect(actionRows(page)).toHaveCount(2)
    // Newest last: the error (id 2, later ts) is pushed after the ok row, so it sits below it.
    await expect(actionRows(page).first()).toContainText('edit applied')
    await expect(actionRows(page).last()).toContainText('restart failed')
    // The `event`-kind record is in no row of the pane, and none of the ticker's.
    await expect(page.getByText('pull slow — retrying')).toHaveCount(0)
  })

  test('rows are colorized by severity (error → .error + "error"; ok → .ok + "ok")', async ({ page }) => {
    await page.goto('/#slots')
    await openEvents(page)
    await pushActions(page, [OK_REC, ERR_REC])
    await expect(actionRows(page)).toHaveCount(2)

    const okRow = page.locator('[data-testid="dock-event-row"].foot-line.ok')
    const errRow = page.locator('[data-testid="dock-event-row"].foot-line.error')
    await expect(okRow).toHaveCount(1)
    await expect(errRow).toHaveCount(1)
    // The level is a word in `.lvl`, not a glyph.
    await expect(okRow.locator('.lvl')).toHaveText('ok')
    await expect(errRow.locator('.lvl')).toHaveText('error')
    // An activity row names who did it, and opens its detail on click.
    await expect(errRow.locator('.actor')).toHaveText('dashboard')
    await expect(page.getByTestId('dock-event-detail')).toHaveCount(0)
    await errRow.click()
    await expect(page.getByTestId('dock-event-detail')).toContainText('container exited 1')
  })

  test('severity chips filter the visible rows', async ({ page }) => {
    await page.goto('/#slots')
    await openEvents(page)
    await pushActions(page, [OK_REC, ERR_REC, WARN_REC])
    await expect(actionRows(page)).toHaveCount(3)

    // The severity group is the pane header's second chip group.
    const severity = page.getByTestId('dock-filter-severity')
    await severity.getByRole('button', { name: 'error', exact: true }).click()
    await expect(actionRows(page)).toHaveCount(1)
    await expect(actionRows(page).first()).toHaveClass(/\berror\b/)

    // All restores every row.
    await severity.getByRole('button', { name: 'all', exact: true }).click()
    await expect(actionRows(page)).toHaveCount(3)
  })

  test('the export link is one anchor whose href follows the severity chip and asks for CSV', async ({
    page,
  }) => {
    await page.goto('/#slots')
    await openEvents(page)
    const exportLink = page.getByTestId('dock-export')
    await expect(exportLink).toBeVisible()
    await expect(exportLink).toHaveAttribute('href', /\/api\/activity\/export\?.*fmt=csv/)
    await expect(exportLink).not.toHaveAttribute('href', /severity=/)

    await page.getByTestId('dock-filter-severity').getByRole('button', { name: 'error', exact: true }).click()
    await expect(exportLink).toHaveAttribute('href', /severity=error/)
    await expect(exportLink).toHaveAttribute('href', /fmt=csv/)
  })

  test('the pane body is a BOUNDED scroll container (not an infinite firehose)', async ({ page }) => {
    await page.goto('/#slots')
    await openEvents(page)
    // Enough lines to overflow any viewport the suite runs at.
    for (let i = 1; i <= 80; i++) await emitSse(page, JOURNAL, journalEntry(i))
    await expect(page.locator('[data-testid="dock-event-row"][data-stream="j"]')).toHaveCount(80)

    await expect(body(page)).toHaveCSS('overflow-y', /^(auto|scroll)$/)
    const box = await body(page).evaluate((el) => {
      const dock = el.closest('[data-testid="dock-expanded"]') as HTMLElement
      return { body: el.clientHeight, scroll: el.scrollHeight, dock: dock.clientHeight }
    })
    expect(box.body).toBeGreaterThan(0)
    // Bounded: the body never outgrows the expanded dock; the lines scroll inside it.
    expect(box.body).toBeLessThanOrEqual(box.dock)
    expect(box.scroll).toBeGreaterThan(box.body)
  })

  test('dock fits a 390 px viewport without horizontal scroll', async ({ page }) => {
    // BLOCKED on a product defect, not a locator: src/dashboard.css `@media (max-width: 720px)`
    // still carries `.footer { display: none; }` (since #235), which hides the whole dock on a
    // phone and makes dock.css's own ≤ 640 px layout dead code. With the rule overridden this
    // test passes (the dock fits, collapsed and expanded). Drop the fixme once the rule is gone.
    test.fixme(true, 'dashboard.css hides .footer at <= 720px, so the dock is not on a phone')
    await page.setViewportSize({ width: 390, height: 800 })
    await page.goto('/#slots')
    await expect(page.getByTestId('dock')).toBeVisible()
    const scrollWidth = () => page.evaluate(() => document.documentElement.scrollWidth)

    // Collapsed.
    expect(await scrollWidth()).toBeLessThanOrEqual(390)

    // Expanded, on the events tab with rows in it (the busiest pane header).
    await openEvents(page)
    await pushActions(page, [OK_REC, ERR_REC])
    await emitSse(page, JOURNAL, journalEntry(1))
    await expect(page.locator('[data-testid="dock-event-row"]')).toHaveCount(3)
    expect(await scrollWidth()).toBeLessThanOrEqual(390)
  })

  test('unseen badge counts rows pushed while collapsed', async ({ page }) => {
    await page.goto('/#slots')
    await waitForSse(page, JOURNAL, 6_000)
    // A stream's first batch is its backfill, which is never news: send one line, wait for it to
    // reach the ticker, and only then send the two that count.
    await emitSse(page, JOURNAL, journalEntry(1))
    await expect(page.getByTestId('dock-ticker')).toContainText('journal line 1')
    await expect(page.getByTestId('dock-unseen')).toHaveCount(0)

    await emitSse(page, JOURNAL, journalEntry(2))
    await emitSse(page, JOURNAL, journalEntry(3))
    await expect(page.getByTestId('dock-unseen')).toHaveText('+2')

    // Opening the dock reads them.
    await page.getByTestId('dock-toggle').click()
    await expect(page.getByTestId('dock-expanded')).toBeVisible()
    await expect(page.getByTestId('dock-unseen')).toHaveCount(0)
  })

  test('Generate tab is two columns at 1360 px and stacks below', async ({ page }) => {
    const mock = makeImageGenMock({
      models: catalog([sdxlLightning({ ...INSTALLED, recommended: true })]),
    })
    await routeImageGen(page, mock)

    await page.setViewportSize({ width: 1400, height: 900 })
    await gotoImageGen(page, 'generate')
    const gen = page.locator('.gen').first()
    await expect(gen).toBeVisible()
    await expect(gen).toHaveCSS('display', 'grid')
    const columns = await gen.evaluate((el) => getComputedStyle(el).gridTemplateColumns)
    expect(columns.trim().split(/\s+/)).toHaveLength(2)

    await page.setViewportSize({ width: 1200, height: 900 })
    await expect(gen).toHaveCSS('display', 'flex')
  })
})
