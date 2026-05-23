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
import { MOCK_DATA } from './mock-data'

export const LIVE = process.env.HAL0_E2E_LIVE === '1'

/**
 * Re-export the v2 mock-data constants so specs and helpers don't have
 * to know which file owns the shape. Mirrors `MOCK_DATA` in
 * `ui/src/composables/useMock.js` per slice #166.
 */
export { MOCK_DATA } from './mock-data'

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
/**
 * PR-13 Lemonade admin defaults — what GET /api/lemonade/config returns
 * in the absence of a per-spec override. Shape matches the real lemond
 * /internal/config snapshot plus the hal0-added ``_hal0`` metadata
 * block the route appends (see hal0.api.routes.lemonade_admin).
 */
export const DEFAULT_LEMONADE_CONFIG = {
  host: '127.0.0.1',
  port: 13305,
  ctx_size: 4096,
  max_loaded_models: 4,
  extra_models_dir: '/var/lib/hal0/models',
  global_timeout: 900,
  no_broadcast: true,
  log_level: 'info',
  llamacpp: { args: '--parallel 1 --threads 8', backend: 'rocm' },
  flm: { args: '--asr 1 --embed 1' },
  whispercpp: { backend: 'vulkan' },
  sdcpp: { backend: 'rocm', steps: 20, cfg_scale: 7.0, width: 512, height: 512 },
  _hal0: {
    effects: {
      immediate: [
        'extra_models_dir',
        'global_timeout',
        'host',
        'log_level',
        'no_broadcast',
        'port',
      ],
      deferred: [
        'cfg_scale',
        'ctx_size',
        'flm_args',
        'height',
        'llamacpp_args',
        'llamacpp_backend',
        'max_loaded_models',
        'sdcpp_backend',
        'steps',
        'whispercpp_backend',
        'width',
      ],
    },
    locked: { extra_models_dir: '/var/lib/hal0/models' },
  },
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
  /** Lemonade /internal/config snapshot returned by GET /api/lemonade/config (PR-13). */
  lemonadeConfig: any
  /** Last POST /api/lemonade/config patch body — for assertions. */
  lemonadeLastPatch: Record<string, any> | null
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
    lemonadeConfig: JSON.parse(JSON.stringify(DEFAULT_LEMONADE_CONFIG)),
    lemonadeLastPatch: null,
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

  /* PR-13: Lemonade admin panel. GET returns the seeded snapshot;
     POST validates against the same key gates the backend enforces
     (unknown key → 400, llamacpp_args missing --threads or below 2 →
     400, flm_args missing either trio flag → 400). The mock mirrors
     the real route's response envelope so the UI's success-toast +
     inline-error code paths are exercised end-to-end. */
  await page.route('**/api/lemonade/config', (route) => {
    const req: Request = route.request()
    if (req.method() === 'POST') {
      const patch = JSON.parse(req.postData() || '{}')
      state.lemonadeLastPatch = patch
      const immediate = new Set([
        'port', 'host', 'log_level', 'global_timeout', 'no_broadcast', 'extra_models_dir',
      ])
      const deferred = new Set([
        'max_loaded_models', 'ctx_size', 'llamacpp_backend', 'llamacpp_args',
        'sdcpp_backend', 'whispercpp_backend', 'steps', 'cfg_scale', 'width', 'height',
        'flm_args',
      ])
      const known = new Set([...immediate, ...deferred])
      const errors: Record<string, string> = {}
      for (const [key, value] of Object.entries(patch)) {
        if (!known.has(key)) {
          errors[key] = `unknown key — admin-editable keys are ${[...known].sort()}`
          continue
        }
        if (key === 'llamacpp_args' && typeof value === 'string') {
          const m = value.match(/(?:^|\s)--threads(?:\s+|=)(\d+)(?=\s|$)/)
          if (!m) {
            errors.llamacpp_args = 'must include --threads N where N >= 2'
          } else if (parseInt(m[1], 10) < 2) {
            errors.llamacpp_args = `--threads ${m[1]} is below the required minimum of 2`
          }
        }
        if (key === 'flm_args' && typeof value === 'string') {
          if (!/--asr\s+1(?:\s|$)/.test(value)) errors.flm_args = 'must include --asr 1 (FLM trio mandate, plan §5)'
          else if (!/--embed\s+1(?:\s|$)/.test(value)) errors.flm_args = 'must include --embed 1 (FLM trio mandate, plan §5)'
        }
        if (key === 'extra_models_dir' && value !== '/var/lib/hal0/models') {
          errors.extra_models_dir = "must equal '/var/lib/hal0/models'"
        }
      }
      if (Object.keys(errors).length > 0) {
        return route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            error: {
              code: 'lemonade.config_invalid',
              message: 'one or more keys failed validation',
              details: errors,
            },
          }),
        })
      }
      // Merge accepted patch into the snapshot (flat keys → nested for
      // llamacpp / flm / whispercpp / sdcpp).
      for (const [k, v] of Object.entries(patch)) {
        if (k === 'llamacpp_args') state.lemonadeConfig.llamacpp.args = v
        else if (k === 'llamacpp_backend') state.lemonadeConfig.llamacpp.backend = v
        else if (k === 'flm_args') state.lemonadeConfig.flm.args = v
        else if (k === 'whispercpp_backend') state.lemonadeConfig.whispercpp.backend = v
        else if (k === 'sdcpp_backend') state.lemonadeConfig.sdcpp.backend = v
        else if (['steps', 'cfg_scale', 'width', 'height'].includes(k)) state.lemonadeConfig.sdcpp[k] = v
        else state.lemonadeConfig[k] = v
      }
      const touched = Object.keys(patch)
      return json(route, {
        applied: { applied: touched },
        effects: {
          immediate: touched.filter((k) => immediate.has(k)).sort(),
          deferred: touched.filter((k) => deferred.has(k)).sort(),
        },
      })
    }
    return json(route, state.lemonadeConfig)
  })
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

/**
 * Pre-route every `/api/mcp/*` endpoint to the v0.3 mock shapes from
 * `useMock.js`. Slice #14 (#180) will switch to a real store and add
 * its own routing; until then any spec touching the MCP surface can
 * call `await mockMcpEndpoints(page)` after `installDefaultMocks` to
 * cover servers / clients / catalog in one line.
 *
 * Routes registered here win over the catch-all because Playwright
 * matches in reverse-registration order — call AFTER installDefaultMocks.
 */
export async function mockMcpEndpoints(page: Page) {
  if (LIVE) return
  await page.route('**/api/mcp/servers', (route) =>
    json(route, { servers: MOCK_DATA.mcpServers }),
  )
  await page.route('**/api/mcp/clients', (route) =>
    json(route, { clients: MOCK_DATA.mcpClients }),
  )
  await page.route('**/api/mcp/catalog', (route) =>
    json(route, { entries: MOCK_DATA.mcpCatalog }),
  )
  await page.route('**/api/mcp/servers/*', (route) => {
    const url = route.request().url()
    const id = url.split('/').pop()!.split('?')[0]
    const found = MOCK_DATA.mcpServers.find((s) => s.id === id)
    return json(route, found || {}, found ? 200 : 404)
  })
}

/**
 * Pre-route `/v1/stats` (Lemonade-native, consumed by PR-12 #179 server
 * side; mirrored by useMock.js client side). Specs that exercise the
 * lemonade store's stats polling should call this after default mocks.
 */
export async function mockV1Stats(page: Page) {
  if (LIVE) return
  await page.route('**/v1/stats', (route) => json(route, MOCK_DATA.v1Stats))
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
