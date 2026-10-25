/**
 * settings-v3 — `#settings` route renders the grouped rail nav (GENERAL /
 * MODELS & INFERENCE / SYSTEM / INTEGRATIONS) and swaps the right pane on
 * click.
 *
 * settings-panel cleanup collapsed the old 8-group / 20-page tree:
 * Overview (new default landing) absorbed Health & Stats + General;
 * Loaded Models absorbed Library & Downloads; Hardware & Runtimes merged
 * Backend & GPU + Runtimes; Updates absorbed About; the blocked Hardware
 * Tuning stub was removed. Legacy #settings/<id> deep links resolve via
 * SECTION_ALIASES. Section list below is sourced from `SettingsNav.jsx`'s
 * NAV_GROUPS — the authoritative IA.
 */
import { test, expect, json } from '../fixtures/apiMock'

const SECTIONS = [
  'Overview', 'Security', 'Network', 'Doctor',
  'Loaded Models', 'Model Defaults', 'AI Capabilities',
  'Hardware & Runtimes', 'Storage', 'Memory', 'Updates', 'Advanced',
  'Secrets', 'Connected Accounts', 'Agent Chat', 'Realtime',
]

test.describe('Settings v3 (/settings)', () => {
  test('renders rail nav with all sections', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.view .vh h1')).toHaveText('Settings')
    const nav = page.locator('.settings-nav .nav-item')
    expect(await nav.count()).toBe(SECTIONS.length)
    for (const label of SECTIONS) {
      await expect(page.locator('.settings-nav .nav-item', { hasText: label })).toBeVisible()
    }
  })

  test('default section is Overview', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')
  })

  // An id that is not a settings page falls back to Overview.
  test('unknown ids fall back to Overview', async ({ page }) => {
    await page.goto('/#settings/health')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')
  })

  test('clicking Updates swaps the section', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Updates' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
  })

  test('no legacy "Runtime" section remains (#687 Phase E)', async ({ page }) => {
    await page.goto('/#settings')
    // The old singular "Runtime" page is gone; the new D3 "Runtimes" evidence
    // page is a different section (exact-match so it isn't caught here).
    await expect(page.locator('.settings-nav .nav-item', { hasText: /^Runtime$/ })).toHaveCount(0)
  })

  // ML-4-unblocked pages (R5 data seam): both mount without a runtime error
  // (spec risk #5 — the ESM split must thread the old window-globals or pages
  // blow up at click time with no compile error).
  test('Model Defaults section mounts', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Model Defaults' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Model Defaults')
  })

  // #2105 + ADR-0029: Model Defaults edits FILE FACTS only. NGL is slot-owned
  // hardware (spec-hw-slot-ownership §2) and the context window is a profile
  // field, so neither "GPU layers" nor "Context size" may be offered here —
  // an operator could otherwise set a value that looked saved and was then
  // silently discarded (the server answers a tuning key under `defaults` with
  // a 422). The per-model panels must render (Chat template proves that), with
  // the native context window shown read-only.
  test('Model Defaults offers file facts only — no GPU layers, no Context size', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Model Defaults' }).click()
    const content = page.locator('.settings-content')
    // Chat template proves the per-model panels rendered at all, so the
    // absence below is a real absence and not an empty page.
    await expect(content.locator('.s-row .k span', { hasText: /^Chat template$/ }).first()).toBeVisible()
    await expect(content.locator('.s-row .k span', { hasText: /^Native context$/ }).first()).toBeVisible()
    await expect(content.locator('.s-row .k span', { hasText: /^GPU layers$/ })).toHaveCount(0)
    await expect(content.locator('.s-row .k span', { hasText: /^Context size$/ })).toHaveCount(0)
  })

  // ADR-0029 §6: the chat template is a top-level fact on the model. The save
  // body carries exactly that key — a `defaults` object is a 422 server-side
  // (model.defaults_removed) and no other fact or tune rides along.
  test('Model Defaults saves the chat template as a top-level fact', async ({ page }) => {
    await page.route('**/api/chat-templates', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          { id: 'auto', label: 'Auto (GGUF embedded)' },
          { id: 'chatml', label: 'ChatML' },
        ]),
      }),
    )
    const puts: any[] = []
    await page.route('**/api/models/*', async (route) => {
      if (route.request().method() === 'PUT') {
        const body = route.request().postDataJSON()
        puts.push(body)
        return route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ id: 'm', ...body }),
        })
      }
      return route.fallback()
    })

    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Model Defaults' }).click()
    const panel = page.locator('.settings-content .s-panel').filter({ has: page.locator('select') }).first()
    await panel.locator('select').selectOption('chatml')
    await panel.getByRole('button', { name: 'Save' }).click()

    await expect.poll(() => puts.length).toBe(1)
    expect(puts[0]).toEqual({ chat_template: 'chatml' })
  })

  test('Hardware & Runtimes section mounts', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Hardware & Runtimes' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Hardware & Runtimes')
  })

  // The pages absorbed by the cleanup must surface their content on the new
  // owners: library panels on Loaded Models, health panels on Overview.
  test('Loaded Models carries the absorbed library panels', async ({ page }) => {
    await page.goto('/#settings')
    await page.locator('.settings-nav .nav-item', { hasText: 'Loaded Models' }).click()
    await expect(page.locator('.settings-content h2').first()).toHaveText('Loaded Models')
    await expect(page.locator('.settings-content')).toContainText('Models known to the catalog')
    await expect(page.locator('.settings-content')).not.toContainText('not yet wired')
  })

  test('Overview carries the absorbed health panels', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Overview')
    await expect(page.locator('.settings-content')).toContainText('Health checks')
    await expect(page.locator('.settings-content')).toContainText('Anonymous telemetry')
    await expect(page.locator('.settings-content')).not.toContainText('not yet wired')
  })

  // VERS-flash (docs/rework/handoff-r5-drive2.md §3): index.html no longer
  // hardcodes a version literal — the build-time stamp (ui/package.json,
  // reconciled to the backend release) is what first paints, and App()
  // then syncs document.title to the *live* `/api/updates/state` value
  // (mocked here as hal0.current, see src/api/mock.ts) once it resolves.
  // Asserting against the live-mocked version (not the build-time stamp)
  // proves the post-mount sync actually runs, not just the HTML bake.
  test('document.title syncs to the live hal0 version, not a stale literal', async ({ page }) => {
    await page.goto('/#settings')
    await expect(page).toHaveTitle(/v0\.3\.0-alpha\.1/)
    await expect(page).not.toHaveTitle(/v0\.5\.0-alpha\.1/)
  })

  test('Updates ▸ About panel shows the same live version as the tab title', async ({ page }) => {
    await page.goto('/#settings/updates')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Updates')
    await expect(page.locator('.settings-content')).toContainText('Apache-2.0')
    await expect(page.locator('.settings-content')).toContainText('0.3.0-alpha.1')
  })
})

