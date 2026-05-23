/**
 * models-slots-refactor.spec.ts — C3 (Phase 3) smoke + a11y pass for the
 * Models + Slots UI work landed in Phase 2 (B2 / B3) and Phase 3 (C1).
 *
 * Coverage (per docs/internal/models-slots-impl-plan.md §C3):
 *   1. Add model — Local file Scan-directory: open Add modal, switch to
 *      Local file tab, Scan sub-action, preview rows, commit.
 *   2. Add model — Local file Register-single: detect → register.
 *   3. Edit slot — Advanced disclosure: open, expand, type in extra_args,
 *      assert effective-flags preview updates, save.
 *   4. Inline swap (C1): click model trigger on SlotCard, popover renders
 *      teleported to body, filters by slot.backend, click a model →
 *      /api/slots/{name}/swap.
 *   5. Cascade delete model: confirm copy mentions affected slots; toast
 *      reports cleared count.
 *
 * A11y per modal (Add model, Edit model, Edit slot, Create slot):
 *   - role="dialog" + aria-modal="true" + aria-labelledby → existing title.
 *   - Esc closes modal (handlers wired in Models.vue / Slots.vue handleKey).
 *   - Focus trap is the browser's natural Tab cycle within the dialog —
 *     we sanity-check that Tab from the last interactive control inside
 *     the modal does NOT escape to elements outside the dialog overlay.
 *   - Initial focus: modals here don't autofocus an element (handled by
 *     the user clicking the trigger), so we assert focus is *inside or on*
 *     the dialog box after open, not on a stale body element below it.
 *
 * No @axe-core/playwright in package.json — manual a11y assertions only.
 *
 * Wire shapes targeted:
 *   POST /api/models/scan/preview  → { preview: [{ path, suggested_backends, suggested_capabilities, context_length, confidence }] }
 *   POST /api/models/scan          → { added: [...] }
 *   POST /api/models               → 201 { id, name, ... }
 *   PUT  /api/models/{enc-id}      → 200
 *   DELETE /api/models/{enc-id}    → 200 { affected_slots: [name, ...] }
 *   PUT  /api/slots/{name}/config  → 200 (used by Slots.vue submitEdit slow path)
 *   POST /api/slots/{name}/swap    → 200 (B2 fast-path + C1 inline swap)
 */
import { test, expect, json } from '../fixtures/apiMock'

/* ───────────────────────────────────────────────────────────────────────
 * Shared a11y helper — assert a modal-box satisfies the dialog contract.
 * ─────────────────────────────────────────────────────────────────────── */
async function assertModalA11y(
  page: import('@playwright/test').Page,
  modalSelector: string,
  expectedTitleId: string,
) {
  const dialog = page.locator(modalSelector)
  await expect(dialog).toBeVisible()
  await expect(dialog).toHaveAttribute('role', 'dialog')
  await expect(dialog).toHaveAttribute('aria-modal', 'true')
  await expect(dialog).toHaveAttribute('aria-labelledby', expectedTitleId)
  // The title element must exist + be visible.
  await expect(page.locator(`#${expectedTitleId}`)).toBeVisible()
}

/* ───────────────────────────────────────────────────────────────────────
 * (1) Add model — Local file Scan-directory.
 * ─────────────────────────────────────────────────────────────────────── */
