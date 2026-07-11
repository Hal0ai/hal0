/**
 * notification-bell-v3 — topbar notification center.
 *
 * The bell aggregates every "wants the operator's eye" source into one
 * topbar surface:
 *   1. items needing attention (pending approvals, slots in error) — same
 *      sources as the dashboard's Needs Attention card;
 *   2. model downloads (queued/running pulls + failed ones);
 *   3. updates (hal0 release available via useUpdateState);
 *   4. developer messages published via `window.hal0Notify(...)` or the
 *      `hal0:notify` CustomEvent.
 *
 * Badge count = live actionable items; rows route to the surface that owns
 * the action (ApprovalModal via hal0:open-approvals, footer downloads pane
 * via hal0:open-downloads, Settings → Updates).
 *
 * Forced-mock notes: /api/updates/state is short-circuited by mockFetch and
 * its seed always offers an update, so specs pin the payload through the
 * `window.__hal0UpdateStateOverride` seam (same as footer-update-chip-v3).
 * /api/models/pulls and /api/agent/approvals ARE reachable via page.route.
 */
import { test, expect } from '../fixtures/apiMock'

const NO_UPDATE = {
  hal0: { current: '0.3.0-alpha.1', available: null, channel: 'stable' },
  flm: { current: 'v0.9.42', source: 'manual-deb' },
  autoCheck: true,
}

async function withUpdateState(page: import('@playwright/test').Page, payload: unknown) {
  await page.addInitScript((p) => {
    ;(window as any).__hal0UpdateStateOverride = p
  }, payload)
}

