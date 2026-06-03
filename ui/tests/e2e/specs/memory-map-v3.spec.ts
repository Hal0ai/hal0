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

  test('headroom labelled "pool" on bare-metal', async ({ page }) => {
    await mockStatsHardware(page, { configured: false, detected: false })
    await page.goto('/#dashboard')
    // Scope to sidebar — expanded variant also renders .memmap-headroom
    await expect(page.locator('.memmap-sidebar .memmap-headroom')).toContainText('limited by pool')
  })

  test('headroom labelled "host" when host free is the binding constraint', async ({ page }) => {
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
    // Scope to sidebar — expanded variant also renders .memmap-headroom
    await expect(page.locator('.memmap-sidebar .memmap-headroom')).toContainText('limited by host')
  })
})

test.describe('Memory map — expanded', () => {
  test('expanded variant renders host pool + inside-LXC + legend', async ({ page }) => {
    await mockStatsHardware(page, {
      configured: true,
      ok: true,
      node: 'pve',
      host_mem_total_mb: 131072,
      host_mem_used_mb: 24576,
      host_mem_free_mb: 106496,
      tenants_running: 1,
      tenants_total: 1,
    })
    await mockProxmoxSettings(page, {
      configured: true,
      ok: true,
      node: 'pve',
      host_mem_total_mb: 131072,
      host_mem_used_mb: 24576,
      host_mem_free_mb: 106496,
      tenants_running: 1,
      tenants_total: 1,
      tenants: [
        { vmid: 159, name: 'halodev', type: 'lxc', status: 'running', mem_mb: 3072, maxmem_mb: 8192 },
      ],
    })
    await page.goto('/#dashboard')
    const card = page.locator('.memmap-expanded')
    await expect(card).toBeVisible()
    // wave-1: expanded variant has TWO sections — primary "model memory"
    // (vs the unified pool) and a separate ".memmap-host-section" for
    // host/Proxmox pressure ("host pressure" / "free on host").
    await expect(card).toContainText('model memory')
    const hostSection = card.locator('.memmap-host-section')
    await expect(hostSection).toBeVisible()
    await expect(hostSection).toContainText('host pressure')
    await expect(hostSection).toContainText('free on host')
    // Tenant legend (excluding self) lists halodev.
    await expect(hostSection).toContainText('halodev')
  })
})
