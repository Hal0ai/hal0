/**
 * model-drawer-reset-profile-v3 — D1 "Reset to profile" re-stamp.
 *
 * After a model's flags have diverged from the profile that seeded them, the
 * explicit "Reset to profile" action re-stamps: it replaces the model's launch
 * flags with the profile's CURRENT text (confirm first), and the diverged chip
 * clears. The profile is never mutated by any of this.
 *
 * Option A drawer (Task 8, PR-3): the old always-visible `model-template-
 * select` is gone — stamping now goes through the "⤵ Seed from profile…"
 * button (`model-seed-profile-open` → `model-seed-profile-option-{name}`),
 * which POSTs /api/models/{id}/seed-profile (Task 7 hook) instead of copying
 * the profile's flags client-side. The reset flow itself is untouched: it
 * only ever touched local form state (`sourceProfile.flags`), never the
 * network.
 *
 * TODO(#2198): the seed-profile server route lands with #2198 (not merged as
 * of this branch). Mocked here per the wire contract useModelSeedProfile.ts
 * documents (POST body {profile} → the updated model dict, {defaults:
 * {profile, extra_args}}) — unmock (drop mockSeedProfile) once #2198 merges
 * and the mock harness's real backend serves the route.
 */
import { test, expect } from '../fixtures/apiMock'

const MODEL_ID = 'qwen3.6-27b-mtp' // first installed row — models.jsx auto-selects it
const PROFILE_FLAGS = '-fa on -b 2048 -ub 512 --threads 8'

function mockProfiles(page: import('@playwright/test').Page) {
  return page.route('**/api/profiles', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        {
          name: 'rocm-moe',
          image: 'ghcr.io/hal0ai/hal0-rocmfp4',
          flags: PROFILE_FLAGS,
          resolved_flags: PROFILE_FLAGS,
          mtp: false,
          intent: 'MoE agents',
        },
      ]),
    }),
  )
}

function mockChatTemplates(page: import('@playwright/test').Page) {
  return page.route('**/api/chat-templates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ id: 'auto', label: 'Auto (GGUF embedded)' }]),
    }),
  )
}

/** TODO(#2198): stand-in for the not-yet-merged seed-profile route. */
async function mockSeedProfile(page: import('@playwright/test').Page) {
  const requests: any[] = []
  await page.route(`**/api/models/${MODEL_ID}/seed-profile`, async (route) => {
    const body = route.request().postDataJSON()
    requests.push(body)
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: MODEL_ID,
        defaults: { profile: body.profile, extra_args: PROFILE_FLAGS },
      }),
    })
  })
  return requests
}

async function stampFromProfile(page: import('@playwright/test').Page, name: string) {
  await page.getByTestId('model-seed-profile-open').click()
  await page.getByTestId(`model-seed-profile-option-${name}`).click()
}

test.describe('Model drawer — reset to profile', () => {
  test('reset re-stamps the profile text and clears the diverged chip', async ({ page }) => {
    await mockProfiles(page)
    await mockChatTemplates(page)
    const seedRequests = await mockSeedProfile(page)

    await page.goto('/#models')
    await page.locator('button:has-text("Edit options")').click()
    await expect(page.getByTestId('model-flags-input')).toBeVisible()

    // Stamp (now a POST /api/models/{id}/seed-profile round-trip), then diverge.
    await stampFromProfile(page, 'rocm-moe')
    await expect(page.getByTestId('model-flags-input')).toHaveValue(PROFILE_FLAGS)
    expect(seedRequests).toEqual([{ profile: 'rocm-moe' }])

    await page.getByTestId('model-flags-input').fill(`${PROFILE_FLAGS} --cache-type-k q8_0`)
    await expect(page.getByTestId('model-diverged-chip')).toBeVisible()

    // Reset → confirm → flags restored to the profile's current text. This
    // leg stays client-side (no further network call) — sourceProfile.flags
    // is already in hand from the /api/profiles fixture.
    await page.getByTestId('model-reset-profile').click()
    await page.getByRole('button', { name: 'Reset to profile', exact: true }).click()

    await expect(page.getByTestId('model-flags-input')).toHaveValue(PROFILE_FLAGS)
    await expect(page.getByTestId('model-diverged-chip')).toHaveCount(0)
    // Reset never re-fires the seed-profile POST.
    expect(seedRequests).toHaveLength(1)
  })
})
