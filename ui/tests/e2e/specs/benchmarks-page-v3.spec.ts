/**
 * benchmarks-page-v3 — Playwright coverage for the Benchmarks page style
 * refresh (hal0.dev roster idiom: decode-speed bucket meter, capability
 * glyph legend, segmented outcome filter, /history-backed run drawer with a
 * regression-dip sparkline and a resolved-flags copy button).
 *
 * Routes: GET /api/benchmarks/roster|regressions|runs|runs/{id}|history
 * fulfilled directly via page.route (the default apiMock catch-all answers
 * everything else under /api/ with {}).
 */

import { test, expect, json } from '../fixtures/apiMock'

const ROSTER = {
  schema: 1,
  host: { gpu: 'Radeon 8060S', mem_gb: 128, hal0: '1.0.0-rc.3' },
  models: [
    {
      id: 'qwen3.6-35b-a3b',
      gguf: '/mnt/ai-models/qwen3.6-35b-a3b-q4_k_m.gguf',
      name: 'Qwen3.6 35B A3B',
      hf_repo: 'Qwen/Qwen3.6-35B-A3B',
      decode_ts: 71.4,
      prefill_ts: 812.3,
      accept: 0.82,
      caps: ['tools', 'coding'],
      spec: 'draft-mtp',
      kv: 'q8_0',
      size_gb: 21.3,
      detail: {
        run_id: '2026-08-01T10:00:00Z-abc123', measured: '2026-08-01',
        lane: 'rocm', image: 'ghcr.io/hal0ai/toolboxes:rocm-7.2.4',
        llamacpp_build: 'b9219-1faa48eef', depth: 2048, sampler: 'greedy',
        reps: 5, stddev: 1.2, ttft_ms_p50: 220,
      },
      runs: 12,
      last_run: '2026-08-01',
      measured: true,
    },
    {
      id: 'llama-3.1-8b',
      gguf: '/mnt/ai-models/llama-3.1-8b-q4_k_m.gguf',
      name: 'Llama 3.1 8B',
      hf_repo: null,
      decode_ts: 18.6,
      prefill_ts: 210.0,
      accept: null,
      caps: ['chat'],
      spec: null,
      kv: 'f16',
      size_gb: 4.9,
      detail: { run_id: '2026-07-20T09:00:00Z-def456', lane: 'vulkan_radv', depth: 2048, reps: 3 },
      runs: 4,
      last_run: '2026-07-20',
      measured: true,
    },
  ],
}

const REGRESSIONS = {
  count: 1,
  flags: [
    {
      cell_key: 'qwen3.6-35b-a3b|rocm|tg|2048|default',
      model_id: 'qwen3.6-35b-a3b',
      delta_pct: -22.5,
      newest_ts: '2026-08-01T10:00:00Z',
      trailing_median: 92.1,
      run_ids: ['2026-08-01T10:00:00Z-abc123'],
    },
  ],
}

const RUN_SUMMARY = {
  run_id: '2026-08-01T10:00:00Z-abc123',
  suite: 'roster',
  trigger: 'scheduled',
  model: 'qwen3.6-35b-a3b',
  lane: 'rocm',
  kind: 'tg',
  depth: 2048,
  outcome: 'ok',
  decode_ts_med: 71.4,
  reps: 5,
  config: 'default',
}

const RUNS = { count: 1, outcomes: { ok: 1 }, runs: [RUN_SUMMARY] }

