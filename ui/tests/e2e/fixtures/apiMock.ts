/**
 * apiMock fixture — page.route stubs for the `/api/*` + `/v1/*` endpoints
 * the v3 React dashboard will start touching in Phase B1. Phase A (current)
 * is HAL0_DATA-driven and renders without any fetch, so the fixture's main
 * job today is to catch stray calls so they don't leak to the vite proxy
 * and hit a live backend by accident.
 *
 * Each spec installs the fixture via `test.use({ cleanState: true })`-style
 * extension below, then overrides per-route as it grows. Phase B1 should
 * fold real response shapes into MOCK_DATA without touching specs that
 * don't need them.
 *
 * Live-mode bypass: when HAL0_E2E_LIVE=1 the fixture installs no routes;
 * the dev-server proxy in vite.config.ts forwards /api+/v1 to 127.0.0.1:8080.
 */
import { test as base, Page, Route } from '@playwright/test'
import {
  MOCK_DATA,
  BOARD_BOARDS,
  BOARD_PROFILES,
  BOARD_ASSIGNEES,
  BOARD_STATS,
  BOARD_CONFIG,
  BOARD_WORKERS_ACTIVE,
  BOARD_TASKS,
  BOARD_ORCH_DEFAULT,
  makeBoardLanesResponse,
} from './mock-data'

export const LIVE = process.env.HAL0_E2E_LIVE === '1'
export { MOCK_DATA } from './mock-data'

/* ── Default mock state (cloned per spec) ────────────────────────── */

export type MockState = {
  host: typeof MOCK_DATA.host
  slots: typeof MOCK_DATA.slots
  models: typeof MOCK_DATA.models
  backends: typeof MOCK_DATA.backends
  approvals: any[]
  // Board mock state — tasks array cloned per spec so mutations don't bleed
  boardTasks: typeof BOARD_TASKS
}

export function makeMockState(): MockState {
  return {
    ...JSON.parse(JSON.stringify(MOCK_DATA)),
    boardTasks: JSON.parse(JSON.stringify(BOARD_TASKS)),
  }
}

/* ── helper: JSON fulfil ─────────────────────────────────────────── */