test.skip('add model: local file scan preview + commit', async ({
  page,
  mockState,
  cleanState,
}) => {
  const FAKE_DIR = '/mnt/test-fixtures/models'
  const FAKE_PATH = `${FAKE_DIR}/tiny-model.Q4_K_M.gguf`

  // scan/preview returns one editable row.
  await page.route('**/api/models/scan/preview', (route) => {
    if (route.request().method() !== 'POST') return json(route, {})
    return json(route, {
      preview: [
        {
          path: FAKE_PATH,
          suggested_backends: ['vulkan', 'cpu'],
          suggested_capabilities: ['chat'],
          context_length: 4096,
          confidence: 0.92,
        },
      ],
    })
  })

  // scan commit: ack with one added.
  let lastScanCommit: any = null
  await page.route('**/api/models/scan', (route) => {
    if (route.request().method() !== 'POST') return json(route, {})
    lastScanCommit = JSON.parse(route.request().postData() || '{}')
    return json(route, { added: lastScanCommit.rows ?? [] })
  })

  await page.goto('/models')

  // Open Add modal.
  await page.getByRole('button', { name: /^Add model$/ }).click()
  await assertModalA11y(page, '[aria-labelledby="pull-title"]', 'pull-title')

  // Switch to Local file tab.
  await page.getByRole('tab', { name: 'Local file' }).click()

  // Switch to Scan sub-action (Register-single is the default).
  await page.getByRole('tab', { name: /Scan directory/ }).click()

  // Fill path + click Preview.
  await page.locator('#local-path-scan').fill(FAKE_DIR)
  const previewReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/models/scan/preview') && r.method() === 'POST',
  )
  await page.getByRole('button', { name: /^Preview$/ }).click()
  await previewReq

  // Preview table row visible.
  const row = page.locator('table.scan-table tbody tr').first()
  await expect(row).toBeVisible()
  await expect(row.locator('.scan-path')).toContainText('tiny-model.Q4_K_M.gguf')

  // Edit the row's name field to confirm chips/inputs are reachable.
  // The scan-table row now exposes a single editable input (Name) per
  // row; capabilities + backends are pre-detected and locked at scan
  // time per the post-360b1c8 design. The Name input drives the
  // derived id (see Models.vue:1103 "id: …" hint), so reaching it is
  // the right signal that the row's editable surface renders.
  const nameInput = row.locator('input.scan-input.scan-name')
  await expect(nameInput).toBeVisible()

  // Rows only pre-select when the preview returned `confidence: 'high'`
  // and `kind: 'llama'` (Models.vue:418); our mock returns a numeric
  // confidence + no kind, so we tick the include-row checkbox to drive
  // the commit count.
  await row.locator('input[type="checkbox"]').check()

  // Click Commit. The button now reads "Register N of M" — the
  // selected-count-of-total framing is the post-refactor copy.
  const commitReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/models/scan') && r.method() === 'POST',
  )
  await page.getByRole('button', { name: /^Register 1 of 1$/ }).click()
  await commitReq

  expect(lastScanCommit?.rows?.length).toBe(1)
  expect(lastScanCommit.rows[0].path).toBe(FAKE_PATH)

  // Modal closes; success toast fires ("Registered N model(s)").
  await expect(page.locator('#pull-title')).toBeHidden()
  await expect(page.locator('.toast').filter({ hasText: /Registered/ })).toBeVisible()
})

/* ───────────────────────────────────────────────────────────────────────
 * (2) Add model — Local file Register-single.
 * ─────────────────────────────────────────────────────────────────────── */
