/**
 * memory-map-v3 — MemoryMap sidebar widget across the three host modes
 * (off / detected_unconfigured / configured) + the attribution edge case.
 *
 * The sidebar mounts on /dashboard and /slots. Specs target /dashboard.
 *
 * NOTE: This spec is .skip until Tasks 9-11 wire the MemoryMap
 * component into the dashboard routes. The mocks below are correct;
 * unskip the `describe` blocks when the consumer wire-ups land.
 */
import { test, expect, json } from '../fixtures/apiMock'
import type { Page } from '@playwright/test'

function mockStatsHardware(page: Page, host: object, overrides: object = {}) {
  return page.route('**/api/stats/hardware', (route) =>
    json(route, {
      ram_total_mb: 96000,
      ram_used_mb: 1863,
      ram_available_mb: 94577,
      gtt_used_mb: 6200,
      vram_used_mb: 0,
      npu_status: { ok: true, model_mb: 1100 },
      host,
      ...overrides,
    }),
  )
}

function mockProxmoxSettings(page: Page, status: object) {
  return page.route('**/api/settings/proxmox', (route) =>
    json(route, {
      configured: true,
      host: '10.0.1.110',
      port: 8006,
      user: 'hal0@pve',
      token_name: 'dashboard',
      verify_ssl: false,
      token_value_set: true,
      status,
    }),
  )
}

