/**
 * hf-modal-vision-gate — the Add-model-from-HF modal must refuse the `vision`
 * label when there is no projector behind it, without ever deadlocking a repo
 * that legitimately ships none (#1394).
 *
 * The create-path twin of #1380 (fixed for the edit drawer in PR #1392): the
 * "vision label requires an mmproj file" div at model-modals.jsx:226 was
 * decorative — `canPull` carried no mmproj term, so ticking `vision` with
 * nothing picked left the Pull button live and `onPull` sent
 * `mmproj_filename: undefined`, landing a registry row that advertises
 * `vision` with no `--mmproj` for the launch path to hand the runner.
 *
 * The gate has to split two cases, because unlike the drawer's free-text path
 * field the operator here can only pick what the repo actually contains
 * (`mmprojChoices = variants.filter(v => /mmproj/i.test(v.id))`):
 *   · repo ships projectors, none picked → actionable, so block the Pull and
 *     say "pick one below" (the message is finally true).
 *   · repo ships none → nothing to pick, so refuse the LABEL instead: the
 *     checkbox is disabled and a carried-over tick is stripped. The pull
 *     itself is fine and must stay available.
 */
import { test, expect } from '../fixtures/apiMock'

type Variant = { id: string; size_bytes: number; size: string; info: string }

const QUANT: Variant = {
  id: 'qwen3-vl-8b-q4_k_m.gguf',
  size_bytes: 4_900_000_000,
  size: '4.56 GB',
  info: '4.56 GB · single file',
}
const PROJECTOR: Variant = {
  id: 'mmproj-Qwen3-VL-8B-Q8_0.gguf',
  size_bytes: 900_000_000,
  size: '858 MB',
  info: '858 MB · single file',
}

/**
 * Stub POST /api/models/inspect. `byRepo` maps an HF coord to the variant list
 * the backend would report, so a spec can inspect one repo and then another
 * within the same modal session (the carry-over case).
 */
async function mockInspect(
  page: import('@playwright/test').Page,
  byRepo: Record<string, Variant[]>,
) {
  await page.route('**/api/models/inspect', (route) => {
    const body = route.request().postDataJSON?.() ?? {}
    const repo = body.hf_repo || body.hf_url || ''
    return route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        repo,
        cached: false,
        variants: byRepo[repo] ?? [],
        tags: ['text-generation', 'gguf'],
        metadata: { license: 'apache-2.0' },
      }),
    })
  })
}

/** Capture POSTs to /api/models/{id}/pull; returns the accumulating array. */
async function capturePulls(page: import('@playwright/test').Page) {
  const pulls: { id: string; body: any }[] = []
  await page.route('**/api/models/*/pull', (route) => {
    const id = decodeURIComponent(
      new URL(route.request().url()).pathname.split('/').slice(-2)[0],
    )
    pulls.push({ id, body: route.request().postDataJSON?.() ?? {} })
    return route.fulfill({
      status: 202,
      contentType: 'application/json',
      body: JSON.stringify({ id: 'job-1', state: 'queued' }),
    })
  })
  return pulls
}

async function openModal(page: import('@playwright/test').Page) {
  await page.goto('/#models')
  await page.locator('.view .vh button:has-text("Add by HF coords")').click()
}

async function inspectRepo(page: import('@playwright/test').Page, repo: string) {
  await page.locator('input[placeholder*="unsloth/Qwen3-8B-GGUF"]').fill(repo)
  await page.locator('button:has-text("Inspect")').click()
  await expect(page.getByTestId('hf-label-vision')).toBeVisible()
}

const VISION_REPO = 'unsloth/Qwen3-VL-8B-GGUF'
const PLAIN_REPO = 'unsloth/Qwen3-8B-GGUF'

