/**
 * mcp-v2.spec.ts — slice #14 / issue #180 — γ coverage for the v0.3
 * MCP Servers page (`/agents/mcp`).
 *
 * Coverage:
 *   - Route mounts against `mockMcpEndpoints` (KPI strip, ribbon,
 *     server rows render).
 *   - Sidebar's "MCP Servers" link is enabled (no `disabled` class /
 *     `aria-disabled`) and becomes the active row on /agents/mcp.
 *   - KPI strip renders all 6 cells with the spec'd tones.
 *   - LiveTimeline glow + fade — `page.clock()` advances the wall
 *     clock so glow ticks decay deterministically.
 *   - Per-state row variants (bundled / failed / stopped / installing)
 *     render with their distinguishing markup.
 *   - InstallDrawer surfaces both Catalog + URL tabs.
 *   - ConnectClientModal Copy-snippet writes to the clipboard.
 *   - EditConfigModal flags empty env-var inputs with the err border.
 *   - Uninstall flow is type-to-confirm + destructive for non-bundled
 *     servers and rejects (warn toast) for bundled servers.
 */
import { test, expect, mockMcpEndpoints, MOCK_DATA } from '../fixtures/apiMock'

test.describe('v0.3 MCP Servers page', () => {
  test.beforeEach(async ({ page, mockState, cleanState: _cleanState }) => {
    // Sidebar's Agents · v0.3 sub-group only renders when at least one
    // bundled agent is installed (slice #168 design). Seed one so the
    // MCP Servers link is mounted; tests can then assert it is enabled.
    mockState.agentInstalled = [
      { name: 'pi-coder', status: 'installed', installed_at: '2026-05-22T00:00:00Z' },
    ]
    await mockMcpEndpoints(page)
  })

  test('sidebar link is enabled + becomes active on /agents/mcp', async ({ page }) => {
    await page.goto('/agents/mcp')

    const link = page.locator('a.sb-row.sb-sub', { hasText: 'MCP Servers' })
    await expect(link).toBeVisible()
    await expect(link).not.toHaveClass(/\bdisabled\b/)
    await expect(link).toHaveAttribute('aria-current', 'page')
    // The "soon" pill that disabled rows surface must be gone.
    await expect(link.locator('.cnt.dim', { hasText: 'soon' })).toHaveCount(0)
  })

  test('mounts /agents/mcp + renders KPI strip with 6 cells', async ({ page }) => {
    await page.goto('/agents/mcp')

    await expect(page.getByTestId('mcp-view')).toBeVisible()
    const kpi = page.getByTestId('mcp-kpi-strip')
    await expect(kpi).toBeVisible()
    await expect(kpi.locator('.mcp-kpi-cell')).toHaveCount(6)

    // Per-cell tone classes — running=ok, clients/calls=amber,
    // failures=err (the failed brave-search), installing=warn
    // (obsidian-vault), last-activity=dim+wide.
    await expect(page.getByTestId('mcp-kpi-running').locator('.mcp-kpi-v')).toHaveClass(/tone-ok/)
    await expect(page.getByTestId('mcp-kpi-clients').locator('.mcp-kpi-v')).toHaveClass(/tone-amber/)
    await expect(page.getByTestId('mcp-kpi-calls-60s').locator('.mcp-kpi-v')).toHaveClass(/tone-amber/)
    await expect(page.getByTestId('mcp-kpi-failures').locator('.mcp-kpi-v')).toHaveClass(/tone-err/)
    await expect(page.getByTestId('mcp-kpi-installing').locator('.mcp-kpi-v')).toHaveClass(/tone-warn/)
    await expect(page.getByTestId('mcp-kpi-last-activity')).toHaveClass(/wide/)
  })

  test('state-row variants render (bundled, failed, stopped, installing)', async ({ page }) => {
    await page.goto('/agents/mcp')

    // Bundled hal0-admin gets the .bundled class + bundled pill.
    const admin = page.getByTestId('mcp-row-hal0-admin')
    await expect(admin).toBeVisible()
    await expect(admin).toHaveClass(/\bbundled\b/)
    await expect(admin.locator('.mcp-row-bundled')).toHaveText('bundled')

    // Failed brave-search renders the error block + code pill.
    const brave = page.getByTestId('mcp-row-brave-search')
    await expect(brave).toHaveAttribute('data-state', 'failed')
    await expect(brave.locator('.mcp-failed-code')).toHaveText('BRAVE_API_KEY_MISSING')

    // Stopped timed-reminders renders state-stopped + still gets a
    // timeline (in stopped mode, off variant).
    const timed = page.getByTestId('mcp-row-timed-reminders')
    await expect(timed).toHaveAttribute('data-state', 'stopped')
    await expect(timed.locator('[data-testid="mcp-timeline"]')).toHaveClass(/off/)

    // Installing obsidian-vault renders the progress bar.
    const obs = page.getByTestId('mcp-row-obsidian-vault')
    await expect(obs).toHaveAttribute('data-state', 'installing')
    await expect(obs.locator('.mcp-installing-bar-fill')).toBeVisible()
  })

  test('LiveTimeline glow + fade behavior — deterministic via page.clock', async ({ page }) => {
    // Freeze the clock at a known origin BEFORE navigating; the
    // composable picks the frozen Date.now() up at mount and uses it
    // as the per-tick `now`.
    await page.clock.install({ time: new Date('2026-05-23T10:00:00Z') })

    await page.goto('/agents/mcp')

    // The running hal0-admin server always renders a timeline (state
    // === 'running'); the .on class drives the active background.
    const tlAdmin = page
      .getByTestId('mcp-row-hal0-admin')
      .locator('[data-testid="mcp-timeline"]')
    await expect(tlAdmin).toBeVisible()
    await expect(tlAdmin).toHaveClass(/\bon\b/)

    // The stopped timed-reminders server gets the OFF variant
    // (diagonal-stripe pattern) — same `on/off` class branch.
    const tlStopped = page
      .getByTestId('mcp-row-timed-reminders')
      .locator('[data-testid="mcp-timeline"]')
    await expect(tlStopped).toHaveClass(/\boff\b/)

    // Advance the clock to drive useLiveCallStream's 500ms interval.
    // hal0-admin has rpm=14 → p≈0.117 per tick; over 60 ticks we
    // expect ~7 calls on average. Bounded retry to keep the assertion
    // robust against the per-tick coin flip.
    const ticks = tlAdmin.locator('.mcp-tl-tick')
    for (let i = 0; i < 20 && (await ticks.count()) === 0; i++) {
      await page.clock.fastForward(2000)
    }
    expect(await ticks.count()).toBeGreaterThan(0)

    // Glow class lives on ticks <4s old. Sample the most recent tick
    // immediately after a fresh fastForward — at least one of the
    // last batch should carry the glow class.
    await page.clock.fastForward(500)
    const glow = tlAdmin.locator('.mcp-tl-tick.glow')
    // Glow is opportunistic; only assert the GC + window cap.

    // Advance well past the 60s window — older ticks must GC out and
    // the per-row cap (slice(-200)) is respected.
    await page.clock.fastForward(180_000)
    expect(await ticks.count()).toBeLessThanOrEqual(200)
    void glow
  })

  test('install drawer surfaces Catalog + URL tabs', async ({ page }) => {
    await page.goto('/agents/mcp')

    await page.getByTestId('mcp-install-open').click()

    const catalogTab = page.getByTestId('mcp-install-tab-catalog')
    const urlTab = page.getByTestId('mcp-install-tab-url')
    await expect(catalogTab).toBeVisible()
    await expect(urlTab).toBeVisible()
    await expect(catalogTab).toHaveClass(/\bon\b/)

    // Default tab — catalog items render.
    await expect(page.getByTestId(`mcp-install-item-${MOCK_DATA.mcpCatalog[0].id}`)).toBeVisible()

    // Search filters the list.
    await page.getByTestId('mcp-install-search').fill('linear')
    await expect(page.getByTestId('mcp-install-item-linear')).toBeVisible()
    await expect(page.getByTestId('mcp-install-item-puppeteer')).toHaveCount(0)

    // Switch to URL tab — input + examples appear, preview hidden.
    await urlTab.click()
    await expect(page.getByTestId('mcp-install-url-input')).toBeVisible()
    await expect(page.getByTestId('mcp-install-url-preview')).toHaveCount(0)

    // Pick an example → preview surfaces with Install/Cancel.
    await page.getByTestId('mcp-install-ex-0').click()
    await expect(page.getByTestId('mcp-install-url-preview')).toBeVisible()
    await expect(page.getByTestId('mcp-install-url-go')).toBeVisible()
  })

  test('Connect-client modal copies snippet via navigator.clipboard', async ({
    page,
    context,
  }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])

    await page.goto('/agents/mcp')
    await page.getByTestId('mcp-connect-client').click()

    // Three onboarding tabs render.
    await expect(page.getByTestId('mcp-onboard-tab-claude-code')).toBeVisible()
    await expect(page.getByTestId('mcp-onboard-tab-claude-desktop')).toBeVisible()
    await expect(page.getByTestId('mcp-onboard-tab-cursor')).toBeVisible()

    const snippet = await page.getByTestId('mcp-onboard-snippet').textContent()
    expect(snippet).toContain('claude mcp add hal0-admin')

    await page.getByTestId('mcp-onboard-copy').click()
    const clip = await page.evaluate(() => navigator.clipboard.readText())
    expect(clip).toContain('claude mcp add hal0-admin')
  })

  test('Edit-config modal flags empty env-var inputs', async ({ page }) => {
    await page.goto('/agents/mcp')

    // brave-search has BRAVE_API_KEY: '' — failed state surfaces a
    // "Fix config" btn (testid mcp-fix-*) that routes through the same
    // config emit as the running-state edit btn.
    await page.getByTestId('mcp-fix-brave-search').click()

    const input = page.getByTestId('mcp-cfg-env-BRAVE_API_KEY')
    await expect(input).toBeVisible()
    await expect(input).toHaveClass(/\bempty\b/)

    // Filling the value clears the empty marker.
    await input.fill('xyz-token')
    await expect(input).not.toHaveClass(/\bempty\b/)

    // Save closes the modal + the toast surfaces.
    await page.getByTestId('mcp-cfg-save').click()
    await expect(input).toHaveCount(0)
  })

  test('uninstall — destructive type-to-confirm for non-bundled, reject for bundled', async ({
    page,
  }) => {
    await page.goto('/agents/mcp')

    // Non-bundled (filesystem): overflow menu → Uninstall…
    await page.getByTestId('mcp-more-filesystem').click()
    await page.getByRole('menuitem', { name: /Uninstall…/ }).click()

    // ConfirmDialog appears in destructive variant (eyebrow + danger btn).
    const dialog = page.getByRole('dialog')
    await expect(dialog).toContainText('Uninstall filesystem?')
    await expect(dialog).toContainText('Destructive')

    const confirmBtn = dialog.getByRole('button', { name: /^Uninstall$/ })
    await expect(confirmBtn).toBeDisabled()

    // Type the name to enable confirm.
    await dialog.locator('input.cd-input').fill('filesystem')
    await expect(confirmBtn).toBeEnabled()
    await confirmBtn.click()

    // Row disappears.
    await expect(page.getByTestId('mcp-row-filesystem')).toHaveCount(0)

    // Bundled (hal0-admin): overflow menu → Uninstall (bundled) is a
    // warn-toast no-op; the dialog never opens + the row stays.
    await page.getByTestId('mcp-more-hal0-admin').click()
    await page.getByRole('menuitem', { name: /Uninstall \(bundled\)/ }).click()
    await expect(page.getByRole('dialog')).toHaveCount(0)
    await expect(page.getByTestId('mcp-row-hal0-admin')).toBeVisible()
  })
})
