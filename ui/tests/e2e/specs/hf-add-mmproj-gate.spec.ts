/**
 * hf-add-mmproj-gate — the Add-model-from-HF modal's vision/mmproj error must
 * gate Pull, not decorate it (#1394).
 *
 * Structural twin of #1380 (drawer, PR #1392): the modal rendered
 * "vision label requires an mmproj file" while `canPull` ignored it entirely,
 * so ticking `vision` with no projector selected pulled anyway and
 * `seed_registry_from_body` landed a registry row labelled `vision` with no
 * mmproj — the same broken state, created rather than edited.
 *
 * The no-projector-in-repo case gets its own assertion: "pick one below" is
 * unactionable when the repo ships no mmproj, so the gate points at the label
 * instead (the pull itself is fine — only the label is unsupportable).
 */
import { test, expect } from '../fixtures/apiMock'

const VISION_REPO = 'org/vision-GGUF'
const TEXT_REPO = 'org/text-GGUF'

const VISION_VARIANTS = [
  { id: 'vision-Q4_K_M.gguf', size: '4.1 GB', info: 'Q4_K_M · 4.1 GB' },
  { id: 'mmproj-F16.gguf', size: '0.6 GB', info: 'mmproj · 0.6 GB' },
]
const TEXT_VARIANTS = [{ id: 'text-Q4_K_M.gguf', size: '4.1 GB', info: 'Q4_K_M · 4.1 GB' }]

/** Stub POST /api/models/inspect and capture any pull that escapes the gate. */
async function mockInspectAndPulls(page: import('@playwright/test').Page) {
  const pulls: { url: string; body: any }[] = []
  await page.route('**/api/models/inspect', (route) => {
    const repo = route.request().postDataJSON()?.hf_repo ?? ''
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        repo,
        variants: repo === VISION_REPO ? VISION_VARIANTS : TEXT_VARIANTS,
      }),
    })
  })
  await page.route('**/api/models/*/pull', (route) => {
    pulls.push({ url: route.request().url(), body: route.request().postDataJSON() })
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'job-1', state: 'queued' }),
    })
  })
  return pulls
}

async function openAndInspect(page: import('@playwright/test').Page, repo: string) {
  await page.goto('/#models')
  await page.locator('button:has-text("Add by HF coords")').first().click()
  await page.locator('input[placeholder="unsloth/Qwen3-8B-GGUF"]').fill(repo)
  await page.locator('button:has-text("Inspect")').click()
  // Variants render only once the inspect settles.
  await expect(page.locator('.variant-row').first()).toBeVisible()
}

const pullButton = (page: import('@playwright/test').Page) =>
  page.locator('button:has-text("Pull")')

test.describe('Add-by-HF modal — the vision/mmproj error gates Pull (#1394)', () => {
  test('ticking vision with no mmproj selected disables Pull until one is picked', async ({ page }) => {
    const pulls = await mockInspectAndPulls(page)
    await openAndInspect(page, VISION_REPO)

    // Pick the main quant — every other gate term (inspected/variant/name) is
    // satisfied, so Pull is live and only the mmproj term is under test.
    await page.locator('.variant-row:has-text("vision-Q4_K_M.gguf")').click()
    await expect(page.getByTestId('hf-add-mmproj-error')).toHaveCount(0)
    await expect(pullButton(page)).toBeEnabled()

    // Tick `vision` with nothing selected in the projector picker.
    await page.locator('label.checkbox-row:has-text("vision") input').check()

    const err = page.getByTestId('hf-add-mmproj-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText('vision label requires an mmproj file')
    await expect(err).toContainText('pick one below')

    // The gate, not the decoration: the button is dead and a forced click is
    // a no-op (onPull early-returns on !canPull).
    await expect(pullButton(page)).toBeDisabled()
    await pullButton(page).click({ force: true })
    await page.waitForTimeout(300)
    expect(pulls).toEqual([])

    // Selecting the repo's projector clears the error and re-arms Pull.
    await page.getByTestId('hf-add-mmproj-select').selectOption('mmproj-F16.gguf')
    await expect(page.getByTestId('hf-add-mmproj-error')).toHaveCount(0)
    await expect(pullButton(page)).toBeEnabled()

    await pullButton(page).click()
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.mmproj_filename).toBe('mmproj-F16.gguf')
    expect(pulls[0].body.labels).toContain('vision')
  })

  test('a repo with no mmproj files points at the label, not at an empty picker', async ({ page }) => {
    const pulls = await mockInspectAndPulls(page)
    await openAndInspect(page, TEXT_REPO)
    await page.locator('.variant-row:has-text("text-Q4_K_M.gguf")').click()

    await page.locator('label.checkbox-row:has-text("vision") input').check()

    // "pick one below" would be a lie — the picker's only option is the
    // "— no mmproj files in repo —" placeholder.
    const err = page.getByTestId('hf-add-mmproj-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText('untick vision to pull')
    await expect(pullButton(page)).toBeDisabled()

    // Unticking the label is the escape hatch: the pull itself was always fine.
    await page.locator('label.checkbox-row:has-text("vision") input').uncheck()
    await expect(page.getByTestId('hf-add-mmproj-error')).toHaveCount(0)
    await expect(pullButton(page)).toBeEnabled()

    await pullButton(page).click()
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.labels).not.toContain('vision')
    expect(pulls[0].body.mmproj_filename).toBeUndefined()
  })
})
