/**
 * agent-unit-liveness-v3 — #1459.
 *
 * `GET /api/agents` returns `status:"installed"` for an agent whose bundle is
 * on disk. That is an install-state, not a liveness signal — the Agents
 * overview treated it as "running", so a box with
 * `hal0-agent@hermes.service` inactive still showed Hermes as ready.
 *
 * The payload now carries `unit_active` (true / false / null-for-unknown) and
 * the card's dot derives from it:
 *   installed + unit_active:true   → ready (or serving when the slot is hot)
 *   installed + unit_active:false  → down, with the Restart action
 *   installed + unit_active:null   → unknown — never rendered as healthy
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'

const FIVE_S = 5_500

function hermes(unitActive: boolean | null) {
  return {
    name: 'hermes',
    installed_at: '2026-07-30T00:00:00Z',
    status: 'installed',
    data_dir: '/var/lib/hal0/agents/hermes',
    config_path: '/etc/hal0/agents/hermes.toml',
    unit_active: unitActive,
  }
}

/** A resident-but-idle chat slot — keeps `_derive` off the "serving" upgrade
 *  so the assertions read the agent-liveness signal, not slot traffic. */
const IDLE_PRIMARY = {
  name: 'primary',
  type: 'llm',
  group: 'chat',
  isDefault: true,
  state: 'ready',
  model: 'qwen3-30b',
  model_id: 'qwen3-30b',
  container_status: 'running',
  ctx_max: 32_000,
  metrics: { toks: 0, ctx: 0 },
}

async function mockAgents(page: Page, unitActive: boolean | null) {
  await page.route('**/api/agents', (route) =>
    json(route, { agents: [hermes(unitActive)], count: 1 }),
  )
  // /api/slots is served from window.HAL0_DATA by the in-bundle mock layer,
  // so it has to be seeded there rather than via page.route.
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() {
        return real
      },
      set(v) {
        real = v
        if (v && typeof v === 'object') v.slots = slots
      },
    })
  }, [IDLE_PRIMARY])
  // The Memory tab's live endpoints — keep the shell hermetic.
  await page.route('**/api/memory/graph/status', (route) =>
    json(route, { enabled: false, route: 'upstream' }),
  )
  await page.route('**/api/memory/search', (route) => json(route, { items: [] }))
}

const hermesCard = (page: Page) => page.locator('[data-testid="agent-card-hermes"]')

test.describe('Agent status reflects unit liveness (#1459)', () => {
  test('installed + inactive unit renders DOWN, not ready', async ({ page }) => {
    await mockAgents(page, false)
    await page.goto('/#agent')

    const card = hermesCard(page)
    await expect(card).toBeVisible({ timeout: FIVE_S })
    await expect(card.locator('.fc-st')).toContainText('down', { timeout: FIVE_S })
    await expect(card.locator('.fc-st')).not.toContainText('ready')
    // The error dot, so the colour matches the words.
    await expect(card.locator('.fc-st .sdot.error')).toBeVisible()
    // The operator gets an actionable way out (card back, one tap away).
    await expect(card.getByTestId('agent-action-restart')).toHaveCount(1)
  })

  test('installed + active unit renders ready', async ({ page }) => {
    await mockAgents(page, true)
    await page.goto('/#agent')

    const card = hermesCard(page)
    await expect(card).toBeVisible({ timeout: FIVE_S })
    await expect(card.locator('.fc-st')).toContainText('ready', { timeout: FIVE_S })
    await expect(card.locator('.fc-st')).not.toContainText('down')
  })

  test('an unavailable probe renders UNKNOWN, never healthy', async ({ page }) => {
    await mockAgents(page, null)
    await page.goto('/#agent')

    const card = hermesCard(page)
    await expect(card).toBeVisible({ timeout: FIVE_S })
    await expect(card.locator('.fc-st')).toContainText('unknown', { timeout: FIVE_S })
    await expect(card.locator('.fc-st')).not.toContainText('ready')
    await expect(card.locator('.fc-st .sdot.serving')).toHaveCount(0)
  })
})