const RUN_RECORD = {
  run_id: RUN_SUMMARY.run_id,
  suite: 'roster',
  trigger: 'scheduled',
  outcome: 'ok',
  config: 'default',
  cell_key: 'qwen3.6-35b-a3b|rocm|tg|2048|default',
  identity: {
    lane: 'rocm',
    model: { id: 'qwen3.6-35b-a3b', sha256: 'a1b2c3d4e5f6a7b8c9d0e1f2' },
    engine: { kind: 'llama-server', image: 'ghcr.io/hal0ai/toolboxes:rocm-7.2.4', llamacpp_build: 'b9219-1faa48eef' },
    config: { argv: ['llama-server', '-m', '/mnt/ai-models/qwen3.6-35b-a3b-q4_k_m.gguf', '-ngl', '99', '-c', '2048', '-fa', '1'], ctx: 2048, parallel: 1 },
    workload: { depth: 2048, sampler: { mode: 'greedy' } },
  },
  host: { name: 'hal0-105', platform: 'Proxmox LXC', gpu: 'Radeon 8060S', kernel: '7.0.6', hal0_version: '1.0.0-rc.3' },
  summary: { decode_ts_med: 71.4, decode_ts_stddev: 1.2, prefill_ts_med: 812.3, ttft_ms_p50: 220, ttft_ms_p95: 260, accept_med: 0.82 },
  telemetry: {},
  reps: [
    { decode_ts: 70.9, prefill_ts: 800.1, ttft_ms: 218, accept_rate: 0.81, t_s: 12.3 },
  ],
  artifacts_files: [],
}

const HISTORY = {
  cell_key: RUN_RECORD.cell_key,
  points: [
    { ts: '2026-07-20T10:00:00Z', decode_ts_med: 91.0, prefill_ts_med: 800 },
    { ts: '2026-07-27T10:00:00Z', decode_ts_med: 92.1, prefill_ts_med: 805 },
    { ts: '2026-08-01T10:00:00Z', decode_ts_med: 71.4, prefill_ts_med: 812.3 }, // >10% dip
  ],
}

// 7 raw run records for the model-detail accordion, MIXED across both lanes
// and newer than 2 minutes apart (so each is its own sweep) — one more
// record than the backend would ever return under a correct limit=5
// request, so the display's defensive slice(0, 5) is actually exercised,
// AND enough of a lane mix to prove the single-lane run-list filter works.
// Sorted newest-first (matches the real API): the latest 5 are rocm@76/75/74
// and vulkan_radv@61/60; the two oldest (vulkan_radv@59, rocm@71.4) are
// dropped by the cap.
const MODEL_RUNS_QWEN = [
  { run_id: '2026-08-07T09:00:00Z-run0', lane: 'rocm', decode_ts_med: 76 },
  { run_id: '2026-08-06T09:00:00Z-run1', lane: 'vulkan_radv', decode_ts_med: 61 },
  { run_id: '2026-08-05T09:00:00Z-run2', lane: 'rocm', decode_ts_med: 75 },
  { run_id: '2026-08-04T09:00:00Z-run3', lane: 'vulkan_radv', decode_ts_med: 60 },
  { run_id: '2026-08-03T09:00:00Z-run4', lane: 'rocm', decode_ts_med: 74 },
  { run_id: '2026-08-02T09:00:00Z-run5', lane: 'vulkan_radv', decode_ts_med: 59 },
  { run_id: '2026-08-01T09:00:00Z-run6', lane: 'rocm', decode_ts_med: 71.4 },
].map((r) => ({
  suite: 'roster', trigger: 'scheduled', model: 'qwen3.6-35b-a3b',
  kind: 'tg', depth: 2048, outcome: 'ok', reps: 5, config: 'default', ...r,
}))

const MODEL_RUNS_LLAMA = [
  { run_id: '2026-07-20T09:00:00Z-def456', lane: 'vulkan_radv', decode_ts_med: 18.6 },
].map((r) => ({
  suite: 'roster', trigger: 'scheduled', model: 'llama-3.1-8b',
  kind: 'tg', depth: 2048, outcome: 'ok', reps: 3, config: 'default', ...r,
}))

