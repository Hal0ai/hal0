/**
 * agent-flow.spec.ts — γ test for Wave 2 dashboard-UI work (Phase 8).
 *
 * Covers:
 *   a) First-run wizard reaches the new Agent step + picks pi-coder.
 *   b) Header bell shows a badge when an approval is enqueued (SSE-driven).
 *   c) Modal opens, approve button calls the right endpoint, row clears.
 *
 * Backend is mocked end-to-end via page.route + the SSE harness. No
 * real hal0-api is started — that's the live-mode (E2E) suite's job.
 *
 * Visual policy: this spec must finish under ~5s on CI per the
 * playwright.config budget (8 min total / 11+ specs).
 */
import { test, expect, json } from '../fixtures/apiMock'
import { installSseHarness, emitSse, waitForSse } from '../fixtures/sseHarness'

test.describe('Phase 8 — agent surface', () => {
  test.beforeEach(async ({ page }) => {
    await installSseHarness(page)

    // Agent-store routes — empty install + empty inbox by default.
    // Individual tests override these as needed.
    await page.route('**/api/agents', (route) =>
      json(route, { agents: [], count: 0 }),
    )
    await page.route('**/api/agent/approvals', (route) =>
      json(route, { approvals: [] }),
    )
    await page.route('**/api/agent/approvals/events', (route) =>
      // The SSE harness intercepts EventSource construction itself, so
      // the actual response body never gets read — but page.route still
      // has to fulfil the HTTP request so the fake-EventSource's url
      // entry stays consistent with what a real client would see.
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
    )
    await page.route(/\/api\/agent\/approvals\/[^/]+\/approve$/, (route) =>
      json(route, { approval: { id: 'a1', state: 'executed' } }),
    )
    await page.route(/\/api\/agent\/approvals\/[^/]+\/deny$/, (route) =>
      json(route, { approval: { id: 'a1', state: 'denied' } }),
    )
    await page.route('**/api/agents/install', (route) =>
      json(route, {
        name: 'pi-coder',
        installed_at: new Date().toISOString(),
        status: 'installed',
        data_dir: '/var/lib/hal0/agents/pi-coder',
        config_path: '/etc/hal0/agents/pi-coder.toml',
      }),
    )
  })

  // Slice #172 (v2 FirstRun rework) replaced the v1 8-step wizard with a
  // 3-state bundle picker (pick → confirm → progress). The bundled-agent
  // step was REMOVED from the firstrun flow per the v0.3 design — agent
  // install lives behind the /agent route + ADR-0004's settings entry
  // point. The original assertion (agent picker in firstrun) no longer
  // applies. Re-target the agent install flow in slice #174's extras
  // pass; until then we skip to keep the rest of agent-flow green.
  test.skip('first-run wizard surfaces the agent step + picks pi-coder', async ({
    page,
    mockState,
    cleanState: _cleanState,
  }) => {
    mockState.installState.first_run = true

    // Routes the wizard touches that aren't in the default mock bundle.
    await page.route('**/api/auth/status', (route) => json(route, { password_set: false }))
    await page.route('**/api/auth/password', (route) =>
      json(route, { ok: true, password_set: true, rotated: false }),
    )
    await page.route('**/api/auth/disable', (route) => json(route, { ok: true }))
    await page.route('**/api/capabilities', (route) =>
      json(route, {
        backends: [],
        catalogs: {
          embed: { embed: [], rerank: [] },
          voice: { stt: [], tts: [] },
          img: { img: [] },
        },
        selections: {},
      }),
    )
    await page.route('**/api/config/models', (route) => {
      if (route.request().method() === 'PUT') {
        return json(route, { roots: ['/var/lib/hal0/models'] })
      }
      return json(route, { roots: ['/var/lib/hal0/models'] })
    })
    await page.route('**/api/models/*/pull/stream', (route) =>
      route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' }),
    )
    await page.route('**/api/models/*/pull', (route) =>
      json(route, { id: 'job1', state: 'queued' }),
    )

    await page.goto('/')
    await expect(page).toHaveURL(/\/firstrun$/)

    // Walk the wizard up to the agent step. Skip password, accept hw,
    // accept curated default (none picked), skip primary, skip caps,
    // accept license-empty-state, land on agent picker.
    await page.getByRole('button', { name: /Skip — leave open/ }).click()
    // step 2 — hardware
    await page.getByRole('button', { name: /^Next →$/ }).click()
    // step 3 — primary chat (skip to keep this a thin flow)
    await page.getByRole('button', { name: /Skip — no chat model/ }).click()
    // step 4 — capabilities (smart defaults; just advance)
    await page.getByRole('button', { name: /^Next →$/ }).click()
    // step 6 — license (nothing to download → "Finish setup →" button)
    await page.getByRole('button', { name: /Finish setup/ }).click()

    // Step 7 — Agent picker visible. Pi-coder option present, default
    // is "No agent".
    await expect(page.getByText('Bundle a third-party agent')).toBeVisible()
    // Multiple .agent-option labels match "pi-coder" as substring (the
    // "No agent" copy mentions it too), so we drive the picker through
    // the radio input — its value is unique. The input is sr-only via
    // CSS, hence force:true on the check.
    const piRadio = page.locator('input[name="firstrun-agent"][value="pi-coder"]')
    await piRadio.check({ force: true })
    await expect(piRadio).toBeChecked()

    // Click "Install agent + models →" — this calls /api/agents/install
    // and advances to step 8 (Install). We can't easily assert step 8
    // here without driving pulls; just check the install call fired.
    const installReq = page.waitForRequest(
      (req) =>
        req.url().endsWith('/api/agents/install') && req.method() === 'POST',
    )
    await page.getByRole('button', { name: /Install agent \+ models/ }).click()
    const req = await installReq
    expect(JSON.parse(req.postData() || '{}').name).toBe('pi-coder')
  })

  test('header bell shows badge on SSE enqueued event', async ({
    page,
    cleanState: _cleanState,
  }) => {
    // Land on the dashboard with no pending approvals + no agent.
    await page.goto('/')

    // Wait for the bell to mount and its SSE socket to be live (the
    // store opens it via ensureBootstrapped on bell mount).
    await waitForSse(page, '/api/agent/approvals/events')

    // Initial state — no badge.
    const bell = page.locator('.bell')
    await expect(bell).toBeVisible()
    await expect(page.locator('.bell .badge')).toHaveCount(0)

    // Drive an "enqueued" frame through the SSE harness.
    await emitSse(page, '/api/agent/approvals/events', {
      kind: 'enqueued',
      entry: {
        id: 'a1',
        tool: 'model_pull',
        args: { model_id: 'qwen3:0.6b' },
        client_id: 'pi-coder',
        enqueued_at: Date.now() / 1000,
        state: 'pending',
        hit_count: 1,
        decided_at: null,
        result: null,
        error: null,
      },
    })

    // Badge appears with count 1.
    await expect(page.locator('.bell .badge')).toHaveText('1')
  })

  test('modal opens, approve clears the row', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.goto('/')
    await waitForSse(page, '/api/agent/approvals/events')

    // Enqueue an approval via SSE.
    await emitSse(page, '/api/agent/approvals/events', {
      kind: 'enqueued',
      entry: {
        id: 'a-test-1',
        tool: 'slot_delete',
        args: { slot: 'primary' },
        client_id: 'pi-coder',
        enqueued_at: Date.now() / 1000,
        state: 'pending',
        hit_count: 1,
        decided_at: null,
        result: null,
        error: null,
      },
    })
    await expect(page.locator('.bell .badge')).toHaveText('1')

    // Click the bell — modal opens.
    await page.locator('.bell').click()
    const modal = page.locator('.modal-card')
    await expect(modal).toBeVisible()
    await expect(modal.getByText('slot_delete')).toBeVisible()

    // The Approve POST. The route mock returns executed; the store
    // optimistically clears the row before the SSE frame even arrives.
    const approveReq = page.waitForRequest(
      (req) =>
        /\/api\/agent\/approvals\/a-test-1\/approve$/.test(req.url()) &&
        req.method() === 'POST',
    )
    await modal.getByRole('button', { name: /^Approve$/ }).click()
    await approveReq

    // Row is gone; badge gone.
    await expect(page.locator('.bell .badge')).toHaveCount(0)
  })

  /* ── New γ coverage for ADR-0004 §5 gaps ─────────────────────────── */

  test('@agent-approval deny clears the row + badge', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.goto('/')
    await waitForSse(page, '/api/agent/approvals/events')

    // Enqueue + verify the badge appears.
    await emitSse(page, '/api/agent/approvals/events', {
      kind: 'enqueued',
      entry: {
        id: 'a-deny-1',
        tool: 'model_delete',
        args: { model_id: 'qwen3:0.6b' },
        client_id: 'pi-coder',
        enqueued_at: Date.now() / 1000,
        state: 'pending',
        hit_count: 1,
        decided_at: null,
        result: null,
        error: null,
      },
    })
    await expect(page.locator('.bell .badge')).toHaveText('1')

    // Open modal, click Deny, intercept the POST.
    await page.locator('.bell').click()
    const modal = page.locator('.modal-card')
    await expect(modal).toBeVisible()
    await expect(modal.getByText('model_delete')).toBeVisible()

    const denyReq = page.waitForRequest(
      (req) =>
        /\/api\/agent\/approvals\/a-deny-1\/deny$/.test(req.url()) &&
        req.method() === 'POST',
    )
    await modal.getByRole('button', { name: /^Deny$/ }).click()
    await denyReq

    // Store optimistically clears on deny resolve; badge gone, row gone.
    await expect(page.locator('.bell .badge')).toHaveCount(0)
    await expect(modal.getByText('model_delete')).toHaveCount(0)
  })

  test('@agent-approval badge decrements as multi-pending get approved', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.goto('/')
    await waitForSse(page, '/api/agent/approvals/events')

    // Drive two enqueueds — badge ticks to 2.
    const baseEntry = {
      tool: 'model_pull',
      args: {},
      client_id: 'pi-coder',
      enqueued_at: Date.now() / 1000,
      state: 'pending',
      hit_count: 1,
      decided_at: null,
      result: null,
      error: null,
    }
    await emitSse(page, '/api/agent/approvals/events', {
      kind: 'enqueued',
      entry: { ...baseEntry, id: 'a-multi-1', args: { model_id: 'qwen3:0.6b' } },
    })
    await emitSse(page, '/api/agent/approvals/events', {
      kind: 'enqueued',
      entry: { ...baseEntry, id: 'a-multi-2', args: { model_id: 'phi3-mini' } },
    })
    await expect(page.locator('.bell .badge')).toHaveText('2')

    // Approve #1 — badge ticks down to 1. The store optimistically
    // drops the row on the approve resolve (per stores/agent.js:182).
    await page.locator('.bell').click()
    const modal = page.locator('.modal-card')
    await expect(modal).toBeVisible()
    const row1 = modal.locator('.approval-row', { hasText: 'qwen3:0.6b' })
    await expect(row1).toBeVisible()
    const approve1 = page.waitForRequest(
      (req) =>
        /\/api\/agent\/approvals\/a-multi-1\/approve$/.test(req.url()) &&
        req.method() === 'POST',
    )
    await row1.getByRole('button', { name: /^Approve$/ }).click()
    await approve1
    await expect(page.locator('.bell .badge')).toHaveText('1')

    // Approve #2 — badge gone.
    const row2 = modal.locator('.approval-row', { hasText: 'phi3-mini' })
    await expect(row2).toBeVisible()
    const approve2 = page.waitForRequest(
      (req) =>
        /\/api\/agent\/approvals\/a-multi-2\/approve$/.test(req.url()) &&
        req.method() === 'POST',
    )
    await row2.getByRole('button', { name: /^Approve$/ }).click()
    await approve2
    await expect(page.locator('.bell .badge')).toHaveCount(0)
  })

  test('@agent-approval badge persists across reload via GET backfill', async ({
    page,
    mockState,
    cleanState: _cleanState,
  }) => {
    await page.goto('/')
    await waitForSse(page, '/api/agent/approvals/events')

    // Step 1: emit the pending via SSE → badge '1'.
    const entry = {
      id: 'a-persist-1',
      tool: 'slot_restart',
      args: { slot: 'primary' },
      client_id: 'pi-coder',
      enqueued_at: Date.now() / 1000,
      state: 'pending',
      hit_count: 1,
      decided_at: null,
      result: null,
      error: null,
    }
    await emitSse(page, '/api/agent/approvals/events', { kind: 'enqueued', entry })
    await expect(page.locator('.bell .badge')).toHaveText('1')

    // Step 2: stage the GET-backfill response and reload. After reload
    // the store calls fetchPending() during ensureBootstrapped, so the
    // backfill is what populates the badge on the fresh page.
    mockState.agentApprovals = [entry]
    await page.reload()

    // SSE re-opens after reload; backfill from GET re-seeds pending.
    await waitForSse(page, '/api/agent/approvals/events')
    await expect(page.locator('.bell .badge')).toHaveText('1')
  })

  test('@agent-approval clear-all denies every pending', async ({
    page,
    cleanState: _cleanState,
  }) => {
    await page.goto('/')
    await waitForSse(page, '/api/agent/approvals/events')

    // Emit three enqueueds; badge climbs to '3'.
    const baseEntry = {
      tool: 'model_pull',
      args: {},
      client_id: 'pi-coder',
      enqueued_at: Date.now() / 1000,
      state: 'pending',
      hit_count: 1,
      decided_at: null,
      result: null,
      error: null,
    }
    for (const i of [1, 2, 3]) {
      await emitSse(page, '/api/agent/approvals/events', {
        kind: 'enqueued',
        entry: { ...baseEntry, id: `a-clear-${i}`, args: { model_id: `m-${i}` } },
      })
    }
    await expect(page.locator('.bell .badge')).toHaveText('3')

    // Open modal.
    await page.locator('.bell').click()
    const modal = page.locator('.modal-card')
    await expect(modal).toBeVisible()

    // Track all three deny POSTs. stores/agent.js:clearAll loops the
    // pending list and calls deny() on each entry; the existing default
    // route mock for /deny is already in beforeEach.
    const denyIds = new Set<string>()
    page.on('request', (req) => {
      const m = req.url().match(/\/api\/agent\/approvals\/([^/]+)\/deny$/)
      if (m && req.method() === 'POST') denyIds.add(m[1])
    })

    // window.confirm is the user-facing gate for Clear-all — auto-accept.
    page.once('dialog', (d) => d.accept())
    await modal.getByRole('button', { name: /^Clear all$/ }).click()

    // All three deny POSTs fired; badge cleared.
    await expect.poll(() => denyIds.size, { timeout: 5000 }).toBe(3)
    expect(denyIds.has('a-clear-1')).toBe(true)
    expect(denyIds.has('a-clear-2')).toBe(true)
    expect(denyIds.has('a-clear-3')).toBe(true)
    await expect(page.locator('.bell .badge')).toHaveCount(0)
  })
})
