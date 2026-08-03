/**
 * slot-card-reorder-v3 — drag-to-reorder the Inference-pane slot cards.
 *
 * The cards are `SlotScard` / `MiniCard` in dash/inference-pane.jsx, ordered by
 * dash/slots/card-order.js. Arrangement is a per-browser VIEW preference: it
 * persists to localStorage as it happens, with no Save step and no request to
 * the backend.
 *
 *   R1. the grip renders on every reorderable card, top-centre.
 *   R2. dragging a card by its grip onto another reorders the grid live and
 *       lands the arrangement in localStorage — no Save step, no slot config.
 *   R3. the new order survives a reload.
 *   R4. arrow keys on a focused grip move the card one place (the accessible
 *       path, and the only one on touch — HTML5 DnD is pointer-only).
 *   R5. the utility (mini-card) tier arranges independently of the chat tier.
 *   R6. a slot created after the save lands at the end, and the saved cards
 *       keep their positions.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const CHAT_KEY = 'hal0.slots.order.inference.chat'
const UTIL_KEY = 'hal0.slots.order.inference.util'

const llm = (name: string, port: number) => ({
  name, type: 'llm', device: 'gpu-rocm', profile: 'rocm',
  runtime: 'container', container_status: 'running', container_health: true,
  model: 'qwen3.6-27b-mtp', model_id: 'qwen3.6-27b-mtp',
  group: 'chat', state: 'serving', port,
  n_gpu_layers: -1,
  metrics: { ctx: 8192, toks: 42, ttft: 180, kv: 35 },
})
const util = (name: string, type: string, port: number) => ({
  name, type, device: 'cpu', profile: 'cpu',
  model: 'nomic-v1.5', model_id: 'nomic-v1.5',
  state: 'ready', port, metrics: {},
})

const ALPHA = llm('alpha', 8092)
const BRAVO = llm('bravo', 8093)
const CHARLIE = llm('charlie', 8094)

// The dev-server suite is forced-mock: `src/api/mock.ts` answers the allowlisted
// GETs straight from `window.HAL0_DATA`, so slot seeding goes through that
// global rather than the apiMock page.route stubs (same shim the slot-card
// model-edit spec uses).
async function seedSlots(page: Page, slots: unknown[]) {
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

const pane = (page: Page) => page.locator('.infer-pane:not(.infer-hero-top)').first()
const card = (page: Page, name: string) => pane(page).getByTestId(`infer-slot-${name}`)
const grip = (page: Page, name: string) => page.getByTestId(`infer-grip-${name}`)

const namesOf = (sel: string) => async (page: Page) => {
  const ids = await pane(page)
    .locator(sel)
    .evaluateAll((els) => els.map((el) => el.getAttribute('data-testid') || ''))
  return ids.map((id) => id.replace('infer-slot-', ''))
}
/** Slot names in DOM order within the headline (chat/agent) card grid. */
const chatOrder = namesOf('.scards.full > .scard')
/** Slot names in DOM order within the utility mini-card row. */
const utilOrder = namesOf('.util-mini > .mcard')

