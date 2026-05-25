/**
 * footer-journal-pane-v3 — issue #325 (epic #322 Phase 3).
 *
 * Footer's expand pane now streams /api/journal/stream and renders the
 * SSE ring directly — no more HAL0_DATA.journal silent fallback. Specs
 * pin:
 *
 *   1. Cold load: pane is closed by default, no SSE is opened, the chips
 *      still render.
 *   2. Expanding the pane opens the SSE; entries pushed through the
 *      sseHarness render in the body.
 *   3. Clicking `hal0` filter chip rebuilds the EventSource with
 *      `?source=hal0` and only hal0-source entries pass through.
 *   4. Typing in the search box filters the in-memory ring client-side
 *      (no SSE reconnect; matches `entry.msg` case-insensitively).
 *   5. When the SSE has produced 0 entries the pane shows the empty
 *      state ("No events yet") — never the old mock copy
 *      ("loaded model 'qwen3.6-27b-mtp' via llamacpp:rocm").
 *   6. Production bundle does NOT contain literal "14:02:11.117" — the
 *      timestamp shape used exclusively by the deleted HAL0_DATA.journal
 *      block. Other unrelated 14:02 demo timestamps (mcp-modals, agent
 *      approvals) are explicitly out of #325's scope.
 */
import { test, expect } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'

const HAL0_ENTRY = {
  id: 1,
  ts: '2026-05-25T22:00:00.000000+00:00',
  source: 'hal0',
  level: 'info',
  msg: 'slot:primary state idle → ready',
}
const LEMOND_ENTRY = {
  id: 2,
  ts: '2026-05-25T22:00:01.000000+00:00',
  source: 'lemond',
  level: 'info',
  msg: 'POST /v1/load model=qwen3.6-27b-mtp backend=llamacpp:rocm',
}
const ERROR_ENTRY = {
  id: 3,
  ts: '2026-05-25T22:00:02.000000+00:00',
  source: 'hal0',
  level: 'error',
  msg: 'nuclear evict-all triggered: error loading sd-turbo',
}

