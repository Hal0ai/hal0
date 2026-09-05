/**
 * memory-v2-bankbar — Playwright coverage for the Memory v2 Bank workspace
 * BankBar + Add modal (`window.MemV2BankBar` / `window.MemV2AddModal`,
 * task C3).
 *
 * Unskipped by task C6: `#memory/bank?bank=primary` now renders
 * `MemoryView`'s Bank sub-tab (memory-bank-workspace.jsx), which embeds
 * `window.MemV2BankBar` at the top. Mutations the GET-only forced-mock
 * can't serve (add-fact POST) use `page.route` overrides.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('memory-v2 BankBar + Add modal', () => {
  test('reflect round-trip against the mock reflect payload', async ({ page }) => {
    // Mutation (POST) — mock.ts's client-side forced-mock substitution is
    // GET-only (confirmed in the C3 report), so this always falls through
    // to the real network, which the apiMock fixture's blanket `/api/`
    // catch-all answers with `{}` unless overridden here.
    let postBody: unknown = null
    await page.route('**/api/memory/banks/primary/reflect', (route) => {
      postBody = route.request().postDataJSON()
      return route.fulfill({
        json: {
          text: 'Over the last six weeks the strix-halo-01 operator hardened a fresh hal0 install into a resilient daily driver.',
          based_on: { facts: 22, documents: 6, mental_models: 3 },
        },
      })
    })
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-reflect-tab').click()
    await page.getByTestId('mv-reflect-q').fill('why is extraction lagging?')
    await page.getByTestId('mv-reflect-run').click()
    await expect(page.getByTestId('mv-reflect-out')).toBeVisible()
    await expect(page.getByTestId('mv-reflect-out')).toContainText('strix-halo-01')
    // Post-smoke fix: upstream Hindsight 0.8.4 requires `query`, not `text`,
    // in the request body (curl-verified live, 2026-08-21 — `text` 422s).
    // The response the mock above returns still carries `text` (the
    // answer), asserted above via mv-reflect-out — only the request field
    // changed.
    expect(postBody).toMatchObject({ query: 'why is extraction lagging?' })
    expect(postBody).not.toHaveProperty('text')
  })

  test('the bank-select dropdown actually switches banks (regression, live-reproduced 2026-08-22)', async ({
    page,
  }) => {
    // Live bug: picking a different bank from the dropdown briefly fetched
    // the new bank's data (network requests fired) then immediately
    // snapped the hash and the dropdown's value back to the old bank —
    // stuck. Root cause: BankWorkspace's `switchBank` called `setSel(null)`
    // right after `setBank(id)`; memory.jsx's `setSel` closes over its own
    // (stale, not-yet-updated-this-render) `bankId` when it rewrites the
    // hash, clobbering the bank `setBank` had just written. Pins both the
    // URL and the select element's own value actually landing on the new
    // bank, not just a transient fetch.
    await page.goto('/#memory/bank?bank=primary')
    await expect(page.getByTestId('mv-bank-select')).toHaveValue('primary')

    await page.getByTestId('mv-bank-select').selectOption('scratch')

    await expect(page).toHaveURL(/#memory\/bank\?bank=scratch/)
    await expect(page.getByTestId('mv-bank-select')).toHaveValue('scratch')
  })

  test('rules tab lists directives and mental models from the mock', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-rules-tab').click()
    // buildDirectives()/buildMentalModels() mock rows — at least one
    // mv-rule-row-{id} renders (shared testid pattern for both sub-lists).
    await expect(page.locator('[data-testid^="mv-rule-row-"]').first()).toBeVisible()
  })

  // Live screenshot feedback (2026-08-22): the "…" button next to + Add had
  // no onClick at all — did nothing when clicked, despite its
  // "consolidate · export · wipe" title. Wired to the one real, safe,
  // already-hooked action (consolidate-now); this pins it actually fires.
  test('the "more" button next to + Add now triggers consolidate-now', async ({ page }) => {
    let posted = false
    await page.route('**/api/memory/banks/primary/consolidate', (route) => {
      posted = true
      return route.fulfill({ json: { queued: true } })
    })
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-consolidate-now').click()
    await expect.poll(() => posted).toBe(true)
    await expect(page.getByText('Consolidation queued')).toBeVisible()
  })

  test('add-fact posts to /api/memory/add and toasts on success', async ({ page }) => {
    // Mutation — the GET-only forced-mock never substitutes this route.
    await page.route('**/api/memory/add', (route) =>
      route.fulfill({ json: { id: 'm-test', timestamp: new Date().toISOString() } }),
    )
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-add-open').click()
    await expect(page.getByTestId('mv-add-modal')).toBeVisible()
    await page.getByTestId('mv-add-fact-text').fill('a new fact to remember')
    await page.getByTestId('mv-add-fact-tags').fill('ci, thermal')
    await page.getByTestId('mv-add-submit').click()
    await expect(page.getByText('Fact added')).toBeVisible()
  })

  test('bank delete: trash → type-to-confirm → DELETE with the echoed confirm (#2107)', async ({
    page,
  }) => {
    // The only UI path to delete a bank (the pre-v2 MemBankDetail danger
    // zone) was unreachable after the v2 rewrite; #2107 moved it onto the
    // BankBar. Mutation — the GET-only forced-mock never substitutes this
    // route, so intercept the DELETE and assert the backend's #1024
    // echoed-id guard (?confirm=<bank_id>) is satisfied.
    let confirmEcho: string | null = null
    await page.route(
      (url) => url.pathname.endsWith('/api/memory/banks/primary'),
      async (route) => {
        if (route.request().method() !== 'DELETE') return route.fallback()
        confirmEcho = new URL(route.request().url()).searchParams.get('confirm')
        await route.fulfill({ json: { status: 'deleted' } })
      },
    )
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-bank-delete').click()
    await expect(page.getByText('Delete bank "primary"?')).toBeVisible()
    // Blast radius names what dies with the bank.
    await expect(page.getByTestId('mv-bank-delete-blast')).toContainText('facts')
    // Destructive gate: Delete bank stays disabled until the id is typed back.
    const confirmBtn = page.getByRole('button', { name: 'Delete bank', exact: true })
    await expect(confirmBtn).toBeDisabled()
    await page.locator('.modal-backdrop input').fill('primary')
    await expect(confirmBtn).toBeEnabled()
    await confirmBtn.click()
    await expect(page.getByText('Bank primary deleted')).toBeVisible()
    expect(confirmEcho).toBe('primary')
  })
})
