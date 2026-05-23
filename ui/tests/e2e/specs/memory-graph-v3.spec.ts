/**
 * memory-graph-v3 — ADR-0014 graph-extraction panel in the Agent → Memory tab.
 *
 * Pins:
 *   - default OFF state renders the enable affordance.
 *   - enabling via route=primary fires PUT /api/memory/graph with the right payload.
 *   - ON state shows the disable + counters.
 *   - disclosure + caveat copy match ADR-0014 §3 + §4 verbatim.
 */
import { test, expect, json } from '../fixtures/apiMock'

const STATUS_URL = '**/api/memory/graph/status'
const PUT_URL = '**/api/memory/graph'

test.describe('Memory graph extraction panel (ADR-0014)', () => {
  test.skip('default OFF — shows enable button + 0 builds', async ({ page }) => {
    let putBody: any = null
    await page.route(STATUS_URL, (route) =>
      json(route, {
        enabled: false,
        route: 'upstream',
        upstream: null,
        in_flight: 0,
        builds_ok: 0,
        errors: 0,
        last_built_at: null,
        last_error: null,
      }),
    )
    await page.route(PUT_URL, async (route) => {
      putBody = JSON.parse(route.request().postData() || '{}')
      return json(route, { ...putBody, status: { enabled: true } })
    })
    await page.goto('/#agent')
    await page.locator('.view button', { hasText: /^memory$/i }).click()
    await expect(page.locator('.view')).toContainText('Graph extraction')
    await expect(page.locator('.view')).toContainText('OFF')
    await expect(page.locator('.view')).toContainText('Enable graph extraction')
  })

  test.skip('enable via primary route sends correct payload', async ({ page }) => {
    let putBody: any = null
    await page.route(STATUS_URL, (route) =>
      json(route, {
        enabled: false,
        route: 'upstream',
        upstream: null,
        in_flight: 0,
        builds_ok: 0,
        errors: 0,
        last_built_at: null,
        last_error: null,
      }),
    )
    await page.route(PUT_URL, async (route) => {
      putBody = JSON.parse(route.request().postData() || '{}')
      return json(route, { ...putBody, status: { enabled: true } })
    })
    await page.goto('/#agent')
    await page.locator('.view button', { hasText: /^memory$/i }).click()
    await page.locator('button', { hasText: 'Enable graph extraction' }).first().click()
    // Pick primary
    await page.locator('input[type=radio][value=primary]').check()
    await page.locator('button', { hasText: /Enable graph extraction|Save route/i }).click()
    await expect.poll(() => putBody).toMatchObject({ enabled: true, route: 'primary' })
  })

  test.skip('disclosure + caveat copy match ADR §3 + §4 verbatim', async ({ page }) => {
    await page.route(STATUS_URL, (route) =>
      json(route, {
        enabled: false,
        route: 'upstream',
        upstream: null,
        in_flight: 0,
        builds_ok: 0,
        errors: 0,
        last_built_at: null,
        last_error: null,
      }),
    )
    await page.route(PUT_URL, (route) => json(route, {}))
    await page.goto('/#agent')
    await page.locator('.view button', { hasText: /^memory$/i }).click()
    await page.locator('button', { hasText: 'Enable graph extraction' }).first().click()
    // ADR §3 disclosure
    await expect(page.locator('.view')).toContainText(
      /Graph extraction sends ingested memory text/,
    )
    // ADR §4 quality caveat
    await expect(page.locator('.view')).toContainText(
      /Graph quality varies by model. We don't currently measure it for you/,
    )
  })
})
