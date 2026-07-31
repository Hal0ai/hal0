/**
 * slot-drawer-sunset-removal-v3 — #1379: the slot drawer stops presenting
 * launch controls that have no launch effect.
 *
 * `spec-flags-ownership` §1/§4 put launch flags on the MODEL and left slots
 * with "(slot_id, name-label, model ref, port, lifecycle state)" plus — after
 * `spec-hw-slot-ownership` §2 brought the hardware axis back — the 4-field HW
 * grid. `spec-hw-slot-ownership` §8 prescribes the slot editor surface
 * exhaustively, and Template / Parallel / Extra Args are not in it.
 *
 * The launch-side readers were already deleted (`providers/container.py`:
 * `del profile_flags, slot_parallel, extra_args`), and both `ServerConfig.
 * extra_args` and `SlotConfig.parallel` describe themselves as "INERT at
 * launch … Retained for TOML round-trip". So these three controls persisted to
 * the slot TOML and reached nothing:
 *
 *   - **Template** wrote `chat_template` AND fired `POST /restart` — a cold
 *     model-load that changed no argv. The operator concludes it "didn't
 *     work" and tries again.
 *   - **Parallel** rendered a confident explainer ("N slots share the
 *     {ctx}-token context pool (--kv-unified) …") describing argv never
 *     emitted.
 *   - **Extra Args** + **Regenerate** gave false feedback: clicking Regenerate
 *     persisted the value, the stale-command overlay cleared because the
 *     baseline now matched, and the resolved command was byte-identical. The
 *     overlay disappearing was the only signal, and it was a false positive.
 *
 * Removed outright rather than left read-only, matching how Reasoning/MTP/
 * Vision were handled under `spec-hw-slot-ownership` §1. The migrator that
 * folds any already-persisted slot tune into the bound model shipped first as
 * `hal0 slot migrate-flags` (#1396/#1397), so this removal strands nothing.
 *
 * Test roles, stated plainly because they differ:
 *
 *   S1, S8 are the RED-FIRST tests — they fail on the pre-removal drawer and
 *   describe the change itself (controls gone; operator told where the launch
 *   tune actually lives).
 *
 *   S2–S7 are GUARDS, and pass before and after by design. Two of them guard
 *   against over-removal (the resolved-command preview and the restart trigger
 *   for real hardware keys must survive), and the rest guard the trap in the
 *   naive version of this change: deleting the controls but leaving the save
 *   logic. `templateDesired` is derived from a now-absent control, so it
 *   collapses to `""`; against a slot with `chat_template = "chatml"` on disk
 *   that reads as a CHANGE, and every Save would silently PUT
 *   `chat_template: null` **and cold-restart** — turning a dead control into an
 *   active data-loss bug. The save body and the restart trigger have to lose
 *   the key too, not just the JSX.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

/** A slot whose TOML still carries all three sunset keys (pre-migration box). */
const SUNSET_SLOT = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b-mtp', model_id: 'qwen3.6-27b-mtp',
  modelLong: 'Qwen3.6-27B-MTP', model_default: 'qwen3.6-27b-mtp',
  group: 'chat', state: 'serving', port: 8092,
  container_status: 'running', container_health: true,
  n_gpu_layers: -1, threads: 0, binary: '', image_pin: null,
  // The three sunset keys, all persisted.
  chat_template: 'chatml',
  parallel: 4,
  llamacpp_args: '--threads 6 --no-mmap',
  resolved_command: ['llama-server', '--model', '/models/qwen.gguf'],
  metrics: { ctx: 8192, toks: 42 },
}

async function seedSlots(page: Page, slots: any[]) {
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() {
        return real
      },
      set(v) {
        real = v
        if (v && typeof v === 'object') v.slots = slots
      },
    })
  }, slots)
}

