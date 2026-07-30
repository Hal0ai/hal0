/**
 * model-drawer-dirty-baseline-v3 — the model drawer's baseline is frozen at
 * open, and Save is gated on the dirty aggregate (#1441; closes the #1398 class).
 *
 * The slot drawer's half of #1398 landed in #1447. This is the other half, and
 * it is the same shape: the drawer seeds its form ONCE (effect keyed on
 * `[open, model?.id]`) but answers "did this field change?" against the LIVE
 * `model` prop. Both mounts feed it a live-polled value —
 * `models.jsx:136` does `modelList.find(m => m.id === selId)` off
 * `modelsQuery.data` (`useModels`, 30s poll + an invalidation on every model
 * mutation), and the slot drawer's stacked editor does
 * `(modelsQuery.data ?? []).find(m => m.id === curModelId)`. The in-file comment
 * claiming `model` is "a SNAPSHOT captured when the drawer opened" is simply
 * not true of either caller.
 *
 * Three consequences, all asserted on the wire below:
 *
 *   M1 — a concurrent write (another operator, a slot-drawer save, the CLI)
 *        moves `defaults.context_size` under the open drawer. The operator
 *        touches nothing and clicks Save: pre-fix the drawer sees its
 *        once-seeded 8192 against a live 32768, calls that an edit, and PUTs
 *        8192 — silently reverting the newer value. A lost update caused by
 *        doing nothing.
 *   M2 — Save is not gated on `dirty` at all (#1441), and `onSave` always
 *        rebuilds the whole `defaults` block, so a zero-edit Save is not the
 *        harmless no-op the issue assumes: it rewrites context_size,
 *        extra_args, chat_template, profile, n_gpu_layers and all four
 *        tri-state caps.
 *   M3 — `onSave` starts from `{ ...init }` (live `model.defaults`) so the keys
 *        this drawer does not render — rope_freq_base, … — "ride along
 *        unchanged". Read live, they ride along as a mid-edit poll left them,
 *        not as the operator saw them.
 *
 * There is deliberately no degraded-payload case here, unlike the slot half:
 * `useModels` has no union/soft-fail fallback (`fetchSlotsUnion`'s `/api/status`
 * leg is what made #1391 possible), so `/api/models` either answers or the
 * query errors. Nothing can hand this drawer a shape-degraded row.
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

/** Registry row shaped like production: typed defaults + an unrendered key. */
const SEEDED = {
  name: 'Original model name',
  tags: ['curated'],
  capabilities: ['chat'],
  backends: ['rocm'],
  mmproj: null,
  hf_repo: 'org/original-repo',
  hf_filename: 'original.gguf',
  defaults: {
    context_size: 8192,
    // The drawer renders none of these; `onSave` carries them through.
    rope_freq_base: 1_000_000,
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
  await page.route('**/api/profiles', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '[]' }),
  )
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
  await expect(page.getByTestId('model-flags-input')).toBeVisible()
}

const saveBtn = (page: Page) => page.getByTestId('model-save')
const ctxInput = (page: Page) => page.getByTestId('model-ctx-input')

test.describe('Model drawer — frozen baseline + dirty-gated Save', () => {
  test('M1 — a concurrent context_size write is not reverted by an untouched Save', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)
    await expect(ctxInput(page)).toHaveValue('8192')

    // Somebody else raises the context window while this drawer sits open.
    await landConcurrentWrite(page, { context_size: 32768 })

    // The drawer does not re-seed mid-edit — that part is deliberate. The bug
    // is that it then reads its own stale seed as an operator edit.
    await expect(ctxInput(page)).toHaveValue('8192')

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
    await expect(ctxInput(page)).toHaveValue('8192')
    await landConcurrentWrite(page, { context_size: 32768 })

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
    // Nothing touched — Save must not offer to rewrite the whole defaults block.
    await expect(saveBtn(page)).toBeDisabled()
    await saveBtn(page).click({ force: true })
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
  })

  test('M3 — unrendered defaults ride along as the operator saw them, not as a mid-edit poll left them', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)
    await expect(ctxInput(page)).toHaveValue('8192')

    // A concurrent write touches a key this drawer never renders.
    await landConcurrentWrite(page, { rope_freq_base: 500_000 })

    // The operator makes a real, unrelated edit and saves.
    await ctxInput(page).fill('16384')
    await expect(saveBtn(page)).toBeEnabled()
    await saveBtn(page).click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0].defaults.context_size).toBe(16384)
    // The carried-through key is the one the drawer opened with. Sending the
    // mid-edit 500_000 would be writing a value the operator never saw.
    expect(puts[0].defaults.rope_freq_base).toBe(1_000_000)
  })

  test('M4 — a real edit still enables Save and writes exactly that field', async ({
    page,
  }) => {
    await seedModel(page)
    await mockDrawerLookups(page)
    const puts = await capturePuts(page)

    await openDrawer(page)
    await expect(saveBtn(page)).toBeDisabled()

    await page.getByTestId('model-name-input').fill('Renamed model')
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
    await ctxInput(page).fill('16384')
    await expect(saveBtn(page)).toBeEnabled()
    // Typing the original value back is not an edit.
    await ctxInput(page).fill('8192')
    await expect(saveBtn(page)).toBeDisabled()
    expect(puts).toEqual([])
  })
})
