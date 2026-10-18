/**
 * hf-add-mmproj-gate — the Add-model-from-HF modal treats the mmproj
 * projector as first-class (#2193).
 *
 * Labels checkboxes are gone from this modal entirely — there is no
 * user-editable capability vocabulary here any more. Instead, when a repo
 * ships a projector file the modal auto-offers it (pre-selected, since one
 * projector per repo is the overwhelming case) and the `vision` pull label
 * is *derived* from that choice rather than hand-ticked. A repo with no
 * mmproj files renders no projector row at all, and Pull is never gated on
 * anything projector-related.
 */
import { test, expect } from '../fixtures/apiMock'

const VISION_REPO = 'org/vision-GGUF'
const TEXT_REPO = 'org/text-GGUF'

const VISION_VARIANTS = [
  {
    id: 'vision-Q4_K_M.gguf',
    quant: 'Q4_K_M',
    files: ['vision-Q4_K_M.gguf'],
    parts: 1,
    complete: true,
    size_bytes: 4_400_000_000,
    size: '4.1 GB',
    info: '4.1 GB · single file',
  },
  {
    id: 'Q8_0/vision-Q8_0-00001-of-00002.gguf',
    quant: 'Q8_0',
    files: ['Q8_0/vision-Q8_0-00001-of-00002.gguf', 'Q8_0/vision-Q8_0-00002-of-00002.gguf'],
    parts: 2,
    complete: true,
    size_bytes: 8_600_000_000,
    size: '8.01 GB',
    info: '8.01 GB · 2 parts',
  },
]
const VISION_MMPROJ = [{ id: 'mmproj-F16.gguf', quant: 'F16', size_bytes: 644_245_094, size: '0.6 GB' }]
const TEXT_VARIANTS = [
  {
    id: 'text-Q4_K_M.gguf',
    quant: 'Q4_K_M',
    files: ['text-Q4_K_M.gguf'],
    parts: 1,
    complete: true,
    size_bytes: 4_400_000_000,
    size: '4.1 GB',
    info: '4.1 GB · single file',
  },
]

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
        mmproj: repo === VISION_REPO ? VISION_MMPROJ : [],
        drafts: [],
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

test.describe('Add-by-HF modal — mmproj pairing is first-class (#2193)', () => {
  test('a repo shipping an mmproj auto-offers it and derives the vision label', async ({ page }) => {
    const pulls = await mockInspectAndPulls(page)
    await openAndInspect(page, VISION_REPO)

    // No Labels checkboxes anywhere:
    await expect(page.locator('.checkbox-row')).toHaveCount(0)

    // The projector row is offered without any prior toggle, pre-selected:
    const sel = page.getByTestId('hf-add-mmproj-select')
    await expect(sel).toBeVisible()
    await expect(sel).toHaveValue('mmproj-F16.gguf')

    // Pick the main quant — name is already auto-derived from the repo tail.
    await page.locator('.variant-row:has-text("Q4_K_M")').click()
    await expect(pullButton(page)).toBeEnabled()

    await pullButton(page).click()
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.mmproj_filename).toBe('mmproj-F16.gguf')
    expect(pulls[0].body.labels).toEqual(['chat', 'vision'])
  })

  test('choosing "None" pulls text-only with no vision label', async ({ page }) => {
    const pulls = await mockInspectAndPulls(page)
    await openAndInspect(page, VISION_REPO)

    await page.locator('.variant-row:has-text("Q4_K_M")').click()
    await page.getByTestId('hf-add-mmproj-select').selectOption('')
    await expect(pullButton(page)).toBeEnabled()

    await pullButton(page).click()
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.mmproj_filename).toBeUndefined()
    expect(pulls[0].body.labels).toEqual(['chat'])
  })

  test('a repo with no mmproj files renders no projector row at all', async ({ page }) => {
    const pulls = await mockInspectAndPulls(page)
    await openAndInspect(page, TEXT_REPO)

    await expect(page.getByTestId('hf-add-mmproj-select')).toHaveCount(0)

    // Pull is not gated on anything projector-related:
    await page.locator('.variant-row:has-text("Q4_K_M")').click()
    await expect(pullButton(page)).toBeEnabled()

    await pullButton(page).click()
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.mmproj_filename).toBeUndefined()
    expect(pulls[0].body.labels).toEqual(['chat'])
  })
})

test.describe('Add-by-HF modal — one row per quant', () => {
  test('a split quant is one row and pulls by its first part', async ({ page }) => {
    const pulls = await mockInspectAndPulls(page)
    await openAndInspect(page, VISION_REPO)

    // One row per quant: the projector is never offered as a quant, and the
    // two-part Q8_0 is a single row labelled with its part count.
    await expect(page.getByTestId('hf-add-variant')).toHaveCount(2)
    const split = page.locator('.variant-row', { hasText: 'Q8_0' })
    await expect(split).toContainText('2 parts')

    await split.click()
    await expect(pullButton(page)).toContainText('8.61 GB')
    await pullButton(page).click()
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.hf_filename).toBe('Q8_0/vision-Q8_0-00001-of-00002.gguf')
    expect(pulls[0].body.mmproj_filename).toBe('mmproj-F16.gguf')
  })
})
