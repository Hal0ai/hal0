/**
 * memory-chunk-check-v3 (#3326) — Settings ▸ Data ▸ Memory, why a bank fails.
 *
 * hindsight-api refuses every retain into a bank whose chunk size is not below
 * [memory.graph].extraction_max_tokens. GET /api/memory/graph/status now carries
 * `chunk_check`, the audit of the banks that have failed operations. This spec
 * pins: a `warn` verdict shows its message above the per-bank notes, and an `ok`
 * verdict (or an API without the field) shows only the per-bank notes.
 */
import { test, expect, json } from '../fixtures/apiMock'

const BANK = 'coding-agent::hal0-mono'
const MESSAGE =
  `[memory.graph].extraction_max_tokens = 4096 is not above the retain chunk size of 1 bank(s): ${BANK} (12000). ` +
  "The engine refuses every retain into them. Raise extraction_max_tokens above 12000 (16384 fits the plugin's 12000), then use 'Retry failed'."

const STATUS = {
  enabled: true,
  extraction_slot: 'utility',
  slot_resolves: true,
  available_slots: ['utility'],
  llm_timeout_s: 300,
  extraction_limits: { max_concurrent: 1, max_tokens: 4096, llm_retries: 1, task_retries: 2, retry_backoff_s: 120 },
  in_flight: 0,
  builds_ok: 3,
  errors: 7300,
  failed_by_bank: { [BANK]: 7300 },
  last_built_at: null,
  last_error: null,
}

test.describe('Memory graph chunk check', () => {
  test('a warn verdict explains the failing bank', async ({ page }) => {
    const chunk_check = {
      verdict: 'warn',
      max_tokens: 4096,
      checked: 1,
      skipped: 0,
      conflicts: [{ bank_id: BANK, chunk_size: 12000 }],
      message: MESSAGE,
    }
    await page.route('**/api/memory/graph/status', (route) => json(route, { ...STATUS, chunk_check }))
    await page.goto('/#settings/memory')

    const cause = page.getByTestId('mem-graph-chunk-check')
    await expect(cause).toContainText('is not above the retain chunk size')
    await expect(cause).toContainText('16384')
    await expect(page.getByTestId('mem-graph-bank-failures')).toContainText(`Bank "${BANK}" has 7300 failed operations.`)
  })

  test('an ok verdict, or an older API, shows only the per-bank notes', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, { ...STATUS, chunk_check: { verdict: 'ok', max_tokens: 16384, checked: 1, skipped: 0, conflicts: [], message: null } }),
    )
    await page.goto('/#settings/memory')
    await expect(page.getByTestId('mem-graph-bank-failures')).toContainText('7300 failed operations')
    await expect(page.getByTestId('mem-graph-chunk-check')).toHaveCount(0)
  })
})