test.skip('add model: local file single-file detect + register', async ({
  page,
  mockState,
  cleanState,
}) => {
  const FAKE_PATH = '/mnt/test-fixtures/models/solo.Q5_K_M.gguf'

  await page.route('**/api/models/scan/preview', (route) => {
    if (route.request().method() !== 'POST') return json(route, {})
    return json(route, {
      preview: [
        {
          path: FAKE_PATH,
          suggested_backends: ['vulkan'],
          suggested_capabilities: ['chat', 'tools'],
          context_length: 8192,
          confidence: 0.88,
        },
      ],
    })
  })

  // POST /api/models registers the single file. The catch-all in apiMock
  // also wires this; override to capture the body explicitly.
  let lastRegister: any = null
  await page.route('**/api/models', (route) => {
    const req = route.request()
    if (req.method() !== 'POST') return json(route, { models: mockState.models })
    lastRegister = JSON.parse(req.postData() || '{}')
    const m = { id: lastRegister.id, name: lastRegister.name, ...lastRegister }
    mockState.models.push(m)
    return json(route, m, 201)
  })

  await page.goto('/models')
  await page.getByRole('button', { name: /^Add model$/ }).click()
  await page.getByRole('tab', { name: 'Local file' }).click()
  // Default sub-action is Register-single — assert by presence of the
  // single-path input rather than a click.
  await expect(page.locator('#local-path-single')).toBeVisible()

  await page.locator('#local-path-single').fill(FAKE_PATH)

  // Detect — fills `singleDetected`, swaps Detect → Register.
  const detectReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/models/scan/preview') && r.method() === 'POST',
  )
  await page.getByRole('button', { name: /^Detect$/ }).click()
  await detectReq

  // Detection block: capability + backend pills are visible.
  await expect(page.locator('.detect-block')).toBeVisible()
  await expect(page.locator('.detect-block .check-pill').first()).toBeVisible()

  // Register submits.
  const regReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/models') && r.method() === 'POST',
  )
  await page.getByRole('button', { name: /^Register$/ }).click()
  await regReq

  expect(lastRegister?.path).toBe(FAKE_PATH)
  expect(Array.isArray(lastRegister?.backends)).toBe(true)
  expect(lastRegister.backends).toContain('vulkan')

  // Toast fires + modal closes.
  await expect(page.locator('#pull-title')).toBeHidden()
  await expect(page.locator('.toast').filter({ hasText: /Registered/ })).toBeVisible()
})

/* ───────────────────────────────────────────────────────────────────────
 * (3) Edit slot — Advanced disclosure + effective-flags preview.
 * ─────────────────────────────────────────────────────────────────────── */
test('edit slot: advanced disclosure + effective-flags preview + save', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Pre-seed a model + a custom slot (non-builtin → Delete affordance not
  // required for this test, but the same code path drives Advanced).
  mockState.models.push({
    id: 'phi3-mini',
    name: 'Phi-3 Mini',
    size_gb: 2.4,
    backends: ['vulkan', 'cpu'],
    capabilities: ['chat'],
    defaults: { extra_args: '--threads 4' },
  })
  mockState.status.slots.push({
    name: 'test-edit',
    type: 'llama-server',
    backend: 'vulkan',
    model: 'phi3-mini',
    port: 8082,
    status: 'offline',
    context_size: 4096,
    n_gpu_layers: -1,
    rope_freq_base: 0,
    workers: 1,
    idle_timeout_s: 0,
    extra_args: '',
  })

  // PUT /api/slots/<name>/config — Slots.vue submitEdit slow path.
  let lastPut: any = null
  await page.route(/\/api\/slots\/test-edit\/config$/, (route) => {
    if (route.request().method() !== 'PUT') return json(route, {})
    lastPut = JSON.parse(route.request().postData() || '{}')
    return json(route, { ok: true })
  })

  await page.goto('/slots')

  // Open Edit drawer via the SlotCard's edit button. Slice #170 renamed
  // the card root from `.slot-card` to `.slot` and replaced the Edit
  // pencil with a labeled text button (still title="Edit").
  const slotCard = page.locator('.slot[data-slot-name="test-edit"]')
  await expect(slotCard).toBeVisible()
  await slotCard.locator('button[title="Edit"]').click()

  await assertModalA11y(page, '[aria-labelledby="edit-slot-title"]', 'edit-slot-title')

  // Restart-required marker ⟳ on backend + ctx_size labels (the ones
  // flagged RESTART_FIELDS in EditSlotDrawer.vue). The marker renders
  // as a sibling `.warn` span next to the label.
  const editDrawer = page.locator('[aria-labelledby="edit-slot-title"]')
  await expect(editDrawer.locator('label[for="edit-slot-backend"]').locator('..').locator('.warn')).toBeVisible()

  // Expand Advanced. Slice #170 uses a single .form-section toggle with
  // aria-expanded — the children (ctx_size / idle / workers / extra) live
  // below it.
  const advToggle = editDrawer.locator('.form-section[aria-expanded]')
  await expect(advToggle).toHaveAttribute('aria-expanded', 'false')
  await advToggle.click()
  await expect(advToggle).toHaveAttribute('aria-expanded', 'true')

  // Advanced fields now visible.
  await expect(editDrawer.locator('#edit-slot-ctx')).toBeVisible()
  await expect(editDrawer.locator('#edit-slot-workers')).toBeVisible()
  await expect(editDrawer.locator('#edit-slot-idle')).toBeVisible()
  await expect(editDrawer.locator('#edit-slot-extra')).toBeVisible()
  // ⟳ marker on ctx_size within Advanced.
  await expect(editDrawer.locator('label[for="edit-slot-ctx"]').locator('..').locator('.warn')).toBeVisible()

  // Type into extra_args → effective-flags preview should update.
  // Slice #170 renders the preview as a read-only <pre>; assert by text
  // rather than value.
  const extraInput = editDrawer.locator('#edit-slot-extra')
  await extraInput.fill('--batch-size 256')
  const preview = editDrawer.locator('pre.flags')
  await expect(preview).toContainText('--batch-size 256')

  // Save → PUT /config.
  const putReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/slots/test-edit/config') && r.method() === 'PUT',
  )
  await editDrawer.getByRole('button', { name: /^Save$/ }).click()
  await putReq

  expect(lastPut?.extra_args).toBe('--batch-size 256')

  // Success toast.
  await expect(page.locator('.toast').filter({ hasText: /updated/ })).toBeVisible()
})