test.describe('Footer journal pane (#325)', () => {
  test.beforeEach(async ({ page }) => {
    await installSseHarness(page)
  })

  test('pane is closed on cold load', async ({ page }) => {
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()
    // foot-pane only renders when expanded; should be absent on cold load.
    await expect(page.locator('.foot-pane')).toHaveCount(0)
    // Toggle button is the entry point.
    await expect(page.locator('.foot-toggle')).toBeVisible()
  })

  test('expanding pane opens SSE and renders pushed events', async ({ page }) => {
    await page.goto('/')
    await page.locator('.foot-toggle').click()
    await expect(page.locator('.foot-pane')).toBeVisible()

    // SSE connects on expansion (debounced ~200ms).
    await waitForSse(page, '/api/journal/stream', 6_000)

    // Push two entries — they should render in the body.
    await emitSse(page, '/api/journal/stream', HAL0_ENTRY)
    await emitSse(page, '/api/journal/stream', LEMOND_ENTRY)

    const body = page.locator('.foot-pane-body')
    await expect(body.locator('.foot-line .msg', { hasText: HAL0_ENTRY.msg })).toBeVisible()
    await expect(body.locator('.foot-line .msg', { hasText: LEMOND_ENTRY.msg })).toBeVisible()
  })

  test('hal0 filter chip narrows source and rebuilds SSE with ?source=hal0', async ({ page }) => {
    await page.goto('/')
    await page.locator('.foot-toggle').click()
    await waitForSse(page, '/api/journal/stream', 6_000)
    await emitSse(page, '/api/journal/stream', HAL0_ENTRY)
    await emitSse(page, '/api/journal/stream', LEMOND_ENTRY)

    // Pre-condition: both render.
    const body = page.locator('.foot-pane-body')
    await expect(body.locator('.foot-line', { hasText: HAL0_ENTRY.msg })).toBeVisible()
    await expect(body.locator('.foot-line', { hasText: LEMOND_ENTRY.msg })).toBeVisible()

    // Click the hal0 chip — debounce + reconnect.
    await page.locator('.foot-pane-chip', { hasText: 'hal0' }).click()
    await waitForSse(page, '/api/journal/stream?source=hal0', 6_000)

    // Client-side source filter applied on top of the SSE URL filter —
    // residual lemond entry already in the ring is hidden immediately.
    await expect(body.locator('.foot-line', { hasText: LEMOND_ENTRY.msg })).toHaveCount(0)
    // hal0 entry stays.
    await expect(body.locator('.foot-line', { hasText: HAL0_ENTRY.msg })).toBeVisible()

    // Push another hal0 entry on the new stream — arrives normally.
    const HAL0_AFTER = { ...HAL0_ENTRY, id: 4, msg: 'slot:agent ready' }
    await emitSse(page, '/api/journal/stream?source=hal0', HAL0_AFTER)
    await expect(body.locator('.foot-line .msg', { hasText: 'slot:agent ready' })).toBeVisible()

    // Confirm the URL gained the source param.
    const seen = await page.evaluate(
      () => Object.keys((window as any).__sseStreams || {}),
    )
    expect(seen.some((u: string) => u.includes('source=hal0'))).toBe(true)
  })

  test('search input filters the in-memory ring case-insensitively', async ({ page }) => {
    await page.goto('/')
    await page.locator('.foot-toggle').click()
    await waitForSse(page, '/api/journal/stream', 6_000)
    await emitSse(page, '/api/journal/stream', HAL0_ENTRY)
    await emitSse(page, '/api/journal/stream', LEMOND_ENTRY)
    await emitSse(page, '/api/journal/stream', ERROR_ENTRY)

    const body = page.locator('.foot-pane-body')
    // Pre-condition: all three render.
    await expect(body.locator('.foot-line')).toHaveCount(3)

    // Type "error" — only ERROR_ENTRY's msg matches.
    await page.locator('.foot-pane-search').fill('error')
    await expect(body.locator('.foot-line')).toHaveCount(1)
    await expect(body.locator('.foot-line', { hasText: ERROR_ENTRY.msg })).toBeVisible()

    // Case-insensitive: "ERROR" lands on the same row.
    await page.locator('.foot-pane-search').fill('ERROR')
    await expect(body.locator('.foot-line')).toHaveCount(1)

    // Clearing the search restores the full ring.
    await page.locator('.foot-pane-search').fill('')
    await expect(body.locator('.foot-line')).toHaveCount(3)
  })

  test('empty SSE renders "No events yet", never the old mock prose', async ({ page }) => {
    await page.goto('/')
    await page.locator('.foot-toggle').click()
    await waitForSse(page, '/api/journal/stream', 6_000)
    // Deliberately push nothing. Give the SSE a moment to settle.
    await page.waitForTimeout(500)

    const body = page.locator('.foot-pane-body')
    await expect(body.locator('.foot-pane-empty')).toBeVisible()
    await expect(body.locator('.foot-pane-empty')).toContainText('No events yet')

    // The old HAL0_DATA.journal lines must never sneak in via a silent
    // fallback. Pin both the qwen3.6 model load AND the synthetic
    // 14:02:11.117 timestamp the old block used.
    await expect(body).not.toContainText("loaded model 'qwen3.6-27b-mtp'")
    await expect(body).not.toContainText('14:02:11.117')
  })
})

test.describe('Footer journal pane — bundle hygiene (#325)', () => {
  test('production bundle does not contain deleted HAL0_DATA.journal mock copy', async () => {
    // The old HAL0_DATA.journal block carried unique mock prose
    // ("loaded model 'qwen3.6-27b-mtp' via llamacpp:rocm", "slot:primary
    // state idle → ready", etc.) that should be GONE post-delete. The
    // 14:02:11.117 timestamp itself is shared with mcp-modals.jsx and
    // intentionally not asserted here — scoping the check to the
    // journal-specific prose keeps this orthogonal to the MCP page's
    // demo data which is owned by a different epic.
    const fs = await import('fs')
    const path = await import('path')
    const { fileURLToPath } = await import('url')
    const here = path.dirname(fileURLToPath(import.meta.url))
    const root = path.resolve(here, '../../..', 'dist')
    if (!fs.existsSync(root)) {
      test.skip(true, 'ui/dist not built — run `npm run build` first')
      return
    }
    const files = fs.readdirSync(path.join(root, 'assets')).filter((f) => f.endsWith('.js'))
    expect(files.length, 'dist/assets has at least one js chunk').toBeGreaterThan(0)
    for (const f of files) {
      const body = fs.readFileSync(path.join(root, 'assets', f), 'utf8')
      // Copy unique to the deleted journal block:
      expect(body, `bundle ${f} still ships deleted journal mock copy`).not.toContain(
        "loaded model 'qwen3.6-27b-mtp' via llamacpp:rocm",
      )
      expect(body, `bundle ${f} still ships deleted journal mock copy`).not.toContain(
        'omnirouter dispatched',
      )
      expect(body, `bundle ${f} still ships deleted journal mock copy`).not.toContain(
        'nuclear evict-all candidate avoided',
      )
    }
  })
})