// #2382: Settings ▸ Network is its own page (Security stays input-free) and a
// save sends the origins as a diff, so an origin added elsewhere since the
// page loaded is never written over by the page's stale copy of the list.
test.describe('Settings ▸ Network (#2382)', () => {
  test('saves origins as a diff through POST /api/settings/network', async ({ page }) => {
    const network = { public_url: null as string | null, extra_origins: ['http://a.lan:8080', 'http://b.lan:8080'] }
    let editBody: any = null
    let putCalled = false
    await page.route('**/api/settings', (r) => {
      if (r.request().method() === 'PUT') putCalled = true
      return json(r, { security: {}, network })
    })
    await page.route('**/api/settings/network', async (r) => {
      editBody = await r.request().postDataJSON()
      return json(r, {
        security: {},
        network: { public_url: 'https://hal0.example.com', extra_origins: ['http://a.lan:8080', 'http://c.lan:8080'] },
      })
    })

    await page.goto('/#settings/network')
    await expect(page.locator('.settings-content h2').first()).toHaveText('Network')
    const origins = page.getByTestId('network-extra-origins')
    await expect(origins).toHaveValue('http://a.lan:8080\nhttp://b.lan:8080')

    await page.getByTestId('network-public-url').fill('https://hal0.example.com')
    await origins.fill('http://a.lan:8080\nhttp://c.lan:8080')
    await Promise.all([
      page.waitForResponse((r) => r.url().endsWith('/api/settings/network') && r.request().method() === 'POST'),
      page.getByTestId('network-save').click(),
    ])
    expect(editBody).toEqual({
      public_url: 'https://hal0.example.com',
      add_origins: ['http://c.lan:8080'],
      remove_origins: ['http://b.lan:8080'],
    })
    expect(putCalled).toBe(false)
    await expect(origins).toHaveValue('http://a.lan:8080\nhttp://c.lan:8080')
  })

  test('an edit typed before the settings arrive is kept, not overwritten by the load', async ({ page }) => {
    let release!: () => void
    const loaded = new Promise<void>((resolve) => { release = resolve })
    await page.route('**/api/settings', async (r) => {
      await loaded
      return json(r, { security: {}, network: { public_url: null, extra_origins: [] } })
    })

    await page.goto('/#settings/network')
    const origins = page.getByTestId('network-extra-origins')
    await origins.fill('http://typed.lan:8080')
    release()
    await page.waitForResponse((r) => r.url().endsWith('/api/settings'))
    await expect(page.getByTestId('network-save')).toBeEnabled()
    await expect(origins).toHaveValue('http://typed.lan:8080')
  })

  test('a refused origin shows the server reason', async ({ page }) => {
    await page.route('**/api/settings', (r) => json(r, { security: {}, network: { public_url: null, extra_origins: [] } }))
    await page.route('**/api/settings/network', (r) =>
      r.fulfill({
        status: 400,
        contentType: 'application/json',
        body: JSON.stringify({
          error: {
            code: 'config.invalid',
            message: 'hal0 config failed schema validation',
            details: { 'network.extra_origins': "origin 'https://x.example/app' must be scheme://host[:port] only" },
          },
        }),
      }))

    await page.goto('/#settings/network')
    await page.getByTestId('network-extra-origins').fill('https://x.example/app')
    await page.getByTestId('network-save').click()
    await expect(page.getByTestId('network-error')).toContainText('must be scheme://host[:port] only')
  })
})
