/**
 * agent-jobs-v3 — the Hermes card's upgrade and repair jobs.
 *
 * Upgrade is `POST /api/updates/components/hermes/converge`, repair is
 * `POST /api/agents/hermes/repair`; both answer the queued job's id, and the
 * card then polls `GET /api/updates/status/{id}` (every 1.5 s) until the job is
 * `applied` or `failed`. While the job is queued or running the card reads
 * `working` and its front shows the job row (`agent-job-hermes`) with the
 * steps the status route reports; on the terminal state the phase falls back to
 * the overview's word and a toast says how it went.
 *
 * The status route is staged by the test (queued → running → terminal) rather
 * than counted by call, so no assertion races the 1.5 s poll.
 */
import { test, expect, json, type Page } from '../fixtures/apiMock'
import type { AgentOverview } from '../../../src/api/hooks/useAgents'

const FIVE_S = 5_500
const PIN_REF = '7e4863ff3c1a9d2b5e6f8a0b1c2d3e4f5a6b7c8d'
const OLD_REF = '3b1c0a9e8d7f6a5b4c3d2e1f0a9b8c7d6e5f4a3b'

// ── fixtures ────────────────────────────────────────────────────────────

const HERMES_RECORD = {
  name: 'hermes',
  installed_at: '2026-07-30T00:00:00Z',
  status: 'installed',
  data_dir: '/var/lib/hal0/agents/hermes',
  config_path: '/etc/hal0/agents/hermes.toml',
  unit_active: true,
}

/** An installed Hermes. `behind` puts the live release one pin back. */
function hermesOverview(opts: { behind?: boolean; phase?: AgentOverview['phase'] } = {}): AgentOverview {
  const behind = opts.behind === true
  return {
    name: 'hermes',
    kind: 'daemon',
    installed: true,
    status: opts.phase === 'broken' ? 'broken' : 'installed',
    unit_active: opts.phase !== 'broken',
    generated_at: new Date().toISOString(),
    job: null,
    phase: opts.phase ?? 'ready',
    degraded: [],
    doctor: [],
    version: {
      live: behind
        ? { describe: 'v0.22.2', ref: OLD_REF, python_minor: '3.14' }
        : { describe: 'v0.22.4', ref: PIN_REF, python_minor: '3.14' },
      previous: behind ? null : { describe: 'v0.22.2', ref: OLD_REF },
      pin: { describe: 'v0.22.4', ref: PIN_REF },
      update_available: behind,
    },
    units: { agent: 'active', gateway: 'active', gateway_enabled: true },
    api_server: { endpoint: 'http://127.0.0.1:8642', healthy: true, profiles: ['default'] },
    platforms: ['api_server'],
    provision: { present: true, ok: 9, warn: 0, fail: 0, terminal_tool: 'on', persona: 'default' },
    approvals: { mode: 'smart', effective: 'smart', in_sync: true, pending: 0 },
    memory: { available: true },
    links: { desktop: null },
  }
}

/** A resident-but-idle chat slot. `/api/slots` is served from `window.HAL0_DATA`
 *  by the in-bundle mock layer (not reachable through `page.route`), and the
 *  default fixture's hot slot would turn a `ready` card into `serving`. */
async function seedIdleSlot(page: Page) {
  await page.addInitScript((slots) => {
    let real: any
    Object.defineProperty(window, 'HAL0_DATA', {
      configurable: true,
      get() {
        return real
      },
      set(v) {
        real = v
        if (v && typeof v === 'object') v.slots = slots
      },
    })
  }, [
    {
      name: 'primary',
      type: 'llm',
      group: 'chat',
      isDefault: true,
      state: 'ready',
      model: 'qwen3-30b',
      model_id: 'qwen3-30b',
      container_status: 'running',
      ctx_max: 32_000,
      metrics: { toks: 0, ctx: 0 },
    },
  ])
}

type Stage = 'queued' | 'running' | 'terminal'

interface JobScript {
  id: string
  /** `converge:hermes` (upgrade) or `repair:hermes`. */
  phase: string
  steps: string[]
  terminal: 'applied' | 'failed'
  error?: string
}

/** `GET /api/updates/status/{id}`, advanced by the test: queued, then running
 *  with the steps, then the terminal state. */
