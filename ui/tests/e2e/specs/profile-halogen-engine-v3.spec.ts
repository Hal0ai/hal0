/**
 * profile-halogen-engine-v3 (ADR-0047) — a profile on the optional `halogen`
 * runner is tuned through typed engine controls, not llama arguments.
 *
 * The rows come from `backends.halogen.knobs` and the refused names from
 * `.knob_denied` on GET /api/system-info (hal0.runners.knobs). The controls and
 * the collapsed "Advanced: flags" text edit one string, which the POST carries as
 * `flags`. The knob list is shared with the vitest suite
 * (src/dash/__tests__/fixtures/halogen-knobs.ts) so the wire shape has one owner.
 */
import { test, expect, json } from '../fixtures/apiMock'
import { MOCK_DATA } from '../fixtures/mock-data'
import { HALOGEN_DENIED, HALOGEN_KNOBS } from '../../../src/dash/__tests__/fixtures/halogen-knobs'

const SYSTEM_INFO = {
  hardware: { gpus: [{ compute_capable: true, vulkan_capable: true }] },
  features: {},
  backends: {
    standard: {
      image: 'ghcr.io/hal0ai/hal0-runner-standard:1',
      runtime_family: 'llamacpp',
      device_class: 'gpu',
      backend: 'rocm',
      title: 'Standard',
      tier: 'default',
      supported_backends: ['rocm'],
      format_arch: 'gguf',
      state: 'installed',
    },
    halogen: {
      image: 'ghcr.io/peonist-ai/halogen-flash-server@sha256:0000',
      runtime_family: 'halogen',
      device_class: 'gpu',
      backend: 'rocm',
      title: 'halogen (Flash-Next)',
      tier: 'optional',
      supported_backends: ['rocm'],
      format_arch: 'hgn',
      knobs: HALOGEN_KNOBS,
      knob_denied: HALOGEN_DENIED,
      state: 'installed',
    },
  },
}

async function openNewProfile(page: any) {
  await page.route('**/api/system-info', (route: any) => json(route, SYSTEM_INFO))
  await page.route('**/api/profiles', (route: any) => json(route, MOCK_DATA.profiles ?? []))
  await page.goto('/#models/profiles')
  await page.waitForSelector('.stk-lib-card', { timeout: 10_000 })
  await page.click('[data-testid="pf-btn-new"]')
  await expect(page.locator('.pf-form-panel')).toBeVisible()
  await page.fill('[data-testid="pf-input-name"]', 'flash')
}

test.describe('Profile drawer — halogen engine block', () => {
  test('a llama.cpp runtime keeps the flags field; picking halogen swaps in the engine block', async ({ page }) => {
    await openNewProfile(page)
    await page.selectOption('[data-testid="profile-runner"]', 'standard')
    await expect(page.getByTestId('pf-input-flags')).toBeVisible()
    await expect(page.getByTestId('pf-engine-settings')).toHaveCount(0)

    await page.selectOption('[data-testid="profile-runner"]', 'halogen')
    await expect(page.getByTestId('pf-engine-settings')).toBeVisible()
    await expect(page.getByTestId('engine-input-HALOGEN_KV_SLOTS')).toBeVisible()
    // The raw flags text collapses behind "Advanced: flags".
    await expect(page.getByTestId('pf-input-flags')).toHaveCount(0)
    await page.getByTestId('pf-advanced-toggle').click()
    await expect(page.getByTestId('pf-input-flags')).toBeVisible()
  })

  test('the typed controls write HALOGEN_* tokens into the flags the POST carries', async ({ page }) => {
    const posts: any[] = []
    await openNewProfile(page)
    await page.route('**/api/profiles', async (route: any) => {
      if (route.request().method() !== 'POST') return json(route, MOCK_DATA.profiles ?? [])
      posts.push(route.request().postDataJSON())
      return json(route, { ...posts[0], seed: false, resolved_flags: posts[0].flags }, 201)
    })
    await page.selectOption('[data-testid="profile-runner"]', 'halogen')

    await page.getByTestId('engine-input-HALOGEN_KV_SLOTS').fill('4')
    await page.getByTestId('engine-switch-HALOGEN_KV8').click()
    await page.getByTestId('engine-opt-HALOGEN_MAX_TOK-32768').click()

    // Controls and the Advanced text are one string.
    await page.getByTestId('pf-advanced-toggle').click()
    const flags = page.getByTestId('pf-input-flags')
    await expect(flags).toHaveValue(/HALOGEN_KV_SLOTS=4/)
    await expect(flags).toHaveValue(/HALOGEN_KV8=1/)
    await expect(flags).toHaveValue(/HALOGEN_MAX_TOK=32768/)

    await page.click('[data-testid="pf-btn-submit"]')
    await expect(page.locator('.pf-form-panel')).not.toBeVisible({ timeout: 5_000 })
    expect(posts).toHaveLength(1)
    expect(posts[0].runner).toBe('halogen')
    expect(posts[0].flags).toMatch(/\bHALOGEN_KV_SLOTS=4\b/)
    expect(posts[0].flags).toMatch(/\bHALOGEN_KV8=1\b/)
    expect(posts[0].flags).toMatch(/\bHALOGEN_MAX_TOK=32768\b/)
  })

  test('editing the Advanced text moves the typed controls', async ({ page }) => {
    await openNewProfile(page)
    await page.selectOption('[data-testid="profile-runner"]', 'halogen')
    await page.getByTestId('pf-advanced-toggle').click()
    await page.getByTestId('pf-input-flags').fill('HALOGEN_KV_SLOTS=3 HALOGEN_KV8=1')
    await expect(page.getByTestId('engine-input-HALOGEN_KV_SLOTS')).toHaveValue('3')
    await expect(page.getByTestId('engine-switch-HALOGEN_KV8')).toHaveAttribute('aria-checked', 'true')
  })

  test('a name the engine refuses blocks the save with an inline error', async ({ page }) => {
    const posts: any[] = []
    await openNewProfile(page)
    await page.route('**/api/profiles', async (route: any) => {
      if (route.request().method() === 'POST') posts.push(route.request().postDataJSON())
      return json(route, MOCK_DATA.profiles ?? [])
    })
    await page.selectOption('[data-testid="profile-runner"]', 'halogen')
    await page.getByTestId('pf-advanced-toggle').click()
    await page.getByTestId('pf-input-flags').fill('HALOGEN_API_PORT=9999')

    await expect(page.getByTestId('pf-engine-error')).toBeVisible()
    await expect(page.getByTestId('pf-engine-error')).toContainText('HALOGEN_API_PORT')
    await page.click('[data-testid="pf-btn-submit"]')
    await expect(page.locator('.pf-form-panel')).toBeVisible()
    expect(posts).toHaveLength(0)
  })

  test('n-gram speculation is not offered for a halogen runtime', async ({ page }) => {
    await openNewProfile(page)
    await page.selectOption('[data-testid="profile-runner"]', 'standard')
    await expect(page.locator('option[value="ngram"]')).toHaveCount(1)
    await page.selectOption('[data-testid="profile-runner"]', 'halogen')
    await expect(page.locator('option[value="ngram"]')).toHaveCount(0)
  })
})
