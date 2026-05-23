/**
 * settings.spec.ts — v2 Settings page smoke (slice #173).
 *
 * Replaces the v1 telemetry.channel round-trip — that flow lived on
 * Settings.vue's old hal0.toml form, which is no longer surfaced in
 * the v2 layout. The intent of the original spec was "Settings can
 * persist a change to a restart-required field and surface the
 * restart banner"; in v2 the same behaviour is exercised against the
 * Lemonade admin section's save-and-restart confirm flow (see
 * settings-v2.spec.ts for the dedicated coverage).
 *
 * This file keeps a slim navigational smoke so the route mounts under
 * the apiMock fixture even when contributors only run a subset of
 * specs.
 */
import { test, expect } from '../fixtures/apiMock'

test('renders the v2 Settings page with the rail and all 9 sections', async ({
  page,
  mockState,
  cleanState,
}) => {
  await page.goto('/settings')

  // Page chrome.
  await expect(page.locator('[data-testid="settings-v2"]')).toBeVisible()
  await expect(page.locator('.page-title')).toContainText('Settings')

  // Rail renders all 9 anchor labels.
  const rail = page.locator('[data-testid="settings-rail"]')
  await expect(rail).toBeVisible()
  for (const label of [
    'Auth', 'Secrets', 'Updates', 'Lemonade admin', 'OmniRouter',
    'Agent policy', 'Memory (Cognee)', 'Appearance', 'About',
  ]) {
    await expect(rail.locator('.nav-item', { hasText: label })).toBeVisible()
  }

  // PR-13 deep-link still exists.
  await expect(page.locator('[data-testid="lemonade-admin-link"]')).toBeVisible()
})
