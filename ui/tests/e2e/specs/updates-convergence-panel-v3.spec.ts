/**
 * updates-convergence-panel-v3 — Settings ▸ Updates ▸ ownership-migration
 * banner (#1845/H8).
 *
 * `hal0.updater.updater.detect_pending_ownership_migrations` decides,
 * server-side, whether the pending migrate-* command needs `--stop-services`
 * (hal0 units live) or not (units down) — see
 * tests/updater/test_convergence_report.py for the backend cases. This spec
 * covers the OTHER half of the promise in the brief: the dashboard panel
 * must print that exact command line, with no client-side reassembly.
 *
 * `/api/updates/state` is in `mockFetch`'s FORCED-mock allowlist (not
 * `networkFirst`), so `page.route` can never see it — same shape
 * update-banner-v3.spec.ts documents. Override it via
 * `window.__hal0UpdateStateOverride` instead. `/api/updates/apply` (POST)
 * and `/api/updates/status/:id` (unrouted GET) aren't allowlisted, so both
 * reach `page.route` normally.
 */
import { test, expect } from '../fixtures/apiMock'

const JOB_ID = 'job-convergence-1'

async function mockApplyAndStatus(page: import('@playwright/test').Page, convergence: unknown) {
  await page.addInitScript(() => {
    ;(window as any).__hal0UpdateStateOverride = {
      hal0: { current: '1.1.0', available: '1.2.0', channel: 'stable' },
      flm: { current: 'v0.9.42', source: 'manual-deb' },
      autoCheck: true,
    }
  })
  await page.route('**/api/updates/apply', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: JOB_ID,
        state: 'queued',
        channel: 'stable',
        version: '1.2.0',
        created_at: Date.now() / 1000,
        updated_at: Date.now() / 1000,
        error: null,
      }),
    }),
  )
  await page.route(`**/api/updates/status/${JOB_ID}`, (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        id: JOB_ID,
        state: 'applied',
        channel: 'stable',
        version: '1.2.0',
        created_at: Date.now() / 1000,
        updated_at: Date.now() / 1000,
        error: null,
        convergence,
      }),
    }),
  )
}

test.describe('Updates page — ownership-migration panel (#1845/H8)', () => {
  test('prints the server command verbatim, --stop-services included, when units are up', async ({
    page,
  }) => {
    await mockApplyAndStatus(page, {
      profile_reset: { due: false },
      ownership_migrations: {
        pending: ['hw'],
        detail: {
          hw: {
            command: 'hal0 slot migrate-hw --apply --stop-services',
            lines: ["would fold slot 'chat': ngl=99 binary='rocmfpx'"],
            error: null,
          },
        },
        commands: ['hal0 slot migrate-hw --apply --stop-services'],
      },
      converged: false,
    })
    await page.goto('/#settings/updates')
    await page.locator('[data-testid="updates-hal0-version"]').waitFor()
    await page.getByRole('button', { name: 'Install update' }).click()

    const panel = page.locator('[data-testid="updates-convergence-pending"]')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('hal0 slot migrate-hw --apply --stop-services')
  })

  test('prints the plain command with no flag when units are down', async ({ page }) => {
    await mockApplyAndStatus(page, {
      profile_reset: { due: false },
      ownership_migrations: {
        pending: ['hw'],
        detail: {
          hw: {
            command: 'hal0 slot migrate-hw --apply',
            lines: ["would fold slot 'chat': ngl=99 binary='rocmfpx'"],
            error: null,
          },
        },
        commands: ['hal0 slot migrate-hw --apply'],
      },
      converged: false,
    })
    await page.goto('/#settings/updates')
    await page.locator('[data-testid="updates-hal0-version"]').waitFor()
    await page.getByRole('button', { name: 'Install update' }).click()

    const panel = page.locator('[data-testid="updates-convergence-pending"]')
    await expect(panel).toBeVisible()
    await expect(panel).toContainText('hal0 slot migrate-hw --apply')
    await expect(panel).not.toContainText('--stop-services')
  })

  test('no panel when fully converged', async ({ page }) => {
    await mockApplyAndStatus(page, {
      profile_reset: { due: false },
      ownership_migrations: { pending: [], detail: {}, commands: [] },
      converged: true,
    })
    await page.goto('/#settings/updates')
    await page.locator('[data-testid="updates-hal0-version"]').waitFor()
    await page.getByRole('button', { name: 'Install update' }).click()
    await expect(page.locator('[data-testid="updates-convergence-pending"]')).toHaveCount(0)
  })
})
