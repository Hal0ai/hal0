/**
 * dashboard-names-live — NAMES-stale regression (handoff-r5-drive2.md §3).
 *
 * Slots named "primary"/"legacy" are the dev/e2e-mock convention (data.jsx,
 * mockFixtures.ts, tests/e2e/fixtures/mock-data.ts) — the mock-prod-gate
 * (docs/rework/r5-sync-assessment-2026-07-19.md §1.1) already keeps that
 * fetch-fallback path out of production. What it does NOT cover is any
 * component that still picks "the primary slot" by matching the literal
 * name `"primary"`/`"agent"` instead of a real role signal (`isDefault` +
 * `group`/`type`) — that kind of heuristic silently renders the *fixture's*
 * naming convention as if it were meaningful, and breaks the moment an
 * operator renames a slot (useSlotRename allows any name).
 *
 * `/api/status` and `/api/slots` are FORCED-mock allowlisted (mock.ts
 * MOCK_ALLOWLIST) — under VITE_MOCK_HAL0=1 (this suite's webServer flag)
 * `mockFetch` answers them synchronously from `window.HAL0_DATA` without
 * ever leaving the JS runtime, so a `page.route` stub for those two GETs
 * cannot intercept anything (confirmed empirically — see git history on
 * this file). The repo's own convention for driving "live" slot state
 * (dashboard-redesign-v3.spec.ts's `gotoDashboard`, slot-card-container-v3,
 * memory-map-v3, …) is therefore `page.addInitScript` overriding
 * `window.HAL0_DATA.slots` before module load — used below. `/api/agents`
 * is NOT allowlisted, so it takes the real fetch path and IS `page.route`-
 * interceptable.
 *
 * Injects slots named after an operator's own convention (never
 * "primary"/"legacy"), with an isDefault EMBED slot ordered BEFORE the
 * isDefault CHAT slot (the exact ordering that breaks a bare
 * `.find(s => s.isDefault)` heuristic — every group's representative slot
 * can carry `isDefault: true`). Asserts:
 *   1. the words "primary"/"legacy" never render anywhere on the Dashboard
 *      or Agents-overview pages, and
 *   2. the role-based selectors (dashboard-redesign.jsx QuickActions,
 *      agents-overview.jsx _primarySlot, useSlots.ts inferSlotShape) still
 *      resolve the real default chat slot correctly.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

const CHAT_SLOT = {
  name: 'ops-chat-01',
  state: 'serving',
  backend: 'rocm',
  device: 'gpu-rocm',
  model: 'ops-llm-9000',
  model_id: 'ops-llm-9000',
  port: 8100,
  type: 'llm',
  group: 'chat',
  enabled: true,
  isDefault: true,
  container_status: 'running',
  container_health: true,
  last_used_at: Date.now() / 1000,
  ctx_max: 32000,
  metrics: { toks: 12.5, ttft: 210, ctx: 900 },
}

// Deliberately ALSO isDefault:true, and ordered BEFORE the chat slot below —
// this is the regression case for ".find(s => s.isDefault)" without a
// group/type filter: every group's representative slot can carry
// isDefault:true, so array order alone must not decide "the agent slot".
const EMBED_SLOT = {
  name: 'ops-embed-01',
  state: 'ready',
  backend: 'vulkan',
  device: 'gpu-vulkan',
  model: 'ops-embed-model',
  type: 'embedding',
  group: 'embed',
  enabled: true,
  isDefault: true,
  container_status: 'running',
  container_health: true,
  metrics: { toks: 0, ttft: null, ctx: 0 },
}

const OFFLINE_SLOT = {
  name: 'ops-old-vulkan-01',
  state: 'offline',
  backend: 'vulkan',
  device: 'gpu-vulkan',
  model: 'ops-fallback-model-x',
  type: 'llm',
  group: 'chat',
  enabled: false,
  isDefault: false,
  container_status: 'stopped',
  container_health: false,
  metrics: { toks: 0, ttft: null, ctx: 0 },
}

const LIVE_SLOTS = [EMBED_SLOT, OFFLINE_SLOT, CHAT_SLOT]

async function gotoWithLiveSlots(page: Page, hash: string) {
  await page.addInitScript((injected) => {
    const apply = () => {
      const w = window as any
      w.HAL0_DATA = w.HAL0_DATA || {}
      w.HAL0_DATA.slots = injected
    }
    apply()
    document.addEventListener('DOMContentLoaded', apply)
  }, LIVE_SLOTS)
  await page.goto(hash)
}

test.describe('NAMES-stale: live-shaped slot names never fall back to fixture names', () => {
  test('dashboard renders the real slot names, never fixture "primary"/"legacy"', async ({ page }) => {
    await gotoWithLiveSlots(page, '/#dashboard')
    await expect(page.locator('.rd-slot-row').first()).toBeVisible()

    const bodyText = await page.locator('body').innerText()
    // Whole-word match — a substring match would also flag unrelated copy;
    // the fixture convention names are the only "primary"/"legacy" strings
    // in the app's own vocabulary.
    expect(bodyText).not.toMatch(/\bprimary\b/i)
    expect(bodyText).not.toMatch(/\blegacy\b/i)

    // Positive control: the real live names DO render — proves the
    // assertion above isn't vacuously true because nothing painted.
    expect(bodyText).toContain('ops-chat-01')
    expect(bodyText).toContain('ops-embed-01')
  })

  test('agents overview resolves the real chat slot by role, not name, even when an isDefault non-chat slot sorts first', async ({ page }) => {
    await page.route('**/api/agents', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          agents: [{ id: 'hermes', name: 'hermes', status: 'installed' }],
        }),
      }),
    )

    await gotoWithLiveSlots(page, '/#agent')
    const overview = page.locator('.agents-overview')
    await expect(overview).toBeVisible()
    // Wait for the live Hermes model readout so the assertions below aren't
    // racing the first /api/agents + /api/slots resolution.
    await expect(overview.locator('[data-testid="agent-card-hermes"]')).toContainText(
      'ops-llm-9000',
      { timeout: 5_500 },
    )

    const bodyText = await overview.innerText()
    expect(bodyText).not.toMatch(/\bprimary\b/i)
    expect(bodyText).not.toMatch(/\blegacy\b/i)

    // The Hermes card must show the real CHAT slot's model — not the embed
    // slot's (which would mean the bare isDefault heuristic won instead of
    // the group/type-filtered one).
    expect(bodyText).toContain('ops-llm-9000')
    expect(bodyText).not.toContain('ops-embed-model')
  })
})
