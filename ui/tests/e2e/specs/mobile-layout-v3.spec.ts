/**
 * mobile-layout-v3 — phone and tablet layout contract.
 *
 * `.main` is the page scroller. Anything wider than it either makes the whole page scroll
 * sideways or, inside an `overflow: hidden` panel, is silently cut off; both happened (the
 * Overview Services chips, the Settings rows, the Models rows that lost their name column).
 * The sweep below asserts neither can happen on any route at phone or tablet width, and the
 * tests after it pin the phone-only folds (settings nav, model filters, telemetry, cards).
 */
import { test, expect, json } from '../fixtures/apiMock'

const PHONE = { width: 390, height: 844 }
const TABLET = { width: 820, height: 1180 }

const SETTINGS = [
  'overview', 'security', 'network', 'doctor', 'slots', 'modeldefaults', 'capabilities', 'hardware',
  'storage', 'memory', 'updates', 'advanced', 'secrets', 'accounts', 'agents', 'realtime',
]
const ROUTES = [
  'dashboard', 'slots', 'slots/image', 'slots/npu', 'slots/endpoints', 'slots/runner-images', 'slots/stacks',
  'models', 'models/image', 'models/providers', 'models/flm', 'models/profiles', 'logs', 'agent', 'mcp',
  'memory/health', 'memory/facts', 'memory/knowledge', 'memory/operations', 'memory/agents',
  'board', 'services', 'benchmarks', ...SETTINGS.map((s) => 'settings/' + s),
]

/** OpenWebUI wired to everything, and no link to it: the widest Services row the Overview draws. */
async function routeWideServices(page: import('@playwright/test').Page) {
  await page.route('**/api/config/urls', (route) => json(route, {
    api: 'https://hal0.example', openwebui: '', openwebui_ip: '', openwebui_enabled: true,
    openwebui_reason: 'proxy_needs_public_url', hermes: '', hermes_enabled: false,
    comfyui: '', comfyui_ip: '', comfyui_lan: true, comfyui_public: false,
  }))
  await page.route('**/api/services', (route) => json(route, {
    services: [
      { id: 'openwebui', name: 'OpenWebUI', managed: true, up: true, detail: 'reachable — /health ok', url: 'http://x:3001',
        wired: { chat: true, voice: true, documents: true, images: true, web_search: true, mcp: true } },
      { id: 'comfyui', name: 'ComfyUI', managed: true, up: false, detail: 'unreachable' },
      { id: 'hermes', name: 'Hermes', managed: true, up: true, detail: 'systemd unit active' },
    ],
    mdns: { available: false, hostname: 'hal0', base_advertised: false, advertised: [] },
  }))
}

/**
 * Elements of the page body that stick out past the viewport's right or left edge, ignoring what
 * sits inside its own horizontal scroller (tab strips, wide tables) and off-canvas drawers.
 */
async function overflowing(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const vw = window.innerWidth
    const main = document.querySelector('.main') as HTMLElement | null
    const out: string[] = []
    if (main && main.scrollWidth > main.clientWidth + 1) out.push(`.main scrolls sideways by ${main.scrollWidth - main.clientWidth}px`)
    const inScroller = (el: Element) => {
      for (let e = el.parentElement; e && e !== main; e = e.parentElement) {
        const ox = getComputedStyle(e).overflowX
        if ((ox === 'auto' || ox === 'scroll') && e.scrollWidth > e.clientWidth + 1) return true
      }
      return false
    }
    const seen = new Set<Element>()
    for (const el of Array.from((main ?? document.body).querySelectorAll('*'))) {
      const r = el.getBoundingClientRect()
      if (r.width === 0 || r.height === 0) continue
      if (r.right <= vw + 1 && r.left >= -1) continue
      if (el.closest('.drawer, .nav-drawer')) continue
      if (inScroller(el)) continue
      seen.add(el)
      if (el.parentElement && seen.has(el.parentElement)) continue
      out.push(`${el.tagName.toLowerCase()}.${String((el as HTMLElement).className).split(/\s+/).join('.')} right=${Math.round(r.right)} left=${Math.round(r.left)}`)
    }
    return out
  })
}

for (const vp of [{ name: 'phone', size: PHONE }, { name: 'tablet', size: TABLET }]) {
  test.describe(`no sideways overflow — ${vp.name} ${vp.size.width}px`, () => {
    test.use({ viewport: vp.size })
    for (const r of ROUTES) {
      test(`#${r}`, async ({ page }) => {
        if (r === 'dashboard') await routeWideServices(page)
        await page.goto('/#' + r)
        await expect(page.locator('.main')).toBeVisible()
        await expect.poll(() => overflowing(page), { timeout: 5_000 }).toEqual([])
      })
    }
  })
}