test.describe('Notification bell (topbar)', () => {
  test('all clear — no badge, empty panel', async ({ page }) => {
    await withUpdateState(page, NO_UPDATE)
    await page.goto('/')
    const bell = page.getByTestId('tb-bell')
    await expect(bell).toBeVisible()
    await expect(page.getByTestId('tb-bell-badge')).toHaveCount(0)
    await bell.click()
    await expect(page.getByTestId('notif-pop')).toBeVisible()
    await expect(page.getByTestId('notif-empty')).toBeVisible()
    // Escape closes the panel.
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('notif-pop')).toHaveCount(0)
  })

  test('update available — badge counts it, updates section renders', async ({ page }) => {
    await withUpdateState(page, {
      ...NO_UPDATE,
      hal0: { current: '0.3.0-alpha.1', available: '0.9.9', channel: 'stable' },
    })
    await page.goto('/')
    await expect(page.getByTestId('tb-bell-badge')).toHaveText('1', { timeout: 6_000 })
    await page.getByTestId('tb-bell').click()
    const sec = page.getByTestId('notif-sec-updates')
    await expect(sec).toBeVisible()
    await expect(sec).toContainText('0.9.9')
    // The Update action deep-links to Settings → Updates.
    await sec.getByRole('button', { name: 'Update' }).click()
    await expect(page).toHaveURL(/#settings\/updates/)
  })

  test('pending approvals — attention section, Review opens the approval modal', async ({
    page,
    mockState,
  }) => {
    await withUpdateState(page, NO_UPDATE)
    mockState.approvals = [
      {
        id: 'ap-1',
        tool: 'fs_write',
        args: { path: '/etc/hal0.toml' },
        client_id: 'hermes',
        enqueued_at: Date.now() / 1000 - 120,
        state: 'pending',
      },
      {
        id: 'ap-2',
        tool: 'shell_exec',
        args: { cmd: 'systemctl restart hal0-api' },
        client_id: 'coder',
        enqueued_at: Date.now() / 1000 - 60,
        state: 'pending',
      },
    ]
    await page.goto('/')
    await expect(page.getByTestId('tb-bell-badge')).toHaveText('2', { timeout: 6_000 })
    await page.getByTestId('tb-bell').click()
    const sec = page.getByTestId('notif-sec-attention')
    await expect(sec).toBeVisible()
    await expect(sec).toContainText('fs_write')
    await expect(sec).toContainText('shell_exec')
    await sec.getByRole('button', { name: 'Review' }).first().click()
    // Bell panel closes; the existing ApprovalModal takes over.
    await expect(page.getByTestId('notif-pop')).toHaveCount(0)
    await expect(page.locator('.approval-modal')).toBeVisible()
    await expect(page.locator('.approval-modal')).toContainText('fs_write')
  })

  test('running download — downloads section with progress, link opens footer pane', async ({
    page,
  }) => {
    await withUpdateState(page, NO_UPDATE)
    await page.route('**/api/models/pulls', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            job_id: 'job-1',
            model_id: 'qwen3-8b',
            hf_repo: 'Qwen/Qwen3-8B-GGUF',
            dest_path: '/var/lib/hal0/models/qwen3-8b.gguf',
            state: 'running',
            bytes_downloaded: 2_500_000_000,
            bytes_total: 5_000_000_000,
            speed_bps: 50_000_000,
            eta_s: 50,
            error: null,
            started_at: Date.now() / 1000,
            finished_at: null,
          },
        ]),
      }),
    )
    await page.goto('/')
    await expect(page.getByTestId('tb-bell-badge')).toHaveText('1', { timeout: 6_000 })
    await page.getByTestId('tb-bell').click()
    const sec = page.getByTestId('notif-sec-downloads')
    await expect(sec).toBeVisible()
    await expect(sec).toContainText('Qwen/Qwen3-8B-GGUF')
    await expect(sec).toContainText('50%')
    // "Open downloads pane →" expands the footer on its downloads tab.
    await sec.getByRole('button', { name: /Open downloads pane/ }).click()
    await expect(page.getByTestId('notif-pop')).toHaveCount(0)
    await expect(page.locator('.footer.expanded')).toBeVisible()
    await expect(page.locator('.foot-tab.active')).toHaveText('downloads')
  })

  test('developer messages — hal0Notify shows a dismissible message', async ({ page }) => {
    await withUpdateState(page, NO_UPDATE)
    await page.goto('/')
    await expect(page.getByTestId('tb-bell')).toBeVisible()
    await page.evaluate(() => {
      ;(window as any).hal0Notify({
        id: 'dev-note-1',
        title: 'Heads up from the hal0 team',
        body: 'v0.10 changes the slot config format — back up hal0.toml first.',
        kind: 'warn',
      })
    })
    await expect(page.getByTestId('tb-bell-badge')).toHaveText('1')
    await page.getByTestId('tb-bell').click()
    const sec = page.getByTestId('notif-sec-messages')
    await expect(sec).toBeVisible()
    await expect(sec).toContainText('Heads up from the hal0 team')
    await expect(sec).toContainText('back up hal0.toml')
    // Dismiss removes the message and clears the badge.
    await sec.getByRole('button', { name: 'Dismiss message' }).click()
    await expect(page.getByTestId('notif-sec-messages')).toHaveCount(0)
    await expect(page.getByTestId('tb-bell-badge')).toHaveCount(0)
    // Dismissal persists — re-publishing the same id is a no-op.
    await page.evaluate(() => {
      ;(window as any).hal0Notify({ id: 'dev-note-1', title: 'Heads up from the hal0 team' })
    })
    await expect(page.getByTestId('tb-bell-badge')).toHaveCount(0)
  })

  test('hal0:notify CustomEvent is an equivalent publish channel', async ({ page }) => {
    await withUpdateState(page, NO_UPDATE)
    await page.goto('/')
    await expect(page.getByTestId('tb-bell')).toBeVisible()
    await page.evaluate(() => {
      window.dispatchEvent(
        new CustomEvent('hal0:notify', {
          detail: { title: 'Nightly channel now available', kind: 'update' },
        }),
      )
    })
    await expect(page.getByTestId('tb-bell-badge')).toHaveText('1')
    await page.getByTestId('tb-bell').click()
    await expect(page.getByTestId('notif-sec-messages')).toContainText(
      'Nightly channel now available',
    )
  })

  test('model updates — badge + row render app-level, Update all fans out', async ({ page }) => {
    await withUpdateState(page, NO_UPDATE)
    // Flag one installed mock model as updatable — forced-mock serves
    // HAL0_DATA.models verbatim, and update_available flows through
    // normalizeApiModel untouched. Note: no navigation to #models — the
    // bell must fire from the app shell alone.
    await page.addInitScript(() => {
      document.addEventListener('DOMContentLoaded', () => {
        const D = (window as any).HAL0_DATA
        const m = D?.models?.find((x: any) => x.id === 'qwen3.6-27b-mtp')
        if (m) m.update_available = true
      })
    })
    const updates: string[] = []
    await page.route('**/api/models/qwen3.6-27b-mtp/update', async (route) => {
      updates.push(route.request().url())
      await route.fulfill({ status: 202, contentType: 'application/json', body: '{}' })
    })
    await page.goto('/')
    await expect(page.getByTestId('tb-bell-badge')).toHaveText('1', { timeout: 6_000 })
    await page.getByTestId('tb-bell').click()
    const row = page.getByTestId('notif-model-updates')
    await expect(row).toBeVisible()
    await expect(row).toContainText('1 model update available on HuggingFace')
    await row.getByRole('button', { name: 'Update all' }).click()
    await expect.poll(() => updates.length).toBeGreaterThan(0)
  })

  test('no outdated models — the model-updates row self-clears (absent)', async ({ page }) => {
    await withUpdateState(page, NO_UPDATE)
    await page.goto('/')
    await expect(page.getByTestId('tb-bell-badge')).toHaveCount(0)
    await page.getByTestId('tb-bell').click()
    await expect(page.getByTestId('notif-model-updates')).toHaveCount(0)
  })
})
