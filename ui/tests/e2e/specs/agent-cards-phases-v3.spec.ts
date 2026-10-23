/**
 * agent-cards-phases-v3 — the state-aware agent cards, one test per phase.
 *
 * `GET /api/agents/{name}/overview` carries the server's word on a card's face
 * (`phase`, `degraded[]`, `units`, `version`); the dashboard folds it with the
 * `/api/agents` record and the backing slot (agents/agent-phase.ts) and paints
 * a different front per phase. Each test mocks `/api/agents` plus the overview,
 * then pins what the operator sees:
 *
 *   - `[data-testid="agent-phase-<id>"]` carries `data-phase`
 *   - the status word beside the dot (`.fc-st`)
 *   - the phase's primary control is visible and wired
 *
 * Hermes: not_installed · broken · down · degraded · ready · serving · unknown.
 * Pi (a CLI tool, no unit): installed behind its pin · not_installed.
 *
 * The overview's phase wins over the record; `serving` is the one phase the
 * browser derives (a `ready` overview plus a hot slot), so that test seeds the
 * slot through `window.HAL0_DATA` — `/api/slots` is answered by the in-bundle
 * mock layer and is not reachable through `page.route`.
 */
import { test, expect, json, seedSlots, type Page } from '../fixtures/apiMock'
import type { AgentOverview } from '../../../src/api/hooks/useAgents'

const FIVE_S = 5_500

// ── fixtures ────────────────────────────────────────────────────────────

const PIN_REF = '7e4863ff3c1a9d2b5e6f8a0b1c2d3e4f5a6b7c8d'

function hermesRecord(unitActive: boolean | null = true) {
  return {
    name: 'hermes',
    installed_at: '2026-07-30T00:00:00Z',
    status: 'installed',
    data_dir: '/var/lib/hal0/agents/hermes',
    config_path: '/etc/hal0/agents/hermes.toml',
    unit_active: unitActive,
  }
}

/** An installed, healthy Hermes overview; each test overrides what it pins. */
function hermesOverview(over: Partial<AgentOverview> = {}): AgentOverview {
  return {
    name: 'hermes',
    kind: 'daemon',
    installed: true,
    status: 'installed',
    unit_active: true,
    generated_at: new Date().toISOString(),
    job: null,
    phase: 'ready',
    degraded: [],
    doctor: [],
    version: {
      live: {
        describe: 'v0.22.4',
        ref: PIN_REF,
        python_minor: '3.14',
        built_at: '2026-10-22T09:00:00Z',
      },
      previous: null,
      pin: { describe: 'v0.22.4', ref: PIN_REF },
      update_available: false,
    },
    units: { agent: 'active', gateway: 'active', gateway_enabled: true },
    api_server: { endpoint: 'http://127.0.0.1:8642', healthy: true, profiles: ['default'] },
    platforms: ['api_server', 'telegram'],
    provision: {
      present: true,
      hal0_version: '1.6.0',
      completed_at: '2026-10-22T09:05:00Z',
      reported_at: '2026-10-22T09:05:00Z',
      stale: false,
      live_built_at: '2026-10-22T09:00:00Z',
      ok: 9,
      warn: 0,
      fail: 0,
      primary_model: 'qwen3-30b',
      persona: 'default',
      terminal_tool: 'on',
      approvals_mode: 'smart',
      memory_provider: 'hindsight',
    },
    approvals: { mode: 'smart', effective: 'smart', in_sync: true, pending: 0 },
    memory: { available: true, writes: 12 },
    links: { desktop: null },
    ...over,
  }
}

function piOverview(over: Partial<AgentOverview> = {}): AgentOverview {
  return {
    name: 'pi',
    kind: 'cli',
    installed: true,
    status: 'installed',
    generated_at: new Date().toISOString(),
    job: null,
    phase: 'installed',
    degraded: [],
    version: {
      installed: { pi: '0.9.2', adapter: '1.2.0' },
      pin: { pi: '0.10.0', adapter: '1.3.0' },
      update_available: false,
    },
    profile: {
      home: '~/.pi',
      profiled_at: '2026-10-22T09:00:00Z',
      home_readable: true,
      checks: [
        { id: 'binary', ok: true },
        { id: 'provider_ext', ok: true },
      ],
    },
    memory: { mcp_entry: true, hindsight_config: true, hindsight_api_reachable: true },
    commands: {
      install: 'sudo hal0 agent install pi',
      upgrade: 'sudo hal0 agent upgrade pi',
      uninstall: 'sudo hal0 agent uninstall pi',
    },
    ...over,
  }
}