async function stageJobStatus(page: Page, job: JobScript) {
  let stage: Stage = 'queued'
  let polls = 0
  const created = Math.floor(Date.now() / 1000)
  await page.route(`**/api/updates/status/${job.id}`, (route) => {
    polls += 1
    const base = {
      id: job.id,
      phase: job.phase,
      channel: 'stable',
      version: null,
      created_at: created,
      updated_at: created,
      error: null,
    }
    if (stage === 'queued') return json(route, { ...base, state: 'queued' })
    if (stage === 'running') return json(route, { ...base, state: 'running', steps: job.steps })
    return json(route, {
      ...base,
      state: job.terminal,
      steps: job.steps,
      error: job.terminal === 'failed' ? (job.error ?? 'failed') : null,
    })
  })
  return {
    polls: () => polls,
    run: () => {
      stage = 'running'
    },
    finish: () => {
      stage = 'terminal'
    },
  }
}

async function mockShell(page: Page) {
  await seedIdleSlot(page)
  // The Memory tab's live endpoints — keep the shell hermetic.
  await page.route('**/api/memory/graph/status', (route) =>
    json(route, { enabled: false, route: 'upstream' }),
  )
  await page.route('**/api/memory/search', (route) => json(route, { items: [] }))
}

/** Flip by clicking the header strip — empty on both faces, so the click
 *  reaches the card and never a control. */
async function flip(page: Page) {
  await page.getByTestId('agent-card-hermes').click({ position: { x: 170, y: 24 } })
}

async function openHermes(page: Page) {
  await page.goto('/#agent')
  await expect(page.getByTestId('agent-card-hermes')).toBeVisible({ timeout: FIVE_S })
}

/** Back face → Lifecycle tab. */
async function openLifecycle(page: Page) {
  await flip(page)
  await page.getByTestId('agent-card-hermes').getByTestId('agent-tab-lifecycle').click()
}

/** The confirm button holding the testid span — `disabled` lives on the button. */
const confirmButton = (page: Page, kind: 'upgrade' | 'repair') =>
  page.getByRole('button').filter({ has: page.getByTestId(`agent-confirm-${kind}`) })

const toast = (page: Page, kind: 'success' | 'error' | 'warning', text: string) =>
  page.locator(`.hal0-toast.${kind}`, { hasText: text })

// ── tests ───────────────────────────────────────────────────────────────

test.describe('Hermes upgrade job', () => {
  test('confirm posts converge, the card works through the job, then reads ready with a toast', async ({
    page,
  }) => {
    await mockShell(page)
    let upgraded = false
    await page.route('**/api/agents', (route) => json(route, { agents: [HERMES_RECORD], count: 1 }))
    await page.route('**/api/agents/hermes/overview*', (route) =>
      json(route, hermesOverview({ behind: !upgraded })),
    )
    await page.route('**/api/updates/components/hermes/converge', (route) =>
      json(route, { id: 'job-up-1', state: 'queued', phase: 'converge:hermes' }),
    )
    const job = await stageJobStatus(page, {
      id: 'job-up-1',
      phase: 'converge:hermes',
      steps: ['preflight'],
      terminal: 'applied',
    })

    await openHermes(page)
    const front = page.getByTestId('agent-phase-hermes')
    await expect(front).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })
    await expect(page.getByTestId('agent-version-hermes')).toHaveText('v0.22.2')
    await expect(page.getByTestId('agent-update-badge-hermes')).toContainText('v0.22.4')

    // Back → Lifecycle → Upgrade → confirm.
    await openLifecycle(page)
    const upgrade = page.getByTestId('agent-card-hermes').getByTestId('agent-action-upgrade')
    await expect(upgrade).toContainText('Upgrade to v0.22.4')
    await upgrade.click()

    await expect(page.getByRole('dialog')).toContainText('v0.22.2 → v0.22.4')
    await expect(confirmButton(page, 'upgrade')).toBeEnabled()
    const [post] = await Promise.all([
      page.waitForRequest(
        (r) =>
          r.method() === 'POST' &&
          r.url().endsWith('/api/updates/components/hermes/converge'),
      ),
      page.getByTestId('agent-confirm-upgrade').click(),
    ])
    expect(post.method()).toBe('POST')

    // The mutation's id is enough to turn the card to `working`.
    await expect(front).toHaveAttribute('data-phase', 'working', { timeout: FIVE_S })
    await flip(page) // back to the front for the job row
    const row = page.getByTestId('agent-job-hermes')
    await expect(row).toBeVisible()
    await expect(row).toHaveAttribute('data-state', 'queued')
    await expect(row.locator('.fc-job-h b')).toHaveText('upgrading')
    await expect(front.locator('.fc-st')).toContainText('working')

    // running, with the steps the status route reports.
    job.run()
    await expect(row).toHaveAttribute('data-state', 'running', { timeout: FIVE_S })
    await expect(row.locator('.fc-step.cur')).toHaveText('preflight')

    // applied: the overview now has the new release, the toast says so.
    upgraded = true
    job.finish()
    await expect(toast(page, 'success', 'Hermes upgraded')).toBeVisible({ timeout: FIVE_S })
    await expect(front).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })
    await expect(row).toHaveCount(0)
    await expect(page.getByTestId('agent-version-hermes')).toHaveText('v0.22.4')
    await expect(page.getByTestId('agent-update-badge-hermes')).toHaveCount(0)
  })
})