test.describe('Memory map — sidebar', () => {
  test('off — single-tier bar, no PVE band', async ({ page }) => {
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const card = page.locator('.memmap-sidebar')
    await expect(card).toBeVisible()
    await expect(card).not.toContainText('Hosted on Proxmox')
    await expect(card).not.toContainText('host free')
  })

  test('detected_unconfigured — amber band with Configure link', async ({ page }) => {
    await mockStatsHardware(page, {
      configured: false,
      detected: true,
      detection: 'detected',
      hint: 'Configure /etc/hal0/proxmox.json to see host pressure.',
    })
    await page.goto('/#dashboard')
    const card = page.locator('.memmap-sidebar')
    await expect(card).toContainText('Hosted on Proxmox')
    await expect(card.locator('a', { hasText: 'Configure' })).toBeVisible()
  })

  test('configured — sidebar shows MODEL memory only, no host section', async ({ page }) => {
    // wave-1: host/Proxmox pressure moved OUT of the sidebar variant and
    // into the EXPANDED variant's separate .memmap-host-section. The
    // sidebar now renders model memory vs the unified pool only — no host
    // teaser, no tenant bar, no "host free".
    await mockStatsHardware(page, {
      configured: true,
      ok: true,
      node: 'pve',
      host_mem_total_mb: 131072,
      host_mem_used_mb: 24576,
      host_mem_free_mb: 106496,
      tenants_running: 3,
      tenants_total: 5,
    })
    await mockProxmoxSettings(page, {
      configured: true,
      ok: true,
      node: 'pve',
      host_mem_total_mb: 131072,
      host_mem_used_mb: 24576,
      host_mem_free_mb: 106496,
      tenants_running: 3,
      tenants_total: 5,
      tenants: [
        { vmid: 105, name: 'hal0', type: 'lxc', status: 'running', mem_mb: 9216, maxmem_mb: 98304 },
        { vmid: 159, name: 'halodev', type: 'lxc', status: 'running', mem_mb: 3072, maxmem_mb: 8192 },
        { vmid: 200, name: 'backup', type: 'qemu', status: 'running', mem_mb: 2150, maxmem_mb: 4096 },
      ],
    })
    await page.goto('/#dashboard')
    const card = page.locator('.memmap-sidebar')
    await expect(card).toBeVisible()
    // Primary model-memory framing is present.
    await expect(card).toContainText('model memory')
    // Configured-but-not-detected: the amber nudge must NOT appear.
    await expect(card).not.toContainText('Hosted on Proxmox')
    // Host pressure surface is EXPANDED-only — absent from the sidebar.
    await expect(card.locator('.memmap-host-section')).toHaveCount(0)
    await expect(card.locator('.memmap-bar-host')).toHaveCount(0)
    await expect(card).not.toContainText('host pressure')
    await expect(card).not.toContainText('free on host')
  })

  test('UMA pool labelled "GPU pool (GTT)", not "unified"', async ({ page }) => {
    // The default mock host is a Strix Halo UMA box (memory_kind: 'unified').
    // On UMA the pool ceiling is the GTT cap, so the header must read as the
    // GPU/GTT pool — never the misleading raw "unified" kind. See issue #462.
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const card = page.locator('.memmap-sidebar')
    await expect(card.locator('.side-card-h .right')).toContainText('GPU pool (GTT)')
    await expect(card.locator('.side-card-h .right')).not.toContainText('unified')
  })

  test('sidebar no longer renders the headroom line (pool scenario)', async ({ page }) => {
    // The oversized "Headroom for new models … limited by pool/host" line was
    // dropped from the SIDEBAR variant — the "<free> free" value above the bar
    // is the kept signal. The headroom + limited-by string now lives ONLY in
    // the expanded variant (see EXPANDED-variant coverage below).
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const card = page.locator('.memmap-sidebar')
    await expect(card).toBeVisible()
    await expect(card.locator('.memmap-headroom')).toHaveCount(0)
    // The retained free signal is still present in the sidebar header row.
    await expect(card.locator('.memmap-h')).toContainText('free')
  })

  test('sidebar drops headroom even when host is the binding constraint', async ({ page }) => {
    // Host-limited pool: previously the sidebar showed "limited by host". The
    // limited-by distinction is now expanded-only; the sidebar must show no
    // headroom line regardless of which constraint binds.
    await mockStatsHardware(page, {
      configured: true,
      ok: true,
      host_mem_total_mb: 131072,
      host_mem_used_mb: 125000,
      host_mem_free_mb: 6072,
      tenants_running: 0,
      tenants_total: 0,
    })
    await mockProxmoxSettings(page, {
      configured: true,
      ok: true,
      host_mem_total_mb: 131072,
      host_mem_used_mb: 125000,
      host_mem_free_mb: 6072,
      tenants_running: 0,
      tenants_total: 0,
      tenants: [],
    })
    await page.goto('/#dashboard')
    const card = page.locator('.memmap-sidebar')
    await expect(card).toBeVisible()
    await expect(card.locator('.memmap-headroom')).toHaveCount(0)
  })

  test('co-resident slots render distinct legend swatch colours', async ({ page }) => {
    // Change 1: each loaded model slot gets its OWN stable colour so
    // co-resident models are visually distinguishable. The default mock has
    // four live slots with mem_mb > 0 (primary/agent/coder/embed) — three of
    // them share device=gpu-rocm, which used to collapse to one device hue.
    // Their legend swatches must now differ.
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    const swatches = page.locator('.memmap-sidebar .memmap-legend .ln .sw')
    // free row adds one swatch; expect ≥3 (>=2 live slots + free).
    await expect(swatches.first()).toBeVisible()
    const count = await swatches.count()
    expect(count).toBeGreaterThanOrEqual(3)
    // First two slot swatches (sorted by name) must be distinct colours.
    const c0 = await swatches.nth(0).evaluate((el) => getComputedStyle(el).backgroundColor)
    const c1 = await swatches.nth(1).evaluate((el) => getComputedStyle(el).backgroundColor)
    expect(c0).not.toBe(c1)
  })
})

// NOTE: the `Memory map — expanded` variant (with its Proxmox host-pressure
// + tenant-breakdown section) was removed from the /#dashboard layout — the
// dashboard now carries a single memory map (the sticky sidebar) plus the
// live Memory hardware card. The MemoryMap component still supports
// `variant="expanded"`, but nothing mounts it on the dashboard, so the
// former dashboard-scoped expanded suite was retired.
//
// HEADROOM coverage: the "Headroom for new models … limited by pool/host"
// line (and the pool-vs-host limited-by distinction) now renders ONLY in the
// expanded variant — it was dropped from the sidebar (the "<free> free" value
// above the bar is the kept signal). Because no live route mounts the
// expanded variant, the limited-by string is not e2e-reachable today; the
// `limitedBy` logic still lives in useMemoryMapModel() and the expanded
// <HeadroomLabel> render. The former sidebar "limited by pool"/"limited by
// host" assertions were converted above to assert the sidebar no longer shows
// the headroom line (under both pool- and host-constrained mocks). When a
// route mounts variant="expanded", re-add a `.memmap-expanded .memmap-headroom`
// limited-by assertion here.
