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
 * The pips carry `data-service-id`, so the assertion diffs the chip id set
 * against the fixture's `.services[].id` set (the issue's repro verbatim) —
 * display-copy renames cannot break or mask the contract.
 *
 * The third case pins the same defect's other door: when
 * /api/services/health itself is unavailable (500/404/network — the hook
 * fails soft to `pending` + empty list), the group must NOT collapse to the
 * hal0 self-check's unqualified green "1 / 1 ready" — it renders a
 * warn-toned "services unknown" placeholder pip and trips the warn styling.
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

async function mockServicesHealth(page: Page, body: unknown, status = 200) {
  await page.route('**/api/services/health', (route) =>
    route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    }),
  )
}

const servicesChip = (page: Page) => page.locator('[data-testid="foot-health-services"]')

/** The rendered pip id set, via data-service-id (not display copy). */
async function chipIds(page: Page): Promise<string[]> {
  return servicesChip(page)
    .locator('.pip')
    .evaluateAll((els) => els.map((el) => el.getAttribute('data-service-id') ?? ''))
}

test.describe('Footer services group includes comfyui (#1899)', () => {
  test('all services up: 4 pips, 4/4 ready — chip id set matches the payload id set plus hal0', async ({
    page,
  }) => {
    const fixture = servicesHealth()
    await mockServicesHealth(page, fixture)
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()

    const chip = servicesChip(page)
    await expect(chip).toBeVisible()

    // hal0 (self) + comfyui + hermes + openwebui = 4 backing services.
    await expect(chip.locator('.pip')).toHaveCount(4, { timeout: 10_000 })
    await expect(chip.locator('.v b')).toHaveText('4 / 4')
    await expect(chip.locator('.v')).not.toHaveClass(/warn/)

    // Diff the chip id set against the fixture's .services[].id set — the
    // contract is the id set, not the backend's display names.
    expect((await chipIds(page)).sort()).toEqual(
      ['hal0', ...fixture.services.map((s) => s.id)].sort(),
    )
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

    const comfyPip = chip.locator('.pip[data-service-id="comfyui"]')
    await expect(comfyPip).toHaveCount(1)
    await expect(comfyPip).toHaveAttribute('aria-label', /err/)
  })

  test('services health endpoint 500s: group shows a warn "unknown" pip, not unqualified green', async ({
    page,
  }) => {
    await mockServicesHealth(page, { detail: 'boom' }, 500)
    await page.goto('/')
    await expect(page.locator('.footer')).toBeVisible()

    const chip = servicesChip(page)
    await expect(chip).toBeVisible()

    // hal0 self-check + the "services unknown" placeholder — never a bare
    // all-green "1 / 1 ready" while three real services are unaccounted for.
    await expect(chip.locator('.pip')).toHaveCount(2, { timeout: 10_000 })
    await expect(chip.locator('.v b')).toHaveText('1 / 2')
    await expect(chip.locator('.v')).toHaveClass(/warn/)

    const unknownPip = chip.locator('.pip[data-service-id="services-unknown"]')
    await expect(unknownPip).toHaveCount(1)
    await expect(unknownPip).toHaveAttribute('aria-label', /warn/)
  })
})