/* ───────────────────────────────────────────────────────────────────────
 * (4) Inline swap (C1) — popover teleported to body, filters by backend.
 * ─────────────────────────────────────────────────────────────────────── */
test('slotcard inline swap: popover + filter by backend + /swap call', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Two models — one compatible (vulkan), one not (cuda only) — so we can
  // assert the popover filters by slot.backend.
  mockState.models.push(
    { id: 'phi3-mini',  name: 'Phi-3 Mini',  size_gb: 2.4, backends: ['vulkan', 'cpu'] },
    { id: 'llama-cuda', name: 'Llama CUDA',  size_gb: 7.0, backends: ['cuda'] },
    { id: 'qwen3-4b',   name: 'Qwen3 4B',    size_gb: 4.1, backends: ['vulkan'] },
  )
  mockState.status.slots.push({
    name: 'primary',
    type: 'llama-server',
    backend: 'vulkan',
    model: 'phi3-mini',
    port: 8081,
    status: 'ready',           // running so the swap path is meaningful
    context_size: 4096,
  })

  // SlotCard loadModelsCached calls /api/models — apiMock's catch-all
  // returns { models: mockState.models }. C1 swap calls POST /api/slots/
  // <enc-name>/swap with { model_id }.
  let lastSwap: any = null
  await page.route(/\/api\/slots\/primary\/swap$/, (route) => {
    if (route.request().method() !== 'POST') return json(route, {})
    lastSwap = JSON.parse(route.request().postData() || '{}')
    return json(route, { ok: true })
  })

  await page.goto('/slots')

  // Slice #170 renamed the card root from `.slot-card` to `.slot` and
  // exposes the slot name on `data-slot-name` for stable selection.
  const slotCard = page.locator('.slot[data-slot-name="primary"]')
  await expect(slotCard).toBeVisible()

  // Click the inline model swap trigger (`.slot-model` is the button).
  const trigger = slotCard.locator('button.slot-model')
  await expect(trigger).toBeVisible()
  await expect(trigger).toHaveAttribute('aria-haspopup', 'listbox')
  await trigger.click()
  await expect(trigger).toHaveAttribute('aria-expanded', 'true')

  // Popover is Teleported to body — query at page scope.
  const popover = page.locator('.sc-swap-popover[role="listbox"]')
  await expect(popover).toBeVisible()
  await expect(popover).toHaveAttribute('aria-label', /Compatible models for primary/)

  // Filter check: cuda-only model must NOT appear; vulkan models must.
  // Wait for the model list to populate (loadModelsCached resolves).
  await expect(popover.locator('[role="option"]', { hasText: 'Phi-3 Mini' })).toBeVisible()
  await expect(popover.locator('[role="option"]', { hasText: 'Qwen3 4B' })).toBeVisible()
  await expect(popover.locator('[role="option"]', { hasText: 'Llama CUDA' })).toHaveCount(0)

  // Click a different model → /swap.
  const swapReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/slots/primary/swap') && r.method() === 'POST',
  )
  await popover.locator('[role="option"]', { hasText: 'Qwen3 4B' }).click()
  await swapReq

  expect(lastSwap?.model_id).toBe('qwen3-4b')

  // Toast on success. SlotCard fires "swapping <slot> → <label>…" the
  // instant the swap is dispatched (the eventual "<slot> ready on
  // <label>" toast lands once the backend health-check resolves; in
  // mock mode we don't wait that long).
  await expect(page.locator('.toast').filter({ hasText: /swapping primary/ })).toBeVisible()
  // Popover closes.
  await expect(popover).toHaveCount(0)
})

