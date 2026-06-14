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
})