// Per-lane default cells — one per (model, lane), config='default', the
// basis for the lane segmented control, the paired stat cards, and the
// per-lane /history?cell_key= fetch.
const QWEN_ROCM_CELL_KEY = 'qwen3.6-35b-a3b|rocm|tg|2048|default'
const QWEN_VULKAN_CELL_KEY = 'qwen3.6-35b-a3b|vulkan_radv|tg|2048|default'
const LLAMA_VULKAN_CELL_KEY = 'llama-3.1-8b|vulkan_radv|tg|2048|default'

const CELLS_QWEN = [
  {
    lane: 'rocm', depth: 2048, kind: 'tg', cell_key: QWEN_ROCM_CELL_KEY, config: 'default', decode_ts_med: 71.4,
    record: { config: 'default', summary: { decode_ts_med: 71.4, prefill_ts_med: 812.3, accept_med: 0.82, ttft_ms_p50: 220, decode_ts_stddev: 1.2 }, reps: [1, 2, 3, 4, 5] },
  },
  {
    lane: 'vulkan_radv', depth: 2048, kind: 'tg', cell_key: QWEN_VULKAN_CELL_KEY, config: 'default', decode_ts_med: 60.0,
    record: { config: 'default', summary: { decode_ts_med: 60.0, prefill_ts_med: 700.0, accept_med: 0.75, ttft_ms_p50: 260, decode_ts_stddev: 0.9 }, reps: [1, 2, 3] },
  },
]

const CELLS_LLAMA = [
  {
    lane: 'vulkan_radv', depth: 2048, kind: 'tg', cell_key: LLAMA_VULKAN_CELL_KEY, config: 'default', decode_ts_med: 18.6,
    record: { config: 'default', summary: { decode_ts_med: 18.6, prefill_ts_med: 210.0, accept_med: null, ttft_ms_p50: null }, reps: [1, 2, 3] },
  },
]

// Per-lane history series — deliberately DIFFERENT arrays per cell_key, never
// pooled, so a compare-mode test can assert two distinct series render.
const HISTORY_BY_CELL: Record<string, { points: any[] }> = {
  [QWEN_ROCM_CELL_KEY]: HISTORY, // reuse the existing dip-series fixture (used by the run-drawer test too)
  [QWEN_VULKAN_CELL_KEY]: {
    points: [
      { ts: '2026-07-20T10:00:00Z', decode_ts_med: 58.0, prefill_ts_med: 690 },
      { ts: '2026-07-27T10:00:00Z', decode_ts_med: 59.1, prefill_ts_med: 695 },
      { ts: '2026-08-04T10:00:00Z', decode_ts_med: 60.0, prefill_ts_med: 700.0 },
    ],
  },
  [LLAMA_VULKAN_CELL_KEY]: {
    points: [
      { ts: '2026-07-13T10:00:00Z', decode_ts_med: 17.0 },
      { ts: '2026-07-20T10:00:00Z', decode_ts_med: 18.6 },
    ],
  },
}