export function json(route: Route, body: any, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/* ── Install default mocks on a page ─────────────────────────────── */

export async function installDefaultMocks(page: Page, state: MockState) {
  if (LIVE) return

  // Catch-all FIRST so per-route registrations after this win
  // (Playwright matches routes in reverse-registration order).
  //
  // Patterns are anchored on the URL origin so they don't accidentally
  // intercept Vite module imports under `/src/api/…` (which would be
  // fulfilled with JSON and break React mount with a MIME error).
  await page.route(/^https?:\/\/[^/]+\/api\//, (route) => json(route, {}))
  await page.route(/^https?:\/\/[^/]+\/v1\//, (route) => json(route, {}))

  await page.route('**/api/status', (route) =>
    json(route, {
      version: '0.4.0',
      update_available: false,
      slots: state.slots,
      hardware: state.host,
      // 0.4 gate: the dashboard hides the Agent (Memory) nav unless
      // /api/status reports memory live. The γ-suite exercises the memory
      // UI, so the default mock keeps it on; a dedicated spec flips it off.
      memory_enabled: true,
    }),
  )
  await page.route('**/api/hardware', (route) => json(route, state.host))
  await page.route('**/api/models', (route) =>
    json(route, { models: state.models, count: state.models.length }),
  )
  await page.route('**/api/slots', (route) => json(route, { slots: state.slots }))
  await page.route('**/api/slots/metrics', (route) => json(route, {}))
  await page.route('**/api/backends', (route) => json(route, { backends: state.backends }))
  // GET /api/upstreams returns a bare UpstreamEntry[] (providers.py). Without
  // this stub the `/api/` catch-all above answers `{}`, and Connections' pane
  // does `(upstreamsQuery.data ?? []).filter(…)` — `{}` is truthy so the
  // `?? []` misses and `{}.filter` throws, blanking the whole ConnectionsView
  // (the #slots/endpoints "Local endpoints" pane then renders 0 rows). An empty
  // list means no managed remotes, so the local-endpoint row count is unaffected.
  await page.route('**/api/upstreams', (route) => json(route, []))
  await page.route('**/api/profiles', (route) => json(route, MOCK_DATA.profiles ?? []))
  await page.route('**/api/agent/approvals', (route) =>
    json(route, { approvals: state.approvals }),
  )

  // ── Operator Board routes (/api/board/*) ────────────────────────────
  //
  // Specific routes are registered AFTER the catch-all above; Playwright
  // matches in reverse-registration order so these specific patterns win
  // over the broad `/api/` catch-all.
  //
  // Pattern note: tasks/bulk must appear before tasks/:id wildcards so
  // the bulk path isn't swallowed by the per-task glob.

  // GET /api/board/boards + POST (create board)
  await page.route('**/api/board/boards', (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON?.() ?? {}
      return json(route, { ...body, slug: body.slug ?? 'new-board' }, 201)
    }
    return json(route, BOARD_BOARDS)
  })

  // PATCH/DELETE /api/board/boards/:slug + /switch
  await page.route(/\/api\/board\/boards\/[^/]+\/switch/, (route) =>
    json(route, { ok: true }),
  )
  await page.route(/\/api\/board\/boards\/[^/]+/, (route) => {
    const method = route.request().method()
    if (method === 'PATCH') return json(route, { ok: true })
    if (method === 'DELETE') return json(route, { ok: true })
    return json(route, {})
  })

  // GET /api/board/profiles + PATCH /api/board/profiles/:name
  await page.route(/\/api\/board\/profiles\/[^/]+/, (route) =>
    json(route, { ok: true }),
  )
  await page.route('**/api/board/profiles', (route) =>
    json(route, BOARD_PROFILES),
  )

  // GET /api/board/assignees?board=  (regex: the ?board= query must match)
  await page.route(/\/api\/board\/assignees(\?|$)/, (route) =>
    json(route, BOARD_ASSIGNEES),
  )

  // GET /api/board/stats?board=
  await page.route('**/api/board/stats', (route) =>
    json(route, BOARD_STATS),
  )

  // GET /api/board/config
  await page.route('**/api/board/config', (route) =>
    json(route, BOARD_CONFIG),
  )

  // GET/PUT /api/board/orchestration
  await page.route('**/api/board/orchestration', (route) => {
    if (route.request().method() === 'PUT') return json(route, { ok: true })
    return json(route, BOARD_ORCH_DEFAULT)
  })

  // GET /api/board/workers/active
  await page.route('**/api/board/workers/active', (route) =>
    json(route, BOARD_WORKERS_ACTIVE),
  )

  // POST /api/board/dispatch?max=N
  await page.route('**/api/board/dispatch', (route) =>
    json(route, { dispatched: 1 }),
  )

  // POST /api/board/tasks/bulk?board= (must be before /tasks/:id patterns;
  // regex so the optional ?board= query matches)
  await page.route(/\/api\/board\/tasks\/bulk(\?|$)/, (route) =>
    json(route, { updated: 0 }),
  )

  // POST /api/board/links  +  DELETE /api/board/links?parent_id=&child_id=
  // (regex: DELETE carries its ids as QUERY params per the backend contract)
  await page.route(/\/api\/board\/links(\?|$)/, (route) => {
    const method = route.request().method()
    if (method === 'POST')   return json(route, { ok: true }, 201)
    if (method === 'DELETE') return json(route, { ok: true })
    return json(route, {})
  })

  // Task sub-resource routes (/tasks/:id/*)
  await page.route(/\/api\/board\/tasks\/[^/]+\/log/, (route) =>
    json(route, []),
  )
  await page.route(/\/api\/board\/tasks\/[^/]+\/comments/, (route) =>
    json(route, { ok: true }, 201),
  )
  await page.route(/\/api\/board\/tasks\/[^/]+\/reassign/, (route) =>
    json(route, { ok: true }),
  )
  await page.route(/\/api\/board\/tasks\/[^/]+\/specify/, (route) =>
    json(route, { ok: true }),
  )
  await page.route(/\/api\/board\/tasks\/[^/]+\/decompose/, (route) =>
    json(route, { ok: true }),
  )
  await page.route(/\/api\/board\/tasks\/[^/]+\/reclaim/, (route) =>
    json(route, { ok: true }),
  )

  // GET/PATCH/DELETE /api/board/tasks/:id (specific task)
  await page.route(/\/api\/board\/tasks\/[^/]+$/, (route) => {
    const method = route.request().method()
    const url = route.request().url()
    const id = url.split('/').pop()?.split('?')[0] ?? ''
    const task = state.boardTasks.find((t) => t.id === id)
    if (method === 'GET') return json(route, task ?? {})
    if (method === 'PATCH') {
      const body = route.request().postDataJSON?.() ?? {}
      return json(route, { ...(task ?? {}), ...body })
    }
    if (method === 'DELETE') return json(route, { ok: true })
    return json(route, {})
  })

  // POST /api/board/tasks (create; regex so an optional ?board= matches —
  // trailing (\?|$) keeps /tasks/:id out of this route)
  await page.route(/\/api\/board\/tasks(\?|$)/, (route) => {
    if (route.request().method() === 'POST') {
      const body = route.request().postDataJSON?.() ?? {}
      const newTask = {
        id: `t_mock_${Date.now()}`,
        status: 'triage',
        block_reason: null,
        deps: { parents: [], children: [] },
        comments: [],
        events: [],
        runs: [],
        comment_count: 0,
        dep_count: null,
        ...body,
      }
      return json(route, newTask, 201)
    }
    return json(route, state.boardTasks)
  })

  // GET /api/board/runs/:id
  await page.route(/\/api\/board\/runs\/[^/]+/, (route) =>
    json(route, { id: 'run_mock', state: 'completed', profile: 'admin-agent' }),
  )

  // GET /api/board/diagnostics
  await page.route('**/api/board/diagnostics', (route) =>
    json(route, { ok: true }),
  )

  // GET /api/board/board?board=&include_archived= (main board view — lanes
  // format; regex so the query params the hook now threads actually match)
  await page.route(/\/api\/board\/board(\?|$)/, (route) => {
    const url = route.request().url()
    const includeArchived = url.includes('include_archived=true')
    return json(route, makeBoardLanesResponse(includeArchived))
  })

  // POST /api/board/chat (SSE stub — returns a minimal SSE body)
  // Note: real SSE not feasible in page.route; return a minimal done frame.
  await page.route('**/api/board/chat', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: 'data: {"delta":"Board chat stub response."}\n\ndata: [DONE]\n\n',
    }),
  )
}

