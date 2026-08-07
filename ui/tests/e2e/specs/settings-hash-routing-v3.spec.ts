/**
 * settings-hash-routing-v3 (GH #1438) — Settings section navigation must
 * track the URL hash both ways.
 *
 * SettingsShell used to seed a local `useState(initialSection)` from the
 * `param` prop ONCE on mount, and SettingsNav's onSelect wrote only that
 * local state — never the hash. Since the outer router (main.jsx) keeps the
 * same `route` ("settings") across every #settings/<section> hash and only
 * re-renders SettingsShell with a new `param` prop (no remount), two things
 * broke:
 *
 *   1. Clicking a nav item never changed the URL — bookmarking/reload/
 *      sharing a link always lands on whatever section happened to render
 *      first, not the one being viewed.
 *   2. A hash change that doesn't remount the component (browser back/
 *      forward, or any other code setting `window.location.hash` while
 *      already on the settings route) was silently ignored — the local
 *      state never re-derives from the new `param`.
 *
 * Fixed by making `section` a pure derivation of the `param` prop (no local
 * state) and having the nav's onSelect write the hash instead of local
 * state, so the existing `hashchange` listener in main.jsx is the single
 * feedback loop for both directions.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Settings — hash routing (#1438)', () => {
  test('deep link #settings/security renders the Security section directly', async ({ page }) => {
    await page.goto('/#settings/security')
    await expect(page.locator('.settings-nav .nav-item.active')).toHaveText('Security')
  })

  test('clicking a nav item writes the hash', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Updates' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
    await expect.poll(() => page.evaluate(() => window.location.hash)).toBe('#settings/updates')
  })

  test('a hash change while already on /settings re-renders the section (no remount)', async ({ page }) => {
    // legacy id — resolves to Overview via SECTION_ALIASES
    await page.goto('/#settings/general')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')

    // Simulate what browser back/forward (or any other hash write) does:
    // change the hash directly, without going through SettingsNav's click.
    await page.evaluate(() => { window.location.hash = '#settings/updates' })

    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
    await expect(page.locator('.settings-nav .nav-item.active')).toHaveText('Updates')
  })

  test('browser back navigates between two visited sections', async ({ page }) => {
    await page.goto('/#settings/overview')
    await page.locator('.settings-nav .nav-item', { hasText: 'Memory' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Memory')

    await page.goBack()

    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')
  })
})
