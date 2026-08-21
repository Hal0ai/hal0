/**
 * memory-v2-bankbar — Playwright coverage for the Memory v2 Bank workspace
 * BankBar + Add modal (`window.MemV2BankBar` / `window.MemV2AddModal`,
 * task C3).
 *
 * NOT ROUTED YET: same situation as memory-v2-overview.spec.ts (task C2) —
 * `#memory` still renders the pre-v2 `MemoryView` (dash/memory.jsx), and
 * BankBar/AddModal have no route/mount point of their own yet (the intended
 * home is a bank-detail route, e.g. `#memory/bank?bank=<id>`, which is task
 * C6's job). The brief explicitly forbids touching memory.jsx/agent-view.jsx
 * here to force a route just for this spec, and no standalone
 * component-mount harness exists in this e2e suite to mount it another way.
 * Per the same fallback used in C2, this whole spec is `.skip`-ed; the
 * component itself is smoke-tested in the meantime by
 * `ui/src/dash/__tests__/memoryBankBar.smoke.test.tsx` (mounts
 * `window.MemV2BankBar` and `window.MemV2AddModal` under a real
 * QueryClientProvider with the real hook globals installed, via
 * `react-dom/server`'s `renderToStaticMarkup`).
 *
 * C6 unskips: once BankBar is mounted somewhere reachable (most likely a
 * bank-detail route), remove the `.skip` and this comment block's first two
 * paragraphs. The assertions below describe the intended contract per the
 * task C3 brief. Mutations that the GET-only forced-mock can't serve
 * (directive create, mental-model create, add-fact POST) will need
 * `page.route` overrides once unskipped — noted per-test below.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe.skip('memory-v2 BankBar + Add modal (C6 unskips — not routed yet)', () => {
  test('reflect round-trip against the mock reflect payload', async ({ page }) => {
    // Would need a real mount point (see header). Once routed:
    //   await page.goto('/#memory/bank?bank=primary')
    //   await page.getByTestId('mv-reflect-tab').click()
    //   await page.getByTestId('mv-reflect-q').fill('why is extraction lagging?')
    //   await page.getByTestId('mv-reflect-run').click()
    //   await expect(page.getByTestId('mv-reflect-out')).toBeVisible()
    //   // buildBankReflect() mock: fixed narrative text + based_on counts.
    //   await expect(page.getByTestId('mv-reflect-out')).toContainText('strix-halo-01')
  })

  test('rules tab lists directives and mental models from the mock', async ({ page }) => {
    // await page.goto('/#memory/bank?bank=primary')
    // await page.getByTestId('mv-rules-tab').click()
    // buildDirectives()/buildMentalModels() mock rows — assert at least one
    // mv-rule-row-{id} per list renders.
  })

  test('add-fact posts to /api/memory/add and toasts on success', async ({ page }) => {
    // Mutation — the GET-only forced-mock never substitutes this route, so
    // once unskipped this needs a page.route('**/api/memory/add', ...)
    // fulfil (200 {id, timestamp}) before asserting the toast.
    // await page.route('**/api/memory/add', (route) =>
    //   route.fulfill({ json: { id: 'm-test', timestamp: new Date().toISOString() } }),
    // )
    // await page.goto('/#memory/bank?bank=primary')
    // await page.getByTestId('mv-add-open').click()
    // await expect(page.getByTestId('mv-add-modal')).toBeVisible()
    // await page.getByTestId('mv-add-fact-text').fill('a new fact to remember')
    // await page.getByTestId('mv-add-fact-tags').fill('ci, thermal')
    // await page.getByTestId('mv-add-submit').click()
    // await expect(page.getByText('Fact added')).toBeVisible()
  })
})
