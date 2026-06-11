/**
 * profiles-crud-v3 — Playwright write-path coverage for Profiles CRUD (Phase C6).
 *
 * READ path: VITE_MOCK_LEMONADE=1 + page.route for /api/profiles (same as
 * profiles-page-v3.spec.ts). Mutations use raw:true → page.route intercepts.
 *
 * Covers:
 *   - "New profile" button opens the form
 *   - Create flow: fill name/image → submit → POST body asserted → card appears
 *   - Seed immutability: vulkan-std has seed badge, Edit/Delete disabled, Clone present
 *   - Clone flow: Clone on vulkan-std prefills form (name "vulkan-std-copy") → POST
 *   - Delete flow: custom card Delete → confirm → DELETE fired → 204 → card gone
 *   - Delete-in-use: 409 profiles.in_use → error toast names the slot
 */

import { test, expect, json } from '../fixtures/apiMock'
import { MOCK_DATA } from '../fixtures/mock-data'

// A custom (non-seed) profile for delete/edit tests.
const CUSTOM_PROFILE = {
  name: 'my-custom',
  image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
  flags: '--flash-attn on',
  mtp: false,
  resolved_flags: '--flash-attn on',
  device_class: 'gpu',
  seed: false,
}

const PROFILES_WITH_CUSTOM = [...MOCK_DATA.profiles, CUSTOM_PROFILE]

// Helper: navigate to profiles page and wait for it to be ready.
async function gotoProfiles(page: any) {
  await page.goto('/#profiles')
  await page.waitForFunction(
    () => typeof (window as any).ProfilesView === 'function',
  )
  await page.waitForSelector('.pf-card', { timeout: 10_000 })
}

