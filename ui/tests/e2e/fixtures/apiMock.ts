/**
 * apiMock fixture — page.route stubs for every `/api/*` endpoint the
 * UI touches. Each spec installs the fixture, then overrides the
 * routes it cares about. This keeps each spec readable (override
 * deltas only) while staying explicit about what's mocked.
 *
 * Live-mode bypass: when HAL0_E2E_LIVE=1 the fixture installs **no**
 * routes; the dev-server proxy talks to the real backend. Specs
 * should not assume any particular response shape in live mode beyond
 * the documented API contract.
 */
import { test as base, Route, Request, Page } from '@playwright/test'

export const LIVE = process.env.HAL0_E2E_LIVE === '1'

/* ── Default mock responses ──────────────────────────────────────── */

export const DEFAULT_INSTALL_STATE = { first_run: false }
export const DEFAULT_CONFIG_URLS = {
  openwebui: 'http://127.0.0.1:3001',
  api: 'http://127.0.0.1:8080',
}
export const DEFAULT_STATUS = {
  version: '0.1.0',
  update_available: false,
  slots: [],
  hardware: {
    is_uma: true,
    unified_memory_mb: 128 * 1024,
    gtt_used_mb: 2048,
    gtt_total_mb: 96 * 1024,
    ram_total_mb: 128 * 1024,
    ram_used_mb: 32 * 1024,
    vram_total_mb: 0,
    vram_used_mb: 0,
    gpu_name: 'AMD Radeon 8060S (Strix Halo)',
    gpu_vendor: 'AMD',
    cpu_name: 'AMD Ryzen AI Max+ 395',
    cpu_cores: 16,
    cpu_threads: 32,
    disk_total_mb: 1_000_000,
    disk_free_mb: 500_000,
    npu_present: true,
    npu_name: 'AMD XDNA NPU',
  },
}

export const DEFAULT_HARDWARE = DEFAULT_STATUS.hardware
export const DEFAULT_STATS_HARDWARE = {
  ...DEFAULT_STATUS.hardware,
  gpu_util: 0.12,
  ram_used_gb: 32,
}
export const DEFAULT_MODELS: any[] = []
export const DEFAULT_SLOTS_METRICS: Record<string, any> = {}
export const DEFAULT_SETTINGS = {
  meta: { schema_version: 1 },
  slots: { max_slots: 0, port_range_start: 8081, port_range_end: 8099 },
  dispatcher: { prefetch_timeout_s: 8.0, prefetch_parallel_cap: 4 },
  telemetry: { enabled: false, channel: 'stable' },
}
export const DEFAULT_CURATED_MODELS = {
  models: [
    {
      id: 'phi3-mini',
      display_name: 'Phi-3 Mini',
      description: 'Efficient reasoning, smallest curated pick.',
      size_gb: 2.4,
      vram_gb_min: 4,
      license: 'MIT',
      license_url: 'https://opensource.org/license/MIT',
      tags: ['general', 'fast'],
    },
    {
      id: 'llama32-3b',
      display_name: 'Llama 3.2 3B',
      description: 'General purpose, small.',
      size_gb: 2.0,
      vram_gb_min: 4,
      license: 'Llama',
      license_url: 'https://www.llama.com/llama3_2/license/',
      tags: ['general'],
    },
    {
      id: 'qwen3-4b',
      display_name: 'Qwen3 4B',
      description: 'Fast, multilingual, vision-capable.',
      size_gb: 4.1,
      vram_gb_min: 6,
      license: 'Apache 2.0',
      license_url: 'https://www.apache.org/licenses/LICENSE-2.0',
      tags: ['general', 'vision'],
    },
  ],
  custom_allowed: true,
}

/* ── Helpers ─────────────────────────────────────────────────────── */

export type MockState = {
  installState: { first_run: boolean }
  configUrls: typeof DEFAULT_CONFIG_URLS
  status: any
  hardware: any
  statsHardware: any
  models: any[]
  slotsMetrics: Record<string, any>
  settings: any
  curatedModels: any
  installCompleteCount: number
  installProbeCount: number
  /** Map of slot snapshots keyed by name — used for slot lifecycle. */
  slotSnapshots: Record<string, any>
  /** Approval entries returned by GET /api/agent/approvals (Phase 8). */
  agentApprovals: any[]
  /** Installed bundled agents returned by GET /api/agents (Phase 8). */
  agentInstalled: any[]
}

