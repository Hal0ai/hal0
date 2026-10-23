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
 *
 * Two sources feed that word, and each case runs against both:
 *   - `overview`      — GET /api/agents/hermes/overview names the `phase` (the
 *                       server's face for the same unit state) and wins
 *   - `agents record` — a daemon that predates the overview route answers 404,
 *                       and the card falls back to `unit_active` on the
 *                       /api/agents record alone
 */
import { test, expect, json, seedSlots, type Page } from '../fixtures/apiMock'
import type { AgentOverview } from '../../../src/api/hooks/useAgents'

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

/** The overview the server sends for the same unit state: `phase` agrees with
 *  `unit_active` (active → ready, inactive → down, unreadable → unknown). */
function hermesOverview(unitActive: boolean | null): AgentOverview {
  const unit = unitActive === true ? 'active' : unitActive === false ? 'inactive' : 'unknown'
  return {
    name: 'hermes',
    kind: 'daemon',
    installed: true,
    status: 'installed',
    unit_active: unitActive,
    generated_at: new Date().toISOString(),
    phase: unitActive === true ? 'ready' : unitActive === false ? 'down' : 'unknown',
    degraded: [],
    units: { agent: unit, gateway: unit, gateway_enabled: true },
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

type Source = 'overview' | 'agents record'

async function mockAgents(page: Page, unitActive: boolean | null, source: Source) {
  await page.route('**/api/agents', (route) =>
    json(route, { agents: [hermes(unitActive)], count: 1 }),
  )
  await page.route('**/api/agents/hermes/overview*', (route) =>
    source === 'overview'
      ? json(route, hermesOverview(unitActive))
      : json(route, { error: { code: 'not_found', message: 'no such route' } }, 404),
  )
  // /api/slots is served from window.HAL0_DATA by the in-bundle mock layer,
  // so it has to be seeded there rather than via page.route.
  await seedSlots(page, [IDLE_PRIMARY])
  // The Memory tab's live endpoints — keep the shell hermetic.
  await page.route('**/api/memory/graph/status', (route) =>
    json(route, { enabled: false, route: 'upstream' }),
  )
  await page.route('**/api/memory/search', (route) => json(route, { items: [] }))
}

const hermesCard = (page: Page) => page.locator('[data-testid="agent-card-hermes"]')

for (const source of ['overview', 'agents record'] as const) {
  test.describe(`Agent status reflects unit liveness (#1459) — from the ${source}`, () => {
    test('installed + inactive unit renders DOWN, not ready', async ({ page }) => {
      await mockAgents(page, false, source)
      await page.goto('/#agent')

      const card = hermesCard(page)
      await expect(card).toBeVisible({ timeout: FIVE_S })
      await expect(card.locator('.fc-st')).toHaveText(/^down$/, { timeout: FIVE_S })
      await expect(card.locator('.fc-st')).not.toContainText('ready')
      // The error dot, so the colour matches the words.
      await expect(card.locator('.fc-st .sdot.error')).toBeVisible()
      // The operator gets an actionable way out (Start, on the down face).
      await expect(card.getByTestId('agent-action-restart')).toHaveCount(1)
    })

    test('installed + active unit renders ready', async ({ page }) => {
      await mockAgents(page, true, source)
      await page.goto('/#agent')

      const card = hermesCard(page)
      await expect(card).toBeVisible({ timeout: FIVE_S })
      await expect(card.locator('.fc-st')).toHaveText(/^ready$/, { timeout: FIVE_S })
      await expect(card.locator('.fc-st')).not.toContainText('down')
    })

    test('an unavailable probe renders UNKNOWN, never healthy', async ({ page }) => {
      await mockAgents(page, null, source)
      await page.goto('/#agent')

      const card = hermesCard(page)
      await expect(card).toBeVisible({ timeout: FIVE_S })
      await expect(card.locator('.fc-st')).toHaveText(/^unknown$/, { timeout: FIVE_S })
      await expect(card.locator('.fc-st')).not.toContainText('ready')
      await expect(card.locator('.fc-st .sdot.serving')).toHaveCount(0)
    })
  })
}
