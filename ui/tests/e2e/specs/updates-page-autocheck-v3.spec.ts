/**
 * updates-page-autocheck-v3 — Settings ▸ Updates ▸ Auto-check row (#1467
 * item 6).
 *
 * GET /api/updates/state hardcoded `"autoCheck": True` (routes/updater.py)
 * — not derived from any timer/unit state or config knob (no such signal
 * exists anywhere in the updater: no systemd .timer, no auto-check config
 * field, nothing in registry/update_check.py either — that module is the
 * model-registry checker, unrelated to the hal0 self-updater). The UI row
 * rendered it as "Background update checks by the daemon · enabled" and
 * could never read "disabled" even if an operator masked the check
 * mechanism, since the literal never varies. A permanently-green evidence
 * row is worse than none for a GA release, so the row is dropped rather
 * than papered over with a fabricated derivation.
 */
import { test, expect } from '../fixtures/apiMock'

test.describe('Updates page — Auto-check row dropped (#1467)', () => {
  test('the Auto-check evidence row is gone; the rest of the Updates page is intact', async ({
    page,
  }) => {
    await page.goto('/#settings/updates', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')

    // The fabricated row is gone.
    const rows = page.locator('.s-row').filter({ has: page.locator('.k span', { hasText: /^Auto-check$/ }) })
    await expect(rows).toHaveCount(0)
    await expect(page.locator('.settings-content')).not.toContainText('Auto-check')

    // Sibling real rows survive the removal (channel is a genuine, backed
    // control — proves the page still renders past the dropped row).
    const channelRow = page.locator('.s-row').filter({ has: page.locator('.k span', { hasText: /^Channel$/ }) })
    await expect(channelRow).toBeVisible()
  })
})
