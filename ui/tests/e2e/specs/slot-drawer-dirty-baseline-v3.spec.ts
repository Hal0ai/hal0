/**
 * slot-drawer-dirty-baseline-v3 — the drawer's persisted-config baseline is
 * frozen at open (#1390 + #1391, root cause #1398).
 *
 * The class of bug (#1398): the drawer seeds its form ONCE (effect keyed on
 * `[slot?.name]`) but computes "is this field dirty?" against the LIVE `slot`
 * prop, which `useSlots` re-derives every 5s. The two sides drift with no
 * operator input, so Save writes fields nobody touched — and a hardware key in
 * that write triggers a cold restart.
 *
 * ADR-0029: the slot carries placement only — the context window is the
 * profile's, shown read-only as `ctx_effective` + `ctx_source` — so there is no
 * context ceiling to baseline and PATCH /defaults is never fired by the drawer.
 *
 * Two live instances, one spec each below:
 *
 *   B1 (#1390) — the drawer once seeded a ctx field from `slot.metrics.ctx`, a
 *       LIVE runtime metric, so a slot that started serving under the open
 *       drawer made an untouched Context field dirty. The metric must stay
 *       out of the picture: open on a cold slot, let it start serving
 *       (metrics.ctx 0 → 4096), touch nothing, Save — zero writes, and the
 *       effective context still reads the PROFILE's window, never the metric.
 *
 *   B2 (#1391) — a dropped `/api/slots` poll degrades the entry to the bare
 *       `/api/status` shape, which carries none of the TOML-derived config
 *       fields. Pre-fix every batched field read dirty for that interval, so an
 *       idle Save rewrote binary/NGL and fired `POST /restart`.
 *       Reachable from the harness via the `__hal0MockSlotsDegraded` knob
 *       (mockFixtures.ts), which fails `GET /api/slots` and strips the config
 *       keys off the `/api/status` union entries — exactly what the backend
 *       does (config_enrichment is applied only in the /api/slots builder).
 *
 * Both are asserted on the WIRE (zero writes), not on a rendered value — a
 * spurious write looks identical to an intentional one in the API log, which
 * is why the class went unnoticed for so long.
 *
 * Harness note (mirrors slot-edit-controls-v3): VITE_MOCK_HAL0=1 short-circuits
 * GET /api/slots + /api/status before page.route sees them, so the slot list is
 * controlled through `window.HAL0_DATA.slots`. Mutations are NOT allowlisted,
 * so page.route captures every PUT/PATCH/POST the drawer fires.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

/** Cold slot: its profile sets a 32K window, and the live metric reads 0. */
const COLD = {
  name: 'primary', type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  model: 'qwen3.6-27b', model_id: 'qwen3.6-27b', model_default: 'qwen3.6-27b',
  group: 'chat', state: 'ready', port: 8092,
  container_status: 'running', container_health: true,
  n_gpu_layers: -1, threads: 0, binary: '', image_pin: null,
  ctx_effective: 32768, ctx_source: 'profile',
  metrics: { ctx: 0, toks: 0 },
}

/** The same slot one poll later — it started serving and reports a live ctx. */
const HOT = { ...COLD, state: 'serving', metrics: { ctx: 4096, toks: 42 } }

/** A slot with real persisted config — the payload #1391 degrades away. */
const CONFIGURED = {
  ...COLD,
  state: 'serving',
  ctx_effective: 16384,
  ctx_source: 'native',
  binary: 'rocm-7.2',
  n_gpu_layers: 48,
  threads: 12,
  metrics: { ctx: 16384, toks: 42 },
}

/**
 * Override the in-bundle HAL0_DATA.slots. `data.jsx` assigns
 * `window.HAL0_DATA = {...}` at module load, so install a setter that patches
 * `.slots` as the assignment lands; the mock builders read it on every poll.
 * A later `window.HAL0_DATA.slots = [...]` from the test goes through the
 * getter and lands on the same object, so the NEXT poll sees it.
 */
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

/** Capture every mutating call the drawer can make for `primary`. */
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
const saveBtn = (page: Page) => page.locator('.drawer button:has-text("Save")')
const stateChip = (page: Page) => page.getByTestId('slot-state-readonly')

// The effective context is a read-only hint under the Profile row — there is
// no input for it any more (ADR-0029).
const ctxEffective = (page: Page) => page.getByTestId('slot-ctx-effective')
const noCtxInput = (page: Page) =>
  page.locator('.drawer .form-lbl > span', { hasText: /^Context \(ceiling\)$/ })

