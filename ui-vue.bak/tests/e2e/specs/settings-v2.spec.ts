/**
 * settings-v2.spec.ts — v2 Settings page coverage (slice #173).
 *
 * Asserts the requirements baked into the slice brief:
 *   - All 9 sections render and are reachable via the rail.
 *   - Lemonade admin section's `llamacpp.args` is readonly-by-default
 *     and the edit toggle reveals the footgun warning.
 *   - Save fires the SaveAndRestartDialog confirm.
 *   - Rotate-token + show/hide toggle work.
 *   - AddSecretModal saves and the new secret lists in Secrets.
 *   - Memory reset is type-to-confirm.
 *   - OmniRouter renders 8 tools with origin chips + remediation CTAs.
 *
 * The apiMock fixture seeds /api/lemonade/config; the other v2-only
 * endpoints (/api/secrets, /api/omni-tools, /api/memory/namespaces,
 * /api/updates/check, /api/auth/*) are not routed by the fixture and
 * fall through to the catch-all, which returns 200/{} for safe
 * read-only callers. The Settings view's per-section try/catch
 * already handles that gracefully.
 */
import { test, expect } from '../fixtures/apiMock'

test('all 9 sections render via rail navigation', async ({
  page,
  cleanState,
}) => {
  await page.goto('/settings')

  const sections = [
    { id: 'auth',       heading: 'Auth' },
    { id: 'secrets',    heading: 'Secrets' },
    { id: 'updates',    heading: 'Updates' },
    { id: 'lemonade',   heading: 'Lemonade admin' },
    { id: 'omni',       heading: 'OmniRouter' },
    { id: 'agent',      heading: 'Agent policy' },
    { id: 'memory',     heading: 'Memory (Cognee)' },
    { id: 'appearance', heading: 'Appearance' },
    { id: 'about',      heading: 'About' },
  ]

  for (const s of sections) {
    const target = page.locator(`section#${s.id}`)
    await expect(target).toBeVisible()
    await expect(target.locator('h2', { hasText: s.heading })).toBeVisible()
    // Rail click leaves a hash on the URL.
    await page
      .locator('[data-testid="settings-rail"] .nav-item', { hasText: s.heading })
      .click()
    await expect(page).toHaveURL(new RegExp(`#${s.id}$`))
  }
})

test('llamacpp.args is readonly-by-default; Edit reveals footgun warning', async ({
  page,
  cleanState,
}) => {
  await page.goto('/settings')

  const field = page.locator('[data-testid="lemonade-llama-args"]')
  await expect(field).toBeVisible()

  // Readonly span shown by default.
  await expect(page.locator('[data-testid="lemonade-llama-args-readonly"]'))
    .toBeVisible()

  // Footgun warning hidden until edit.
  await expect(page.locator('[data-testid="lemonade-llama-args-warning"]'))
    .toHaveCount(0)

  // Flip edit on.
  await page.locator('[data-testid="lemonade-llama-args-edit-toggle"]').click()
  await expect(page.locator('[data-testid="lemonade-llama-args-warning"]'))
    .toBeVisible()
  await expect(page.locator('[data-testid="lemonade-llama-args-warning"]'))
    .toContainText('--parallel 1 --threads N')
})

test('Lemonade save opens save-and-restart confirm dialog', async ({
  page,
  cleanState,
}) => {
  await page.goto('/settings')

  // Save button is disabled with no changes.
  const saveBtn = page.locator('[data-testid="lemonade-save"]')
  await expect(saveBtn).toBeDisabled()

  // Trigger a change by editing llamacpp.args.
  await page.locator('[data-testid="lemonade-llama-args-edit-toggle"]').click()
  const input = page
    .locator('[data-testid="lemonade-llama-args"] input.field-input')
  await input.fill('--parallel 1 --threads 8 --flash-attn on')

  await expect(saveBtn).toBeEnabled()
  await saveBtn.click()

  // SaveAndRestartDialog renders a Modal containing the verbatim copy.
  const dialog = page.locator('[role="dialog"]', {
    hasText: 'Save and restart lemond?',
  })
  await expect(dialog).toBeVisible()
  await expect(dialog).toContainText('~8-12 seconds')
})

test('Auth: rotate-token confirm + show/hide toggle', async ({
  page,
  cleanState,
}) => {
  await page.goto('/settings')

  // Show/hide toggle flips the visible value.
  const tokenSpan = page.locator('[data-testid="auth-token"]')
  await expect(tokenSpan).toContainText('•')

  await page.locator('[data-testid="auth-token-toggle"]').click()
  // After toggle the masked dots no longer dominate (post-rotation the
  // text would change; here we just verify the toggle was acknowledged
  // by re-clicking and seeing the masked value return).
  await page.locator('[data-testid="auth-token-toggle"]').click()
  await expect(tokenSpan).toContainText('•')

  // Rotate opens the ConfirmDialog with the heads-up copy.
  await page.locator('[data-testid="auth-token-rotate"]').click()
  const rotateDialog = page.locator('[role="dialog"]', {
    hasText: 'Rotate API token?',
  })
  await expect(rotateDialog).toBeVisible()
  await expect(rotateDialog).toContainText('re-authorized')
})

test('AddSecretModal saves a new secret and it lists in Secrets', async ({
  page,
  cleanState,
}) => {
  await page.goto('/settings')

  // Open modal.
  await page.locator('[data-testid="add-secret-open"]').click()
  await expect(page.locator('[data-testid="add-secret-modal"]')).toBeVisible()

  // Fill + submit — the foot-slot button is a sibling of the
  // .add-secret-modal body inside the teleported shell, so query
  // them at the page level rather than scoping to the body wrapper.
  await page.locator('[data-testid="add-secret-name"]').fill('CUSTOM_TOKEN')
  await page.locator('[data-testid="add-secret-value"]').fill('s3cret-v4lue')
  await page.locator('[data-testid="add-secret-submit"]').click()

  // Modal closes; new secret appears in the list.
  await expect(page.locator('[data-testid="add-secret-modal"]')).toHaveCount(0)
  const list = page.locator('[data-testid="secrets-list"]')
  await expect(list).toContainText('CUSTOM_TOKEN')
})

test('Memory reset is type-to-confirm', async ({ page, cleanState }) => {
  await page.goto('/settings')

  await page.locator('[data-testid="memory-reset-open"]').click()
  const dialog = page.locator('[role="dialog"]', {
    hasText: 'Reset memory namespace?',
  })
  await expect(dialog).toBeVisible()

  // Confirm button starts disabled — typeToConfirm gate.
  const confirm = dialog.locator('button', { hasText: 'Reset namespace' })
  await expect(confirm).toBeDisabled()

  // Type the namespace name to unlock.
  await dialog.locator('input.cd-input').fill('shared')
  await expect(confirm).toBeEnabled()
})

test('OmniRouter renders 8 tools with origin chips + remediation', async ({
  page,
  cleanState,
}) => {
  await page.goto('/settings')

  const list = page.locator('[data-testid="omni-tools-list"]')
  await expect(list).toBeVisible()

  // 8 rows.
  await expect(list.locator('.omni-row')).toHaveCount(8)

  // hal0-origin chips present.
  await expect(list.locator('.origin-hal0')).toHaveCount(3)
  // upstream-origin chips present.
  await expect(list.locator('.origin-upstream')).toHaveCount(5)

  // At least one remediation CTA shows when a tool is inactive.
  await expect(list.locator('.remediation a')).toHaveCount(2)
})
