/**
 * One-off live capture against the hal0 LXC (10.0.1.142:8080). Runs only
 * when HAL0_LIVE_LXC=1 is set, so CI doesn't fail when the LAN host is
 * unreachable. Use this to grab a screenshot of the actual dashboard with
 * real /api/slots data exposing last_used_at.
 *
 *   HAL0_LIVE_LXC=1 npx playwright test slot-indicator-live-screenshot --grep @live-lxc
 */
import { test as base, expect, chromium } from '@playwright/test'

const LIVE = process.env.HAL0_LIVE_LXC === '1'
const LXC_URL = process.env.HAL0_LXC_URL || 'http://10.0.1.142:8080'

const test = base.extend({})

test.skip(!LIVE, 'set HAL0_LIVE_LXC=1 + ensure 10.0.1.142 is reachable')

test('@live-lxc dashboard slots view against hal0 LXC', async () => {
  const browser = await chromium.launch()
  const ctx = await browser.newContext({ baseURL: LXC_URL, viewport: { width: 1440, height: 900 } })
  const page = await ctx.newPage()
  await page.goto('/#slots', { waitUntil: 'networkidle' })
  await expect(page.locator('.view .vh h1')).toHaveText('Slots')
  await page.waitForSelector('.slot .dot, .slot-list-row .dot')
  await page.screenshot({
    path: 'test-results/slot-indicators-live-lxc.png',
    fullPage: true,
  })
  await browser.close()
})
