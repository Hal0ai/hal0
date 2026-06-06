/**
 * slots-v3 — `#slots` route renders grouped sections (Chat / Capabilities /
 * Image / NPU rollup) + slot cards + the "New slot" CTA. The embedding,
 * reranking, transcription and tts slots are merged into one denser
 * "Capabilities" section rendered as a 4-up quarter-width grid (C7).
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Slots v3 (/slots)', () => {
  test('renders grouped sections + slot cards', async ({ page }) => {
    await page.goto('/#slots')
    await expect(page.locator('.view .vh h1')).toHaveText('Slots')
    // at least one group section h2 (chat/capabilities/image) renders
    await expect(page.locator('.view .sec h2').first()).toBeVisible()
    // slot cards or list rows present
    const cards = page.locator('.slots-grid > *, .slots-list > *')
    expect(await cards.count()).toBeGreaterThan(0)
  })

  test('Capabilities section (C7) renders the utility slots in a 4-up quarter grid', async ({ page }) => {
    await page.goto('/#slots')
    // The embedding/reranking/transcription/tts slots collapse into one
    // "Capabilities" section heading (replacing the old Embed + Voice sections).
    await expect(page.locator('.view .sec h2', { hasText: 'Capabilities' })).toBeVisible()
    // ...and that section's grid carries the quarter-width modifier.
    const quarterGrid = page.locator('.slots-grid.quarter')
    await expect(quarterGrid.first()).toBeVisible()
    expect(await quarterGrid.locator('> *').count()).toBeGreaterThan(0)
  })

  test('exposes New-slot button (create modal trigger)', async ({ page }) => {
    await page.goto('/#slots')
    const newBtn = page.locator('.view .vh button:has-text("New slot")')
    await expect(newBtn).toBeVisible()
  })

  test('enabled-first sort (C6): a disabled slot sinks to the end of its section', async ({ page }) => {
    await page.goto('/#slots')
    // The Chat section: HAL0_DATA declares a disabled "legacy" slot early in
    // source order; the enabled-first sort must render it last.
    const chatSection = page.locator('.view section', {
      has: page.locator('.sec h2', { hasText: 'Chat' }),
    })
    const names = await chatSection.locator('.slot .slot-name .nm').allInnerTexts()
    expect(names.length).toBeGreaterThan(1)
    expect(names).toContain('legacy')
    expect(names[names.length - 1]).toBe('legacy') // disabled → last
    expect(names[0]).not.toBe('legacy') // enabled slots come first
  })

  test('NPU rollup section renders when an NPU slot is present', async ({ page }) => {
    await page.goto('/#slots')
    // HAL0_DATA seeds at least one device=npu slot, so the NPU section h2 should appear
    await expect(page.locator('.view .sec h2', { hasText: 'NPU' })).toBeVisible()
  })

  test('NPU trio: chat is a model picker; ASR/embed are read-only labels (model fixed by FLM)', async ({ page }) => {
    await page.goto('/#slots')
    const stack = page.locator('.npu-stack')
    await expect(stack).toBeVisible()
    // Chat (the FLM anchor) keeps a real <select> model picker.
    await expect(stack.locator('.npu-sel')).toHaveCount(1)
    // ASR + embed serve coresident off that one process with the model fixed
    // by the --asr/--embed flags, so they render read-only labels, not pickers.
    await expect(stack.locator('.npu-mod-fixed')).toHaveCount(2)
    await expect(stack.locator('.npu-mod-fixed .npu-fixed-tag')).toHaveCount(2)
  })
})