async function installBenchRoutes(page: any) {
  await page.route('**/api/benchmarks/roster', (route: any) => json(route, ROSTER))
  await page.route('**/api/benchmarks/regressions', (route: any) => json(route, REGRESSIONS))
  // Model-detail accordion fetch (?model=...&limit=5) vs the Runs tab's own
  // fetch (?limit=200, no model) need different fixtures — route on the URL.
  await page.route('**/api/benchmarks/runs?**', (route: any) => {
    const url = new URL(route.request().url())
    const model = url.searchParams.get('model')
    if (model === 'qwen3.6-35b-a3b') {
      return json(route, { count: MODEL_RUNS_QWEN.length, outcomes: { ok: MODEL_RUNS_QWEN.length }, runs: MODEL_RUNS_QWEN })
    }
    if (model === 'llama-3.1-8b') {
      return json(route, { count: MODEL_RUNS_LLAMA.length, outcomes: { ok: MODEL_RUNS_LLAMA.length }, runs: MODEL_RUNS_LLAMA })
    }
    return json(route, RUNS)
  })
  await page.route('**/api/benchmarks/cells?**', (route: any) => {
    const url = new URL(route.request().url())
    const model = url.searchParams.get('model')
    if (model === 'qwen3.6-35b-a3b') return json(route, { count: CELLS_QWEN.length, cells: CELLS_QWEN })
    if (model === 'llama-3.1-8b') return json(route, { count: CELLS_LLAMA.length, cells: CELLS_LLAMA })
    return json(route, { count: 0, cells: [] })
  })
  await page.route(`**/api/benchmarks/runs/${encodeURIComponent(RUN_SUMMARY.run_id)}`, (route: any) => json(route, RUN_RECORD))
  await page.route('**/api/benchmarks/history?**', (route: any) => {
    const url = new URL(route.request().url())
    const cellKey = url.searchParams.get('cell_key')
    if (cellKey && HISTORY_BY_CELL[cellKey]) return json(route, { cell_key: cellKey, points: HISTORY_BY_CELL[cellKey].points })
    if (cellKey) return json(route, { cell_key: cellKey, points: [] })
    return json(route, HISTORY) // pooled ?model= fallback (used only by the no-lane-data path)
  })
  await page.route('**/api/benchmarks/queue', (route: any) => json(route, { control: { state: 'stopped', exclusive: true }, active: null, updated: null, items: [] }))
}

test.beforeEach(async ({ page }) => {
  await installBenchRoutes(page)
})

test('roster tab renders the decode-speed bucket legend and meter', async ({ page }) => {
  await page.goto('/#benchmarks')
  await expect(page.locator('[data-testid="benchmarks-view"]')).toBeVisible()

  // Legend wording — fast/mid/slow thresholds, matches the hal0.dev roster idiom.
  await expect(page.getByText('≥60 t/s')).toBeVisible()
  await expect(page.getByText('25–60')).toBeVisible()
  await expect(page.getByText('<25')).toBeVisible()

  // Fast model shows the winning-bucket number (never colour-only — text is present).
  const fastRow = page.locator('[data-testid="bench-model-row-qwen3.6-35b-a3b"]')
  await expect(fastRow).toBeVisible()
  await expect(fastRow.getByText('71.4')).toBeVisible()

  const slowRow = page.locator('[data-testid="bench-model-row-llama-3.1-8b"]')
  await expect(slowRow.getByText('18.6')).toBeVisible()
})

test('roster row expands and flags the regressed model', async ({ page }) => {
  await page.goto('/#benchmarks')
  const row = page.locator('[data-testid="bench-model-row-qwen3.6-35b-a3b"]')
  await expect(row).toBeVisible()
  // Regression badge from /regressions — real flag, not synthetic.
  await expect(row.getByText('22.5%')).toBeVisible()
  await row.click()
  await expect(page.getByText('current summary')).toBeVisible()
})

test('runs tab: outcome segmented filter narrows the table', async ({ page }) => {
  await page.goto('/#benchmarks')
  await page.getByRole('tab', { name: 'Runs' }).click()
  await expect(page.locator('[data-testid="bench-run-row-2026-08-01T10:00:00Z-abc123"]')).toBeVisible()

  const okBtn = page.locator('button[title="filter: outcome = ok"]')
  await expect(okBtn).toBeVisible()
  await okBtn.click()
  await expect(okBtn).toHaveClass(/on/)
  await expect(page.locator('[data-testid="bench-run-row-2026-08-01T10:00:00Z-abc123"]')).toBeVisible()
  // toggling back off
  await okBtn.click()
  await expect(okBtn).not.toHaveClass(/on/)
})

