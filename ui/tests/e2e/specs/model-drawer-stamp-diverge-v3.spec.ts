/**
 * model-drawer-stamp-diverge-v3 — D1 model editor drawer (post-R3 rework).
 *
 * Exercises the core "the model is the launchable thing" flow:
 *   1. STAMP — picking a profile from the "⤵ Seed from profile…" menu POSTs
 *      /api/models/{id}/seed-profile (Task 7's useModelSeedProfile hook,
 *      server route shipped in #2198 / e31a451b, merged to main); the
 *      response's `defaults.profile` / `defaults.extra_args` are spliced into
 *      the flags editor + provenance chip. Option A drawer (Task 8, PR-3)
 *      retired the old always-visible `model-template-select`, which copied
 *      profile.flags client-side with no network round-trip at all.
 *   2. DIVERGE — editing the flags so they differ from the profile's current
 *      text raises the diverged chip + the inline client-side divergence diff.
 *   3. MANAGED-ARG REJECTION — a managed flag (--port) in the tune text surfaces
 *      an inline error, disables Save, and fires NO PUT (§21.7).
 *
 * /api/profiles is networkFirst in the mock harness, so page.route wins; the
 * drawer auto-targets the first installed model (qwen3.6-27b-mtp).
 *
 * The seed-profile server route shipped in #2198 (e31a451b, merged to main)
 * — mockSeedProfile below stays regardless, because this whole harness is
 * mock-driven (apiMock.ts: page.route stubs, Phase A/HAL0_DATA-seeded), the
 * same reason mockProfiles/mockChatTemplates mock routes that exist
 * server-side too. Its response shape follows useModelSeedProfile.ts's
 * documented contract (POST {profile} → updated model dict,
 * `defaults.profile` + `defaults.extra_args` set to the profile's flags).
 */
import { test, expect } from '../fixtures/apiMock'

const MODEL_ID = 'qwen3.6-27b-mtp'
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

/** Route is live on main (#2198, e31a451b) — mocked because this harness is
 * mock-driven, not because the server doesn't have it. */
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
  // Task 8 (#2205): EVERY pick now opens the consequence-preview confirm —
  // the old wouldClobber shortcut that skipped it for an empty/matching
  // current flags text is gone. Same POST, same effects; one extra click.
  await expect(page.getByTestId('model-seed-preview')).toBeVisible()
  await page.getByRole('button', { name: 'Stamp tune', exact: true }).click()
}

async function openDrawer(page: import('@playwright/test').Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').click()
  await expect(page.getByTestId('model-tune-raw-toggle')).toBeVisible()
}

/** model-drawer-2 Task 4: the tune editor rests on grouped pills; the flags
 *  TEXTAREA is the raw view behind `model-tune-raw-toggle`. Every assertion
 *  below is about the flags STRING, so it reads that string the way an
 *  operator would — by flipping to raw. */
async function showRawFlags(page: import('@playwright/test').Page) {
  await page.getByTestId('model-tune-raw-toggle').click()
  await expect(page.getByTestId('model-flags-input')).toBeVisible()
}

test.describe('Model drawer — stamp & diverge', () => {
  test('selecting a profile copies its flags into the editor + sets provenance', async ({ page }) => {
    await mockProfiles(page)
    await mockChatTemplates(page)
    const seedRequests = await mockSeedProfile(page)
    await openDrawer(page)

    await stampFromProfile(page, 'rocm-moe')

    // The stamp lands as pills first (the resting view): every flag the
    // profile carries gets one, and the hardware flag it smuggles in wears the
    // deny styling before anyone reads the error text.
    await expect(page.getByTestId('model-tune-pill---flash-attn')).toBeVisible()
    await expect(page.getByTestId('model-tune-pill---batch-size')).toBeVisible()
    await expect(page.getByTestId('model-tune-pill---threads')).toHaveAttribute(
      'data-divergence',
      'denied',
    )

    await showRawFlags(page)
    await expect(page.getByTestId('model-flags-input')).toHaveValue(PROFILE_FLAGS)
    await expect(page.getByTestId('model-provenance-chip')).toHaveText(/seeded from rocm-moe/i)
    // Freshly stamped text equals the profile — no divergence yet.
    await expect(page.getByTestId('model-diverged-chip')).toHaveCount(0)
    // The wire contract: exactly one POST, body {profile: name}.
    expect(seedRequests).toEqual([{ profile: 'rocm-moe' }])

    // Coverage hole: PROFILE_FLAGS bakes a slot-hardware flag (--threads)
    // right into the stamp, but none of this file's tests ever checked the
    // inline error or Save's disabled state — a regression in the
    // hardware-flag guard (findSlotHardwareFlags) would go completely
    // uncaught. The stamp must trip it immediately, same as hand-typing it.
    const err = page.getByTestId('model-flags-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText('--threads')
    await expect(page.getByTestId('model-save')).toBeDisabled()
  })

  test('editing stamped flags raises the diverged chip + inline diff', async ({ page }) => {
    await mockProfiles(page)
    await mockChatTemplates(page)
    await mockSeedProfile(page)
    await openDrawer(page)

    await stampFromProfile(page, 'rocm-moe')
    await showRawFlags(page)
    await expect(page.getByTestId('model-flags-input')).toHaveValue(PROFILE_FLAGS)
    // Add a flag the profile doesn't carry → diverged (added token).
    await page.getByTestId('model-flags-input').fill(`${PROFILE_FLAGS} --cache-type-k q8_0`)

    // The chip now counts the drift; the inline diff is the raw-mode view of it.
    await expect(page.getByTestId('model-diverged-chip')).toHaveText('◆ 1 diverged')
    const diff = page.getByTestId('model-divergence-diff')
    await expect(diff).toBeVisible()
    await expect(diff).toContainText('--cache-type-k')

    // Back on the pills, the same fact reads inline — the added flag is marked
    // added, and the diff panel is not duplicated there.
    await page.getByTestId('model-tune-raw-toggle').click()
    await expect(page.getByTestId('model-tune-pill---cache-type-k')).toHaveAttribute(
      'data-divergence',
      'added',
    )
    await expect(page.getByTestId('model-divergence-diff')).toHaveCount(0)
    await expect(page.getByTestId('model-diverged-chip')).toHaveText('◆ 1 diverged')
  })

  test('a managed flag in the tune text blocks Save with an inline error, no PUT', async ({ page }) => {
    let putFired = false
    await page.route('**/api/models/qwen3.6-27b-mtp', async (route) => {
      if (route.request().method() === 'PUT') {
        putFired = true
        return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
      }
      return route.fallback()
    })
    await mockProfiles(page)
    await mockChatTemplates(page)
    await openDrawer(page)
    await showRawFlags(page)

    await page.getByTestId('model-flags-input').fill('-fa on --port 9000')

    const err = page.getByTestId('model-flags-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText('--port')
    await expect(page.getByTestId('model-save')).toBeDisabled()

    // Clicking a disabled Save must not fire a PUT.
    await page.getByTestId('model-save').click({ force: true }).catch(() => {})
    await page.waitForTimeout(150)
    expect(putFired).toBe(false)
  })
})
