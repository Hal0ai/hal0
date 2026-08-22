/**
 * memory-v2-workspace — Playwright coverage for the Memory v2 Bank
 * workspace (`window.MemV2Workspace`, task C4: list/filters/sources,
 * inspector + curation, ego focus view — all three commits of that task).
 *
 * Unskipped by task C6: `#memory/bank?bank=primary` now renders
 * `MemoryView`'s Bank sub-tab, which mounts `window.MemV2Workspace`
 * directly (memory.jsx). Runs against the default forced-mock dataset
 * (26-fact MEM_FACTS set, f9/f20 pre-invalidated) — curation mutations
 * (PATCH/POST/DELETE) use `page.route` overrides since the GET-only
 * forced-mock never substitutes them.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('memory-v2 Bank workspace', () => {
  test('list renders mock units for the primary bank', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    // buildBankUnits() mock: 24 valid units by default for primary (26
    // total, f9/f20 pre-invalidated).
    await expect(page.locator('[data-testid^="mv-fact-row-"]').first()).toBeVisible()
    expect(await page.locator('[data-testid^="mv-fact-row-"]').count()).toBeGreaterThan(0)
  })

  test('search narrows the list', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-search').fill('undervolt')
    // f15 — "Applied −30mV undervolt via ryzenadj" — is the only fact
    // mentioning "undervolt".
    await expect(page.getByTestId('mv-fact-row-f15')).toBeVisible()
    expect(await page.locator('[data-testid^="mv-fact-row-"]').count()).toBe(1)
  })

  test('a tag chip filters the list and shows a count', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-tag-chip-performance').click()
    // performance topic: f13, f14, f15, f16 — all valid.
    for (const id of ['f13', 'f14', 'f15', 'f16']) {
      await expect(page.getByTestId(`mv-fact-row-${id}`)).toBeVisible()
    }
    expect(await page.locator('[data-testid^="mv-fact-row-"]').count()).toBe(4)
  })

  test('focusing a source filters the list by documentId', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-source-row-doc-thermal-notes').click()
    // doc-thermal-notes maps from the performance topic — same 4 facts.
    for (const id of ['f13', 'f14', 'f15', 'f16']) {
      await expect(page.getByTestId(`mv-fact-row-${id}`)).toBeVisible()
    }
    expect(await page.locator('[data-testid^="mv-fact-row-"]').count()).toBe(4)
  })

  test('sort=most-connected reorders the list by salience', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await expect(page.locator('[data-testid^="mv-fact-row-"]').first()).toBeVisible()
    const recentOrder = await page.locator('[data-testid^="mv-fact-row-"]').allTextContents()
    await page.getByTestId('mv-sort').selectOption('salience')
    // Give the refetch a moment, then compare full row order — some
    // individual facts can legitimately tie for the lead position between
    // the two sorts, but the two full 10-row orderings should not be
    // identical across 26 facts with varied link-degree salience.
    await expect(async () => {
      const salienceOrder = await page.locator('[data-testid^="mv-fact-row-"]').allTextContents()
      expect(salienceOrder).not.toEqual(recentOrder)
    }).toPass({ timeout: 5000 })
  })

  test('pagination — mv-fact-page-next advances the page', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    const label = page.locator('.mini.num').first()
    await expect(label).toContainText('1–10')
    await page.getByTestId('mv-fact-page-next').click()
    await expect(label).toContainText('11–20')
  })

  test('inspector opens on row click; invalidate/revert round-trip', async ({ page }) => {
    // f15 ("Applied −30mV undervolt via ryzenadj") is fact_type: world —
    // curatable (only observation-typed facts hide Invalidate, per the
    // expert curation constraint). Isolate it via search rather than
    // assuming its page position under the default recency sort (it's an
    // older fact — not on page 1).
    let state = 'valid'
    await page.route('**/api/memory/banks/primary/memories/f15', (route) => {
      const body = route.request().postDataJSON() as { state?: string }
      if (body.state) state = body.state
      route.fulfill({
        json: {
          id: 'f15',
          text: 'Applied a −30mV iGPU undervolt via ryzenadj; added it to the boot unit.',
          context: 'Applied −30mV undervolt',
          occurred_start: '2026-05-14T16:25:00.000Z',
          fact_type: 'world',
          entities: ['ryzenadj', 'Radeon 8060S'],
          tags: ['performance'],
          document_id: 'doc-thermal-notes',
          state,
          salience: 0.5,
          link_counts_by_type: { temporal: 1 },
        },
      })
    })
    await page.goto('/#memory/bank?bank=primary')
    await page.getByTestId('mv-search').fill('undervolt')
    await page.getByTestId('mv-fact-row-f15').click()
    await expect(page.getByTestId('mv-inspector')).toBeVisible()
    await page.getByTestId('mv-insp-invalidate').click()
    await page.getByRole('button', { name: 'Invalidate' }).click()
    // Scoped to the Inspector — the same "invalidated — excluded from
    // recall" copy also appears in the (separate) success toast.
    const inspector = page.getByTestId('mv-inspector')
    await expect(inspector.getByText('invalidated — excluded from recall')).toBeVisible()
    await page.getByTestId('mv-insp-revert').click()
    await expect(inspector.getByText('invalidated — excluded from recall')).toHaveCount(0)
  })

  test('history renders for an observation fact; the tab is absent for a non-observation fact', async ({
    page,
  }) => {
    await page.goto('/#memory/bank?bank=primary')
    // f6 — "Prefers terse technical answers" — is fact_type: observation.
    // Isolated via search (not on page 1 under the default recency sort).
    await page.getByTestId('mv-search').fill('terse')
    await page.getByTestId('mv-fact-row-f6').click()
    await expect(page.getByTestId('mv-inspector')).toBeVisible()
    await expect(page.getByTestId('mv-insp-history')).toBeVisible()
    await page.getByTestId('mv-insp-history').click()
    await expect(page.locator('.mvi-hist')).toBeVisible()

    // Close the inspector (its right-column slot replaces the filter
    // card — including mv-search — while a fact is selected) before
    // searching for the next one.
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('mv-inspector')).toHaveCount(0)

    // f15 — "Applied −30mV undervolt" — is fact_type: world; the History
    // toggle must not render at all (404-as-empty is a B1 normalization
    // concern, not a UI affordance to offer where it can't answer).
    await page.getByTestId('mv-search').fill('undervolt')
    await page.getByTestId('mv-fact-row-f15').click()
    await expect(page.getByTestId('mv-insp-history')).toHaveCount(0)
  })

  test('ego view renders for the current selection; depth slider updates', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await expect(page.locator('[data-testid^="mv-fact-row-"]').first()).toBeVisible()
    await page.locator('[data-testid^="mv-fact-row-"]').first().click()
    await page.getByTestId('mv-view-graph').click()
    await expect(page.getByTestId('mv-ego')).toBeVisible()
    await expect(page.getByTestId('mv-ego-depth')).toBeVisible()
    await page.getByTestId('mv-ego-depth').fill('5')
    await expect(page.getByTestId('mv-ego').locator('.mv-depthslider b')).toHaveText('5')
  })

  test('Esc closes the inspector', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await expect(page.locator('[data-testid^="mv-fact-row-"]').first()).toBeVisible()
    await page.locator('[data-testid^="mv-fact-row-"]').first().click()
    await expect(page.getByTestId('mv-inspector')).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('mv-inspector')).toHaveCount(0)
    await expect(page.getByTestId('mv-filter-card')).toBeVisible()
  })

  test('↑/↓ moves list selection', async ({ page }) => {
    await page.goto('/#memory/bank?bank=primary')
    await expect(page.locator('[data-testid^="mv-fact-row-"]').first()).toBeVisible()
    await expect(page.locator('.mv-fact.on')).toHaveCount(0)
    await page.keyboard.press('ArrowDown')
    await expect(page.locator('.mv-fact.on')).toHaveCount(1)
    await expect(page.getByTestId('mv-inspector')).toBeVisible()
  })

  // task C8: a genuinely fresh/empty bank (MEM_BANKS' `empty` — fact_count:
  // 0, no filters active) used to render the same "no facts match — clear a
  // filter" copy as a filtered-to-nothing search, telling the operator to
  // clear a filter that doesn't exist. Pins the dedicated empty-bank copy.
  test('a genuinely empty bank shows an "add your first fact" empty-state, not "clear a filter"', async ({
    page,
  }) => {
    await page.goto('/#memory/bank?bank=empty')
    await expect(page.getByTestId('mv-workspace')).toBeVisible()
    const empty = page.getByTestId('mv-units-empty-bank')
    await expect(empty).toBeVisible()
    await expect(empty).toContainText('No memories in this bank yet')
    await expect(page.getByText('no facts match — clear a filter')).toHaveCount(0)
  })
})
