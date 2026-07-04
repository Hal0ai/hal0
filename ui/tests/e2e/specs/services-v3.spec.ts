/**
 * services-v3 — `#services` route renders the companion-service management
 * page: discovery (mDNS) card + one card per registered service with
 * status pill, unit metadata, lifecycle buttons (only the verbs the
 * backend registry advertises), a journald logs drawer, and an mDNS
 * announce/withdraw toggle.
 *
 * The page drives off `useServices()` → GET /api/services (fail-soft:
 * the apiMock catch-all's `{}` yields "source pending"); specs that
 * assert real content override the route with SERVICES_PAYLOAD.
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
      actions: ['start', 'stop', 'restart', 'enable', 'disable'],
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
        unit_file_state: 'disabled',
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
      hints: ['start/stop is GPU-arbiter managed — use the Image-Gen pane switchover'],
    },
    {
      id: 'hermes',
      name: 'Hermes',
      description: 'Bundled agent + dashboard (loopback :9119).',
      managed: true,
      unit: 'hal0-agent@hermes.service',
      unit_state: {
        active_state: 'active',
        sub_state: 'running',
        unit_file_state: 'enabled',
        since: 'Fri 2026-07-03 09:12:01 UTC',
      },
      up: true,
      detail: 'systemd unit active',
      stat: null,
      url: null,
      mdns_url: null,
      loopback_port: 9119,
      actions: ['start', 'stop', 'restart', 'enable', 'disable'],
      mdns_capable: false,
      hints: [],
    },
    {
      id: 'n8n',
      name: 'n8n',
      description: 'Workflow automation (external — not deployed by hal0).',
      managed: false,
      unit: null,
      unit_state: null,
      up: false,
      detail: 'unmonitored',
      stat: null,
      url: null,
      mdns_url: null,
      loopback_port: null,
      actions: [],
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

test.describe('Services v3 (/services)', () => {
  test('renders "source pending" when the endpoint is not available', async ({ page }) => {
    // apiMock catch-all serves {} for GET /api/services → fail-soft gate.
    await page.goto('/#services')
    await expect(page.locator('.view .vh h1')).toHaveText('Services')
    await expect(page.getByTestId('svcp-pending')).toBeVisible()
  })

  test('renders discovery card + one card per service', async ({ page }) => {
    await page.route('**/api/services', (route) => json(route, SERVICES_PAYLOAD))
    await page.goto('/#services')

    await expect(page.locator('.view .vh h1')).toHaveText('Services')
    await expect(page.getByTestId('svcp-mdns-toggle')).toBeVisible()
    for (const id of ['openwebui', 'comfyui', 'hermes', 'n8n']) {
      await expect(page.getByTestId(`svcp-card-${id}`)).toBeVisible()
    }
    // Up service shows an "up" pill; unmanaged shows the external note.
    await expect(page.getByTestId('svcp-card-openwebui')).toContainText('up')
    await expect(page.getByTestId('svcp-card-n8n')).toContainText('external — not managed by hal0')
  })

  test('action buttons follow the backend allow-list', async ({ page }) => {
    await page.route('**/api/services', (route) => json(route, SERVICES_PAYLOAD))
    await page.goto('/#services')

    // openwebui is active → Stop + Restart visible, Start hidden.
    await expect(page.getByTestId('svcp-restart-openwebui')).toBeVisible()
    await expect(page.getByTestId('svcp-stop-openwebui')).toBeVisible()
    await expect(page.getByTestId('svcp-start-openwebui')).toHaveCount(0)
    // comfyui: restart only (GPU-arbiter owns start/stop).
    await expect(page.getByTestId('svcp-restart-comfyui')).toBeVisible()
    await expect(page.getByTestId('svcp-start-comfyui')).toHaveCount(0)
    await expect(page.getByTestId('svcp-stop-comfyui')).toHaveCount(0)
    // n8n: no lifecycle buttons at all.
    await expect(page.getByTestId('svcp-restart-n8n')).toHaveCount(0)
  })

  test('restart posts to /api/services/{id}/action and surfaces the result', async ({ page }) => {
    await page.route('**/api/services', (route) => json(route, SERVICES_PAYLOAD))
    let posted: any = null
    await page.route('**/api/services/openwebui/action', (route) => {
      posted = route.request().postDataJSON()
      return json(route, {
        id: 'openwebui',
        unit: 'hal0-openwebui.service',
        action: 'restart',
        ok: true,
        active: true,
        message: 'restart hal0-openwebui.service: ok',
      })
    })
    await page.goto('/#services')

    await page.getByTestId('svcp-restart-openwebui').click()
    await expect(page.getByTestId('svcp-card-openwebui')).toContainText(
      'restart hal0-openwebui.service: ok',
    )
    expect(posted).toEqual({ action: 'restart' })
  })

  test('logs drawer fetches the unit journal tail', async ({ page }) => {
    await page.route('**/api/services', (route) => json(route, SERVICES_PAYLOAD))
    await page.route('**/api/logs?unit=hal0-openwebui.service*', (route) =>
      json(route, {
        unit: 'hal0-openwebui.service',
        lines: ['2026-07-04T10:00:00 hal0 openwebui[1]: listening on :3001'],
        count: 1,
      }),
    )
    await page.goto('/#services')

    await page.getByTestId('svcp-logbtn-openwebui').click()
    await expect(page.getByTestId('svcp-logs-hal0-openwebui.service')).toContainText(
      'listening on :3001',
    )
  })

  test('mDNS toggle posts advertise=true', async ({ page }) => {
    await page.route('**/api/services', (route) => json(route, SERVICES_PAYLOAD))
    let posted: any = null
    await page.route('**/api/services/mdns', (route) => {
      posted = route.request().postDataJSON()
      return json(route, {
        ...SERVICES_PAYLOAD.mdns,
        advertised: ['openwebui', 'comfyui'],
        ok: true,
        message: null,
      })
    })
    await page.goto('/#services')

    await page.getByTestId('svcp-mdns-toggle').click()
    await expect
      .poll(() => posted)
      .toEqual({ advertise: true })
  })

  test('sidebar nav exposes the Services item and routes to the page', async ({ page }) => {
    await page.route('**/api/services', (route) => json(route, SERVICES_PAYLOAD))
    await page.goto('/#dashboard')
    await page.getByTestId('nav-services').click()
    await expect(page).toHaveURL(/#services$/)
    await expect(page.locator('.view .vh h1')).toHaveText('Services')
  })
})
