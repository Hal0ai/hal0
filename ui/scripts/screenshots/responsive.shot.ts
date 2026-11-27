/**
 * Phone (390x844) and tablet (834x1112) captures. The phone layout (#3956,
 * #3964, #3965, #3967) is the bottom tab bar, the one-line dock and the
 * stacked-card tables; the tablet keeps the sidebar off-canvas.
 */
import { PHONE, TABLET, open, shoot, test } from './shot'

const PHONE_PAGES: Array<[string, string]> = [
  ['phone-overview', '#dashboard'],
  ['phone-slots', '#slots'],
  ['phone-slots-npu', '#slots/npu'],
  ['phone-slots-stacks', '#slots/stacks'],
  ['phone-models', '#models'],
  ['phone-models-providers', '#models/providers'],
  ['phone-memory-health', '#memory/health'],
  ['phone-memory-agents', '#memory/agents'],
  ['phone-agents', '#agent'],
  ['phone-settings-updates', '#settings/updates'],
]

const TABLET_PAGES: Array<[string, string]> = [
  ['tablet-overview', '#dashboard'],
  ['tablet-slots', '#slots'],
  ['tablet-models', '#models'],
]

const only = process.env.SHOT_ONLY ? new RegExp(process.env.SHOT_ONLY) : null
const want = (n: string) => !only || only.test(n)

for (const [name, hash] of PHONE_PAGES) {
  if (!want(name)) continue
  test(name, async ({ page }) => {
    await open(page, hash, PHONE)
    await shoot(page, name)
  })
}

for (const [name, hash] of TABLET_PAGES) {
  if (!want(name)) continue
  test(name, async ({ page }) => {
    await open(page, hash, TABLET)
    await shoot(page, name)
  })
}

if (want('phone-more-drawer')) {
  test('phone-more-drawer', async ({ page }) => {
    await open(page, '#dashboard', PHONE)
    await page.getByTestId('tabbar-more').click()
    await shoot(page, 'phone-more-drawer')
  })
}

if (want('phone-dock-open')) {
  test('phone-dock-open', async ({ page }) => {
    await open(page, '#slots', PHONE)
    await page.getByTestId('dock-toggle').click()
    // Opening the dock can leave a scroll container shifted sideways; put every one back.
    await page.evaluate(() => {
      for (const el of document.querySelectorAll<HTMLElement>('*')) if (el.scrollLeft) el.scrollLeft = 0
    })
    await shoot(page, 'phone-dock-open')
  })
}