/* ── Memory provider (Hindsight vs Honcho routing) mock defaults ─────
 *
 * Frozen contract (feat/honcho-ui): GET/PUT /api/memory/provider, GET
 * /api/memory/honcho/stats, GET/PUT /api/memory/honcho/sync, POST
 * /api/memory/honcho/sync/run. None of these are in the forced-mock
 * (VITE_MOCK_HAL0) allowlist in src/api/mock.ts, so unlike /api/memory/
 * engine + /api/memory/banks (which short-circuit before the network),
 * page.route is authoritative here.
 */

export const MEMORY_PROVIDER_DEFAULT = {
  engines: {
    hindsight: { healthy: true, url: 'http://127.0.0.1:9177' },
    honcho: { healthy: true, url: 'http://127.0.0.1:8000' },
  },
  agents: {
    hermes: { provider: 'hindsight', private: false },
    'pi-coder': { provider: 'honcho', private: true },
  },
} as const

export const HONCHO_STATS_ENABLED = {
  enabled: true,
  reachable: true,
  version: '3.1.0',
  url: 'http://127.0.0.1:8000',
  workspace: 'hal0',
  peers: 3,
  observations: 1200,
  conclusions: 67,
  deriver_pending: 2,
  deriver_processing: 1,
} as const

export const HONCHO_STATS_DISABLED = {
  enabled: false,
  reachable: false,
  version: null,
  url: null,
  workspace: null,
  peers: null,
  observations: null,
  conclusions: null,
  deriver_pending: null,
  deriver_processing: null,
} as const

export const HONCHO_SYNC_DEFAULT = {
  timer_enabled: true,
  interval: '6h',
  last_run_at: '2026-07-11T02:00:00Z',
  last_run_ok: true,
  last_run_error: null,
  last_synced_count: 42,
  next_run_at: '2026-07-11T08:00:00Z',
} as const

