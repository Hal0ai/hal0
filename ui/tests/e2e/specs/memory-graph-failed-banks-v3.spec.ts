/**
 * memory-graph-failed-banks-v3 (#3241, #3326) — Settings ▸ Data ▸ Memory names
 * the banks whose Hindsight operations failed.
 *
 * `GET /api/memory/graph/status` carries `failed_by_bank` (`{bank_id: count}`,
 * only banks with failures — src/hal0/api/routes/memory.py
 * `_augment_build_counters`) beside the summed `errors`. The panel renders one
 * warning per failing bank, largest first. An API that predates the field (or
 * a status with no failures) renders none.
 */
import { test, expect, json } from '../fixtures/apiMock'

const STATUS = {
  enabled: true,
  extraction_slot: 'utility',
  slot_resolves: true,
  available_slots: ['utility', 'agent'],
  llm_timeout_s: 300,
  extraction_limits: { max_concurrent: 1, max_tokens: 4096, llm_retries: 1, task_retries: 2, retry_backoff_s: 120 },
  in_flight: 0,
  builds_ok: 3,
  errors: 0,
  last_built_at: null,
  last_error: null,
}

test.describe('Memory graph: failed operations by bank', () => {
  test('names each failing bank, largest first, with singular and plural wording', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, { ...STATUS, errors: 7231, failed_by_bank: { private__hermes: 1, agent__coder: 7230 } }),
    )
    await page.goto('/#settings/memory')

    const panel = page.getByTestId('mem-graph-bank-failures')
    await expect(panel).toBeVisible()
    await expect(panel.getByText(/has \d+ failed operation/)).toHaveText([
      'Bank "agent__coder" has 7230 failed operations.',
      'Bank "private__hermes" has 1 failed operation.',
    ])
  })

  test('shows no bank warning when no bank has failures', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, { ...STATUS, failed_by_bank: {} }),
    )
    await page.goto('/#settings/memory')

    // The panel rendered from this status (the limits echo is a value only it carries) ...
    await expect(page.getByTestId('mem-graph-limit-max_tokens')).toHaveValue('4096')
    // ... and no failure section sits in it.
    await expect(page.getByTestId('mem-graph-bank-failures')).toHaveCount(0)
  })

  test('an API without failed_by_bank shows no bank warning', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) => json(route, { ...STATUS, errors: 4 }))
    await page.goto('/#settings/memory')

    await expect(page.getByTestId('mem-graph-limit-max_tokens')).toHaveValue('4096')
    await expect(page.getByTestId('mem-graph-bank-failures')).toHaveCount(0)
  })
})
