/**
 * model-drawer-dirty-baseline-v3 — the model drawer's baseline is frozen at
 * open, and Save is gated on the dirty aggregate (#1441; closes the #1398 class).
 *
 * The drawer seeds its form ONCE (effect keyed on `[open, model?.id]`) but a
 * live-polled `model` prop feeds it (`models.jsx` does
 * `modelList.find(m => m.id === selId)` off `modelsQuery.data`: 30s poll plus an
 * invalidation on every model mutation). Comparing a once-seeded form against
 * the live row would read background drift as an operator edit. ADR-0029 cut
 * the drawer down to file facts, so the facts are what these tests move:
 *
 *   M1 — a concurrent write (another operator, the CLI) changes
 *        `defaults.chat_template` under the open drawer. The operator touches
 *        nothing and clicks Save: nothing may be written.
 *   M2 — Save is gated on `dirty`; a zero-edit Save writes nothing.
 *   M3 — `onSave` starts from the baseline snapshot, so a fact this drawer does
 *        not render (`tokenizer_repo`) rides along as the operator saw it, not
 *        as a mid-edit poll left it.
 *   M4 — a real edit enables Save and writes exactly that field, and putting a
 *        staged edit back disarms Save again.
 *
 * Harness note: VITE_MOCK_HAL0=1 short-circuits GET /api/models before
 * page.route sees it, so the row is controlled through `window.HAL0_DATA.models`
 * (same trick as model-drawer-save-info-v3). The refetch is forced through the
 * QueryClient that `globals-install.ts` publishes on `window` — the very
 * invalidation every model mutation fires — rather than waiting out the 30s
 * poll, so the "a poll landed" step is deterministic instead of timing-based.
 * PUT is not allowlisted, so page.route captures every write.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const MODEL_ID = 'qwen3.6-27b-mtp'

/** Registry row shaped like production: file facts + an unrendered fact. */
const SEEDED = {
  name: 'Original model name',
  tags: ['curated'],
  capabilities: ['chat'],
  backends: ['rocm'],
  mmproj: null,
  hf_repo: 'org/original-repo',
  hf_filename: 'original.gguf',
  defaults: {
    // The drawer renders no control for this one; `onSave` carries it through.
    tokenizer_repo: 'org/original-tok',
  },
}

async function seedModel(page: Page, overrides: Record<string, unknown> = {}) {
  await page.addInitScript(
    ({ id, model }) => {
      window.addEventListener('DOMContentLoaded', () => {
        const target = (window as any).HAL0_DATA?.models?.find(
          (row: any) => row.id === id,
        )
        if (target) {
          Object.assign(target, model)
          // Drop the legacy display aliases so normalizeApiModel derives the
          // same values it would from a real registry row.
          delete target.longName
          delete target.repo
          delete target.labels
        }
      })
    },
    { id: MODEL_ID, model: { ...SEEDED, ...overrides } },
  )
}

async function mockDrawerLookups(page: Page) {
  await page.route('**/api/chat-templates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([
        { id: 'auto', label: 'Auto (GGUF embedded)' },
        { id: 'chatml', label: 'ChatML' },
      ]),
    }),
  )
}

/** Capture every PUT the drawer can fire for this model. */
async function capturePuts(page: Page) {
  const puts: any[] = []
  await page.route(`**/api/models/${MODEL_ID}`, async (route) => {
    if (route.request().method() === 'PUT') {
      const body = route.request().postDataJSON()
      puts.push(body)
      return route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ id: MODEL_ID, ...body }),
      })
    }
    return route.fallback()
  })
  return puts
}

/**
 * Land a concurrent change on the registry row and force the refetch that a
 * background poll (or any model mutation's invalidation) would have caused.
 */
async function landConcurrentWrite(page: Page, patch: Record<string, unknown>) {
  await page.evaluate(
    ({ id, patch }) => {
      const target = (window as any).HAL0_DATA?.models?.find(
        (row: any) => row.id === id,
      )
      if (target) target.defaults = { ...(target.defaults || {}), ...patch }
    },
    { id: MODEL_ID, patch },
  )
  await page.evaluate(async () => {
    await (window as any).Hal0QueryClient.invalidateQueries({ queryKey: ['models'] })
  })
}