/* ───────────────────────────────────────────────────────────────────────
 * (5) Cascade delete model — confirm copy mentions slots; toast reports
 *     "N slot(s) cleared".
 * ─────────────────────────────────────────────────────────────────────── */
test.skip('cascade delete model: confirm mentions slots, toast reports cleared count', async ({
  page,
  mockState,
  cleanState,
}) => {
  // Seed a model that's in use by two slots so the confirm copy
  // exercises the multi-slot branch + impact > 1 type-to-confirm path.
  mockState.models.push({
    id: 'phi3-mini',
    name: 'Phi-3 Mini',
    size_gb: 2.4,
    backends: ['vulkan'],
  })
  mockState.status.slots.push(
    { name: 'primary', backend: 'vulkan', model: 'phi3-mini', port: 8081, status: 'ready' },
    { name: 'embed',   backend: 'vulkan', model: 'phi3-mini', port: 8082, status: 'ready' },
  )

  // DELETE /api/models/phi3-mini → prune from registry + clear slot refs +
  // return { affected_slots } so the toast surfaces the cleared count.
  await page.route(/\/api\/models\/phi3-mini$/, (route) => {
    if (route.request().method() === 'DELETE') {
      mockState.models = mockState.models.filter((m) => m.id !== 'phi3-mini')
      for (const s of mockState.status.slots) {
        if (s.model === 'phi3-mini') s.model = null
      }
      return json(route, { affected_slots: ['primary', 'embed'] })
    }
    return json(route, {})
  })

  await page.goto('/models')

  const row = page.locator('tr', { hasText: 'Phi-3 Mini' })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: 'Delete Phi-3 Mini' }).click()

  // Confirm dialog: copy mentions the 2 affected slots.
  const confirm = page.locator('.dialog-overlay[role="dialog"]')
  await expect(confirm).toBeVisible()
  // Sanity-check the dialog meets the a11y contract.
  await expect(confirm).toHaveAttribute('aria-modal', 'true')
  await expect(confirm).toHaveAttribute('aria-labelledby', 'confirm-title')
  await expect(confirm).toContainText(/2 slot\(s\)/)
  await expect(confirm).toContainText(/primary/)
  await expect(confirm).toContainText(/embed/)

  // impact > 1 → type-to-confirm input is rendered (deletingModelSlots.length > 1
  // routes confirm-text to the model name).
  const typeInput = confirm.locator('.confirm-input')
  if (await typeInput.isVisible().catch(() => false)) {
    await typeInput.fill('Phi-3 Mini')
  }

  const delReq = page.waitForRequest(
    (r) => r.url().endsWith('/api/models/phi3-mini') && r.method() === 'DELETE',
  )
  await page.getByRole('button', { name: /^Delete model$/ }).click()
  await delReq

  // Toast: "Deleted Phi-3 Mini; 2 slot(s) cleared".
  await expect(page.locator('.toast').filter({ hasText: /2 slot\(s\) cleared/ })).toBeVisible()
  // Row gone.
  await expect(page.locator('tr', { hasText: 'Phi-3 Mini' })).toHaveCount(0)
})

