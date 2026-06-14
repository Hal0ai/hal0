/**
 * activity-log — the durable ActivityLog sidebar pane on the Slots page.
 *
 * Replaces the old SnapshotStrip / MemoryMap / ThroughputCard sidebar stack
 * (#slots → .dash-side). The pane streams /api/activity/stream and renders a
 * colorized, severity-filterable, BOUNDED audit trail. Specs pin:
 *
 *   1. The pane mounts in the Slots sidebar (.dash-side > [data-testid=activity-log])
 *      and the three removed widgets are gone.
 *   2. Records pushed through the SSE harness render newest-first, colorized
 *      by severity (an `error` row carries the `error` class + a ✗ glyph; an
 *      `ok` row carries the `ok` class + a ✓ glyph).
 *   3. The severity filter chips narrow the visible rows.
 *   4. The export buttons (CSV/JSON) are present and honour the active filter.
 *   5. The body is a bounded scroll container (max-height + overflow-y:auto).
 *
 * SSE frame shape (per backend contract):
 *   data: {"record": {...}, "epoch": "<str>"}
 */
import { test, expect } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'

const STREAM = '/api/activity/stream'

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
const WARN_REC = rec({
  id: 3,
  ts: '2026-06-14T12:00:02.000000+00:00',
  category: 'model',
  severity: 'warn',
  outcome: null,
  kind: 'event',
  action: 'model.pull',
  message: 'pull slow — retrying',
})

const EPOCH = 'epoch-test-1'
function frame(record: Record<string, unknown>) {
  return { record, epoch: EPOCH }
}

test.describe('ActivityLog sidebar pane (#slots)', () => {
  test.beforeEach(async ({ page }) => {
    await installSseHarness(page)
  })

  test('mounts in the Slots sidebar; the 3 old widgets are gone', async ({ page }) => {
    await page.goto('/#slots')
    const card = page.locator('.dash-side [data-testid="activity-log"]')
    await expect(card).toBeVisible({ timeout: 6_000 })
    // The retired sidebar widgets no longer render on the slots page.
    await expect(page.locator('.dash-side .memmap-sidebar')).toHaveCount(0)
    await expect(page.locator('.dash-side .snapshot-strip')).toHaveCount(0)
    await expect(page.locator('.dash-side .throughput-card')).toHaveCount(0)
  })

  test('opens the activity SSE and renders pushed records newest-first', async ({ page }) => {
    await page.goto('/#slots')
    await waitForSse(page, STREAM, 6_000)

    await emitSse(page, STREAM, frame(OK_REC))
    await emitSse(page, STREAM, frame(ERR_REC))

    const rows = page.locator('[data-testid="act-row"]')
    await expect(rows).toHaveCount(2)
    // Newest-first: the error (id 2, later ts) is pushed last so it sits on top.
    await expect(rows.first()).toContainText('restart failed')
  })

  test('rows are colorized by severity (error → red class + ✗; ok → green + ✓)', async ({
    page,
  }) => {
    await page.goto('/#slots')
    await waitForSse(page, STREAM, 6_000)

    await emitSse(page, STREAM, frame(OK_REC))
    await emitSse(page, STREAM, frame(ERR_REC))

    const okRow = page.locator('[data-testid="act-row"][data-severity="ok"]')
    const errRow = page.locator('[data-testid="act-row"][data-severity="error"]')
    await expect(okRow).toHaveClass(/\bok\b/)
    await expect(okRow.locator('.act-glyph')).toHaveText('✓')
    await expect(errRow).toHaveClass(/\berror\b/)
    await expect(errRow.locator('.act-glyph')).toHaveText('✗')
  })

  test('severity chips filter the visible rows', async ({ page }) => {
    await page.goto('/#slots')
    await waitForSse(page, STREAM, 6_000)

    await emitSse(page, STREAM, frame(OK_REC))
    await emitSse(page, STREAM, frame(ERR_REC))
    await emitSse(page, STREAM, frame(WARN_REC))
    await expect(page.locator('[data-testid="act-row"]')).toHaveCount(3)

    // Click the `error` chip — only the error row should remain (client-side
    // residual filter applies immediately to the ring).
    await page.locator('[data-testid="act-sev-error"]').click()
    const rows = page.locator('[data-testid="act-row"]')
    await expect(rows).toHaveCount(1)
    await expect(rows.first()).toHaveAttribute('data-severity', 'error')

    // Back to All restores every row.
    await page.locator('[data-testid="act-sev-all"]').click()
    await expect(page.locator('[data-testid="act-row"]')).toHaveCount(3)
  })

  test('export buttons present + honour the active severity filter', async ({ page }) => {
    await page.goto('/#slots')
    const csv = page.locator('[data-testid="act-export-csv"]')
    const json = page.locator('[data-testid="act-export-json"]')
    await expect(csv).toBeVisible({ timeout: 6_000 })
    await expect(json).toBeVisible()
    await expect(csv).toHaveAttribute('href', /\/api\/activity\/export\?.*fmt=csv/)
    await expect(json).toHaveAttribute('href', /\/api\/activity\/export\?.*fmt=json/)

    // Selecting a severity threads it into the export URL.
    await page.locator('[data-testid="act-sev-error"]').click()
    await expect(csv).toHaveAttribute('href', /severity=error/)
  })

  test('body is a BOUNDED scroll container (not an infinite firehose)', async ({ page }) => {
    await page.goto('/#slots')
    const body = page.locator('[data-testid="activity-log-body"]')
    await expect(body).toBeVisible({ timeout: 6_000 })
    const overflowY = await body.evaluate((el) => getComputedStyle(el).overflowY)
    expect(['auto', 'scroll']).toContain(overflowY)
    const maxH = await body.evaluate((el) => getComputedStyle(el).maxHeight)
    // A real px cap (not "none") proves the pane is bounded.
    expect(maxH).not.toBe('none')
    expect(parseFloat(maxH)).toBeGreaterThan(0)
  })
})