test('run drawer opens with real fixture data: identity, resolved flags + copy, summary', async ({ page }) => {
  await page.goto('/#benchmarks')
  await page.getByRole('tab', { name: 'Runs' }).click()
  const row = page.locator('[data-testid="bench-run-row-2026-08-01T10:00:00Z-abc123"]')
  await expect(row).toBeVisible()
  await row.click()

  await expect(page.getByText('identity — qwen3.6-35b-a3b')).toBeVisible()
  // The record's real argv, rendered verbatim — not a synthesized ARGV() template.
  await expect(page.getByText(/llama-server -m \/mnt\/ai-models\/qwen3\.6-35b-a3b-q4_k_m\.gguf/)).toBeVisible()
  await expect(page.getByRole('button', { name: 'copy' })).toBeVisible()

  // Summary tiles from the real record.
  await expect(page.getByText('decode med')).toBeVisible()
  // Telemetry section must NOT render — the fixture's telemetry is empty (unsupported today).
  // (the raw-JSON <details> block legitimately contains the word "telemetry" as JSON text,
  // so this asserts no telemetry *heading* renders rather than a page-wide text search.)
  await expect(page.locator('h4', { hasText: 'telemetry' })).toHaveCount(0)
  // No upload/provenance footer — unsupported, must not appear.
  await expect(page.getByText(/download bundle|uploaded by|link to this run/i)).toHaveCount(0)
})

test('run drawer sparkline renders from the mocked /history payload with a regression dip', async ({ page }) => {
  await page.goto('/#benchmarks')
  await page.getByRole('tab', { name: 'Runs' }).click()
  const row = page.locator('[data-testid="bench-run-row-2026-08-01T10:00:00Z-abc123"]')
  await row.click()

  await expect(page.getByText(/history · decode t\/s · 3 pts for this cell/)).toBeVisible()
  const spark = page.locator('svg').filter({ has: page.locator('circle') }).last()
  await expect(spark).toBeVisible()
  // The dip-highlighted stroke (the 08-01 point drops >10% off the trailing median).
  await expect(page.getByText(/regression flagged for this cell/)).toBeVisible()
})

test('roster columns are clickable sort headers with a direction indicator', async ({ page }) => {
  await page.goto('/#benchmarks')
  await expect(page.locator('[data-testid="benchmarks-view"]')).toBeVisible()

  const rowOrder = () => page.locator('tbody tr[data-testid^="bench-model-row-"]').evaluateAll(
    (rows) => rows.map((r) => r.getAttribute('data-testid'))
  )

  // Natural (unsorted) order: fixture order — qwen (71.4 t/s) before llama (18.6 t/s).
  await expect.poll(rowOrder).toEqual([
    'bench-model-row-qwen3.6-35b-a3b',
    'bench-model-row-llama-3.1-8b',
  ])

  const decodeHeader = page.locator('th[title="sort by decode"]')
  await expect(decodeHeader).toBeVisible()

  // First click: ascending — slower model (llama, 18.6) first.
  await decodeHeader.click()
  await expect(decodeHeader).toHaveAttribute('aria-sort', 'ascending')
  await expect(decodeHeader).toContainText('▲')
  await expect.poll(rowOrder).toEqual([
    'bench-model-row-llama-3.1-8b',
    'bench-model-row-qwen3.6-35b-a3b',
  ])

  // Second click on the same header: descending — faster model (qwen, 71.4) first.
  await decodeHeader.click()
  await expect(decodeHeader).toHaveAttribute('aria-sort', 'descending')
  await expect(decodeHeader).toContainText('▼')
  await expect.poll(rowOrder).toEqual([
    'bench-model-row-qwen3.6-35b-a3b',
    'bench-model-row-llama-3.1-8b',
  ])

  // A different header takes over the active-sort indicator.
  const modelHeader = page.locator('th[title="sort by model"]')
  await modelHeader.click()
  await expect(modelHeader).toHaveAttribute('aria-sort', 'ascending')
  await expect(decodeHeader).toHaveAttribute('aria-sort', 'none')
})