test.describe('phone folds (≤720px)', () => {
  test.use({ viewport: PHONE })

  test('Overview Services: wired chips stay inside the card and the URL note is not cut off', async ({ page }) => {
    await routeWideServices(page)
    await page.goto('/#dashboard')
    const row = page.locator('.ov-svc').first()
    await expect(row.locator('.ov-chip')).toHaveCount(6)
    const card = await page.locator('.ov-card', { has: row }).boundingBox()
    for (const chip of await row.locator('.ov-chip').all()) {
      const b = (await chip.boundingBox())!
      expect(b.x + b.width).toBeLessThanOrEqual(card!.x + card!.width)
    }
    const note = row.getByTestId('ov-svc-open-openwebui-hint')
    expect(await note.evaluate((el) => el.scrollWidth <= el.clientWidth)).toBe(true)
  })

  test('Overview cards fold to their header and remember it', async ({ page }) => {
    await page.goto('/#dashboard')
    const card = page.locator('.ov-card', { has: page.locator('.ov-card-t', { hasText: /^Services$/ }) })
    const toggle = card.locator('.ov-card-tog')
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(card.locator('.ov-card-b')).toBeHidden()
    await page.reload()
    await expect(card.locator('.ov-card-b')).toBeHidden()
    await card.locator('.ov-card-tog').click()
    await expect(card.locator('.ov-card-b')).toBeVisible()
  })

  test('Models rows keep their name; the chip groups fold behind Filters', async ({ page }) => {
    await page.goto('/#models')
    const name = page.locator('.mdl-row .nm').first()
    await expect(name).toBeVisible()
    expect((await name.boundingBox())!.width).toBeGreaterThan(120)
    const group = page.locator('.mdl-toolbar-grp:not(.mdl-sort-grp)').first()
    await expect(group).toBeHidden()
    const toggle = page.getByTestId('mdl-filters-toggle')
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await expect(group).toBeVisible()
  })

  test('Settings nav folds behind a toggle that names the page', async ({ page }) => {
    await page.goto('/#settings/hardware')
    const toggle = page.getByTestId('settings-nav-toggle')
    await expect(toggle).toContainText('Hardware & Runtimes')
    await expect(page.locator('.settings-nav .nav-item').first()).toBeHidden()
    await toggle.click()
    await page.locator('.settings-nav .nav-item', { hasText: 'Updates' }).click()
    await expect(page).toHaveURL(/#settings\/updates$/)
    await expect(toggle).toContainText('Updates')
    await expect(page.locator('.settings-nav .nav-item').first()).toBeHidden()
  })

  test('Slots telemetry shows throughput and folds the hardware cells', async ({ page }) => {
    await page.goto('/#slots')
    const card = page.getByTestId('telemetry-header')
    await expect(card.locator('.th-cell-tps')).toBeVisible()
    await expect(card.locator('.th-ruler')).toBeHidden()
    await page.getByTestId('telemetry-fold').click()
    await expect(card.locator('.th-ruler')).toBeVisible()
  })

  test('notifications popover stays on screen', async ({ page }) => {
    await page.goto('/#dashboard')
    await page.locator('.tb-bell, [aria-label*="otification"]').first().click()
    const b = (await page.locator('.notif-pop').boundingBox())!
    expect(b.x).toBeGreaterThanOrEqual(0)
    expect(b.x + b.width).toBeLessThanOrEqual(PHONE.width)
  })
})

test.describe('desktop keeps the unfolded layout', () => {
  test('the phone-only toggles are not rendered visibly and nothing is folded', async ({ page }) => {
    await page.goto('/#settings/hardware')
    await expect(page.getByTestId('settings-nav-toggle')).toBeHidden()
    await expect(page.locator('.settings-nav .nav-item').first()).toBeVisible()
    await page.goto('/#slots')
    await expect(page.getByTestId('telemetry-fold')).toBeHidden()
    await expect(page.getByTestId('telemetry-header').locator('.th-ruler')).toBeVisible()
    await page.goto('/#models')
    await expect(page.getByTestId('mdl-filters-toggle')).toBeHidden()
    await expect(page.locator('.mdl-toolbar-grp:not(.mdl-sort-grp)').first()).toBeVisible()
  })
})
