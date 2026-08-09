/**
 * warm-color-tones — regression for the UNIFIED warming color (#1157, epic #1156).
 *
 * Every warming-state indicator across the dashboard must render the one
 * canonical warm token, `--warn` (dashboard.css), so a retint is a
 * single-line change. #1156 consolidated the scattered oranges into `--warn`
 * and pointed the overhaul-era `--warming` alias at it; this spec pins that
 * so a future stray hex or a divergent `--warming` re-fork fails CI.
 *
 * WHY COMPUTED-COLOR, NOT PNG SNAPSHOTS
 * The originating issue framed this as committed PNG screenshots, citing a
 * "dashboard-overhaul" snapshot spec as prior art. That pattern does not
 * exist in this repo — the entire e2e suite asserts DOM/computed style
 * (see slot-indicator.spec.ts), and there are zero committed image
 * baselines. Container-rendered PNG baselines also flake against CI on font
 * anti-aliasing, which would violate the issue's own AC ("green in CI on
 * first run"). So this enforces the SAME contract deterministically: it
 * reads the color the browser actually resolves from the var() chain at
 * each warming site and asserts they're all the one token. To retint, change
 * `--warn` in dashboard.css and update WARM_RGB below.
 *
 * Covered warming sites (issue AC — at least slot dot, infer epill, NPU epill):
 *   - slot dot            → `.infer-pane .sdot.warming`   (engine-panes.css)
 *   - connections dot     → `.ep-dot.warming`             (connections.css)
 *   - infer pane epill    → `.infer-pane .epill.starting` (engine-panes.css)
 *   - NPU pane epill      → `.npu-pane .epill.starting`   (engine-panes.css)
 *   - overhaul slot dot   → `.sdot.warming` via `--warming` alias (overhaul.css)
 */
import { test, expect } from '../fixtures/apiMock'

// Canonical --warn (dashboard.css). Retint contract: change --warn there,
// then update this one constant. rgb() is how getComputedStyle reports it.
const WARM_RGB = 'rgb(232, 185, 78)'

type Probe = { site: string; ancestor: string; el: string; prop: 'backgroundColor' | 'color' }

// Each probe injects the REAL selector chain and reads the color the browser
// resolves from the live stylesheet — proving that site is wired to --warn.
const PROBES: Probe[] = [
  { site: 'slot dot (infer pane)', ancestor: 'infer-pane', el: 'sdot warming', prop: 'backgroundColor' },
  { site: 'connections dot', ancestor: '', el: 'ep-dot warming', prop: 'backgroundColor' },
  { site: 'infer pane epill', ancestor: 'infer-pane', el: 'epill starting', prop: 'color' },
  { site: 'NPU pane epill', ancestor: 'npu-pane', el: 'epill starting', prop: 'color' },
  { site: 'overhaul slot dot (--warming alias)', ancestor: '', el: 'sdot warming', prop: 'backgroundColor' },
]

test.describe('unified warm color', () => {
  test.beforeEach(async ({ page }) => {
    // Any route loads the full dashboard CSS bundle; #slots is cheap.
    await page.goto('/#slots')
    await page.waitForLoadState('networkidle')
  })

  test('--warn resolves to the canonical warm rgb', async ({ page }) => {
    const resolved = await page.evaluate((v) => {
      const probe = document.createElement('span')
      probe.style.color = `var(${v})`
      document.body.appendChild(probe)
      const c = getComputedStyle(probe).color
      probe.remove()
      return c
    }, '--warn')
    expect(resolved).toBe(WARM_RGB)
  })

  test('--warming aliases --warn (no re-fork of the overhaul token)', async ({ page }) => {
    const [warn, warming] = await page.evaluate(() => {
      const read = (v: string) => {
        const p = document.createElement('span')
        p.style.color = `var(${v})`
        document.body.appendChild(p)
        const c = getComputedStyle(p).color
        p.remove()
        return c
      }
      return [read('--warn'), read('--warming')]
    })
    expect(warming).toBe(warn)
    expect(warming).toBe(WARM_RGB)
  })

  for (const probe of PROBES) {
    test(`${probe.site} renders the unified warm color`, async ({ page }) => {
      const resolved = await page.evaluate((p) => {
        const root = document.createElement('div')
        if (p.ancestor) root.className = p.ancestor
        const el = document.createElement('span')
        el.className = p.el
        root.appendChild(el)
        document.body.appendChild(root)
        const value = getComputedStyle(el)[p.prop as 'backgroundColor' | 'color']
        root.remove()
        return value
      }, probe)
      expect(resolved, `${probe.site} must resolve to --warn`).toBe(WARM_RGB)
    })
  }
})