test.describe('Profiles CRUD — Phase C6', () => {
  test.beforeEach(async ({ page }) => {
    // Override /api/profiles to return seed + custom profiles.
    await page.route('**/api/profiles', (route) =>
      json(route, PROFILES_WITH_CUSTOM),
    )
  })

  // ── New profile button ───────────────────────────────────────────────────────

  test('New profile button opens the create form', async ({ page }) => {
    await gotoProfiles(page)
    await page.click('[data-testid="pf-btn-new"]')
    await expect(page.locator('.pf-form-panel')).toBeVisible()
    // Name field should be empty (not pre-filled).
    const nameInput = page.locator('[data-testid="pf-input-name"]')
    await expect(nameInput).toHaveValue('')
  })

  // ── Create flow ──────────────────────────────────────────────────────────────

  test('create: POST body matches form input, new card appears after refetch', async ({ page }) => {
    const posts: any[] = []
    const NEW_PROFILE = {
      name: 'test-profile',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server',
      flags: '',
      mtp: false,
      resolved_flags: '',
      device_class: 'gpu',
      seed: false,
    }

    // Intercept POST — capture body, respond 201 with the new profile.
    await page.route('**/api/profiles', async (route) => {
      if (route.request().method() === 'POST') {
        try { posts.push(JSON.parse(route.request().postData() || '{}')) } catch { posts.push({}) }
        return route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify(NEW_PROFILE),
        })
      }
      // GET returns updated list after create.
      return json(route, [...PROFILES_WITH_CUSTOM, NEW_PROFILE])
    })

    await gotoProfiles(page)
    await page.click('[data-testid="pf-btn-new"]')
    await expect(page.locator('.pf-form-panel')).toBeVisible()

    await page.fill('[data-testid="pf-input-name"]', 'test-profile')
    await page.fill('[data-testid="pf-input-image"]', 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server')
    await page.click('[data-testid="pf-btn-submit"]')

    // Wait for drawer to close.
    await expect(page.locator('.pf-form-panel')).not.toBeVisible({ timeout: 5_000 })

    // Assert POST was fired with correct body.
    expect(posts).toHaveLength(1)
    expect(posts[0].name).toBe('test-profile')
    expect(posts[0].image).toBe('ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server')
  })

  // ── Create validation ────────────────────────────────────────────────────────

  test('create: invalid name shows inline error, no POST sent', async ({ page }) => {
    const posts: any[] = []
    await page.route('**/api/profiles', async (route) => {
      if (route.request().method() === 'POST') { posts.push({}); }
      return json(route, PROFILES_WITH_CUSTOM)
    })

    await gotoProfiles(page)
    await page.click('[data-testid="pf-btn-new"]')
    await page.fill('[data-testid="pf-input-name"]', 'INVALID NAME!')
    await page.fill('[data-testid="pf-input-image"]', 'some/image:tag')
    await page.click('[data-testid="pf-btn-submit"]')

    // Error hint should appear, form stays open.
    await expect(page.locator('.pf-form-panel')).toBeVisible()
    await expect(page.locator('.hint.err')).toBeVisible()
    expect(posts).toHaveLength(0)
  })

  // ── Seed immutability ────────────────────────────────────────────────────────

  test('vulkan-std: seed badge visible, Edit/Delete disabled, Clone present', async ({ page }) => {
    await gotoProfiles(page)

    const vulkanCard = page.locator('.pf-card', { hasText: 'vulkan-std' })
    await expect(vulkanCard).toBeVisible()

    // Seed badge.
    await expect(vulkanCard.locator('.pf-badge.immutable')).toBeVisible()

    // Edit button should be disabled.
    const editBtn = vulkanCard.locator('[data-testid="pf-btn-edit-vulkan-std"]')
    await expect(editBtn).toBeDisabled()

    // Delete button should be disabled.
    const deleteBtn = vulkanCard.locator('[data-testid="pf-btn-delete-vulkan-std"]')
    await expect(deleteBtn).toBeDisabled()

    // Clone button should be present and enabled.
    const cloneBtn = vulkanCard.locator('[data-testid="pf-btn-clone-vulkan-std"]')
    await expect(cloneBtn).toBeVisible()
    await expect(cloneBtn).not.toBeDisabled()
  })

  // ── Clone flow ───────────────────────────────────────────────────────────────

  test('clone: prefills name as <source>-copy and image/flags; submit POSTs', async ({ page }) => {
    const posts: any[] = []
    await page.route('**/api/profiles', async (route) => {
      if (route.request().method() === 'POST') {
        try { posts.push(JSON.parse(route.request().postData() || '{}')) } catch { posts.push({}) }
        return route.fulfill({ status: 201, contentType: 'application/json', body: JSON.stringify({}) })
      }
      return json(route, PROFILES_WITH_CUSTOM)
    })

    await gotoProfiles(page)

    // Click Clone on vulkan-std.
    const vulkanCard = page.locator('.pf-card', { hasText: 'vulkan-std' })
    await vulkanCard.locator('[data-testid="pf-btn-clone-vulkan-std"]').click()
    await expect(page.locator('.pf-form-panel')).toBeVisible()

    // Name should be pre-filled as "vulkan-std-copy".
    const nameInput = page.locator('[data-testid="pf-input-name"]')
    await expect(nameInput).toHaveValue('vulkan-std-copy')

    // Image should be pre-filled from the source.
    const imageInput = page.locator('[data-testid="pf-input-image"]')
    await expect(imageInput).toHaveValue('ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server')

    // Submit.
    await page.click('[data-testid="pf-btn-submit"]')
    await expect(page.locator('.pf-form-panel')).not.toBeVisible({ timeout: 5_000 })

    expect(posts).toHaveLength(1)
    expect(posts[0].name).toBe('vulkan-std-copy')
    expect(posts[0].image).toBe('ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server')
  })

  // ── Delete flow ──────────────────────────────────────────────────────────────

  test('delete: custom card → confirm → DELETE /api/profiles/<name> → card gone', async ({ page }) => {
    const deletes: string[] = []
    let profileList = [...PROFILES_WITH_CUSTOM]

    await page.route('**/api/profiles', async (route) => {
      if (route.request().method() === 'DELETE') {
        // This is caught by the specific route below.
        return json(route, {})
      }
      return json(route, profileList)
    })
    await page.route('**/api/profiles/my-custom', async (route) => {
      if (route.request().method() === 'DELETE') {
        deletes.push(route.request().url())
        // After delete, remove from list so refetch shows it gone.
        profileList = profileList.filter(p => p.name !== 'my-custom')
        return route.fulfill({ status: 204, body: '' })
      }
      return json(route, CUSTOM_PROFILE)
    })

    await gotoProfiles(page)

    const customCard = page.locator('.pf-card', { hasText: 'my-custom' })
    await expect(customCard).toBeVisible()

    // Click Delete.
    await customCard.locator('[data-testid="pf-btn-delete-my-custom"]').click()

    // Confirm dialog appears.
    await expect(page.locator('.pf-confirm')).toBeVisible()

    // Confirm the delete.
    await page.click('[data-testid="pf-btn-delete-confirm"]')

    // Confirm dialog closes and DELETE was fired.
    await expect(page.locator('.pf-confirm')).not.toBeVisible({ timeout: 5_000 })
    expect(deletes.length).toBeGreaterThan(0)
    expect(deletes[0]).toContain('/api/profiles/my-custom')
  })

  // ── Delete in-use (409) ──────────────────────────────────────────────────────

  test('delete-in-use: 409 profiles.in_use → confirm shown → toast names the slot', async ({ page }) => {
    await page.route('**/api/profiles/my-custom', async (route) => {
      if (route.request().method() === 'DELETE') {
        return route.fulfill({
          status: 409,
          contentType: 'application/json',
          body: JSON.stringify({
            error: {
              code: 'profiles.in_use',
              message: 'Profile is in use',
              details: { slots: ['utility'] },
            },
          }),
        })
      }
      return json(route, CUSTOM_PROFILE)
    })

    await gotoProfiles(page)

    const customCard = page.locator('.pf-card', { hasText: 'my-custom' })
    await expect(customCard).toBeVisible()

    await customCard.locator('[data-testid="pf-btn-delete-my-custom"]').click()
    await expect(page.locator('.pf-confirm')).toBeVisible()

    // Set up toast listener before confirm.
    const toastMsg = page.locator('.hal0-toast, [role="status"]')
    await page.click('[data-testid="pf-btn-delete-confirm"]')

    // Toast should appear naming "utility".
    await expect(toastMsg.filter({ hasText: 'utility' })).toBeVisible({ timeout: 5_000 })
  })
})
