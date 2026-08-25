/**
 * Overview "Services" widget (RDServicesCard) — regression cover for #1836.
 *
 * The widget read `svc.data?.services`, but `useServices()` returns
 * `{ services, mdns, pending }` with no `data` key. `svc.data` was therefore
 * always `undefined`, the derived list was always `[]`, and the widget
 * permanently rendered the "no companion services" empty state — for four
 * release candidates — while `/api/services` was answering with a full list.
 *
 * The only way to catch that class of defect is to render the widget against
 * a real `/api/services` response and assert on the DOM, which is what this
 * spec does: `services` is the default widget for dashboard cell c2
 * (useDashLayout.ts CELL_DEFS), so a plain `/#dashboard` visit mounts it.
 *
 * Reuses the SERVICES_PAYLOAD shape from services-v3.spec.ts (the dedicated
 * Services page), trimmed to the fields the Overview widget actually reads:
 * id, name, up, managed, detail, url, unit_state.unit_file_state.
 */
import { test, expect, json } from '../fixtures/apiMock'

const SERVICES_PAYLOAD = {
  services: [
    {
      id: 'openwebui',
      name: 'OpenWebUI',
      description: 'Chat UI companion (podman container, LAN :3001).',
      managed: true,
      unit: 'hal0-openwebui.service',
      unit_state: {
        active_state: 'active',
        sub_state: 'running',
        unit_file_state: 'enabled',
        since: 'Fri 2026-07-03 09:12:01 UTC',
      },
      up: true,
      detail: 'reachable — /health ok',
      stat: null,
      url: 'http://hal0.local:3001',
      mdns_url: null,
      loopback_port: null,
      actions: ['start', 'stop', 'restart'],
      mdns_capable: true,
      hints: [],
    },
    {
      id: 'comfyui',
      name: 'ComfyUI',
      description: 'Image generation engine (img slot container, LAN :8188).',
      managed: true,
      unit: 'hal0-slot@img.service',
      unit_state: {
        active_state: 'inactive',
        sub_state: 'dead',
        unit_file_state: 'enabled',
        since: null,
      },
      up: false,
      detail: 'unreachable',
      stat: null,
      url: 'http://hal0.local:8188',
      mdns_url: null,
      loopback_port: null,
      actions: ['restart'],
      mdns_capable: true,
      hints: [],
    },
    {
      id: 'hermes',
      name: 'Hermes',
      description: 'Agent runtime.',
      managed: true,
      unit: 'hal0-hermes.service',
      unit_state: {
        active_state: 'active',
        sub_state: 'running',
        unit_file_state: 'enabled',
        since: 'Fri 2026-07-03 09:12:01 UTC',
      },
      up: true,
      detail: 'reachable',
      stat: null,
      url: null,
      mdns_url: null,
      loopback_port: null,
      actions: ['restart'],
      mdns_capable: false,
      hints: [],
    },
  ],
  mdns: {
    available: true,
    hostname: 'hal0.local',
    base_advertised: true,
    advertised: [],
  },
}

test.describe('Overview Services widget (#1836)', () => {
  test('renders the services from /api/services, not the empty state', async ({ page }) => {
    await page.route('**/api/services', (route) => json(route, SERVICES_PAYLOAD))
    await page.goto('/#dashboard')

    const card = page
      .locator('.rd-card')
      .filter({ has: page.locator('.rd-card-title', { hasText: /^Services$/ }) })
    await expect(card).toBeVisible()

    // The defect: the widget always fell through to this empty state.
    await expect(card.locator('.rd-empty')).toHaveCount(0)

    const cells = card.locator('.rd-svc-cell')
    await expect(cells).toHaveCount(3)
    await expect(card.locator('.rd-svc-name')).toHaveText(['openwebui', 'comfyui', 'hermes'])
    // Real per-service detail text, not a fabricated placeholder.
    await expect(cells.first()).toContainText('reachable')
  })

  test('still shows "no companion services" when the backend reports none', async ({ page }) => {
    await page.route('**/api/services', (route) =>
      json(route, { services: [], mdns: SERVICES_PAYLOAD.mdns }),
    )
    await page.goto('/#dashboard')

    const card = page
      .locator('.rd-card')
      .filter({ has: page.locator('.rd-card-title', { hasText: /^Services$/ }) })
    await expect(card.locator('.rd-empty')).toHaveText('no companion services')
  })
})
