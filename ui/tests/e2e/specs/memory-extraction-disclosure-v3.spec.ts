/**
 * memory-extraction-disclosure-v3 — Settings ▸ Data ▸ Memory, what graph extraction sends where.
 *
 * The extraction disclosure (ADR-0023 §3 and §4) used to sit in the engine panel of the old
 * Memory overview, which is deleted. It lives beside the extraction control now: with a local
 * slot it names the slot and says the text stays on the box; with an upstream model the Engine
 * model row already says the text leaves the box, so only the quality caveat shows. The caveat
 * shows either way.
 */
import { test, expect, json } from '../fixtures/apiMock'

const STATUS = {
  enabled: true,
  extraction_slot: 'memory',
  slot_resolves: true,
  available_slots: ['memory', 'agent'],
  llm_timeout_s: 300,
  in_flight: 0,
  builds_ok: 3,
  errors: 0,
  last_built_at: null,
  last_error: null,
}

const QUALITY = "Graph quality varies by model. We don't currently measure it for you — your results may vary."

test.describe('graph extraction disclosure', () => {
  test('a local slot: names the slot, says the text stays on the box, and carries the caveat', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) => json(route, STATUS))
    await page.goto('/#settings/memory')

    const routing = page.getByTestId('mem-graph-disclosure-routing')
    await expect(routing).toContainText('Graph extraction sends ingested memory text to the memory slot')
    await expect(routing).toContainText('Everything stays on-box')
    await expect(page.getByTestId('mem-graph-disclosure-quality')).toHaveText(QUALITY)
  })

  test('the slot named follows the picker', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) => json(route, STATUS))
    await page.goto('/#settings/memory')
    await page.getByTestId('mem-graph-target').selectOption('slot:agent')
    await expect(page.getByTestId('mem-graph-disclosure-routing')).toContainText('to the agent slot')
  })

  test('an upstream model: no on-box claim, the caveat stays', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, {
        ...STATUS,
        extraction_model: 'MiniMax-M2.5',
        available_models: [{ id: 'MiniMax-M2.5', owned_by: 'minimax' }],
      }),
    )
    await page.goto('/#settings/memory')
    await expect(page.getByTestId('mem-graph-disclosure-quality')).toHaveText(QUALITY)
    await expect(page.getByTestId('mem-graph-disclosure-routing')).toHaveCount(0)
    await expect(page.getByTestId('mem-graph-disclosure')).not.toContainText('on-box')
  })
})

test.describe('engine env drift', () => {
  const WARNING =
    'hindsight-api runs with HINDSIGHT_API_LLM_TIMEOUT=120 (hal0 renders 300): another drop-in overrides it'

  test('the status warning shows beside Engine running', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, {
        ...STATUS,
        engine_env: {
          checked: true,
          drift: [{ key: 'HINDSIGHT_API_LLM_TIMEOUT', expected: '300', effective: '120' }],
          legacy_drop_in: null,
          warning: WARNING,
        },
      }),
    )
    await page.goto('/#settings/memory')
    await expect(page.getByTestId('mem-graph-engine-env-warning')).toContainText(WARNING)
  })

  test('a clean env, or an API that does not report one, shows nothing', async ({ page }) => {
    await page.route('**/api/memory/graph/status', (route) =>
      json(route, { ...STATUS, engine_env: { checked: true, drift: [], legacy_drop_in: null, warning: null } }),
    )
    await page.goto('/#settings/memory')
    await expect(page.getByTestId('mem-graph-engine-running')).toBeVisible()
    await expect(page.getByTestId('mem-graph-engine-env-warning')).toHaveCount(0)
  })
})
