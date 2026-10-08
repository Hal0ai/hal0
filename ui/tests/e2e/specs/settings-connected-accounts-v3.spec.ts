/**
 * settings-connected-accounts-v3 — the OAuth "Connected accounts" pane is
 * reachable from the dashboard (#2267).
 *
 * #2252 shipped ConnectedAccountsPanel, but only the legacy ConnectionsView
 * rendered it, and main.jsx redirects #connections to #slots/endpoints, so no
 * navigation path reached it. It now lives at Settings ▸ Integrations ▸
 * Connected Accounts (#settings/accounts). GET /api/oauth/providers is stubbed
 * with the shape `_serialize_provider` returns (src/hal0/api/routes/oauth.py).
 */
import { test, expect, json } from '../fixtures/apiMock'

const PROVIDERS = [
  {
    id: 'github',
    name: 'GitHub',
    skill_id: 'github',
    scopes: ['repo'],
    pkce: true,
    configured: true,
    requires_client_secret: false,
    has_client_secret: false,
    connected: true,
    expires_at: null,
    expired: false,
    notes: '',
  },
  {
    id: 'spotify',
    name: 'Spotify',
    skill_id: 'spotify',
    scopes: ['user-read-playback-state'],
    pkce: true,
    configured: false,
    requires_client_secret: true,
    has_client_secret: false,
    connected: false,
    expires_at: null,
    expired: null,
    notes: '',
  },
]

test.describe('Settings ▸ Integrations ▸ Connected Accounts (#2267)', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/oauth/providers', (r) => json(r, { providers: PROVIDERS }))
  })

  test('the settings nav reaches the Connected accounts pane', async ({ page }) => {
    await page.goto('/#settings', { waitUntil: 'domcontentloaded' })
    await page.locator('.settings-nav .nav-item', { hasText: 'Connected Accounts' }).click()

    await expect(page).toHaveURL(/#settings\/accounts$/)
    await expect(page.locator('.cpane-title', { hasText: 'Connected accounts' })).toBeVisible()
    await expect(page.locator('.oarow')).toHaveCount(2)
    await expect(page.locator('.oarow', { hasText: 'GitHub' }).locator('.chip.ok')).toHaveText(
      'connected',
    )
    await expect(
      page.locator('.oarow', { hasText: 'Spotify' }).getByRole('button', { name: 'Set client secret' }),
    ).toBeVisible()
  })

  test('the #settings/oauth alias deep-links to the same pane', async ({ page }) => {
    await page.goto('/#settings/oauth', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.settings-nav .nav-item.active')).toHaveText('Connected Accounts')
    await expect(page.locator('.cpane-title', { hasText: 'Connected accounts' })).toBeVisible()
  })
})
