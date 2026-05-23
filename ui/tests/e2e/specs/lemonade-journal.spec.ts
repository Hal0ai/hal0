/**
 * lemonade-journal.spec.ts — PR-14 Lemonade daemon log panel.
 *
 * Covers (plan §11 PR-14 + §2.2):
 *   - Tab switch from systemd → Lemonade renders the journal pane
 *   - Streamed entries from /api/lemonade/logs/stream appear with the
 *     correct severity class (info default, warning amber, error red)
 *   - Filter input narrows visible lines (substring match on ``line``)
 *   - Auto-scroll toggle is wired
 *   - Clear-buffer empties the buffer
 *
 * Backend contract (PR-11, src/hal0/api/routes/lemonade_logs.py):
 * each SSE data frame is JSON of one lemond log entry; shape
 * ``{line, severity, tag, timestamp, seq}`` per the
 * hal0_lemonade_ws_protocol memory.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { emitSse, installSseHarness, waitForSse } from '../fixtures/sseHarness'

test.beforeEach(async ({ page }) => {
  await installSseHarness(page)
})

test('renders streamed Lemonade log entries with severity classes', async ({
  page,
  mockState,
  cleanState,
}) => {
  // The Logs view also bootstraps the systemd tab — keep its mocks
  // happy so we don't get console noise in the harness.
  await page.route('**/api/logs*', (route) => {
    if (route.request().url().includes('/api/logs/stream')) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    }
    return json(route, { unit: '', lines: [], count: 0 })
  })

  await page.goto('/logs?tab=lemonade')
  await expect(page.getByTestId('lemonade-journal')).toBeVisible()

  // The journal panel opens its own EventSource on mount.
  await waitForSse(page, '/api/lemonade/logs/stream', 4000)

  // Drive a sequence covering all three severity → colour mappings.
  await emitSse(page, '/api/lemonade/logs/stream', {
    seq: 1,
    severity: 'Info',
    tag: 'Server',
    line: 'lemond ready on port 13305',
  })
  await emitSse(page, '/api/lemonade/logs/stream', {
    seq: 2,
    severity: 'Warning',
    tag: 'Router',
    line: 'evicting qwen3-1b to free budget',
  })
  await emitSse(page, '/api/lemonade/logs/stream', {
    seq: 3,
    severity: 'Error',
    tag: 'Backend',
    line: 'failed to load model x',
  })

  const lines = page.getByTestId('lemonade-journal-line')
  await expect(lines).toHaveCount(3)
  // First line is INFO → no severity-colour class (default).
  await expect(lines.nth(0)).toContainText('lemond ready on port 13305')
  await expect(lines.nth(0)).toContainText('[Server]')
  // Warning line picks up log-line-warn.
  await expect(lines.nth(1)).toHaveClass(/log-line-warn/)
  // Error line picks up log-line-error.
  await expect(lines.nth(2)).toHaveClass(/log-line-error/)
})

test('filter input narrows visible lines', async ({ page, mockState, cleanState }) => {
  await page.route('**/api/logs*', (route) => {
    if (route.request().url().includes('/api/logs/stream')) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    }
    return json(route, { unit: '', lines: [], count: 0 })
  })

  await page.goto('/logs?tab=lemonade')
  await waitForSse(page, '/api/lemonade/logs/stream', 4000)

  for (const line of ['alpha bravo', 'bravo charlie', 'charlie delta']) {
    await emitSse(page, '/api/lemonade/logs/stream', { line, severity: 'Info' })
  }
  const lines = page.getByTestId('lemonade-journal-line')
  await expect(lines).toHaveCount(3)

  // Substring filter on the canonical ``line`` field.
  await page.getByTestId('lemonade-journal-filter').fill('bravo')
  await expect(lines).toHaveCount(2)
  await expect(lines.nth(0)).toContainText('alpha bravo')
  await expect(lines.nth(1)).toContainText('bravo charlie')

  // Empty filter restores all rows.
  await page.getByTestId('lemonade-journal-filter').fill('')
  await expect(lines).toHaveCount(3)
})

test('clear button empties the journal buffer', async ({ page, mockState, cleanState }) => {
  await page.route('**/api/logs*', (route) => {
    if (route.request().url().includes('/api/logs/stream')) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    }
    return json(route, { unit: '', lines: [], count: 0 })
  })

  await page.goto('/logs?tab=lemonade')
  await waitForSse(page, '/api/lemonade/logs/stream', 4000)

  for (let i = 0; i < 5; i++) {
    await emitSse(page, '/api/lemonade/logs/stream', {
      line: `entry-${i}`,
      severity: 'Info',
    })
  }
  await expect(page.getByTestId('lemonade-journal-line')).toHaveCount(5)

  await page.getByTestId('lemonade-journal-clear').click()
  await expect(page.getByTestId('lemonade-journal-line')).toHaveCount(0)
})

test('auto-scroll toggle is wired and reflects checkbox state', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.route('**/api/logs*', (route) => {
    if (route.request().url().includes('/api/logs/stream')) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    }
    return json(route, { unit: '', lines: [], count: 0 })
  })

  await page.goto('/logs?tab=lemonade')
  await waitForSse(page, '/api/lemonade/logs/stream', 4000)

  // Default state: auto-scroll on.
  const toggle = page.getByTestId('lemonade-journal-autoscroll')
  await expect(toggle).toBeChecked()

  // Operator opt-out — the user can stop the viewport from jumping.
  await toggle.uncheck()
  await expect(toggle).not.toBeChecked()

  // Lines still arrive even with auto-scroll disabled — the toggle
  // only governs scroll-pin, not buffer ingestion.
  await emitSse(page, '/api/lemonade/logs/stream', {
    line: 'still ingesting',
    severity: 'Info',
  })
  await expect(page.getByTestId('lemonade-journal-line')).toHaveCount(1)
})

test('switching tabs from systemd to lemonade reveals the journal pane', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.route('**/api/logs*', (route) => {
    if (route.request().url().includes('/api/logs/stream')) {
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' })
    }
    return json(route, { unit: '', lines: [], count: 0 })
  })

  // Default landing on /logs == systemd tab; the journal pane is hidden.
  await page.goto('/logs')
  await expect(page.getByTestId('lemonade-journal')).toHaveCount(0)
  // The existing filters bar (unit selector) is visible.
  await expect(page.locator('#log-unit-filter')).toBeVisible()

  // Tab into Lemonade — pane appears, filters bar disappears.
  await page.getByTestId('logs-tab-lemonade').click()
  await expect(page.getByTestId('lemonade-journal')).toBeVisible()
  await expect(page.locator('#log-unit-filter')).toHaveCount(0)
})