export type MemoryProviderMockOptions = {
  provider?: typeof MEMORY_PROVIDER_DEFAULT
  honchoStats?: typeof HONCHO_STATS_ENABLED | typeof HONCHO_STATS_DISABLED
  sync?: typeof HONCHO_SYNC_DEFAULT
  /** agent ids that 409 (memory.provider_unavailable) when routed to "honcho". */
  unavailableAgents?: string[]
  /**
   * POST /honcho/sync/run response override. The real route is fail-soft —
   * a systemctl failure to start the sync unit still returns HTTP 200 with
   * `{started: false, note}` (routes/memory.py `run_honcho_sync`), so tests
   * need to exercise both outcomes without simulating a network error.
   * Defaults to `{started: true, note: null}`.
   */
  syncRun?: { started: boolean; note?: string | null }
}

/**
 * Installs the memory-provider-routing + Honcho engine surface. Call from
 * a test body (after the auto `cleanState` default mocks, before
 * `page.goto`) so these specific routes win over the `/api/` catch-all.
 * Mutations (PUT provider / PUT sync) are applied to a closure-local clone
 * so subsequent GETs in the same test observe them, mirroring the
 * `state.boardTasks` pattern above.
 */
export async function installMemoryProviderMocks(page: Page, opts: MemoryProviderMockOptions = {}) {
  const provider = JSON.parse(JSON.stringify(opts.provider ?? MEMORY_PROVIDER_DEFAULT)) as {
    engines: typeof MEMORY_PROVIDER_DEFAULT.engines
    agents: Record<string, { provider: string; private: boolean }>
  }
  const honchoStats = JSON.parse(JSON.stringify(opts.honchoStats ?? HONCHO_STATS_ENABLED))
  const sync = JSON.parse(JSON.stringify(opts.sync ?? HONCHO_SYNC_DEFAULT))
  const unavailable = new Set(opts.unavailableAgents ?? [])
  const syncRun = JSON.parse(JSON.stringify(opts.syncRun ?? { started: true, note: null }))

  await page.route('**/api/memory/provider', (route) => {
    if (route.request().method() !== 'PUT') return json(route, provider)
    const body = route.request().postDataJSON?.() ?? {}
    if (unavailable.has(body.agent) && body.provider === 'honcho') {
      return json(
        route,
        {
          error: {
            code: 'memory.provider_unavailable',
            message: `honcho engine is not reachable at ${provider.engines.honcho.url} — start it first`,
            details: { url: provider.engines.honcho.url },
          },
        },
        409,
      )
    }
    provider.agents[body.agent] = { provider: body.provider, private: !!body.private }
    // Real shape (routes/memory.py `set_memory_provider`): {agent, provider,
    // private, restarted, provisioned, note} — no `ok` field, and the UI
    // doesn't read the response body on success, but fixtures shouldn't
    // invent a shape the backend doesn't send.
    return json(route, {
      agent: body.agent,
      ...provider.agents[body.agent],
      restarted: true,
      provisioned: true,
      note: null,
    })
  })

  await page.route('**/api/memory/honcho/stats', (route) => json(route, honchoStats))

  // /sync/run is registered separately below; this literal pattern (no
  // trailing wildcard) does not match the extra /run path segment.
  await page.route('**/api/memory/honcho/sync', (route) => {
    if (route.request().method() !== 'PUT') return json(route, sync)
    const body = route.request().postDataJSON?.() ?? {}
    sync.timer_enabled = !!body.enabled
    return json(route, sync)
  })

  await page.route('**/api/memory/honcho/sync/run', (route) => json(route, syncRun))
}

/* ── Test fixture wiring ─────────────────────────────────────────── */

type Fixtures = {
  mockState: MockState
  cleanState: void
}

export const test = base.extend<Fixtures>({
  mockState: async ({}, use) => {
    await use(makeMockState())
  },
  cleanState: [
    async ({ page, mockState }, use) => {
      await installDefaultMocks(page, mockState)
      await use()
    },
    { auto: true },
  ],
})

export { expect, type Page } from '@playwright/test'
