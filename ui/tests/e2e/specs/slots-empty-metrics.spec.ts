/**
 * slots-empty-metrics — regression guard for the flash-then-crash on the
 * Slots + Dashboard pages. Backend returns slot rows WITHOUT a `metrics`
 * field; the React tree must not unmount.
 *
 * Root cause: `s.metrics.toks` in SnapshotStrip / SlotCard threw
 * TypeError when `metrics` was undefined. Fixed by `normalizeSlot` in
 * the useSlots hook (defaults the metrics shape).
 */
import { test, expect } from '../fixtures/apiMock'
import { MOCK_DATA, json } from '../fixtures/apiMock'

// Strip `metrics` + `spark` from every slot so we mimic a real backend
// that doesn't ship the prototype's mock-only metrics envelope.
function slotsWithoutMetrics() {
  return MOCK_DATA.slots.map((s: any) => {
    const { metrics, spark, ...bare } = s
    return bare
  })
}

test.describe('Slots — empty metrics (defensive normalizer)', () => {
  test.beforeEach(async ({ page }) => {
    const bare = slotsWithoutMetrics()
    // Override the default mocks: same routes, bare slots.
    await page.route('**/api/status', (route) =>
      json(route, {
        version: MOCK_DATA.lemond.version,
        update_available: false,
        slots: bare,
        hardware: MOCK_DATA.host,
      }),
    )
    await page.route('**/api/slots', (route) => json(route, { slots: bare }))
  })

  test('Dashboard renders without JS errors on metrics-less slots', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (err) => errors.push(err.message))

    await page.goto('/')
    await expect(page.locator('.topbar')).toBeVisible()
    await expect(page.locator('.main .view')).toBeVisible()
    await expect(page.locator('.snap')).toBeVisible()
    // Let React Query settle a couple of refetch cycles.
    await page.waitForTimeout(500)

    // Filter out the Vite dev-server HMR WebSocket failure (config points
    // HMR at hal0.thinmint.dev, which is unreachable from CI/headless).
    // We only care about app-level crashes.
    const appErrors = errors.filter((m) => !/WebSocket/.test(m))
    expect(appErrors, appErrors.join('\n')).toHaveLength(0)
  })

  test('Slots view renders cards without crashing', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (err) => errors.push(err.message))

    await page.goto('/#slots')
    await expect(page.locator('.view .vh h1')).toHaveText('Slots')
    await expect(page.locator('.view .sec h2').first()).toBeVisible()
    const cards = page.locator('.slots-grid > *, .slots-list > *')
    expect(await cards.count()).toBeGreaterThan(0)
    await page.waitForTimeout(500)

    // Filter out the Vite dev-server HMR WebSocket failure (config points
    // HMR at hal0.thinmint.dev, which is unreachable from CI/headless).
    // We only care about app-level crashes.
    const appErrors = errors.filter((m) => !/WebSocket/.test(m))
    expect(appErrors, appErrors.join('\n')).toHaveLength(0)
  })
})