// MODEL_ID is the first installed row, so models.jsx auto-selects it and the
// detail pane's "Edit options" opens this drawer (same path as
// model-drawer-save-info-v3).
async function openDrawer(page: Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').first().click()
  await expect(page.getByTestId('model-chat-template')).toBeVisible()
}

const saveBtn = (page: Page) => page.getByTestId('model-save')
const chatTemplate = (page: Page) => page.getByTestId('model-chat-template')

test.describe('Model drawer — frozen baseline + dirty-gated Save', () => {
  test('M1 — a concurrent chat_template write is not reverted by an untouched Save', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)
    await expect(chatTemplate(page)).toContainText('Auto')

    // Somebody else pins a template while this drawer sits open.
    await landConcurrentWrite(page, { chat_template: 'chatml' })

    // The drawer does not re-seed mid-edit — that part is deliberate. The bug
    // would be reading its own stale seed as an operator edit.
    await expect(chatTemplate(page)).toContainText('Auto')

    await expect(saveBtn(page)).toBeDisabled()
    await saveBtn(page).click({ force: true })
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
  })

  test('M1 — a concurrent write does not arm the unsaved-changes guard', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    await capturePuts(page)

    await openDrawer(page)
    await landConcurrentWrite(page, { chat_template: 'chatml' })

    // Clean close: Cancel must not raise the discard confirm.
    await page.locator('.drawer.open button:has-text("Cancel")').click()
    await expect(page.locator('.drawer.open')).toHaveCount(0)
  })

  test('M2 (#1441) — Save is gated on dirty; a zero-edit Save writes nothing', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)
    // Nothing touched — Save must not offer to rewrite the defaults block.
    await expect(saveBtn(page)).toBeDisabled()
    await saveBtn(page).click({ force: true })
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
  })

  test('M3 — unrendered facts ride along as the operator saw them, not as a mid-edit poll left them', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)

    // A concurrent write touches a fact this drawer never renders.
    await landConcurrentWrite(page, { tokenizer_repo: 'org/other-tok' })

    // The operator makes a real, unrelated edit (Jinja unsupported) and saves.
    await page.getByTestId('model-cap-override-add').click()
    await page.getByTestId('model-cap-override-add-jinja-off').click()
    await expect(saveBtn(page)).toBeEnabled()
    await saveBtn(page).click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].defaults.jinja).toBe(false)
    // The carried-through fact is the one the drawer opened with. Sending the
    // mid-edit 'org/other-tok' would be writing a value the operator never saw.
    expect(puts[0].defaults.tokenizer_repo).toBe('org/original-tok')
    // And it is facts only: no tuning key ever rides the write (ADR-0029).
    expect(Object.keys(puts[0].defaults).sort()).toEqual([
      'chat_template',
      'jinja',
      'mtp',
      'tokenizer_repo',
    ])
  })

  test('M4 — a real edit still enables Save and writes exactly that field', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)
    await expect(saveBtn(page)).toBeDisabled()

    // model-drawer-2 Task 3: name edits ride the inline title editor now.
    await page.getByTestId('model-title-edit').click()
    await page.getByTestId('model-title-input').fill('Renamed model')
    await page.getByTestId('model-title-input').press('Enter')
    await expect(saveBtn(page)).toBeEnabled()
    await saveBtn(page).click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].name).toBe('Renamed model')
    // Untouched identity fields never ride the write.
    expect(puts[0]).not.toHaveProperty('tags')
    expect(puts[0]).not.toHaveProperty('capabilities')
    expect(puts[0]).not.toHaveProperty('backends')
  })

  test('M4 — reverting an edit by hand disarms Save again', async ({ page }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)
    await page.getByTestId('model-cap-override-add').click()
    await page.getByTestId('model-cap-override-add-mtp-on').click()
    await expect(saveBtn(page)).toBeEnabled()
    // Returning the fact to Auto is not an edit.
    await page.getByRole('button', { name: 'Remove MTP heads override' }).click()
    await expect(saveBtn(page)).toBeDisabled()
    expect(puts).toEqual([])
  })
})