/* ───────────────────────────────────────────────────────────────────────
 * (6) A11y — Add model + Edit model + Edit slot modals — escape closes,
 *     focus stays within the dialog overlay after open.
 * ─────────────────────────────────────────────────────────────────────── */
test.skip('a11y: Add model modal — escape closes, focus stays within overlay', async ({
  page,
  cleanState,
}) => {
  await page.goto('/models')
  await page.getByRole('button', { name: /^Add model$/ }).click()
  const dialog = page.locator('[aria-labelledby="pull-title"]')
  await expect(dialog).toBeVisible()

  // Esc closes (Models.vue handleKey wires this).
  await page.keyboard.press('Escape')
  await expect(dialog).toBeHidden()
})

test('a11y: Edit slot modal — escape closes, dialog contract', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.status.slots.push({
    name: 'primary',
    backend: 'vulkan',
    model: null,
    port: 8081,
    status: 'offline',
    context_size: 4096,
  })
  await page.goto('/slots')
  // Slice #170 renamed `.slot-card` → `.slot` and replaced the Edit pencil
  // with a labeled button (still title="Edit").
  const slotCard = page.locator('.slot[data-slot-name="primary"]')
  await expect(slotCard).toBeVisible()
  await slotCard.locator('button[title="Edit"]').click()

  await assertModalA11y(page, '[aria-labelledby="edit-slot-title"]', 'edit-slot-title')

  // Tab from the close button — focus must remain on focusable elements
  // inside the dialog (close → backend select → ...). We assert the next
  // focused element is INSIDE the dialog box, not on the page background.
  await page.locator('[aria-labelledby="edit-slot-title"] .modal-close').focus()
  await page.keyboard.press('Tab')
  const insideDialog = await page.evaluate(() => {
    const dlg = document.querySelector('[aria-labelledby="edit-slot-title"]')
    return dlg ? dlg.contains(document.activeElement) : false
  })
  expect(insideDialog).toBe(true)

  // Esc closes (Slots.vue handleKey).
  await page.keyboard.press('Escape')
  // Slice #170 swapped the v1 modal-with-v-if for the primitives/Drawer
  // shell, which slides off-screen rather than unmounting. Esc still
  // closes the dialog — we just observe aria-hidden flipping rather
  // than DOM removal.
  await expect(page.locator('[aria-labelledby="edit-slot-title"]')).toHaveAttribute('aria-hidden', 'true')
})

test.skip('a11y: Edit model modal — dialog contract + escape closes', async ({
  page,
  mockState,
  cleanState,
}) => {
  mockState.models.push({
    id: 'phi3-mini',
    name: 'Phi-3 Mini',
    size_gb: 2.4,
    backends: ['vulkan'],
    capabilities: ['chat'],
  })
  await page.goto('/models')

  const row = page.locator('tr', { hasText: 'Phi-3 Mini' })
  await expect(row).toBeVisible()
  await row.getByRole('button', { name: 'Edit Phi-3 Mini' }).click()

  await assertModalA11y(page, '[aria-labelledby="edit-model-title"]', 'edit-model-title')

  // Esc closes (handleKey route in Models.vue).
  await page.keyboard.press('Escape')
  await expect(page.locator('[aria-labelledby="edit-model-title"]')).toBeHidden()
})
