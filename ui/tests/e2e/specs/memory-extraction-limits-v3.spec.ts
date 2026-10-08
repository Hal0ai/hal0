/**
 * memory-extraction-limits-v3 (#1834) — Settings ▸ Data ▸ Memory, the graph
 * panel's extraction limits.
 *
 * The five [memory.graph].extraction_* knobs cap how hard Hindsight drives the
 * shared extraction slot. They are echoed by GET /api/memory/graph/status and
 * saved through the same PUT /api/memory/graph as the slot and timeout. This
 * spec pins: rendered from status, an out-of-range value blocks the save, and a
 * save carries only the limits that changed, under their PUT keys.
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

test.describe('Memory graph extraction limits', () => {
  test('renders the limits from status and saves only what changed', async ({ page }) => {
    let putBody: Record<string, unknown> | null = null
    await page.route('**/api/memory/graph/status', (route) => json(route, STATUS))
    await page.route('**/api/memory/graph', async (route) => {
      putBody = route.request().postDataJSON()
      await route.fulfill({ json: { ...STATUS, ...(putBody as object), propagation: { written: true, restarted: true, error: null } } })
    })
    await page.goto('/#settings/memory')

    const concurrent = page.getByTestId('mem-graph-limit-max_concurrent')
    const tokens = page.getByTestId('mem-graph-limit-max_tokens')
    await expect(concurrent).toHaveValue('1')
    await expect(tokens).toHaveValue('4096')
    await expect(page.getByTestId('mem-graph-limit-retry_backoff_s')).toHaveValue('120')
    // The status mock is what populated the panel (a value only it carries).
    await expect(page.locator('input[type="number"][min="30"]')).toHaveValue('300')
    const save = page.getByTestId('mem-graph-save')
    await expect(save).toBeDisabled()

    // Out of range (above 8): dirty, but the save stays off.
    await concurrent.fill('9')
    await expect(save).toBeDisabled()

    await concurrent.fill('2')
    await expect(save).toBeEnabled()
    await save.click()

    await expect.poll(() => putBody).toMatchObject({ enabled: true, extraction_slot: 'utility', extraction_max_concurrent: 2 })
    expect(putBody).not.toHaveProperty('extraction_max_tokens')
    expect(putBody).not.toHaveProperty('extraction_retry_backoff_s')
  })

  test('an older API without the limits still renders the defaults', async ({ page }) => {
    const { extraction_limits: _omit, ...legacy } = STATUS
    await page.route('**/api/memory/graph/status', (route) => json(route, legacy))
    await page.goto('/#settings/memory')
    await expect(page.getByTestId('mem-graph-limit-max_tokens')).toHaveValue('4096')
    await expect(page.getByTestId('mem-graph-save')).toBeDisabled()
  })
})
