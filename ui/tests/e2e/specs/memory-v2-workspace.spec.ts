/**
 * memory-v2-workspace — Playwright coverage for the Memory v2 Bank
 * workspace (`window.MemV2Workspace`, task C4: list/filters/sources,
 * inspector + curation, ego focus view — all three commits of this task).
 *
 * NOT ROUTED YET: same situation as C2/C3 — `#memory` still renders the
 * pre-v2 `MemoryView`, and the workspace has no route/mount point of its
 * own yet (the intended home is `#memory/bank?bank=<id>`, task C6's job).
 * The brief forbids touching memory.jsx/agent-view.jsx here, and there is
 * no standalone component-mount harness in this e2e suite. Per the same
 * fallback used in C2/C3, this whole spec is `.skip`-ed; the component is
 * smoke-tested in the meantime by
 * `ui/src/dash/__tests__/memoryBankWorkspace.smoke.test.tsx` (mounts
 * `window.MemV2Workspace` under a real QueryClientProvider with the real
 * hook globals installed, via `react-dom/server`'s `renderToStaticMarkup`).
 *
 * C6 unskips: once the workspace is mounted somewhere reachable, remove
 * the `.skip` and this comment block's first two paragraphs. The
 * assertions below describe the intended contract per the task C4 brief.
 * Curation mutations (invalidate/curate/delete) will need `page.route`
 * overrides once unskipped — the GET-only forced-mock never substitutes
 * PATCH/POST/DELETE.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe.skip('memory-v2 Bank workspace (C6 unskips — not routed yet)', () => {
  test('list renders mock units for the primary bank', async ({ page }) => {
    // await page.goto('/#memory/bank?bank=primary')
    // await expect(page.getByTestId('mv-workspace')).toBeVisible()
    // buildBankUnits() mock: ~24 valid units by default for primary.
    // await expect(page.getByTestId(/mv-fact-row-/).first()).toBeVisible()
  })

  test('search narrows the list', async ({ page }) => {
    // await page.goto('/#memory/bank?bank=primary')
    // await page.getByTestId('mv-search').fill('undervolt')
    // every visible mv-fact-row should mention the undervolt fact (f15)
  })

  test('a tag chip filters the list and shows a count', async ({ page }) => {
    // await page.getByTestId('mv-tag-chip-performance').click()
    // rows narrow to the performance-tagged facts; chip shows its count
  })

  test('focusing a source filters the list by documentId', async ({ page }) => {
    // await page.getByTestId('mv-source-row-doc-thermal-notes').click()
    // list narrows to units with document_id === doc-thermal-notes
  })

  test('sort=most-connected reorders the list by salience', async ({ page }) => {
    // select the "most connected" option; row order changes to descending
    // salience (verified against buildBankUnits' deterministic ordering)
  })

  test('pagination — mv-fact-page-next advances the page', async ({ page }) => {
    // await page.getByTestId('mv-fact-page-next').click()
    // the "showing N–M of T" label advances by PAGE_SIZE
  })

  test('inspector opens on row click; invalidate/revert round-trip', async ({ page }) => {
    // Mutation — needs a page.route('**/api/memory/banks/primary/memories/**', ...)
    // PATCH fulfil once unskipped (GET-only forced-mock can't serve PATCH).
    // await page.getByTestId('mv-fact-row-f6').click()
    // await expect(page.getByTestId('mv-inspector')).toBeVisible()
    // await page.getByTestId('mv-insp-invalidate').click()
    // ... confirm ...
    // row shows "invalidated"; mv-insp-revert restores it (state back to valid)
  })

  test('history timeline renders for an observation fact', async ({ page }) => {
    // f6 is fact_type: observation — history 200s. A non-observation fact's
    // History tab should not render at all (404-as-empty, already
    // normalized in useUnitHistory from B1).
  })

  test('ego view renders for the current selection; depth slider changes node count', async ({ page }) => {
    // await page.getByTestId('mv-view-graph').click()
    // await expect(page.getByTestId('mv-ego')).toBeVisible()
    // drag/set mv-ego-depth and assert the rendered node count changes
  })

  test('Esc closes the inspector', async ({ page }) => {
    // await page.getByTestId('mv-fact-row-f6').click()
    // await page.keyboard.press('Escape')
    // await expect(page.getByTestId('mv-inspector')).toBeHidden()
  })

  test('↑/↓ moves list selection', async ({ page }) => {
    // await page.keyboard.press('ArrowDown')
    // selection advances to the next visible mv-fact-row
  })
})
