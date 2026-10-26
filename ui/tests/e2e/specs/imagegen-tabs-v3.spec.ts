/**
 * imagegen-tabs-v3 — the Image Gen pane's frame: the Generate | Models | Engine tabs, which one
 * opens first, the deep link, the accent tokens the panels draw with, a box with no slots, and
 * reduced motion.
 *
 * Replaces imagegen-v2.spec.ts, which covered the old "render hero" pane (its GTT gauge, fake
 * frames and queue drawer are gone). What it pinned that is still true is here, against the new
 * pane: an empty queue is in-flow and clickable, and reduced motion stops the one looping
 * animation.
 *
 * Harness: VITE_MOCK_HAL0=1 build; `/api/comfyui/*` is not in MOCK_ALLOWLIST, so `routeImageGen`
 * (fixtures/imageGen.ts) drives the pane over the `apiMock` catch-all.
 */
import { test, expect, seedSlots } from '../fixtures/apiMock'
import { catalog, catalogRow, gotoImageGen, makeImageGenMock, routeImageGen } from '../fixtures/imageGen'

test.describe('Image Gen pane: tabs', () => {
  test('opens on Models when no renderable model is installed', async ({ page }) => {
    const mock = makeImageGenMock({ models: catalog([catalogRow('sdxl-lightning', { state: 'none' })]) })
    await routeImageGen(page, mock)
    await gotoImageGen(page)

    await expect(page.getByTestId('igt-tab-models')).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByTestId('igt-tab-generate')).toHaveAttribute('aria-selected', 'false')
  })

  test('opens on Generate when a renderable model is installed', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page)

    await expect(page.getByTestId('igt-tab-generate')).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByTestId('gen-panel')).toBeVisible()
  })

  test('a deep link wins, and a tab change rewrites the URL without adding history', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'engine')
    await expect(page.getByTestId('igt-tab-engine')).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByTestId('engine-card')).toBeVisible()

    const entries = await page.evaluate(() => history.length)
    await page.getByTestId('igt-tab-models').click()
    await expect(page.getByTestId('igt-tab-models')).toHaveAttribute('aria-selected', 'true')
    expect(new URL(page.url()).hash).toBe('#slots/image/models')
    expect(await page.evaluate(() => history.length)).toBe(entries)
  })

  // The strip's 1px rule runs under every tab, and the active tab's 2px underline covers it. A rule
  // drawn as a border below the tabs would leave the underline stacked on top of it, 1px higher.
  test("the active tab's underline overlaps the strip's 1px rule", async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'generate')

    const geo = await page.evaluate(() => {
      const strip = document.querySelector('.igt-tabs') as HTMLElement
      const tab = document.querySelector('.igt-tab.on') as HTMLElement
      const inactive = document.querySelector('.igt-tab:not(.on)') as HTMLElement
      return {
        stripBottom: strip.getBoundingClientRect().bottom,
        tabBottom: tab.getBoundingClientRect().bottom,
        inactiveBottom: inactive.getBoundingClientRect().bottom,
        stripBorder: getComputedStyle(strip).borderBottomWidth,
        underline: getComputedStyle(tab).borderBottomWidth,
      }
    })
    expect(geo.underline).toBe('2px')
    // Nothing sits between the tab and the strip's edge: the rule is behind the tab, not under it.
    expect(geo.stripBorder).toBe('0px')
    expect(geo.tabBottom).toBeCloseTo(geo.stripBottom, 1)
    expect(geo.inactiveBottom).toBeCloseTo(geo.stripBottom, 1)
  })

  test('the sidebar and the Slots tab mark Image Gen on a sub-tab route', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page, 'generate')

    await expect(page.locator('.slot-tab.comfy')).toHaveAttribute('aria-selected', 'true')
    await expect(page.locator('.slot-tab.infer')).toHaveAttribute('aria-selected', 'false')
  })
})

test.describe('Image Gen pane: accent tokens', () => {
  // The old pane's stylesheet re-declared the `--comfy*` tokens under `.comfy-page`. With it gone
  // the pane draws with the ones dashboard.css defines on :root (ComfyUI's blue; `.infer-pane` and
  // `.npu-pane` re-scope them to their own accents, and the Image Gen pane is in neither). Vitest
  // does not load CSS, so this reads them off the mounted root in a real browser and checks that
  // something draws with them.
  test('the tokens the pane draws with resolve to the blue accent, and the active tab uses it', async ({ page }) => {
    await routeImageGen(page, makeImageGenMock())
    await gotoImageGen(page)

    const tokens = await page.getByTestId('image-gen-pane').evaluate((el: Element) => {
      const css = getComputedStyle(el)
      return Object.fromEntries(
        ['--comfy', '--comfy-hi', '--comfy-soft', '--comfy-line', '--ease'].map((name) => [
          name,
          css.getPropertyValue(name).trim(),
        ]),
      )
    })
    for (const [name, value] of Object.entries(tokens)) expect(value, name).not.toBe('')
    expect(tokens['--comfy'].toLowerCase()).toBe('#5ea4f9')

    // The tab fades its colour in (a 0.12 s transition), so wait for it to settle.
    await expect(page.locator('.igt-tab.on')).toHaveCSS('color', 'rgb(94, 164, 249)')
  })
})

test.describe('Image Gen pane: a box with no slots', () => {
  // A fresh box has no slots, which is when you install the first image model. The Slots page's
  // "No slots configured" empty state must not stand in front of the pane.
  test('still shows the pane, not "No slots configured"', async ({ page }) => {
    await seedSlots(page, [])
    await routeImageGen(page, makeImageGenMock({ models: catalog([catalogRow('sdxl-lightning', { state: 'none' })]) }))
    await gotoImageGen(page)

    await expect(page.getByTestId('igt-tab-models')).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByText('No slots configured')).toHaveCount(0)
  })

  test('the Inference tab keeps its empty state', async ({ page }) => {
    await seedSlots(page, [])
    await routeImageGen(page, makeImageGenMock())
    await page.goto('/#slots/inference')

    await expect(page.getByText('No slots configured')).toBeVisible()
    await expect(page.getByTestId('image-gen-pane')).toHaveCount(0)
  })
})

test.describe('Image Gen pane: motion', () => {
  // A held `/v1/images/generations` request leaves the Generate run in flight with nothing to
  // measure, so its bar is the indeterminate one: the pane's only looping animation.
  async function startHeldRun(page: any) {
    let release: () => void = () => {}
    const held = new Promise<void>((resolve) => (release = resolve))
    const mock = makeImageGenMock({
      generate: async (route) => {
        await held
        await route.fulfill({ status: 499, contentType: 'application/json', body: '{}' })
      },
    })
    await routeImageGen(page, mock)
    await gotoImageGen(page, 'generate')
    await page.getByTestId('gen-prompt').fill('a lighthouse at dusk')
    await page.getByTestId('gen-submit').click()
    await expect(page.getByTestId('gen-run')).toBeVisible()
    return release
  }

  test('a run in flight animates its bar', async ({ page }) => {
    const release = await startHeldRun(page)
    const name = await page.locator('.gen-bar > i').evaluate((el: Element) => getComputedStyle(el).animationName)
    expect(name).toBe('gen-slide')
    release()
  })

  test('reduced motion stops it', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    const release = await startHeldRun(page)
    const name = await page.locator('.gen-bar > i').evaluate((el: Element) => getComputedStyle(el).animationName)
    expect(name).toBe('none')
    release()
  })
})
