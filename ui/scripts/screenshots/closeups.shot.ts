/**
 * Close-ups: one element per image, cropped with a little margin, at desktop
 * width and device scale factor 2.
 */
import type { Page } from '@playwright/test'

import { DESKTOP, open, shoot, test } from './shot'

type Closeup = {
  name: string
  hash: string
  /** Returns the element to capture; may click first to open a drawer or dialog. */
  el: (page: Page) => Promise<import('@playwright/test').Locator>
  /** Viewport width for this shot (default 1440). */
  width?: number
  /** Viewport height for this shot (default 900), for panels that fill the window. */
  height?: number
  /** The shot is of the dock itself, so the dock stays visible. */
  keepDock?: boolean
}

const CLOSEUPS: Closeup[] = [
  // Overview
  { name: 'closeup-overview-now-band', hash: '#dashboard', el: async (p) => p.locator('.ov-now').first() },
  { name: 'closeup-overview-serving-now', hash: '#dashboard', el: async (p) => p.locator('.ov-card').filter({ hasText: 'Serving now' }).first() },
  { name: 'closeup-overview-agents', hash: '#dashboard', el: async (p) => p.locator('.ov-card').filter({ has: p.locator('.ov-agent') }).first() },
  { name: 'closeup-overview-usage', hash: '#dashboard', el: async (p) => p.locator('.ov-card').filter({ hasText: 'Usage' }).first() },
  { name: 'hardware-metrics', hash: '#dashboard', el: async (p) => p.locator('.ov-card').filter({ hasText: 'Hardware' }).first() },
  // Slots
  { name: 'closeup-telemetry-gauges', hash: '#slots', el: async (p) => p.getByTestId('telemetry-header') },
  { name: 'closeup-slot-cards', hash: '#slots', el: async (p) => p.getByTestId('infer-slots') },
  { name: 'closeup-slot-rail', hash: '#slots', el: async (p) => p.getByTestId('slot-rail') },
  { name: 'closeup-npu-array', hash: '#slots/npu', el: async (p) => p.getByTestId('npu-array-band') },
  { name: 'closeup-npu-lanes', hash: '#slots/npu', el: async (p) => p.getByTestId('npu-lanes') },
  {
    name: 'slot-detail',
    hash: '#slots/primary',
    el: async (p) => p.locator('.drawer, [role="dialog"]').first(),
  },
  // Models
  {
    name: 'closeup-model-catalog-rows',
    hash: '#models',
    height: 1300,
    el: async (p) => p.locator('.mdl-list, [data-testid="models-list"]').first(),
  },
  { name: 'closeup-providers-tab', hash: '#models/providers', el: async (p) => p.getByTestId('providers-tab') },
  {
    name: 'closeup-add-provider-dialog',
    hash: '#models/providers',
    el: async (p) => {
      await p.getByTestId('mdl-add-provider').first().click().catch(async () => {
        await p.getByRole('button', { name: /Add provider/ }).first().click()
      })
      const modal = p.getByTestId('add-provider-modal')
      await modal.waitFor()
      return p.locator('.modal-shell').filter({ has: modal }).first()
    },
  },
  // Image, Memory, Agents
  { name: 'closeup-image-gen-pane', hash: '#slots/image', el: async (p) => p.getByTestId('image-gen-pane') },
  { name: 'closeup-memory-health', hash: '#memory/health', el: async (p) => p.locator('.ma-page').first() },
  { name: 'closeup-agent-cards', hash: '#agent', el: async (p) => p.locator('.ao-grid').first() },
  // Chrome
  { name: 'closeup-status-dock', hash: '#slots', el: async (p) => p.getByTestId('dock'), width: 1100, keepDock: true },
  {
    name: 'closeup-dock-expanded',
    hash: '#slots',
    el: async (p) => {
      await p.getByTestId('dock-toggle').click()
      return p.getByTestId('dock')
    },
    keepDock: true,
  },
  {
    name: 'closeup-notifications',
    hash: '#dashboard',
    el: async (p) => {
      await p.getByTestId('tb-bell').click()
      return p.getByTestId('notif-pop')
    },
  },
  { name: 'closeup-benchmarks', hash: '#benchmarks', el: async (p) => p.getByTestId('benchmarks-view') },
  { name: 'closeup-settings-updates', hash: '#settings/updates', el: async (p) => p.locator('.settings-content').first() },
]

const only = process.env.SHOT_ONLY ? new RegExp(process.env.SHOT_ONLY) : null
for (const c of CLOSEUPS) {
  if (only && !only.test(c.name)) continue
  test(c.name, async ({ page }) => {
    await open(page, c.hash, { width: c.width ?? DESKTOP.width, height: c.height ?? DESKTOP.height })
    const el = await c.el(page)
    await el.waitFor({ state: 'visible', timeout: 8_000 })
    await shoot(page, c.name, { el, keepDock: c.keepDock })
  })
}