/** A resident chat slot. `/api/slots` is served from `window.HAL0_DATA`, so the
 *  default fixture's hot default slot would upgrade every `ready` card to
 *  `serving`; each test seeds the slot it means. */
function chatSlot(state: 'ready' | 'serving') {
  return {
    name: 'primary',
    type: 'llm',
    group: 'chat',
    isDefault: true,
    state,
    model: 'qwen3-30b',
    model_id: 'qwen3-30b',
    container_status: 'running',
    ctx_max: 32_000,
    ctx_effective: 32_000,
    metrics: { toks: state === 'serving' ? 41 : 0, ctx: state === 'serving' ? 4_000 : 0 },
  }
}

/** Mock the agents list, the two overviews and the slot, hermetically. */
async function mockCards(
  page: Page,
  opts: {
    agents?: unknown[]
    hermes?: AgentOverview
    pi?: AgentOverview
    slot?: ReturnType<typeof chatSlot>
  },
) {
  const agents = opts.agents ?? []
  await page.route('**/api/agents', (route) => json(route, { agents, count: agents.length }))
  if (opts.hermes) {
    await page.route('**/api/agents/hermes/overview*', (route) => json(route, opts.hermes))
  }
  if (opts.pi) {
    await page.route('**/api/agents/pi/overview*', (route) => json(route, opts.pi))
  }
  await seedSlots(page, [opts.slot ?? chatSlot('ready')])
  // The Memory tab's live endpoints — keep the shell hermetic.
  await page.route('**/api/memory/graph/status', (route) =>
    json(route, { enabled: false, route: 'upstream' }),
  )
  await page.route('**/api/memory/search', (route) => json(route, { items: [] }))
}

const card = (page: Page, id: string) => page.getByTestId(`agent-card-${id}`)
const face = (page: Page, id: string) => page.getByTestId(`agent-phase-${id}`)

/** Flip a card by clicking its header strip — empty on every phase's front and
 *  on the back, so the click reaches the card and never a control. */
async function flip(page: Page, id: string) {
  await card(page, id).click({ position: { x: 170, y: 24 } })
}

async function openAgents(page: Page) {
  await page.goto('/#agent')
  await expect(page.getByTestId('agents-overview')).toBeVisible({ timeout: FIVE_S })
}

// ── Hermes ──────────────────────────────────────────────────────────────