export function makeMockState(): MockState {
  return {
    installState: { ...DEFAULT_INSTALL_STATE },
    configUrls: { ...DEFAULT_CONFIG_URLS },
    status: JSON.parse(JSON.stringify(DEFAULT_STATUS)),
    hardware: JSON.parse(JSON.stringify(DEFAULT_HARDWARE)),
    statsHardware: JSON.parse(JSON.stringify(DEFAULT_STATS_HARDWARE)),
    models: [],
    slotsMetrics: {},
    settings: JSON.parse(JSON.stringify(DEFAULT_SETTINGS)),
    curatedModels: JSON.parse(JSON.stringify(DEFAULT_CURATED_MODELS)),
    installCompleteCount: 0,
    installProbeCount: 0,
    slotSnapshots: {},
    agentApprovals: [],
    agentInstalled: [],
  }
}

/* ── JSON helpers ────────────────────────────────────────────────── */

export function json(route: Route, body: any, status = 200) {
  return route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  })
}

/* ── Install default mocks on a page ─────────────────────────────── */

export async function installDefaultMocks(page: Page, state: MockState) {
  if (LIVE) return  // live mode — no mocks

  /* Register the catch-all FIRST so specific routes registered after
     it run first (Playwright matches routes in reverse-registration
     order). Without this, any unhandled /api/* call would leak to
     the vite proxy and hit the configured live backend. */
  await page.route('**/api/**', (route) => json(route, {}))
  await page.route('**/v1/**', (route) => json(route, {}))

  await page.route('**/api/install/state', (route) => json(route, state.installState))
  await page.route('**/api/install/complete', (route) => {
    state.installCompleteCount += 1
    return json(route, { ok: true })
  })
  await page.route('**/api/install/probe', (route) => {
    state.installProbeCount += 1
    return json(route, { ok: true, hardware: state.hardware })
  })
  await page.route('**/api/install/curated-models', (route) =>
    json(route, state.curatedModels),
  )
  await page.route('**/api/install/pick-default', (route) => json(route, { ok: true }))
  await page.route('**/api/config/urls', (route) => json(route, state.configUrls))
  await page.route('**/api/status', (route) => json(route, state.status))
  await page.route('**/api/hardware', (route) => json(route, state.hardware))
  await page.route('**/api/stats/hardware', (route) => json(route, state.statsHardware))
  await page.route('**/api/slots/metrics', (route) => json(route, state.slotsMetrics))
  await page.route('**/api/settings', (route) => {
    const req: Request = route.request()
    if (req.method() === 'PUT') {
      const patch = JSON.parse(req.postData() || '{}')
      for (const section of Object.keys(patch)) {
        state.settings[section] = { ...state.settings[section], ...patch[section] }
      }
      return json(route, state.settings)
    }
    return json(route, state.settings)
  })
  await page.route('**/api/settings/reload', (route) => json(route, state.settings))
  await page.route('**/api/settings/schema', (route) => json(route, {}))
  await page.route('**/api/models', (route) => {
    const req: Request = route.request()
    if (req.method() === 'POST') {
      const body = JSON.parse(req.postData() || '{}')
      const m = {
        id: body.id || `m-${state.models.length + 1}`,
        name: body.name || body.id,
        size_gb: body.size_gb ?? 2.5,
        license: body.license ?? 'unknown',
        ...body,
      }
      state.models.push(m)
      return json(route, m, 201)
    }
    return json(route, { models: state.models })
  })

  /* Agent surface (Phase 8). The header bell mounts on every page and
     hits these two endpoints via ensureBootstrapped(); without explicit
     routes the calls would fall through to the catch-all and yield
     [], which is fine but routing here lets specs seed
     `mockState.agentApprovals` / `agentInstalled` without per-spec
     boilerplate.

     Note: the EventSource for /api/agent/approvals/events is NOT routed
     here — specs that drive SSE install the sseHarness which replaces
     window.EventSource wholesale before navigation. Specs that don't
     install the harness should add an empty-200 stub for the events URL
     to keep the real EventSource from hitting the vite proxy. */
  await page.route('**/api/agents', (route) =>
    json(route, { agents: state.agentInstalled, count: state.agentInstalled.length }),
  )
  await page.route('**/api/agent/approvals', (route) =>
    json(route, { approvals: state.agentApprovals }),
  )
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
  cleanState: async ({ page, mockState }, use) => {
    await installDefaultMocks(page, mockState)
    await use()
  },
})

export { expect } from '@playwright/test'
