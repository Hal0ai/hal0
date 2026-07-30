/**
 * model-drawer-duplicate-v3 — DuplicateModelDialog field → wire contract.
 *
 * The "⋯ Duplicate for device" affordance in the model drawer's footer is
 * wired to POST /api/models/{id}/duplicate (UI-API-1, models.py
 * `duplicate_model`): weights are refcounted, and picking a device template
 * stamps that profile's flags into the new row server-side. Nothing asserted
 * that contract from the UI side, so this file covers:
 *
 *   D1. picked template  → POST body { new_id, profile }
 *   D2. "— no template —" → POST body { new_id } only (no null profile key)
 *   D3. the suggested id derives from `<source id>-<device class>` and stops
 *       being re-derived once the operator hand-edits it
 *   D4. an empty / same-as-source id blocks the POST with an inline error
 *   D5. a server 409 (id taken) surfaces inline + toasts and keeps the dialog
 *       open so the operator can retype
 *
 * Mutations are never mock-substituted (src/api/mock.ts is GET-only), so
 * page.route owns the POST. /api/profiles is a `networkFirst` allowlist row,
 * so the route stub below is authoritative for the device-template list.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const MODEL_ID = 'qwen3.6-27b-mtp'
const DUP_ROUTE = `**/api/models/${MODEL_ID}/duplicate`

const PROFILES = [
  {
    name: 'rocm-gpu',
    image: 'ghcr.io/hal0ai/hal0-rocmfp4',
    flags: '--cache-type-k q8_0',
    resolved_flags: '--cache-type-k q8_0',
    intent: 'GPU template',
    device_class: 'gpu',
    backend: 'rocm',
  },
  {
    name: 'cpu-lite',
    image: 'ghcr.io/hal0ai/hal0-cpu',
    flags: '--threads 8',
    resolved_flags: '--threads 8',
    intent: 'CPU template',
    device_class: 'cpu',
    backend: 'cpu',
  },
  // Second gpu-class profile — devProfiles keeps one representative per
  // device class, so this row must NOT appear in the picker.
  {
    name: 'vulkan-gpu',
    image: 'ghcr.io/hal0ai/hal0-vulkan',
    flags: '',
    resolved_flags: '',
    intent: 'Duplicate device class',
    device_class: 'gpu',
    backend: 'vulkan',
  },
]

async function stubLookups(page: Page) {
  await page.route('**/api/profiles', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(PROFILES) }),
  )
  await page.route('**/api/chat-templates', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([{ id: 'auto', label: 'Auto (GGUF embedded)' }]),
    }),
  )
}

/** Route the duplicate POST, recording bodies; `respond` shapes the reply. */
async function captureDuplicate(
  page: Page,
  respond: (body: any) => { status: number; json: any } = (b) => ({
    status: 200,
    json: { id: b.new_id, duplicated_from: MODEL_ID, files_refcounted: 3 },
  }),
) {
  const posts: any[] = []
  await page.route(DUP_ROUTE, async (route) => {
    const body = route.request().postDataJSON()
    posts.push(body)
    const { status, json } = respond(body)
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(json) })
  })
  return posts
}

async function openDuplicateDialog(page: Page) {
  await page.goto('/#models')
  await page.locator('button:has-text("Edit options")').first().click()
  await expect(page.getByTestId('model-flags-input')).toBeVisible()
  await page.getByTestId('model-duplicate-open').click()
  await expect(page.getByTestId('model-duplicate-id')).toBeVisible()
}

const dialog = (page: Page) => page.locator('.modal-shell', { has: page.getByTestId('model-duplicate-id') })
const confirmBtn = (page: Page) => dialog(page).getByRole('button', { name: /^Duplicat/ })

test.describe('Model drawer — duplicate for device', () => {
  test('D1 — a picked device template POSTs { new_id, profile } and closes with a toast', async ({ page }) => {
    await stubLookups(page)
    const posts = await captureDuplicate(page)

    await openDuplicateDialog(page)
    await page.getByTestId('model-duplicate-device').selectOption('cpu-lite')
    await page.getByTestId('model-duplicate-id').fill(`${MODEL_ID}-cpu`)
    await confirmBtn(page).click()

    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual({ new_id: `${MODEL_ID}-cpu`, profile: 'cpu-lite' })
    await expect(dialog(page)).toHaveCount(0)
    await expect(page.locator('.hal0-toast')).toContainText(`Duplicated → ${MODEL_ID}-cpu`)
  })

  test('D2 — "no template" POSTs { new_id } with no profile key', async ({ page }) => {
    await stubLookups(page)
    const posts = await captureDuplicate(page)

    await openDuplicateDialog(page)
    await page.getByTestId('model-duplicate-device').selectOption('')
    await page.getByTestId('model-duplicate-id').fill(`${MODEL_ID}-bare`)
    await confirmBtn(page).click()

    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual({ new_id: `${MODEL_ID}-bare` })
    expect(posts[0]).not.toHaveProperty('profile')
  })

  test('D3 — the id is suggested from the device class until the operator types', async ({ page }) => {
    await stubLookups(page)
    await captureDuplicate(page)

    await openDuplicateDialog(page)
    const picker = page.getByTestId('model-duplicate-device')
    // One representative profile per device class (+ the "no template" row).
    await expect(picker.locator('option')).toHaveText([
      '— no template —',
      'rocm-gpu · GPU template',
      'cpu-lite · CPU template',
    ])

    const idInput = page.getByTestId('model-duplicate-id')
    await expect(idInput).toHaveValue(`${MODEL_ID}-gpu`)
    await picker.selectOption('cpu-lite')
    await expect(idInput).toHaveValue(`${MODEL_ID}-cpu`)

    // Hand-editing pins the id — a later template change must not clobber it.
    await idInput.fill('my-own-id')
    await picker.selectOption('rocm-gpu')
    await expect(idInput).toHaveValue('my-own-id')
  })

  test('D4 — an empty or same-as-source id blocks the POST with an inline error', async ({ page }) => {
    await stubLookups(page)
    const posts = await captureDuplicate(page)

    await openDuplicateDialog(page)
    await page.getByTestId('model-duplicate-id').fill(MODEL_ID)
    await confirmBtn(page).click()
    await expect(page.getByTestId('model-duplicate-error')).toContainText('pick a new model id')
    expect(posts).toEqual([])

    await page.getByTestId('model-duplicate-id').fill('   ')
    await confirmBtn(page).click()
    await expect(page.getByTestId('model-duplicate-error')).toContainText('pick a new model id')
    expect(posts).toEqual([])
    // Still open — the operator can correct the id in place.
    await expect(dialog(page)).toBeVisible()
  })

  test('D5 — a 409 from the server surfaces inline, toasts, and keeps the dialog open', async ({ page }) => {
    await stubLookups(page)
    const posts = await captureDuplicate(page, () => ({
      status: 409,
      json: {
        error: {
          code: 'model.id_taken',
          message: 'model id "qwen3.6-27b-mtp-cpu" already exists',
          details: {},
        },
      },
    }))

    await openDuplicateDialog(page)
    await page.getByTestId('model-duplicate-device').selectOption('cpu-lite')
    await confirmBtn(page).click()

    await expect.poll(() => posts.length).toBe(1)
    await expect(page.getByTestId('model-duplicate-error')).toContainText('already exists')
    await expect(page.locator('.hal0-toast')).toContainText('Duplicate failed')
    await expect(dialog(page)).toBeVisible()
  })

  test('D6 — the footer states the real contract, not a fabricated "undo" claim (#1442)', async ({ page }) => {
    // Duplication has no undo — delete is the (refcounted) inverse. The
    // shared ConfirmDialog's default non-destructive footer ("You can undo
    // this later.") is false for this specific action.
    await stubLookups(page)
    await openDuplicateDialog(page)

    await expect(dialog(page)).not.toContainText('You can undo this later.')
    await expect(dialog(page)).toContainText(
      'The duplicate can be deleted at any time; weights are shared.',
    )
  })
})
