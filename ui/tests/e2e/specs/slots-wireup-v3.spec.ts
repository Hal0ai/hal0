/**
 * slots-wireup-v3 — Phase B1 slots wireup (PR feat/dash-v3-slots-wireup).
 *
 * Covers the six lifecycle paths the slot panel must drive end-to-end:
 *   1. Create  — `New slot` modal POSTs /api/slots.
 *   2. Edit    — drawer PUTs /config + PATCHes /defaults (ctx_size).
 *   3. Delete  — overflow menu → confirm → DELETE /api/slots/{name}.
 *   4. Swap    — inline popover POSTs /api/slots/{name}/swap.
 *   5. Restart — card button POSTs /api/slots/{name}/restart.
 *   6. Unload  — card button POSTs /api/slots/{name}/unload.
 *
 * Each spec installs a per-route fulfiller that records the request body
 * so we can assert the wire shape, not just the verb. Mutation
 * invalidation triggers re-render via the useSlots polling query — we
 * don't need to seed a state machine; the request itself is the proof.
 */
import { test, expect } from '../fixtures/apiMock'

const BASE_SLOTS = [
  {
    name: 'primary', type: 'llm', device: 'gpu-rocm',
    model: 'qwen3.6-27b-mtp-q4_k_m', model_id: 'qwen3.6-27b-mtp',
    group: 'chat', state: 'serving', port: 8092, isDefault: true,
    metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
  },
]

test.describe('Slots v3 wire-up (/slots)', () => {
  test('Create slot — modal POSTs /api/slots with form body', async ({ page }) => {
    const seen: any[] = []
    await page.route('**/api/slots', async (route) => {
      if (route.request().method() === 'POST') {
        const body = JSON.parse(route.request().postData() || '{}')
        seen.push(body)
        await route.fulfill({
          status: 201,
          contentType: 'application/json',
          body: JSON.stringify({ name: body.name, ...body, state: 'idle' }),
        })
        return
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ slots: BASE_SLOTS }),
      })
    })

    await page.goto('/#slots')
    await page.locator('.view .vh button:has-text("New slot")').click()
    const nameInput = page.locator('.modal-shell .input.mono').first()
    await expect(nameInput).toBeVisible()
    await nameInput.fill('coder-large')
    const createBtn = page.locator('.modal-shell button:has-text("Create slot")')
    await expect(createBtn).toBeEnabled()
    const postReq = page.waitForRequest(
      (req) => req.url().endsWith('/api/slots') && req.method() === 'POST',
    )
    await createBtn.click()
    await postReq
    await expect.poll(() => seen.length).toBeGreaterThan(0)
    expect(seen[0].name).toBe('coder-large')
    expect(seen[0].type).toBe('llm')
  })

  test('Edit slot — drawer PATCHes /defaults with ctx_size + PUTs /config', async ({ page }) => {
    const patchBodies: any[] = []
    const putBodies: any[] = []
    await page.route('**/api/slots/primary/defaults', async (route) => {
      patchBodies.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots/primary/config', async (route) => {
      if (route.request().method() === 'PUT') {
        putBodies.push(JSON.parse(route.request().postData() || '{}'))
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ slots: BASE_SLOTS }),
      }),
    )

    await page.goto('/#slots/primary')
    const ctxInput = page.locator('.drawer .form-row', { hasText: 'ctx_size' }).locator('input')
    await expect(ctxInput).toBeVisible()
    await ctxInput.fill('16384')
    await page.locator('.drawer button:has-text("Save")').click()
    await expect.poll(() => patchBodies.length).toBeGreaterThan(0)
    expect(patchBodies[0].ctx_size).toBe(16384)
    await expect.poll(() => putBodies.length).toBeGreaterThan(0)
  })

  test('Delete slot — overflow → confirm → DELETE /api/slots/{name}', async ({ page }) => {
    const deletes: string[] = []
    await page.route('**/api/slots/primary', async (route) => {
      if (route.request().method() === 'DELETE') {
        deletes.push(route.request().url())
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ slots: BASE_SLOTS }),
      }),
    )

    await page.goto('/#slots/primary')
    page.on('dialog', (d) => d.accept())
    await page.locator('.drawer button.btn.danger:has-text("Delete")').click()
    await expect.poll(() => deletes.length).toBeGreaterThan(0)
  })

  test('Swap model — inline popover POSTs /api/slots/{name}/swap', async ({ page }) => {
    const swaps: any[] = []
    await page.route('**/api/slots/primary/swap', async (route) => {
      swaps.push(JSON.parse(route.request().postData() || '{}'))
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ slots: BASE_SLOTS }),
      }),
    )

    await page.goto('/#slots')
    const card = page.locator('.slot', { hasText: 'primary' }).first()
    await card.locator('.slot-model').click()
    const pick = page.locator('.swap-pop-item').first()
    await expect(pick).toBeVisible()
    await pick.click()
    await expect.poll(() => swaps.length).toBeGreaterThan(0)
    expect(typeof swaps[0].model_id).toBe('string')
  })

  test('Restart slot — card button POSTs /api/slots/{name}/restart', async ({ page }) => {
    const restarts: string[] = []
    await page.route('**/api/slots/primary/restart', async (route) => {
      restarts.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ slots: BASE_SLOTS }),
      }),
    )

    await page.goto('/#slots')
    const card = page.locator('.slot', { hasText: 'primary' }).first()
    await card.locator('button:has-text("Restart")').click()
    await expect.poll(() => restarts.length).toBeGreaterThan(0)
  })

  test('Unload slot — card button POSTs /api/slots/{name}/unload', async ({ page }) => {
    const unloads: string[] = []
    await page.route('**/api/slots/primary/unload', async (route) => {
      unloads.push(route.request().url())
      await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
    })
    await page.route('**/api/slots', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ slots: BASE_SLOTS }),
      }),
    )

    await page.goto('/#slots')
    const card = page.locator('.slot', { hasText: 'primary' }).first()
    await card.locator('button:has-text("Unload")').click()
    await expect.poll(() => unloads.length).toBeGreaterThan(0)
  })
})