async function captureWrites(page: Page) {
  const puts: any[] = []
  const patches: any[] = []
  const restarts: string[] = []
  await page.route('**/api/slots/primary/config', async (route) => {
    if (route.request().method() === 'PUT') {
      puts.push(JSON.parse(route.request().postData() || '{}'))
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route('**/api/slots/primary/defaults', async (route) => {
    patches.push(JSON.parse(route.request().postData() || '{}'))
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  await page.route('**/api/slots/primary/restart', async (route) => {
    restarts.push(route.request().url())
    await route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  })
  return { puts, patches, restarts }
}

const drawer = (page: Page) => page.locator('.drawer')
const rowByLabel = (page: Page, label: RegExp) =>
  page
    .locator('.drawer .form-row')
    .filter({ has: page.locator('.form-lbl > span', { hasText: label }) })

test.describe('Slot drawer — sunset launch controls are gone (#1379)', () => {
  test('S1 — Template / Parallel / Extra Args rows are absent from the drawer', async ({
    page,
  }) => {
    await seedSlots(page, [SUNSET_SLOT])
    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    await expect(rowByLabel(page, /^Template$/)).toHaveCount(0)
    await expect(rowByLabel(page, /^Parallel$/)).toHaveCount(0)
    await expect(rowByLabel(page, /^Extra Args$/)).toHaveCount(0)
    await expect(page.getByTestId('extra-args-input')).toHaveCount(0)
    // The [Override] / [Clear override] affordances go with the Template row,
    // and Regenerate + the stale-command overlay go with Extra Args (they were
    // only ever revealed by `extraArgsDirty`, which no longer exists).
    await expect(
      page.locator('.drawer button', { hasText: /^(Override|Clear override)$/ }),
    ).toHaveCount(0)
    await expect(page.getByTestId('regenerate-resolved')).toHaveCount(0)
    await expect(page.getByTestId('resolved-stale-overlay')).toHaveCount(0)
  })

  test('S3 (guard) — the resolved-command preview survives, undimmed', async ({ page }) => {
    // Over-removal guard: the preview is the one honest thing in that block —
    // server-computed real argv. Only the false "regenerate to fold your flags
    // in" story around it was a lie.
    await seedSlots(page, [SUNSET_SLOT])
    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    const details = page.locator('.drawer details')
    await details.first().click()
    const preview = page.locator('.drawer', { hasText: 'Resolved command' })
    await expect(preview).toBeVisible()
    await expect(page.locator('.drawer', { hasText: 'llama-server' }).first()).toBeVisible()
  })

  test('S4 (guard) — a Save never puts the sunset keys on the wire, even when persisted', async ({
    page,
  }) => {
    const { puts, patches, restarts } = await captureWrites(page)
    await seedSlots(page, [SUNSET_SLOT])
    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    // Touch a field that IS slot-owned, so a real write happens.
    await page.getByTestId('slot-hw-ngl').fill('24')
    await page.locator('.drawer button:has-text("Save")').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0]).toHaveProperty('n_gpu_layers', 24)
    // None of the sunset keys may ride along — the slot TOML carries all three.
    // `chat_template` is the dangerous one: the naive removal derives its
    // desired value from a control that no longer exists, gets `""`, reads
    // that as a change against the persisted "chatml", and clears the key.
    expect(puts[0]).not.toHaveProperty('chat_template')
    expect(puts[0]).not.toHaveProperty('parallel')
    expect(puts[0]).not.toHaveProperty('server')
    expect(patches).toEqual([])
  })

  test('S5 (guard) — a ctx-only Save on a slot with a persisted template does not restart', async ({
    page,
  }) => {
    // The restart trigger used to include the chat_template comparison. Under
    // the naive removal that comparison flips permanently true for any slot
    // carrying a persisted template, so EVERY save would cold-restart. A
    // ctx-only edit touches no hardware, so nothing should restart.
    const { puts, patches, restarts } = await captureWrites(page)
    await seedSlots(page, [SUNSET_SLOT])
    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    const ctxRow = rowByLabel(page, /^Context \(ceiling\)$/)
    await ctxRow.locator('input').fill('16384')
    await page.locator('.drawer button:has-text("Save")').click()

    await expect.poll(() => patches.length).toBe(1)
    expect(patches[0]).toEqual({ ctx_size: 16384 })
    expect(puts).toEqual([])
    // ctx_size takes effect on the next request — no cold restart.
    await page.waitForTimeout(250)
    expect(restarts).toEqual([])
  })

  test('S6 (guard) — a genuine hardware change still restarts', async ({ page }) => {
    // The other half of S5: dropping chat_template from the restart trigger
    // must not disarm it for the keys that really do need a cold reload.
    const { puts, restarts } = await captureWrites(page)
    await seedSlots(page, [SUNSET_SLOT])
    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    await page.getByTestId('slot-hw-threads').fill('8')
    await page.locator('.drawer button:has-text("Save")').click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0]).toHaveProperty('threads', 8)
    await expect.poll(() => restarts.length).toBe(1)
  })

  test('S7 (guard) — an untouched drawer on a fully-sunset slot is not dirty', async ({
    page,
  }) => {
    // Every removed control was in the dirty aggregate. If any residue stayed
    // behind, opening this slot would arm the discard guard on a clean close.
    const { puts, patches, restarts } = await captureWrites(page)
    await seedSlots(page, [SUNSET_SLOT])
    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    await page.locator('.drawer button:has-text("Cancel")').click()
    await expect(drawer(page)).toHaveCount(0)
    expect(puts).toEqual([])
    expect(patches).toEqual([])
    expect(restarts).toEqual([])
  })

  test('S8 — the Model group points the operator at the model for the launch tune', async ({
    page,
  }) => {
    // Removal without a signpost just relocates the confusion: the operator
    // still has to learn WHERE the template/flags went.
    await seedSlots(page, [SUNSET_SLOT])
    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    await expect(page.getByTestId('slot-launch-tune-note')).toContainText(/model/i)
    // The existing in-place editor is the destination.
    await expect(page.getByTestId('slot-model-edit-open')).toBeVisible()
  })
})
