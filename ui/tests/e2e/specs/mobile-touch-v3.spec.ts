/**
 * mobile-touch-v3 — touch targets, tab-strip edge fade, header baseline.
 *
 * Touch emulation (`hasTouch: true`) makes `(pointer: coarse)` match in Chromium, which is what
 * ui/src/dash/mobile/touch.css keys on. Desktop with a mouse must keep every control's size.
 */
import type { Page } from '@playwright/test'
import { test, expect } from '../fixtures/apiMock'

const PHONE = { width: 390, height: 844 }
const DESKTOP = { width: 1280, height: 800 }
const MIN = 40

/** The control's effective hit area: its box, or the taller `::after` that extends it. Found by
 *  asking the browser what sits under the points `MIN/2 - 1` above and below its centre. */
async function hitHeight(page: Page, selector: string, nth = 0) {
  return page.locator(selector).nth(nth).evaluate((el, min) => {
    ;(el as HTMLElement).scrollIntoView({ block: 'center', inline: 'center' })
    const r = el.getBoundingClientRect()
    const cx = r.left + r.width / 2
    const cy = r.top + r.height / 2
    const hits = (y: number) => {
      const h = document.elementFromPoint(cx, y)
      return !!h && el.contains(h)
    }
    const reach = min / 2 - 1
    return { box: r.height, up: hits(cy - reach), down: hits(cy + reach) }
  }, MIN)
}

async function openModelFilters(page: Page) {
  await page.goto('/#models')
  await page.getByTestId('mdl-filters-toggle').click()
  await expect(page.locator('.mdl-chip').first()).toBeVisible()
}

test.describe('touch targets on a touch screen (390px)', () => {
  test.use({ viewport: PHONE, hasTouch: true })

  test('filter chips keep their size and reach 40px', async ({ page }) => {
    await openModelFilters(page)
    const chip = page.locator('.mdl-toolbar-grp:not(.mdl-sort-grp) .mdl-chip').first()
    expect((await chip.boundingBox())!.height).toBeLessThan(MIN)
    const h = await hitHeight(page, '.mdl-toolbar-grp:not(.mdl-sort-grp) .mdl-chip')
    expect(h.up && h.down).toBe(true)
  })

  test('hit areas of neighbouring chips do not overlap', async ({ page }) => {
    await openModelFilters(page)
    const rects = await page.locator('.mdl-toolbar-grp:not(.mdl-sort-grp) .mdl-chip').evaluateAll((els, min) =>
      els.map((el) => {
        const r = el.getBoundingClientRect()
        const h = Math.max(r.height, min)
        const cy = r.top + r.height / 2
        return { l: r.left, r: r.right, t: cy - h / 2, b: cy + h / 2 }
      }), MIN)
    expect(rects.length).toBeGreaterThan(4)
    for (let i = 0; i < rects.length; i++) {
      for (let j = i + 1; j < rects.length; j++) {
        const a = rects[i]
        const b = rects[j]
        const overlap = Math.min(a.r, b.r) - Math.max(a.l, b.l) > 0.5 && Math.min(a.b, b.b) - Math.max(a.t, b.t) > 0.5
        expect(overlap, `chips ${i} and ${j}`).toBe(false)
      }
    }
  })

  test('buttons, icon buttons and tabs are at least 40px tall', async ({ page }) => {
    await page.goto('/#dashboard')
    for (const sel of ['.tb-bell', '.tb-menu', '.banner-dismiss', '.ov-card-tog']) {
      const loc = page.locator(sel).first()
      await expect(loc, sel).toBeVisible()
      const b = (await loc.boundingBox())!
      expect(Math.min(b.width, b.height), sel).toBeGreaterThanOrEqual(MIN)
    }
    for (const sel of ['.btn.sm', '.ov-btn:not(.sm)']) {
      const h = await hitHeight(page, sel)
      expect(h.up && h.down, sel).toBe(true)
    }
    await page.goto('/#slots')
    expect((await page.locator('.slot-tab').first().boundingBox())!.height).toBeGreaterThanOrEqual(MIN)
    await page.goto('/#models')
    expect((await page.locator('.mdl-row-kebab .btn').first().boundingBox())!.height).toBeGreaterThanOrEqual(MIN)
  })

  test('text inputs and selects are at least 40px tall', async ({ page }) => {
    await page.goto('/#models')
    const search = page.locator('.mdl-search input, input.mdl-search').first()
    expect((await search.boundingBox())!.height).toBeGreaterThanOrEqual(MIN)
  })
})