test.describe('Add-by-HF modal — the vision label needs a projector (#1394)', () => {
  test('vision ticked with no mmproj picked blocks the Pull until one is chosen', async ({
    page,
  }) => {
    await mockInspect(page, { [VISION_REPO]: [QUANT, PROJECTOR] })
    const pulls = await capturePulls(page)
    await openModal(page)
    await inspectRepo(page, VISION_REPO)

    // Pick a quant so `variant` + `name` are satisfied — the mmproj term is
    // then the only thing standing between this form and a pull.
    await page.locator('.variant-row', { hasText: QUANT.id }).click()
    const pull = page.getByTestId('hf-pull')
    await expect(pull).toBeEnabled()
    await expect(page.getByTestId('hf-mmproj-error')).toHaveCount(0)

    // Tick vision: the invariant is now violated and the repo HAS a projector,
    // so the error is actionable and must gate the Pull rather than decorate it.
    await page.getByTestId('hf-label-vision').check()
    const err = page.getByTestId('hf-mmproj-error')
    await expect(err).toBeVisible()
    await expect(err).toContainText('vision label requires an mmproj file')
    await expect(pull).toBeDisabled()
    await pull.click({ force: true })
    await page.waitForTimeout(300)
    expect(pulls).toEqual([])

    // Picking the projector clears the error and re-arms the Pull.
    await page.getByTestId('hf-mmproj-select').selectOption(PROJECTOR.id)
    await expect(page.getByTestId('hf-mmproj-error')).toHaveCount(0)
    await expect(pull).toBeEnabled()
    await pull.click()

    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.hf_filename).toBe(QUANT.id)
    expect(pulls[0].body.mmproj_filename).toBe(PROJECTOR.id)
    expect(pulls[0].body.labels).toContain('vision')
  })

  test('a repo with no mmproj file refuses the label, not the pull', async ({ page }) => {
    await mockInspect(page, { [PLAIN_REPO]: [QUANT] })
    const pulls = await capturePulls(page)
    await openModal(page)
    await inspectRepo(page, PLAIN_REPO)

    // There is nothing to pick, so the label itself is unavailable — gating the
    // pull on an mmproj choice here would deadlock the form.
    const visionBox = page.getByTestId('hf-label-vision')
    await expect(visionBox).toBeDisabled()
    await expect(visionBox).not.toBeChecked()
    await expect(page.getByTestId('hf-vision-unsupported')).toBeVisible()

    // Anti-deadlock: the pull is untouched.
    await page.locator('.variant-row', { hasText: QUANT.id }).click()
    const pull = page.getByTestId('hf-pull')
    await expect(pull).toBeEnabled()
    await expect(page.getByTestId('hf-mmproj-error')).toHaveCount(0)
    await pull.click()

    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.labels).toEqual(['chat'])
    expect(pulls[0].body.mmproj_filename).toBeUndefined()
  })

  test('a vision tick carried over from a projector-bearing repo is stripped, not stuck', async ({
    page,
  }) => {
    await mockInspect(page, { [VISION_REPO]: [QUANT, PROJECTOR], [PLAIN_REPO]: [QUANT] })
    const pulls = await capturePulls(page)
    await openModal(page)

    // Tick vision against a repo that supports it…
    await inspectRepo(page, VISION_REPO)
    await page.getByTestId('hf-label-vision').check()
    await expect(page.getByTestId('hf-label-vision')).toBeChecked()

    // …then re-target a repo that does not. The label state survives the repo
    // edit (only `inspect` resets), so a disabled-but-checked box would be an
    // unclearable tick — the pull would be blocked with no way out.
    await inspectRepo(page, PLAIN_REPO)
    const visionBox = page.getByTestId('hf-label-vision')
    await expect(visionBox).not.toBeChecked()
    await expect(visionBox).toBeDisabled()

    await page.locator('.variant-row', { hasText: QUANT.id }).click()
    await expect(page.getByTestId('hf-pull')).toBeEnabled()
    await page.getByTestId('hf-pull').click()
    await expect.poll(() => pulls.length).toBe(1)
    expect(pulls[0].body.labels).not.toContain('vision')
  })
})
