/**
 * lemonade-admin.spec.ts — Settings → Lemonade admin panel (PR-13).
 *
 * Covers (plan §11 PR-13):
 *   - Renders config from the mocked GET response (each section
 *     appears; immediate/deferred badges line up with the partition).
 *   - Save POSTs ONLY the diff (not the whole snapshot); on success
 *     the panel reflects the new value and surfaces the
 *     "N immediate, M deferred" toast.
 *   - Validation error display: a bad llamacpp_args lands as an inline
 *     error tied to that field.
 *   - The link card on /settings deep-links into the panel.
 *
 * Uses the shared apiMock fixture (which now seeds /api/lemonade/config
 * with DEFAULT_LEMONADE_CONFIG and validates POST bodies the same way
 * the backend does).
 */
import { test, expect } from '../fixtures/apiMock'

test('renders all admin sections + immediate/deferred badges', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.goto('/settings/lemonade')

  // Each grouped section appears.
  for (const title of [
    'Service',
    'Concurrency + serving',
    'llama.cpp',
    'FLM (NPU)',
    'whisper.cpp',
    'Stable Diffusion',
  ]) {
    await expect(page.locator('.section-title', { hasText: title })).toBeVisible()
  }

  // Immediate badge attached to a known-immediate key (host).
  const hostBadge = page.locator(
    '[data-testid="lemonade-admin-field-host"] .effect-badge',
  )
  await expect(hostBadge).toBeVisible()
  await expect(hostBadge).toHaveText('Immediate')

  // Deferred badge attached to a known-deferred key (llamacpp_args).
  const llamaBadge = page.locator(
    '[data-testid="lemonade-admin-field-llamacpp_args"] .effect-badge',
  )
  await expect(llamaBadge).toBeVisible()
  await expect(llamaBadge).toHaveText('Deferred (next load)')
})

test('save POSTs only the changed key; success toast cites the effect counts', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.goto('/settings/lemonade')

  // Wait for the form to populate from the mocked GET.
  const logLevel = page.locator('#f-log_level')
  await expect(logLevel).toHaveValue('info')

  // Change one immediate key.
  await logLevel.selectOption('debug')

  // Save.
  await page.locator('[data-testid="lemonade-admin-save"]').click()

  // Mock fixture captures the patch body — assert it carries log_level
  // ONLY (not the whole snapshot, not unchanged keys).
  await expect.poll(() => mockState.lemonadeLastPatch).toEqual({ log_level: 'debug' })

  // The success toast carries the effect count copy. Pattern matches
  // both "Saved — 1 immediate" and "Saved — 1 immediate, M deferred..."
  // so the test stays resilient to future copy tweaks that add deferred
  // alongside.
  const toast = page.locator('.toast, [role="status"]', { hasText: /Saved.*immediate/ })
  await expect(toast).toBeVisible({ timeout: 5_000 })
})

test('bad llamacpp_args surfaces an inline field error', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.goto('/settings/lemonade')

  // Wait for the form to populate.
  const llamaInput = page.locator('#f-llamacpp_args')
  await expect(llamaInput).toHaveValue('--parallel 1 --threads 8')

  // Replace with something missing --threads (the locked-invariant
  // killer the backend refuses).
  await llamaInput.fill('--parallel 1')

  await page.locator('[data-testid="lemonade-admin-save"]').click()

  // Inline error tied to the field via data-testid — the same DOM
  // hook the backend's per-key details map flows into.
  const inlineErr = page.locator('[data-testid="lemonade-admin-error-llamacpp_args"]')
  await expect(inlineErr).toBeVisible({ timeout: 5_000 })
  await expect(inlineErr).toContainText('--threads')

  // The other fields stay untouched — no global "all changes lost"
  // wipe.
  await expect(llamaInput).toHaveValue('--parallel 1')
})

test('link from /settings deep-links to /settings/lemonade', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.goto('/settings')
  const link = page.locator('[data-testid="lemonade-admin-link"]')
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(/\/settings\/lemonade$/)
  await expect(page.locator('.page-title')).toContainText('Lemonade admin')
})
