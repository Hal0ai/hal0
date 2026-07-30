/**
 * P4 — the dedicated HuggingFace-token field in Settings → Secrets writes through
 * the existing /api/secrets/HF_TOKEN store and reflects set/not-set status.
 */
import { test, expect, json } from '../fixtures/apiMock'

test.describe('Settings — HuggingFace token', () => {
  test('save posts to /api/secrets/HF_TOKEN; status reflects set', async ({ page }) => {
    let putBody: any = null
    let hasToken = false
    await page.route('**/api/secrets', (r) =>
      json(r, { secrets: hasToken ? [{ name: 'HF_TOKEN', set: true, masked: '••• · set' }] : [] }))
    await page.route('**/api/secrets/HF_TOKEN', async (r) => {
      if (r.request().method() === 'PUT') {
        putBody = await r.request().postDataJSON()
        hasToken = true
      }
      return r.fulfill({ status: 204, body: '' })
    })

    await page.goto('/#settings', { waitUntil: 'domcontentloaded' })

    // #1163: default section is General — click Secrets nav to show the HF token field
    await page.locator('.settings-nav .nav-item', { hasText: 'Secrets' }).click()

    const field = page.getByLabel('HuggingFace token')
    await expect(field).toBeVisible()

    await field.fill('hf_abc123')

    // Subscribe to the response BEFORE the click — otherwise the 204 can land
    // before waitForResponse attaches (race).
    await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith('/api/secrets/HF_TOKEN') && r.request().method() === 'PUT',
      ),
      page.locator('button', { hasText: /^Save$/ }).first().click(),
    ])
    expect(putBody?.value).toBe('hf_abc123')
  })

  // #1467 item 3: Save had no onError handler — a rejected write (e.g. the
  // backend's secret.value_invalid 400 for non-printable values) vanished
  // silently. The field kept the typed token and status stayed "not set"
  // with zero feedback, so the operator believed the save worked. Contrast
  // the Remove path on SecretsPage (lines 90-96), which already toasts.
  test('a failed save surfaces an error and keeps the typed token', async ({ page }) => {
    await page.route('**/api/secrets', (r) => json(r, { secrets: [] }))
    await page.route('**/api/secrets/HF_TOKEN', async (r) => {
      if (r.request().method() === 'PUT') {
        return r.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            error: { code: 'secret.value_invalid', message: 'value contains non-printable characters' },
          }),
        })
      }
      return r.fulfill({ status: 204, body: '' })
    })

    await page.goto('/#settings', { waitUntil: 'domcontentloaded' })
    await page.locator('.settings-nav .nav-item', { hasText: 'Secrets' }).click()

    const field = page.getByLabel('HuggingFace token')
    await field.fill('hf_bad\x00token')

    await Promise.all([
      page.waitForResponse(
        (r) => r.url().endsWith('/api/secrets/HF_TOKEN') && r.request().method() === 'PUT',
      ),
      page.locator('button', { hasText: /^Save$/ }).first().click(),
    ])

    // The failure is surfaced (toast), and the field is NOT silently cleared
    // as if the save had succeeded.
    await expect(page.locator('.hal0-toast')).toContainText(/failed|invalid/i)
    await expect(field).toHaveValue('hf_bad\x00token')
  })
})
