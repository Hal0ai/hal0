/**
 * models-catalog-controls-v3 — the WS-13 catalog toolbar (sort dropdown +
 * direction toggle, curated tag-filter chips, quant chip on rows + detail
 * cell, "added" detail cell) and the WS-6 chat-template pick in the
 * Add-by-HF modal.
 *
 * Forced-mock note: VITE_MOCK_HAL0 short-circuits page.route for
 * /api/models, so the catalog fixture rows are injected by intercepting the
 * `window.HAL0_DATA = …` assignment (data.jsx) via addInitScript — the mock
 * layer reads HAL0_DATA.models lazily on every fetch. The Add-by-HF flow
 * mocks POST /api/models/inspect directly (it isn't part of HAL0_DATA).
 */
import { test, expect } from '../fixtures/apiMock'

// Three installed local rows with distinct sort keys, quants, tags, and
// added-timestamps so every control has something to bite on.
const ROWS = [
  {
    id: 'local.alpha-7b',
    name: 'alpha-7b',
    longName: 'Alpha 7B',
    installed: true,
    ns: 'pulled',
    capabilities: ['chat', 'tool-calling'],
    backends: ['rocm'],
    size_bytes: 4_500_000_000,
    params: '7B',
    quant: 'Q4_K_M',
    tags: ['coder'],
    created: 1_700_000_000,
  },
  {
    id: 'local.zeta-27b',
    name: 'zeta-27b',
    longName: 'Zeta 27B',
    installed: true,
    ns: 'pulled',
    capabilities: ['chat'],
    backends: ['rocm'],
    size_bytes: 16_000_000_000,
    params: '27B',
    quant: 'Q8_0',
    tags: ['moe'],
    created: 1_600_000_000,
  },
  {
    id: 'local.mid-13b',
    name: 'mid-13b',
    longName: 'Mid 13B',
    installed: true,
    ns: 'pulled',
    capabilities: ['chat'],
    backends: ['vulkan'],
    size_bytes: 9_000_000_000,
    params: '13B',
    quant: 'Q5_K_M',
    tags: ['coder', 'moe'],
    created: 1_650_000_000,
  },
]

test.beforeEach(async ({ page }) => {
  await page.addInitScript((rows) => {
    let stored: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get: () => stored,
      set: (v) => {
        if (v && Array.isArray(v.models)) v.models = [...rows, ...v.models]
        stored = v
      },
    })
  }, ROWS)
})

// Names of the installed section's rows, top to bottom.
async function installedOrder(page: any): Promise<string[]> {
  const names = await page.locator('.mdl-row .nm').allInnerTexts()
  // Keep only our fixtures (the mock catalog may add its own rows).
  return names
    .map((t: string) => t.split('\n')[0].trim())
    .filter((n: string) => ['Alpha 7B', 'Zeta 27B', 'Mid 13B'].includes(n))
}

test.describe('Models v3 — catalog controls (/models)', () => {
  test('sort by size asc/desc reorders within the section', async ({ page }) => {
    await page.goto('/#models')
    await expect(page.locator('.mdl-row', { hasText: 'Alpha 7B' })).toBeVisible()

    await page.getByTestId('mdl-sort-field').selectOption('size')
    // default dir is asc → smallest first
    expect(await installedOrder(page)).toEqual(['Alpha 7B', 'Mid 13B', 'Zeta 27B'])

    await page.getByTestId('mdl-sort-dir').click() // → desc
    expect(await installedOrder(page)).toEqual(['Zeta 27B', 'Mid 13B', 'Alpha 7B'])
  })

  test('sort by params orders by parsed count, not string', async ({ page }) => {
    await page.goto('/#models')
    await page.getByTestId('mdl-sort-field').selectOption('params')
    // asc: 7B < 13B < 27B (numeric, not lexicographic where "13B" < "7B")
    expect(await installedOrder(page)).toEqual(['Alpha 7B', 'Mid 13B', 'Zeta 27B'])
  })

  test('tag chips narrow the catalog with AND semantics', async ({ page }) => {
    await page.goto('/#models')
    // coder → alpha + mid; moe → zeta + mid; coder AND moe → mid only.
    await page.getByTestId('mdl-tag-coder').click()
    await expect(page.locator('.mdl-row', { hasText: 'Alpha 7B' })).toBeVisible()
    await expect(page.locator('.mdl-row', { hasText: 'Mid 13B' })).toBeVisible()
    await expect(page.locator('.mdl-row', { hasText: 'Zeta 27B' })).toHaveCount(0)

    await page.getByTestId('mdl-tag-moe').click()
    await expect(page.locator('.mdl-row', { hasText: 'Mid 13B' })).toBeVisible()
    await expect(page.locator('.mdl-row', { hasText: 'Alpha 7B' })).toHaveCount(0)
    await expect(page.locator('.mdl-row', { hasText: 'Zeta 27B' })).toHaveCount(0)
  })

  test('quant chip renders on rows and in the detail meta grid', async ({ page }) => {
    await page.goto('/#models')
    const row = page.locator('.mdl-row', { hasText: 'Alpha 7B' })
    await expect(row.getByTestId('mdl-row-quant')).toHaveText('Q4_K_M')

    await row.click()
    await expect(page.getByTestId('mdl-detail-quant')).toHaveText('Q4_K_M')
    // "added" cell renders a humane label (not a raw timestamp).
    await expect(
      page.locator('.mdl-detail-meta > div', { hasText: 'added' }).locator('.v'),
    ).not.toHaveText('—')
  })

  test('Add-by-HF modal offers a chat-template pick', async ({ page }) => {
    await page.route('**/api/chat-templates', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 'chatml', label: 'ChatML', valid: true, error: null },
          { id: 'llama3', label: 'Llama 3', valid: true, error: null },
        ]),
      }),
    )
    await page.route('**/api/models/inspect', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          repo: 'unsloth/Qwen3-8B-GGUF',
          cached: false,
          variants: [{ id: 'qwen3-8b-q4_k_m.gguf', size_bytes: 4_900_000_000, size: '4.56 GB' }],
          tags: ['gguf'],
          metadata: {},
        }),
      }),
    )
    await page.goto('/#models')
    await page.locator('.view .vh button:has-text("Add by HF coords")').click()
    await page.locator('input[placeholder*="unsloth/Qwen3-8B-GGUF"]').fill('unsloth/Qwen3-8B-GGUF')
    await page.locator('button:has-text("Inspect")').click()

    const select = page.locator('.chat-template-select')
    await expect(select).toBeVisible()
    // "auto" is the default; the catalogue options come from the mock.
    await expect(select).toHaveValue('auto')
    await expect(select.locator('option', { hasText: 'ChatML' })).toHaveCount(1)
    await select.selectOption('chatml')
    await expect(select).toHaveValue('chatml')
  })
})