test.describe('desktop with a mouse keeps its sizes (1280px)', () => {
  test.use({ viewport: DESKTOP })

  test('controls keep their box and have no extended hit area', async ({ page }) => {
    await page.goto('/#models')
    const pill = page.locator('.mdl-toolbar-grp:not(.mdl-sort-grp) .mdl-chip').first()
    await expect(pill).toBeVisible()
    expect(await pill.evaluate((el) => getComputedStyle(el).height)).toBe('26px')
    expect(await pill.evaluate((el) => getComputedStyle(el, '::after').content)).toBe('none')
    expect(await pill.evaluate((el) => getComputedStyle(el).position)).toBe('static')
    const seg = page.locator('.mdl-seg button').first()
    if (await seg.count()) expect(await seg.evaluate((el) => getComputedStyle(el).height)).toBe('22px')
    await page.goto('/#dashboard')
    const bell = page.locator('.tb-bell').first()
    expect(await bell.evaluate((el) => getComputedStyle(el).height)).toBe('30px')
    const btnSm = page.locator('.btn.sm').first()
    if (await btnSm.count()) expect(await btnSm.evaluate((el) => getComputedStyle(el).height)).toBe('26px')
    await page.goto('/#slots')
    expect(await page.locator('.slot-tab').first().evaluate((el) => getComputedStyle(el).minHeight)).toBe('auto')
    expect(await page.locator('.slot-ctrls').first().evaluate((el) => getComputedStyle(el).columnGap)).toBe('4px')
  })

  test('the view header eyebrow and title share a text baseline; buttons stay centred', async ({ page }) => {
    await page.goto('/#slots')
    const vh = page.locator('.vh').first()
    await expect(vh.locator('.vh-eye')).toBeVisible()
    const info = await vh.evaluate((root) => {
      // An empty inline-block sits on the baseline: its bottom edge is the text baseline.
      const baseline = (el: Element) => {
        const probe = document.createElement('span')
        probe.style.cssText = 'display:inline-block;width:0;height:0'
        el.appendChild(probe)
        const y = probe.getBoundingClientRect().bottom
        probe.remove()
        return y
      }
      const rr = root.getBoundingClientRect()
      const btn = root.querySelector('.btn')?.getBoundingClientRect()
      return {
        eye: baseline(root.querySelector('.vh-eye')!),
        title: baseline(root.querySelector('h1')!),
        rowCentre: rr.top + rr.height / 2,
        btnCentre: btn ? btn.top + btn.height / 2 : null,
      }
    })
    expect(Math.abs(info.eye - info.title)).toBeLessThanOrEqual(2)
    if (info.btnCentre !== null) expect(Math.abs(info.btnCentre - info.rowCentre)).toBeLessThanOrEqual(2)
  })
})

test.describe('tab strips fade at the edge with more to scroll (390px)', () => {
  test.use({ viewport: PHONE, hasTouch: true })

  const fades = (page: Page, sel: string) =>
    page.locator(sel).first().evaluate(async (el) => {
      await new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)))
      const cs = getComputedStyle(el)
      return { l: cs.getPropertyValue('--tab-fade-l').trim(), r: cs.getPropertyValue('--tab-fade-r').trim() }
    })

  test('slot-tabs: right fade at the start, left at the end, never over a tap', async ({ page }) => {
    test.skip(!(await supportsScrollTimeline(page)), 'scroll-driven animations unavailable')
    await page.goto('/#slots')
    const strip = page.locator('.slot-tabs').first()
    await expect(strip).toBeVisible()
    // The strip first scrolls itself to the open tab while it settles; let that finish.
    await page.waitForTimeout(800)
    await strip.evaluate((el) => { el.scrollLeft = 0 })
    expect(await fades(page, '.slot-tabs')).toEqual({ l: '0px', r: '28px' })
    await strip.evaluate((el) => { el.scrollLeft = (el.scrollWidth - el.clientWidth) / 2 })
    expect(await fades(page, '.slot-tabs')).toEqual({ l: '28px', r: '28px' })
    await strip.evaluate((el) => { el.scrollLeft = el.scrollWidth })
    expect(await fades(page, '.slot-tabs')).toEqual({ l: '28px', r: '0px' })
    // A mask intercepts nothing: the tab under the fade is what a tap lands on.
    const last = page.locator('.slot-tab').last()
    const b = (await last.boundingBox())!
    expect(await last.evaluate((el, p) => el.contains(document.elementFromPoint(p.x, p.y)), { x: b.x + b.width - 4, y: b.y + b.height / 2 })).toBe(true)
  })

  test('slot-tabs keep the open tab visible when the page loads deep-linked to it', async ({ page }) => {
    await page.goto('/#slots/stacks')
    const strip = page.locator('.slot-tabs').first()
    await expect(page.locator('.slot-tab.on')).toBeVisible()
    await expect.poll(async () => {
      const s = (await strip.boundingBox())!
      const t = (await page.locator('.slot-tab.on').boundingBox())!
      return t.x >= s.x - 1 && t.x + t.width <= s.x + s.width + 1
    }).toBe(true)
  })

  test('Memory tabs fade the same way', async ({ page }) => {
    test.skip(!(await supportsScrollTimeline(page)), 'scroll-driven animations unavailable')
    await page.goto('/#memory/health')
    await expect(page.getByTestId('ma-tabs')).toBeVisible()
    await page.getByTestId('ma-tabs').evaluate((el) => { el.scrollLeft = 0 })
    expect(await fades(page, '.ma-tabs')).toEqual({ l: '0px', r: '28px' })
  })

  test('nothing fades when every tab fits (1280px)', async ({ page }) => {
    test.skip(!(await supportsScrollTimeline(page)), 'scroll-driven animations unavailable')
    await page.setViewportSize(DESKTOP)
    await page.goto('/#slots')
    await expect(page.locator('.slot-tabs').first()).toBeVisible()
    expect(await fades(page, '.slot-tabs')).toEqual({ l: '0px', r: '0px' })
  })
})

async function supportsScrollTimeline(page: Page) {
  await page.goto('/')
  return page.evaluate(() => CSS.supports('animation-timeline: scroll()') && CSS.supports('mask-composite: add'))
}