test('expanded accordion run history is capped to the latest 5 runs', async ({ page }) => {
  await page.goto('/#benchmarks')
  const row = page.locator('[data-testid="bench-model-row-qwen3.6-35b-a3b"]')
  await expect(row).toBeVisible()

  const runsRequest = page.waitForRequest((req: any) =>
    req.url().includes('/api/benchmarks/runs') && req.url().includes('model=')
  )
  await row.click()
  const req = await runsRequest
  // Frontend requests exactly the latest-5 page from the backend...
  expect(new URL(req.url()).searchParams.get('limit')).toBe('5')

  // ...and even though the mocked fixture hands back 7 records (MODEL_RUNS),
  // the accordion's run-history chips never exceed 5 — the defensive
  // client-side cap holds regardless of what the route returns.
  await expect(page.getByText(/runs — \d+ sweep/)).toBeVisible()
  const chipCount = await page.locator('text=/^\\d{4}-\\d{2}-\\d{2} /').count()
  expect(chipCount).toBeLessThanOrEqual(5)
})

test('a two-lane model defaults to Compare and renders two distinct decode series', async ({ page }) => {
  await page.goto('/#benchmarks')
  await page.locator('[data-testid="bench-model-row-qwen3.6-35b-a3b"]').click()
  await expect(page.getByText('current summary')).toBeVisible()

  const seg = page.locator('.mtp-seg')
  await expect(seg).toBeVisible()
  await expect(seg.locator('.mtp-seg-btn.on')).toHaveText('Compare')
  await expect(page.getByText('decode history · compare')).toBeVisible()

  // Two series in the compare chart: rocm solid (no dasharray), vulkan_radv
  // dashed — colour is never the only differentiator.
  const chartSvg = page.locator('[data-testid="bench-compare-sparkline"]')
  await expect(chartSvg).toBeVisible()
  await expect(chartSvg.locator('path[stroke-dasharray]')).toHaveCount(1)
  await expect(chartSvg.locator('path:not([stroke-dasharray])')).toHaveCount(1)

  // Inline legend names both lanes with their own point counts (never pooled).
  await expect(page.getByText(/ROCM.*3 pts/)).toBeVisible()
  await expect(page.getByText(/VULK.*3 pts/)).toBeVisible()
})

test('compare-mode delta badge is computed from the two lanes\' decode median', async ({ page }) => {
  await page.goto('/#benchmarks')
  await page.locator('[data-testid="bench-model-row-qwen3.6-35b-a3b"]').click()
  await expect(page.getByText('current summary')).toBeVisible()

  // rocm decode 71.4 (baseline) vs vulkan_radv decode 60.0 => (60-71.4)/71.4*100 ≈ -16.0%
  await expect(page.getByText('-16.0%')).toBeVisible()
})

test('lane segment filters the run list to that lane (interleaved order in Compare)', async ({ page }) => {
  await page.goto('/#benchmarks')
  await page.locator('[data-testid="bench-model-row-qwen3.6-35b-a3b"]').click()
  await expect(page.getByText('current summary')).toBeVisible()

  // Compare (default): interleaved, latest-5-capped => 5 distinct sweeps (3 rocm + 2 vulkan_radv).
  await expect(page.getByText('runs — 5 sweeps')).toBeVisible()

  await page.locator('.mtp-seg-btn', { hasText: 'ROCM' }).click()
  await expect(page.getByText('runs — 3 sweeps')).toBeVisible()

  await page.locator('.mtp-seg-btn', { hasText: 'VULK' }).click()
  await expect(page.getByText('runs — 2 sweeps')).toBeVisible()
})

test('a single-lane model hides the empty segment and skips Compare', async ({ page }) => {
  await page.goto('/#benchmarks')
  await page.locator('[data-testid="bench-model-row-llama-3.1-8b"]').click()
  await expect(page.getByText('current summary')).toBeVisible()

  // Only vulkan_radv has data for this model — no segmented control at all
  // (nothing to toggle), and today's single-lane presentation renders directly.
  await expect(page.locator('.mtp-seg')).toHaveCount(0)
  await expect(page.getByText('throughput history')).toBeVisible()
  await expect(page.getByText('decode history · compare')).toHaveCount(0)
})
