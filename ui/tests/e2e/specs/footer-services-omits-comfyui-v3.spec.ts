/**
 * footer-services-omits-comfyui-v3 — #1899.
 *
 * The footer's `services` LED group (`[data-testid="foot-health-services"]`)
 * used to be a hardcoded three-member array (hal0, hermes, openwebui) in
 * src/dash/chrome.jsx — `comfyui` was never a member. On a box where ComfyUI
 * is a real running service, ComfyUI going down left the always-visible
 * footer reporting an unqualified green "services 3 / 3 ready": the three
 * rendered pips were individually truthful, but the group as a whole omitted
 * a real backing service from both the pip count and the down-service warn
 * styling.
 *
 * This spec pins the honest contract: the services group enumerates (as a
 * pip + in the ready count) every id GET /api/services/health reports —
 * not a fixed set baked into the component. A down `comfyui` must both
 * appear as its own pip and move the ready count / trip warn styling.
 */
import { test, expect, type Page } from '../fixtures/apiMock'

/** The real /api/services/health payload shape (services_health.py). */
function servicesHealth(overrides: { comfyui?: boolean; hermes?: boolean; openwebui?: boolean } = {}) {
  const { comfyui = true, hermes = true, openwebui = true } = overrides
  return {
    services: [
      {
        id: 'comfyui',
        name: 'ComfyUI',
        up: comfyui,
        detail: comfyui ? 'running — 0 job(s) active' : 'unreachable',
        url: comfyui ? 'http://127.0.0.1:8188' : null,
        stat: null,
      },
      {
        id: 'hermes',
        name: 'Hermes',
        up: hermes,
        detail: hermes ? 'systemd unit active' : 'systemd unit inactive or absent',
        url: null,
        stat: null,
      },
      {
        id: 'openwebui',
        name: 'OpenWebUI',
        up: openwebui,
        detail: openwebui ? 'reachable — /health ok' : 'unreachable (ConnectError)',
        url: openwebui ? 'http://127.0.0.1:3001' : null,
        stat: null,
      },
    ],
  }
}

async function mockServicesHealth(page: Page, body: unknown) {
  await page.route('**/api/services/health', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(body),
    }),
  )
}

const servicesChip = (page: Page) => page.locator('[data-testid="foot-health-services"]')

test.describe('Footer services group includes comfyui (#1899)', () => {
  test('all services up: 4 pips, 4/4 ready — comfyui included, not just hal0/hermes/openwebui', async ({
    page,
  }) => {
    await mockServicesHealth(page, servicesHealth())
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()

    const chip = servicesChip(page)
    await expect(chip).toBeVisible()

    // hal0 (self) + comfyui + hermes + openwebui = 4 backing services.
    const pips = chip.locator('.pip')
    await expect(pips).toHaveCount(4, { timeout: 10_000 })
    await expect(chip.locator('.v b')).toHaveText('4 / 4')
    await expect(chip.locator('.v')).not.toHaveClass(/warn/)

    // A pip specifically labelled comfyui must exist.
    await expect(chip.locator('[aria-label^="ComfyUI:"]')).toHaveCount(1)
  })

  test('comfyui down: ready count drops and warn styling trips — not silently absorbed', async ({
    page,
  }) => {
    await mockServicesHealth(page, servicesHealth({ comfyui: false }))
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()

    const chip = servicesChip(page)
    await expect(chip).toBeVisible()

    // Still 4 pips (comfyui renders, just down) but only 3 ready.
    await expect(chip.locator('.pip')).toHaveCount(4, { timeout: 10_000 })
    await expect(chip.locator('.v b')).toHaveText('3 / 4')
    await expect(chip.locator('.v')).toHaveClass(/warn/)

    const comfyPip = chip.locator('[aria-label^="ComfyUI:"]')
    await expect(comfyPip).toHaveCount(1)
    await expect(comfyPip).toHaveAttribute('aria-label', /err/)
  })
})