test.describe('Hermes repair job', () => {
  test('a failed repair ends in the error toast and the card falls back from working', async ({
    page,
  }) => {
    await mockShell(page)
    await page.route('**/api/agents', (route) => json(route, { agents: [HERMES_RECORD], count: 1 }))
    await page.route('**/api/agents/hermes/overview*', (route) => json(route, hermesOverview()))
    await page.route('**/api/agents/hermes/repair', (route) =>
      json(route, { id: 'job-rp-1', state: 'queued', phase: 'repair:hermes' }),
    )
    const job = await stageJobStatus(page, {
      id: 'job-rp-1',
      phase: 'repair:hermes',
      steps: ['preflight', 'rebuild venv'],
      terminal: 'failed',
      error: 'venv rebuild failed',
    })

    await openHermes(page)
    const front = page.getByTestId('agent-phase-hermes')
    await expect(front).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })

    // Live matches the pin, so Lifecycle leads with Repair, not Upgrade.
    await openLifecycle(page)
    const lifecycle = page.getByTestId('agent-card-hermes')
    await expect(lifecycle.getByTestId('agent-action-upgrade')).toHaveCount(0)
    await lifecycle.getByTestId('agent-action-repair').click()

    await expect(confirmButton(page, 'repair')).toBeEnabled()
    const [post] = await Promise.all([
      page.waitForRequest(
        (r) => r.method() === 'POST' && r.url().endsWith('/api/agents/hermes/repair'),
      ),
      page.getByTestId('agent-confirm-repair').click(),
    ])
    expect(post.method()).toBe('POST')

    await expect(front).toHaveAttribute('data-phase', 'working', { timeout: FIVE_S })
    await flip(page)
    const row = page.getByTestId('agent-job-hermes')
    await expect(row.locator('.fc-job-h b')).toHaveText('repairing')

    job.run()
    await expect(row).toHaveAttribute('data-state', 'running', { timeout: FIVE_S })
    await expect(row.locator('.fc-step')).toHaveText(['preflight', 'rebuild venv'])
    await expect(row.locator('.fc-step.cur')).toHaveText('rebuild venv')

    job.finish()
    await expect(toast(page, 'error', 'Hermes repair failed: venv rebuild failed')).toBeVisible({
      timeout: FIVE_S,
    })
    await expect(front).toHaveAttribute('data-phase', 'ready', { timeout: FIVE_S })
    await expect(row).toHaveCount(0)
  })

  test('a 409 from repair says the builder is busy and starts no job', async ({ page }) => {
    await mockShell(page)
    await page.route('**/api/agents', (route) =>
      json(route, { agents: [{ ...HERMES_RECORD, status: 'broken', unit_active: false }], count: 1 }),
    )
    await page.route('**/api/agents/hermes/overview*', (route) =>
      json(route, hermesOverview({ phase: 'broken' })),
    )
    await page.route('**/api/agents/hermes/repair', (route) =>
      json(
        route,
        { error: { code: 'agent.builder_busy', message: 'another hermes build holds the lock' } },
        409,
      ),
    )
    const job = await stageJobStatus(page, {
      id: 'job-never',
      phase: 'repair:hermes',
      steps: [],
      terminal: 'applied',
    })

    await openHermes(page)
    const front = page.getByTestId('agent-phase-hermes')
    await expect(front).toHaveAttribute('data-phase', 'broken', { timeout: FIVE_S })

    // A broken install offers Repair on the front face itself.
    await page.getByTestId('agent-card-hermes').getByTestId('agent-action-repair').click()
    await expect(confirmButton(page, 'repair')).toBeEnabled()
    await page.getByTestId('agent-confirm-repair').click()

    await expect(toast(page, 'warning', 'Hermes builder is busy')).toBeVisible({ timeout: FIVE_S })
    // Nothing was queued: no status poll, still broken, no job row.
    await expect.poll(() => job.polls(), { timeout: 2_000 }).toBe(0)
    await expect(front).toHaveAttribute('data-phase', 'broken')
    await expect(page.getByTestId('agent-job-hermes')).toHaveCount(0)
  })
})