test.describe('Slot card — drag to reorder', () => {
  test('R1 — every reorderable card carries a grip, top-centre', async ({ page }) => {
    await seedSlots(page, [ALPHA, BRAVO])
    await page.goto('/#slots')
    await expect(card(page, 'alpha')).toBeVisible()

    for (const name of ['alpha', 'bravo']) {
      const g = grip(page, name)
      await expect(g).toHaveCount(1)
      await expect(g).toHaveAttribute('draggable', 'true')
      await expect(g).toHaveAttribute('aria-label', new RegExp(`Reorder ${name}`))
    }
    const cardBox = (await card(page, 'alpha').boundingBox())!
    const gripBox = (await grip(page, 'alpha').boundingBox())!
    expect(cardBox).toBeTruthy()
    expect(gripBox).toBeTruthy()
    const cardMid = cardBox.x + cardBox.width / 2
    const gripMid = gripBox.x + gripBox.width / 2
    expect(Math.abs(cardMid - gripMid)).toBeLessThan(2)
    expect(gripBox.y - cardBox.y).toBeLessThan(4)
  })

  test('R2/R3 — a drag reorders the grid and the order survives a reload', async ({ page }) => {
    await seedSlots(page, [ALPHA, BRAVO, CHARLIE])
    await page.goto('/#slots')
    await expect(card(page, 'charlie')).toBeVisible()
    expect(await chatOrder(page)).toEqual(['alpha', 'bravo', 'charlie'])

    // The cards sit below the telemetry header; a native drag needs both ends
    // of the gesture inside the viewport, so bring the row up first.
    await card(page, 'charlie').scrollIntoViewIfNeeded()

    // Drag alpha rightwards onto charlie — it lands past charlie.
    await grip(page, 'alpha').dragTo(card(page, 'charlie'))
    await expect.poll(() => chatOrder(page)).toEqual(['bravo', 'charlie', 'alpha'])

    // No Save step: the arrangement is already persisted, client-side only.
    expect(await page.evaluate((k) => localStorage.getItem(k), CHAT_KEY)).toBe(
      JSON.stringify(['bravo', 'charlie', 'alpha']),
    )
    await page.reload()
    await expect(card(page, 'charlie')).toBeVisible()
    expect(await chatOrder(page)).toEqual(['bravo', 'charlie', 'alpha'])
  })

  test('R4 — arrow keys on a focused grip move the card one place', async ({ page }) => {
    await seedSlots(page, [ALPHA, BRAVO, CHARLIE])
    await page.goto('/#slots')
    await expect(card(page, 'charlie')).toBeVisible()

    await grip(page, 'charlie').focus()
    await page.keyboard.press('ArrowLeft')
    await expect.poll(() => chatOrder(page)).toEqual(['alpha', 'charlie', 'bravo'])

    // Focus rides along with the moved card, so the next press continues.
    await page.keyboard.press('ArrowLeft')
    await expect.poll(() => chatOrder(page)).toEqual(['charlie', 'alpha', 'bravo'])

    // Already first — a further press is a no-op, never a wrap-around.
    await page.keyboard.press('ArrowLeft')
    await expect.poll(() => chatOrder(page)).toEqual(['charlie', 'alpha', 'bravo'])
  })

  test('R5 — the utility tier arranges independently of the chat tier', async ({ page }) => {
    await seedSlots(page, [
      ALPHA,
      BRAVO,
      util('embed', 'embedding', 8100),
      util('rerank', 'reranking', 8101),
    ])
    await page.goto('/#slots')
    await expect(card(page, 'rerank')).toBeVisible()
    expect(await utilOrder(page)).toEqual(['embed', 'rerank'])

    await grip(page, 'rerank').focus()
    await page.keyboard.press('ArrowLeft')
    await expect.poll(() => utilOrder(page)).toEqual(['rerank', 'embed'])

    // Its own storage scope — the chat grid is untouched.
    expect(await chatOrder(page)).toEqual(['alpha', 'bravo'])
    expect(await page.evaluate((k) => localStorage.getItem(k), UTIL_KEY)).toBe(
      JSON.stringify(['rerank', 'embed']),
    )
    expect(await page.evaluate((k) => localStorage.getItem(k), CHAT_KEY)).toBeNull()
  })

  test('R6 — a slot created after the save lands at the end', async ({ page }) => {
    await seedSlots(page, [ALPHA, BRAVO, CHARLIE])
    await page.addInitScript(
      ([key, order]) => localStorage.setItem(key as string, JSON.stringify(order)),
      [CHAT_KEY, ['charlie', 'alpha']] as const,
    )
    await page.goto('/#slots')
    await expect(card(page, 'bravo')).toBeVisible()

    // `bravo` was never arranged — it follows the arranged pair rather than
    // scrambling their saved positions.
    expect(await chatOrder(page)).toEqual(['charlie', 'alpha', 'bravo'])
  })
})