test.describe('Hermes card phases', () => {
  test('not_installed: install command to copy, pin shown instead of a version', async ({
    page,
  }) => {
    await mockCards(page, {
      agents: [],
      hermes: {
        name: 'hermes',
        kind: 'daemon',
        installed: false,
        phase: 'not_installed',
        pin: { describe: 'v0.22.4', ref: PIN_REF },
      },
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'not_installed', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^not installed$/)
    await expect(page.getByTestId('agent-version-hermes')).toHaveText('pin v0.22.4')

    const copy = card(page, 'hermes').getByTestId('agent-action-copy-install')
    await expect(copy).toBeVisible()
    await expect(copy).toHaveAttribute('data-command', 'sudo hal0 agent install hermes')
  })

  test('broken: Repair leads, with the journal tail of the unit', async ({ page }) => {
    await page.route('**/api/logs*', (route) =>
      json(route, {
        unit: 'hal0-agent@hermes.service',
        lines: ['hermes: venv missing at the live release', 'hermes: exited with status 1'],
        count: 2,
      }),
    )
    await mockCards(page, {
      agents: [{ ...hermesRecord(false), status: 'broken' }],
      hermes: hermesOverview({
        status: 'broken',
        unit_active: false,
        phase: 'broken',
        units: { agent: 'failed', gateway: 'inactive', gateway_enabled: true },
      }),
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'broken', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^broken$/)
    await expect(front.locator('.fc-st .sdot.error')).toBeVisible()
    await expect(front.locator('.fc-journal-ln').last()).toContainText('exited with status 1')

    const repair = card(page, 'hermes').getByTestId('agent-action-repair')
    await expect(repair).toBeVisible()
    await expect(repair).toBeEnabled()
    // Repair is the primary action on a broken install; Start is still offered.
    await expect(repair).toHaveClass(/primary/)
    await expect(card(page, 'hermes').getByTestId('agent-action-restart')).toBeVisible()
  })

  test('down: Start is the way out and posts to the agent restart route', async ({ page }) => {
    await page.route('**/api/logs*', (route) =>
      json(route, {
        unit: 'hal0-agent@hermes.service',
        lines: ['hermes: stopped by systemctl'],
        count: 1,
      }),
    )
    await page.route('**/api/agents/hermes/restart', (route) =>
      json(route, { status: 'restarted' }),
    )
    await mockCards(page, {
      agents: [hermesRecord(false)],
      hermes: hermesOverview({
        unit_active: false,
        phase: 'down',
        units: { agent: 'inactive', gateway: 'inactive', gateway_enabled: true },
      }),
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'down', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^down$/)
    await expect(front.locator('.fc-st')).not.toContainText('ready')
    await expect(front.locator('.fc-st .sdot.error')).toBeVisible()
    await expect(front.locator('.fc-journal-ln').last()).toContainText('stopped by systemctl')

    const start = card(page, 'hermes').getByTestId('agent-action-restart')
    await expect(start).toBeVisible()
    await expect(start).toContainText('Start')
    const [req] = await Promise.all([
      page.waitForRequest(
        (r) => r.method() === 'POST' && r.url().endsWith('/api/agents/hermes/restart'),
      ),
      start.click(),
    ])
    expect(req.method()).toBe('POST')
  })

  test('degraded: reasons listed in order, the gateway fix posts to the gateway route', async ({
    page,
  }) => {
    let gatewayPosts = 0
    await page.route('**/api/agents/hermes/gateway/restart', (route) => {
      gatewayPosts += 1
      return json(route, { status: 'restarted' })
    })
    await mockCards(page, {
      agents: [hermesRecord(true)],
      hermes: hermesOverview({
        phase: 'degraded',
        units: { agent: 'active', gateway: 'failed', gateway_enabled: true },
        degraded: [
          { reason: 'gateway_down', detail: 'the gateway unit is failed', fix: 'restart_gateway' },
          {
            reason: 'approvals_drift',
            detail: 'config says smart, the gateway runs manual',
            fix: 'resync_approvals',
          },
          { reason: 'provision_warn', detail: '2 phases warned', fix: 'none' },
        ],
      }),
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'degraded', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^degraded$/)
    // The block beside the version counts the reasons.
    await expect(front.locator('.fc-pblock.warn')).toContainText('3')

    const rows = page.getByTestId('agent-reasons-hermes').locator('.fc-reason')
    await expect(rows).toHaveCount(3)
    await expect(rows.nth(0)).toHaveAttribute('data-reason', 'gateway_down')
    await expect(rows.nth(1)).toHaveAttribute('data-reason', 'approvals_drift')
    await expect(rows.nth(2)).toHaveAttribute('data-reason', 'provision_warn')

    const fix = card(page, 'hermes').getByTestId('agent-action-restart-gateway')
    await expect(fix).toBeVisible()
    // The fix sits on the gateway row, not on the others.
    await expect(rows.nth(0).getByTestId('agent-action-restart-gateway')).toHaveCount(1)
    await expect(rows.nth(2).getByRole('button')).toHaveCount(0)

    const [req] = await Promise.all([
      page.waitForRequest(
        (r) => r.method() === 'POST' && r.url().endsWith('/api/agents/hermes/gateway/restart'),
      ),
      fix.click(),
    ])
    expect(req.method()).toBe('POST')
    await expect.poll(() => gatewayPosts).toBe(1)
  })

  test('after a fix the card reads the box past the overview cache (?refresh=1)', async ({
    page,
  }) => {
    const degraded = hermesOverview({
      phase: 'degraded',
      units: { agent: 'active', gateway: 'failed', gateway_enabled: true },
      degraded: [
        { reason: 'gateway_down', detail: 'the gateway unit is failed', fix: 'restart_gateway' },
      ],
    })
    // The route keeps its slow probes for 30 s: a plain read answers from that
    // cache, and only ?refresh=1 rebuilds it, here with the gateway back up.
    let restarted = false
    let cached = degraded
    const refreshes: string[] = []
    await page.route('**/api/agents/hermes/gateway/restart', (route) => {
      restarted = true
      return json(route, { status: 'restarted' })
    })
    await mockCards(page, { agents: [hermesRecord(true)], hermes: degraded })
    await page.route('**/api/agents/hermes/overview*', (route) => {
      if (new URL(route.request().url()).searchParams.get('refresh') === '1') {
        refreshes.push(route.request().url())
        cached = restarted ? hermesOverview() : degraded
      }
      return json(route, cached)
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'degraded', { timeout: FIVE_S })
    await card(page, 'hermes').getByTestId('agent-action-restart-gateway').click()

    // Well inside one 10 s poll: the action itself asked for the fresh read.
    await expect(front).toHaveAttribute('data-phase', 'ready', { timeout: 3_000 })
    expect(refreshes.length).toBe(1)
    await expect(page.locator('.hal0-toast', { hasText: 'Hermes gateway restarted' })).toBeVisible()
  })

  test('ready: chips on the front, Restart on the console back', async ({ page }) => {
    await page.route('**/api/agents/hermes/personas', (route) =>
      json(route, {
        agent_id: 'hermes',
        active: 'default',
        personas: [{ id: 'default', display_name: 'Default' }],
      }),
    )
    await page.route('**/api/agent/approvals', (route) =>
      json(route, { approvals: [{ id: 'appr-1', tool: 'terminal', args: {} }] }),
    )
    await mockCards(page, {
      agents: [hermesRecord(true)],
      hermes: hermesOverview(),
      slot: chatSlot('ready'),
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^ready$/)
    await expect(front.locator('.fc-st')).not.toContainText('serving')
    await expect(front.locator('.fc-chips')).toContainText('approvals')
    await expect(front.locator('.fc-chips')).toContainText('smart')

    // A healthy card has no front-face action; its controls are on the back,
    // where Controls is the default tab.
    await flip(page, 'hermes')
    await expect(card(page, 'hermes').getByTestId('agent-tab-controls')).toHaveAttribute(
      'aria-selected',
      'true',
    )
    await expect(card(page, 'hermes').getByTestId('agent-action-restart')).toBeVisible()
    await expect(card(page, 'hermes').getByTestId('agent-action-restart-gateway')).toBeVisible()
    // The same tab carries the persona and the approvals inbox, read live.
    await expect(card(page, 'hermes').getByTestId('agent-action-persona')).toHaveValue('default')
    await expect(card(page, 'hermes').getByTestId('agent-action-approvals-inbox')).toContainText(
      'inbox · 1',
    )
  })

  test('persona: the select only picks, Activate applies it, the chip follows', async ({ page }) => {
    let active = 'default'
    const activations: string[] = []
    await page.route('**/api/agents/hermes/personas', (route) =>
      json(route, {
        agent_id: 'hermes',
        active,
        personas: [
          { id: 'default', display_name: 'Default', active: active === 'default' },
          { id: 'coder', display_name: 'Coder', active: active === 'coder' },
        ],
      }),
    )
    await page.route('**/api/agents/hermes/personas/*/activate', (route) => {
      activations.push(new URL(route.request().url()).pathname)
      active = 'coder'
      return json(route, { ok: true })
    })
    await mockCards(page, {
      agents: [hermesRecord(true)],
      hermes: hermesOverview(),
      slot: chatSlot('ready'),
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })
    // The chip names the live active persona, not provision.json's.
    await expect(front.locator('.fc-chips')).toContainText('persona · Default')

    await flip(page, 'hermes')
    const select = card(page, 'hermes').getByTestId('agent-action-persona')
    const activate = card(page, 'hermes').getByTestId('agent-action-persona-activate')
    await expect(select).toHaveValue('default')
    await expect(activate).toBeDisabled()

    // Picking a persona changes nothing on the box.
    await select.selectOption('coder')
    await expect(activate).toBeEnabled()
    await page.waitForTimeout(500)
    expect(activations).toEqual([])

    await activate.click()
    await expect.poll(() => activations).toEqual(['/api/agents/hermes/personas/coder/activate'])
    await expect(page.locator('.hal0-toast.ok', { hasText: 'Persona activated' })).toBeVisible()
    await expect(select).toHaveValue('coder')
    await expect(activate).toBeDisabled()

    await flip(page, 'hermes')
    await expect(front.locator('.fc-chips')).toContainText('persona · Coder')
  })

  test('keyboard: the flip button opens the console, Tab reaches an action, Escape flips back', async ({
    page,
  }) => {
    await mockCards(page, {
      agents: [hermesRecord(true)],
      hermes: hermesOverview(),
      slot: chatSlot('ready'),
    })
    await openAgents(page)

    const c = card(page, 'hermes')
    const flipBtn = c.getByTestId('agent-flip-hermes')
    await expect(face(page, 'hermes')).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })
    // The shell is not a button (its button role would flatten the console's
    // controls); the flip is a real button on each face.
    await expect(c).not.toHaveAttribute('role', /.*/)
    await expect(c).not.toHaveAttribute('tabindex', /.*/)
    await expect(flipBtn).toHaveAccessibleName('Open Hermes console')
    await expect(flipBtn).toHaveAttribute('aria-expanded', 'false')

    // Tab from the Agents view's tab strip to the flip button.
    await page.getByTestId('agent-tab-overview').focus()
    let at = ''
    for (let i = 0; i < 12 && at !== 'agent-flip-hermes'; i++) {
      await page.keyboard.press('Tab')
      at = await page.evaluate(() => document.activeElement?.getAttribute('data-testid') ?? '')
    }
    expect(at).toBe('agent-flip-hermes')

    // Enter opens the console with the selected tab focused.
    await page.keyboard.press('Enter')
    await expect(c.getByTestId('agent-tab-controls')).toBeFocused()
    await expect(flipBtn).toHaveAttribute('aria-expanded', 'true')

    // Tab from the tab strip into the panel reaches an action control.
    let reached = ''
    for (let i = 0; i < 6 && !reached.startsWith('agent-action-'); i++) {
      await page.keyboard.press('Tab')
      reached = await page.evaluate(
        () => document.activeElement?.getAttribute('data-testid') ?? '',
      )
    }
    expect(reached).toMatch(/^agent-action-/)

    // Escape flips back and returns focus to the flip button.
    await page.keyboard.press('Escape')
    await expect(flipBtn).toBeFocused()
    await expect(flipBtn).toHaveAttribute('aria-expanded', 'false')

    // Space opens it again; the console's own "front" button closes it.
    await page.keyboard.press(' ')
    await expect(c.getByTestId('agent-tab-controls')).toBeFocused()
    await c.getByTestId('agent-unflip-hermes').focus()
    await expect(c.getByTestId('agent-unflip-hermes')).toHaveAccessibleName('Back to front')
    await page.keyboard.press('Enter')
    await expect(flipBtn).toBeFocused()
  })

  test('a restart that answers status "error" toasts the failure, not success', async ({
    page,
  }) => {
    await page.route('**/api/agents/hermes/restart', (route) =>
      json(route, { status: 'error', detail: 'unit refused to start' }),
    )
    await page.route('**/api/agents/hermes/gateway/restart', (route) =>
      json(route, { status: 'error', detail: 'gateway unit not found' }),
    )
    await mockCards(page, {
      agents: [hermesRecord(true)],
      hermes: hermesOverview(),
      slot: chatSlot('ready'),
    })
    await openAgents(page)
    await expect(face(page, 'hermes')).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })

    await flip(page, 'hermes')
    await card(page, 'hermes').getByTestId('agent-action-restart').click()
    await expect(
      page.locator('.hal0-toast.err', { hasText: 'Hermes restart failed: unit refused to start' }),
    ).toBeVisible()
    await card(page, 'hermes').getByTestId('agent-action-restart-gateway').click()
    await expect(
      page.locator('.hal0-toast.err', { hasText: 'Gateway restart failed: gateway unit not found' }),
    ).toBeVisible()
    await expect(page.locator('.hal0-toast', { hasText: 'Hermes restarted' })).toHaveCount(0)
  })

  test('serving: a ready overview with a hot slot reads serving, with throughput', async ({
    page,
  }) => {
    await mockCards(page, {
      agents: [hermesRecord(true)],
      // The server says `ready`; the browser upgrades it when the slot is hot.
      hermes: hermesOverview({ phase: 'ready' }),
      slot: chatSlot('serving'),
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'serving', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^serving$/)
    await expect(front.locator('.hv-tput')).toContainText('41 tok/s')

    await flip(page, 'hermes')
    await expect(card(page, 'hermes').getByTestId('agent-action-restart')).toBeVisible()
  })

  test('unknown: an unreadable unit is never healthy, Restart is offered', async ({ page }) => {
    await mockCards(page, {
      agents: [hermesRecord(null)],
      hermes: hermesOverview({
        unit_active: null,
        phase: 'unknown',
        units: { agent: 'unknown', gateway: 'unknown', gateway_enabled: null },
      }),
    })
    await openAgents(page)

    const front = face(page, 'hermes')
    await expect(front).toHaveAttribute('data-phase', 'unknown', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^unknown$/)
    await expect(front.locator('.fc-st')).not.toContainText('ready')
    await expect(front.locator('.fc-st .sdot.serving')).toHaveCount(0)

    const restart = card(page, 'hermes').getByTestId('agent-action-restart')
    await expect(restart).toBeVisible()
    await expect(restart).toContainText('Restart')
  })
})

// ── Pi (a CLI tool: no unit, no slot, commands to copy) ─────────────────

test.describe('Pi card phases', () => {
  test('installed behind its pin: update badge and the upgrade command to copy', async ({
    page,
  }) => {
    await mockCards(page, {
      agents: [{ name: 'pi', status: 'installed', unit_active: null }],
      pi: piOverview({
        version: {
          installed: { pi: '0.9.2', adapter: '1.2.0' },
          pin: { pi: '0.10.0', adapter: '1.3.0' },
          update_available: true,
        },
      }),
    })
    await openAgents(page)

    const front = face(page, 'pi')
    await expect(front).toHaveAttribute('data-phase', 'installed', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^installed$/)
    await expect(page.getByTestId('agent-version-pi')).toHaveText('v0.9.2')
    await expect(page.getByTestId('agent-update-badge-pi')).toContainText('v0.10.0')

    const copy = card(page, 'pi').getByTestId('agent-action-copy-upgrade')
    await expect(copy).toBeVisible()
    await expect(copy).toHaveAttribute('data-command', 'sudo hal0 agent upgrade pi')
    // No unit, so no Restart anywhere on the card.
    await expect(card(page, 'pi').getByTestId('agent-action-restart')).toHaveCount(0)
  })

  test('not_installed: install command to copy', async ({ page }) => {
    await mockCards(page, {
      agents: [],
      pi: {
        name: 'pi',
        kind: 'cli',
        installed: false,
        phase: 'not_installed',
        pin: { pi: '0.10.0', adapter: '1.3.0' },
      },
    })
    await openAgents(page)

    const front = face(page, 'pi')
    await expect(front).toHaveAttribute('data-phase', 'not_installed', { timeout: FIVE_S })
    await expect(front.locator('.fc-st')).toHaveText(/^not installed$/)

    const copy = card(page, 'pi').getByTestId('agent-action-copy-install')
    await expect(copy).toBeVisible()
    await expect(copy).toHaveAttribute('data-command', 'sudo hal0 agent install pi')
  })
})