test.describe('Slot drawer — frozen persisted-config baseline', () => {
  test('B1 (#1390) — the live ctx metric never rides Save, and never replaces the profile window', async ({
    page,
  }) => {
    const { puts, patches, restarts } = await captureWrites(page)
    await seedSlots(page, [COLD])

    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()
    // The profile sets 32K; the slot is cold (metrics.ctx = 0). The ceiling
    // input is gone, and the hint reads the profile's window.
    await expect(noCtxInput(page)).toHaveCount(0)
    await expect(ctxEffective(page)).toContainText('32,768')
    await expect(ctxEffective(page)).toContainText('set by profile rocm')

    // The slot starts serving. metrics.ctx moves 0 → 4096 under the open
    // drawer; the state chip flipping is our proof the poll landed.
    await page.evaluate((s) => {
      ;(window as any).HAL0_DATA.slots = s
    }, [HOT])
    await expect(stateChip(page)).toHaveText('serving', { timeout: 20_000 })

    // The operator touched nothing — the hint still shows the profile's
    // window, not the 4096 the runtime reports.
    await expect(ctxEffective(page)).toContainText('32,768')

    await saveBtn(page).click()
    await expect(drawer(page)).toHaveCount(0)
    // A live runtime observation must never become a persisted write.
    expect(patches).toEqual([])
    expect(puts).toEqual([])
    expect(restarts).toEqual([])
  })

  test('B1 (#1390) — a moving ctx metric does not arm the unsaved-changes guard', async ({
    page,
  }) => {
    await captureWrites(page)
    await seedSlots(page, [COLD])

    await page.goto('/#slots/primary')
    await expect(ctxEffective(page)).toContainText('32,768')
    await page.evaluate((s) => {
      ;(window as any).HAL0_DATA.slots = s
    }, [HOT])
    await expect(stateChip(page)).toHaveText('serving', { timeout: 20_000 })

    // Clean close: Cancel must not raise the discard confirm.
    await page.locator('.drawer button:has-text("Cancel")').click()
    await expect(drawer(page)).toHaveCount(0)
  })

  test('B2 (#1391) — a degraded slot payload disables Save instead of rewriting every field', async ({
    page,
  }) => {
    const { puts, patches, restarts } = await captureWrites(page)
    await seedSlots(page, [CONFIGURED])
    // Drop /api/slots for the whole test — the drawer only ever sees the bare
    // /api/status shape, with no config enrichment on it.
    await page.addInitScript(() => {
      ;(window as any).__hal0MockSlotsDegraded = true
    })

    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()

    // No baseline can be derived from a degraded payload, so Save is refused
    // with an operator-legible reason rather than silently writing garbage.
    await expect(saveBtn(page)).toBeDisabled({ timeout: 20_000 })
    await expect(page.getByTestId('slot-drawer-degraded')).toContainText(
      /slot data degraded/i,
    )

    await saveBtn(page).click({ force: true })
    await page.waitForTimeout(250)
    expect(puts).toEqual([])
    expect(patches).toEqual([])
    expect(restarts).toEqual([])
  })

  test('B2 (#1391) — a dropped poll mid-edit cannot poison the baseline or force a restart', async ({
    page,
  }) => {
    const { puts, patches, restarts } = await captureWrites(page)
    await seedSlots(page, [CONFIGURED])

    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()
    await expect(ctxEffective(page)).toContainText('16,384')

    // One /api/slots poll drops. Every config field vanishes off the prop.
    await page.evaluate(() => {
      ;(window as any).__hal0MockSlotsDegraded = true
    })
    await expect(saveBtn(page)).toBeDisabled({ timeout: 20_000 })

    // …and comes back. The frozen baseline is still the one the drawer opened
    // with, so an idle Save stays silent — no rewrite, no cold restart.
    await page.evaluate(() => {
      ;(window as any).__hal0MockSlotsDegraded = false
    })
    await expect(saveBtn(page)).toBeEnabled({ timeout: 20_000 })
    await expect(ctxEffective(page)).toContainText('16,384')

    await saveBtn(page).click()
    await expect(drawer(page)).toHaveCount(0)
    expect(puts).toEqual([])
    expect(patches).toEqual([])
    expect(restarts).toEqual([])
  })

  test('B2 (#1391) — after a degraded poll recovers, Save still writes the touched field', async ({
    page,
  }) => {
    const { puts, patches, restarts } = await captureWrites(page)
    await seedSlots(page, [CONFIGURED])

    await page.goto('/#slots/primary')
    await expect(drawer(page)).toBeVisible()
    // NGL lives behind the Advanced disclosure now (Task 11a).
    await page.getByTestId('slot-hw-advanced').click()
    await page.getByTestId('slot-hw-ngl').fill('24')

    await page.evaluate(() => {
      ;(window as any).__hal0MockSlotsDegraded = true
    })
    await expect(saveBtn(page)).toBeDisabled({ timeout: 20_000 })
    // The operator's in-flight edit survives the blip.
    await expect(page.getByTestId('slot-hw-ngl')).toHaveValue('24')
    await page.evaluate(() => {
      ;(window as any).__hal0MockSlotsDegraded = false
    })
    await expect(saveBtn(page)).toBeEnabled({ timeout: 20_000 })

    await saveBtn(page).click()
    await expect.poll(() => puts.length).toBe(1)
    // Exactly the one field the operator touched — nothing else.
    expect(puts[0]).toEqual({ n_gpu_layers: 24 })
    expect(patches).toEqual([])
    // NGL is a hardware key, so this write legitimately restarts.
    await expect.poll(() => restarts.length).toBe(1)
  })
})
