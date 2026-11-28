/**
 * Showcase overlay: the engines-and-providers story.
 *
 * The image engine is up and idle: ComfyUI answers on :8188, the GPU arbiter
 * holds image mode, four checkpoints are installed (FLUX.1-schnell, SDXL base
 * 1.0, SD 3.5 medium, Juggernaut XL) and the files tree holds their weights
 * plus the VAE, LoRAs and upscaler the renders use. Two providers are
 * connected — OpenRouter with six models switched on, and a local
 * OpenAI-compatible endpoint on 127.0.0.1:1234 serving two models — and the
 * preset catalogue behind the "Add provider" tiles is the real one. The NPU
 * lane has the FLM catalogue with three of its eight models on disk.
 * Benchmarks show a roster of four measured models, three completed runs and
 * one queued run. Two stacks exist ("daily-driver" applied, "coding-night"
 * parked), three MCP servers are healthy, every managed component is current
 * and no slot has drifted.
 *
 * Everything here is invented for the screenshots; nothing is read from a real
 * box. The only performance figures used are the published ones: the decode /
 * prefill tok/s and the memory of the four roster models come from the
 * hal0.dev leaderboard, and no intermediate measurement is invented — a
 * model's history is flat at its measured value. The in-bundle mock
 * (src/api/mock.ts) answers allowlisted GETs before page.route is consulted,
 * so `/api/journal` is opted out of it through `window.__hal0MockPassthrough`
 * — hence ENGINES_PASSTHROUGH. Every payload shape is copied from the hook
 * type, the backend route or the e2e spec; each one is cited below.
 */
import type { Page } from '@playwright/test'

import { json, VERSION } from './core'

/** Paths answered here rather than by the in-bundle mock. */
export const ENGINES_PASSTHROUGH: string[] = ['/api/journal']

const MINUTE = 60
const HOUR = 3600
const DAY = 86_400

/** ISO-8601, `secondsAgo` before the given instant. */
function iso(nowMs: number, secondsAgo = 0): string {
  return new Date(nowMs - secondsAgo * 1000).toISOString()
}

/** "YYYY-MM-DDTHH:MM:SS" — the run-id format the Runs tab slices for date/time. */
function stamp(nowMs: number, secondsAgo = 0): string {
  return iso(nowMs, secondsAgo).replace(/\.\d{3}Z$/, '')
}

/** Epoch seconds, `secondsAgo` before the given instant. */
function epoch(nowMs: number, secondsAgo = 0): number {
  return Math.floor(nowMs / 1000) - secondsAgo
}

/** The four roster models, with the only published figures there are. */
const BENCH_FIGURES = {
  'qwen3.6-35b-a3b-crown': { decode: 84.4, prefill: 873, gb: 22.6 },
  'qwen3-coder-reap-25b-a3b': { decode: 54.7, prefill: 1368, gb: 17.7 },
  'gemma-4-26b-a4b': { decode: 40.9, prefill: 1336, gb: 17.1 },
  'qwen3.5-0.8b': { decode: 169.8, prefill: 6248, gb: 0.6 },
} as const

/** The OpenRouter rows the provider has switched on. Ids are the real ones. */
const OPENROUTER_MODELS = [
  'anthropic/claude-sonnet-5-5',
  'openai/gpt-5-mini',
  'google/gemini-3.8-flash',
  'mistralai/mistral-large-latest',
  'meta-llama/llama-3.3-70b-versatile',
  'deepseek/deepseek-v4-pro',
]

/** The two models the local endpoint serves. */
const LOCAL_MODELS = ['qwen3.5-9b', 'hermes-4-14b']

/** One installed image-model catalogue row. */
function installedModel(
  id: string,
  label: string,
  gb: number,
  seconds: number,
  license: string,
  steps: number,
  cfg: number,
  gpuGb: number,
  ramGb: number,
  sizes: string[],
  notes: string,
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    label,
    task: 'txt2img',
    state: 'complete',
    api_renderable: true,
    recommended: false,
    license,
    est_seconds: seconds,
    sizes,
    notes,
    defaults: { steps, cfg, sampler_name: 'euler', scheduler: 'simple' },
    approx_gb: gb,
    missing_gb: 0,
    missing: [],
    workflow: `${id}.json`,
    job: null,
    fit: {
      verdict: 'fits',
      reason: null,
      need: { gpu_gb: gpuGb, ram_gb: ramGb, gpu_estimated: false, ram_estimated: false },
      render_free_gb: 112.1,
      ram_free_gb: 111.6,
    },
    ...extra,
  }
}

/** One preset row of the provider catalogue. */
function preset(
  id: string,
  name: string,
  category: string,
  baseUrl: string,
  auth: string,
  docsUrl: string,
  notes: string,
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    name,
    category,
    base_url: baseUrl,
    auth,
    auth_header_name: auth === 'bearer' ? 'Authorization' : '',
    models_path: '/models',
    default_models: [] as string[],
    default_model: '',
    capabilities: ['chat'],
    docs_url: docsUrl,
    notes,
    tools: false,
    quirks: [] as string[],
    rate_limit_headers: {} as Record<string, string>,
    models: [] as unknown[],
    ...extra,
  }
}

/** One provider's model row. */
function modelRow(id: string, provider: string) {
  return {
    id,
    canonical_id: `${provider}/${id}`,
    enabled: true,
    type: 'chat',
    context: null,
    price_in: null,
    price_out: null,
    price_unit: 'Mtok',
    discovered: true,
    recommended: false,
    in_use_by: [] as string[],
  }
}

/** One bundled MCP server. */
function mcpServer(id: string, tools: number, rpm: number, connected: string[], details: unknown[]) {
  return {
    id,
    name: id,
    bundled: true,
    state: 'running',
    transport: 'streamable-http',
    connect_url: `http://127.0.0.1:8080/mcp/${id.replace('hal0-', '')}`,
    pid: null,
    version: '0.3.0',
    tools,
    tool_details: details,
    resources: 0,
    prompts: 0,
    activity: { rpm },
    connected,
    description: `hal0 bundled ${id} MCP server (FastMCP, streamable-http).`,
    provider: 'hal0',
  }
}

/** The journal the Logs page shows: 40 lines across four subsystems. */
const JOURNAL: Array<[number, string, string, string | null, string]> = [
  [40 * MINUTE, 'hal0-api', 'info', null, 'api startup complete — 10 slot(s) registered'],
  [39 * MINUTE, 'hal0-api', 'info', null, 'config reloaded: 6 component(s) converged'],
  [35 * MINUTE, 'hal0-api', 'info', null, 'updater check: no release newer than 1.8.2'],
  [30 * MINUTE, 'hal0-api', 'info', null, 'slot registry converged: 10 slot(s) in sync'],
  [25 * MINUTE, 'hal0-api', 'info', null, 'request metrics collector attached to 4 slot(s)'],
  [20 * MINUTE, 'hal0-api', 'info', null, 'memory engine handshake ok (hindsight 1.2.3)'],
  [15 * MINUTE, 'hal0-api', 'info', null, 'bridge client "claude-code" seen at the admin scope'],
  [12 * MINUTE, 'hal0-api', 'info', null, 'agent registry refreshed: 2 agent(s) installed'],
  [8 * MINUTE, 'hal0-api', 'info', null, 'openwebui service answered /health in 41 ms'],
  [5 * MINUTE, 'hal0-api', 'info', null, 'update check finished — channel stable'],
  [2 * MINUTE, 'hal0-api', 'info', null, 'doctor run finished: 0 finding(s)'],
  [45, 'hal0-api', 'info', null, 'served 412 request(s) in the last minute'],
  [8, 'hal0-api', 'info', null, 'api heartbeat — idle'],
  [40 * MINUTE, 'hal0-slot@primary', 'info', 'primary', 'slot primary: container started (rocm lane)'],
  [39 * MINUTE, 'hal0-slot@primary', 'info', 'primary', 'model qwen3.6-35b-a3b-crown loaded in 41200 ms'],
  [38 * MINUTE, 'hal0-slot@primary', 'info', 'primary', 'context 65536 · kv q8_0 · n_gpu_layers -1'],
  [36 * MINUTE, 'hal0-slot@primary', 'info', 'primary', 'slot primary: serving on 127.0.0.1:8092'],
  [30 * MINUTE, 'hal0-slot@primary', 'info', 'primary', 'warmup request completed in 610 ms'],
  [25 * MINUTE, 'hal0-slot@primary', 'info', 'primary', 'POST /v1/chat/completions 200'],
  [15 * MINUTE, 'hal0-slot@primary', 'info', 'primary', 'slot primary: 2 request(s) in flight'],
  [5 * MINUTE, 'hal0-slot@primary', 'warn', 'primary', 'slot primary: kv cache trimmed to 32 k'],
  [20, 'hal0-slot@primary', 'info', 'primary', 'slot primary: idle, 0 request(s) in flight'],
  [39 * MINUTE, 'hal0-slot@coder', 'info', 'coder', 'slot coder: container started (rocm lane)'],
  [38 * MINUTE, 'hal0-slot@coder', 'info', 'coder', 'model qwen3-coder-reap-25b-a3b loaded in 38900 ms'],
  [37 * MINUTE, 'hal0-slot@coder', 'info', 'coder', 'context 131072 · kv q8_0 · n_gpu_layers -1'],
  [35 * MINUTE, 'hal0-slot@coder', 'info', 'coder', 'slot coder: serving on 127.0.0.1:8094'],
  [28 * MINUTE, 'hal0-slot@coder', 'info', 'coder', 'warmup request completed in 540 ms'],
  [23 * MINUTE, 'hal0-slot@coder', 'info', 'coder', 'POST /v1/chat/completions 200'],
  [14 * MINUTE, 'hal0-slot@coder', 'info', 'coder', 'slot coder: 1 request(s) in flight'],
  [4 * MINUTE, 'hal0-slot@coder', 'warn', 'coder', 'slot coder: context trimmed to 131072 for a long prompt'],
  [15, 'hal0-slot@coder', 'info', 'coder', 'slot coder: idle, 0 request(s) in flight'],
  [39 * MINUTE, 'hindsight-api', 'info', null, 'hindsight-api: bank "hal0:shared" opened'],
  [36 * MINUTE, 'hindsight-api', 'info', null, 'hindsight-api: embedder hs-embed-1 ready'],
  [33 * MINUTE, 'hindsight-api', 'info', null, 'hindsight-api: consolidation pass finished (0 failed)'],
  [27 * MINUTE, 'hindsight-api', 'info', null, 'hindsight-api: 902 fact(s) in bank "hal0:shared"'],
  [20 * MINUTE, 'hindsight-api', 'info', null, 'hindsight-api: reranker hs-rerank-1 ready'],
  [15 * MINUTE, 'hindsight-api', 'info', null, 'hindsight-api: retained 12 fact(s) from the last hour'],
  [10 * MINUTE, 'hindsight-api', 'warn', null, 'hindsight-api: knowledge refresh — 2 page(s) stale'],
  [5 * MINUTE, 'hindsight-api', 'info', null, 'hindsight-api: queue drained (0 waiting, 0 failed)'],
  [30, 'hindsight-api', 'info', null, 'hindsight-api: heartbeat ok'],
]

/** One clock for the whole run, so the backfill and the replayed stream carry
 *  identical rows and the Logs page de-duplicates them. */
const JOURNAL_NOW = Date.now()

/** The journal lines as JournalEntry rows, newest last. */
export const journalEntries = (now: number = JOURNAL_NOW) =>
  JOURNAL.map(([ago, source, level, slot, msg], i) => ({
    id: i + 1,
    ts: iso(now, ago).replace('Z', '000+00:00'), // the journal route's isoformat() shape
    source,
    level,
    msg,
    slot,
    data: { type: 'slot.state', source },
  }))

export async function installEngines(page: Page): Promise<void> {
  // ── /api/comfyui/status — the engine is up and idle; the GPU is in chat mode ──
  await page.route('**/api/comfyui/status', (route) =>
    json(route, {
      mode: 'inference',
      reachable: true,
      engine: 'running',
      container: { name: 'hal0-slot@img.service', state: 'running' },
      endpoint: 'http://127.0.0.1:8188',
      queue: { running: 0, pending: 0 },
      util: 0.06,
      temp: 47,
      clock: 1650,
      progress: null,
      inference: { hermes: false },
      inventory: { checkpoints: 4, diffusion: 0, loras: 2, vae: 1, controlnet: 0, upscale: 1, text_encoders: 0 },
      switchover: { active: false, target: null, error: null },
      arbiter: {
        mode: 'llm',
        pinned: false,
        saved_llm_slots: [],
        switching: false,
        image_jobs: 0,
        phase: null,
        restore_at: null,
        handoff_until: null,
        drain_in_flight: 0,
        unload_set: ['primary', 'coder', 'embed', 'rerank'],
        llm_waiters: { count: 0, oldest_s: null },
      },
      gate: { state: 'current', version_seen: VERSION },
      gate_refusal: null,
    }),
  )
  // shape: ComfyuiStatus (ui/src/api/hooks/useComfyui.ts:120) over the route's
  // body (src/hal0/api/routes/comfyui.py:653); arbiter block from
  // src/hal0/slots/arbiter.py; spec ui/tests/e2e/fixtures/apiMock.ts:156

  // ── /api/comfyui/arbiter — the same arbiter truth, on its own route ────
  await page.route('**/api/comfyui/arbiter', (route) =>
    json(route, {
      mode: 'inference',
      arbiter: {
        mode: 'llm',
        pinned: false,
        saved_llm_slots: [],
        switching: false,
        image_jobs: 0,
        phase: null,
        restore_at: null,
        handoff_until: null,
        drain_in_flight: 0,
        unload_set: ['primary', 'coder', 'embed', 'rerank'],
        llm_waiters: { count: 0, oldest_s: null },
      },
      switchover: { active: false, target: null, error: null },
      gate_refusal: null,
    }),
  )
  // shape: ArbiterEnvelope (ui/src/api/hooks/useComfyui.ts:92) over
  // GET /api/comfyui/arbiter (src/hal0/api/routes/comfyui.py:669)

  // ── /api/comfyui/models — the image-model catalogue, four installed ────
  await page.route('**/api/comfyui/models', (route) =>
    json(route, {
      models: [
        installedModel('flux1-schnell', 'FLUX.1-schnell', 16.9, 4, 'apache-2.0', 4, 1.0, 16.9, 17.4, ['1024x1024', '1536x864', '864x1536'], "Black Forest Labs' FLUX.1 schnell — the 4-step distilled build.", { recommended: true }),
        installedModel('sdxl-base-1.0', 'SDXL base 1.0', 6.9, 12, 'creativeml-openrail++', 30, 7.0, 6.9, 7.4, ['1024x1024', '1152x896', '896x1152'], 'Stability AI SDXL base 1.0 — the general-purpose checkpoint.'),
        installedModel('sd3.5-medium', 'SD 3.5 Medium', 5.4, 16, 'stabilityai-community-license', 28, 7.0, 5.4, 6.1, ['1024x1024', '1344x768', '768x1344'], 'Stability AI SD 3.5 medium — 2.5B rectified transformer.'),
        installedModel('juggernaut-xl', 'Juggernaut XL', 6.9, 14, 'creativeml-openrail++', 30, 7.0, 6.9, 7.4, ['1024x1024', '1152x896', '896x1152'], 'SDXL fine-tune tuned for photographic renders.'),
        { id: 'sdxl-lightning', label: 'SDXL Lightning (8-step)', task: 'txt2img', state: 'none', api_renderable: true, recommended: false, license: 'creativeml-openrail++', est_seconds: 10, sizes: ['1024x1024'], notes: 'SDXL base with the 8-step Lightning LoRA and the fp16-fix VAE.', defaults: { steps: 8, cfg: 1.0, sampler_name: 'euler', scheduler: 'simple' }, approx_gb: 7.0, missing_gb: 0.4, missing: ['loras/sdxl_lightning_8step_lora.safetensors'], workflow: 'sdxl-lightning.json', job: null, fit: null },
        { id: 'esrgan-4x', label: 'ESRGAN 4x Upscaler', task: 'upscale', state: 'none', api_renderable: false, recommended: false, license: 'bsd-3-clause', est_seconds: 6, sizes: [], notes: 'Editor-only upscale model — the ComfyUI editor runs it, not the API.', defaults: {}, approx_gb: 0.1, missing_gb: 0.1, missing: ['upscale_models/ESRGAN_4x.pth'], workflow: '', job: null, fit: null },
      ],
      engine: { available: true, reason: null },
      store: { free_gb: 402, path: '/var/lib/hal0/models/comfyui/models' },
      recommendation: { id: 'flux1-schnell', pool_gb: 116, limited_by: 'gpu' },
    }),
  )
  // shape: ComfyuiModelsResponse / ComfyuiCatalogModel
  // (ui/src/api/hooks/useComfyui.ts:449) over GET /api/comfyui/models
  // (src/hal0/api/routes/comfyui.py:1089) over the catalog
  // (src/hal0/comfyui/catalog.py:530)

  // ── /api/comfyui/files — the models tree the renders load from ─────────
  await page.route('**/api/comfyui/files', (route) =>
    json(route, {
      root: '/var/lib/hal0/models/comfyui/models',
      files: [
        { kind: 'checkpoint', subdir: 'checkpoints', relpath: 'flux1-schnell.safetensors', size_bytes: 16_900_000_000, models: ['flux1-schnell'], arch: null, api_id: null, symlink: false },
        { kind: 'checkpoint', subdir: 'checkpoints', relpath: 'sdxl_base_1.0.safetensors', size_bytes: 6_900_000_000, models: ['sdxl-base-1.0'], arch: 'sdxl', api_id: 'ckpt/sdxl_base_1.0', symlink: false },
        { kind: 'checkpoint', subdir: 'checkpoints', relpath: 'sd3.5_medium.safetensors', size_bytes: 5_400_000_000, models: ['sd3.5-medium'], arch: null, api_id: null, symlink: false },
        { kind: 'checkpoint', subdir: 'checkpoints', relpath: 'juggernaut_xl.safetensors', size_bytes: 6_900_000_000, models: ['juggernaut-xl'], arch: 'sdxl', api_id: 'ckpt/juggernaut_xl', symlink: false },
        { kind: 'vae', subdir: 'vae', relpath: 'ae.safetensors', size_bytes: 335_000_000, models: ['sdxl-base-1.0', 'juggernaut-xl'], arch: null, api_id: null, symlink: false },
        { kind: 'lora', subdir: 'loras', relpath: 'sdxl_lightning_8step_lora.safetensors', size_bytes: 400_000_000, models: [], arch: null, api_id: null, symlink: false },
        { kind: 'lora', subdir: 'loras', relpath: 'detail_slider.safetensors', size_bytes: 160_000_000, models: [], arch: null, api_id: null, symlink: false },
        { kind: 'upscaler', subdir: 'upscale_models', relpath: 'ESRGAN_4x.pth', size_bytes: 67_000_000, models: [], arch: null, api_id: null, symlink: false },
      ],
    }),
  )
  // shape: ComfyuiFilesResponse / ComfyuiFile (ui/src/api/hooks/useComfyui.ts:344)
  // over GET /api/comfyui/files (src/hal0/api/routes/comfyui.py:1404) over the
  // tree scan (src/hal0/comfyui/inventory.py:135)

  // ── /api/comfyui/jobs — no install running, the engine is idle ─────────
  await page.route('**/api/comfyui/jobs', (route) => json(route, { jobs: [] }))
  // shape: {jobs: ImageJob[]} (ui/src/api/hooks/useComfyui.ts:541) over
  // GET /api/comfyui/jobs (src/hal0/api/routes/comfyui.py:1378)

  // ── /api/comfyui/workflows — the launchable workflow templates ────────
  await page.route('**/api/comfyui/workflows', (route) =>
    json(route, {
      workflows: [
        { name: 'flux1-schnell', source: 'primary' },
        { name: 'sdxl-base-1.0', source: 'primary' },
        { name: 'sd3.5-medium', source: 'primary' },
        { name: 'juggernaut-xl', source: 'primary' },
        { name: 'portrait-detail', source: 'user' },
      ],
    }),
  )
  // shape: ComfyuiWorkflow (ui/src/api/hooks/useComfyui.ts:314) over
  // GET /api/comfyui/workflows (src/hal0/api/routes/comfyui.py:1528)

  // ── /api/slots/img/config — the img slot's TOML, read flat ────────────
  await page.route(/\/api\/slots\/img\/config(\?|$)/, (route) =>
    json(route, {
      id: 12,
      name: 'img',
      port: 8188,
      device: 'gpu-rocm',
      gpu_index: null,
      n_gpu_layers: -1,
      threads: 0,
      metrics: true,
      binary: 'comfyui',
      image_pin: null,
      provider: 'comfyui',
      runtime: 'container',
      profile: 'comfyui',
      type: 'image',
      default: true,
      served_by: null,
      output_sanity: null,
      system_prompt: null,
      model: { default: 'flux1-schnell' },
      server: { env: null },
      image: { default_size: '1024x1024', default_steps: 20 },
    }),
  )
  // shape: the slot TOML as a flat dict ([slot] hoisted, [model] and [image]
  // kept nested) — src/hal0/config/loader.py:475 over the schema
  // (src/hal0/config/schema.py:426); read by useSlotConfig
  // (ui/src/api/hooks/useSlots.ts:759) and the [image] defaults panel
  // (ui/src/dash/settings/pages/capabilities/ImagePanel.jsx:68)

  // ── /api/upstreams — two providers connected ───────────────────────────
  await page.route(/\/api\/upstreams(\?|$)/, (route) =>
    json(route, [
      {
        name: 'openrouter', kind: 'remote', url: 'https://openrouter.ai/api/v1',
        auth_style: 'bearer', auth_header: 'Authorization',
        // The env-var NAME the key lives under — the API never returns the value.
        auth_value_env: 'OPENROUTER_API_KEY', auth_configured: true, auth_key_present: true,
        timeout_seconds: 60, slot_name: null, warmup_strategy: 'none',
        advertise_models: true, enabled: true, model_filters: null,
        models: OPENROUTER_MODELS, managed_by: null, pinned: false, owned_models: null,
        hint: null, catalog_id: 'openrouter', curated: true,
        budget_monthly_usd: 50, enabled_count: 6, models_total: 6,
      },
      {
        name: 'lmstudio', kind: 'remote', url: 'http://127.0.0.1:1234/v1',
        auth_style: 'none', auth_header: '', auth_value_env: '',
        auth_configured: true, auth_key_present: false,
        timeout_seconds: 30, slot_name: null, warmup_strategy: 'none',
        advertise_models: true, enabled: true, model_filters: null,
        models: LOCAL_MODELS, managed_by: null, pinned: false, owned_models: null,
        hint: 'Local OpenAI-compatible endpoint; no key needed.',
        catalog_id: 'custom', curated: true,
        budget_monthly_usd: null, enabled_count: 2, models_total: 2,
      },
    ]),
  )
  // shape: UpstreamEntry[] (ui/src/api/hooks/useUpstreams.ts:20) over
  // GET /api/upstreams (src/hal0/api/routes/providers.py:255); spec
  // ui/tests/e2e/fixtures/apiMock.ts:166

  // ── /api/providers/catalog — the built-in preset catalogue ────────────
  await page.route(/\/api\/providers\/catalog(\?|$)/, (route) =>
    json(route, {
      openrouter: preset('openrouter', 'OpenRouter', 'cloud', 'https://openrouter.ai/api/v1', 'bearer', 'https://openrouter.ai/keys', 'OpenRouter aggregates many models behind one OpenAI-compatible endpoint. Model ids are discovered from GET /models.'),
      ollama: preset('ollama', 'Ollama', 'local', 'http://localhost:11434/v1', 'none', 'https://docs.ollama.com/api/openai-compatibility', 'Local Ollama server. The OpenAI-compatible surface ignores the API key.'),
      custom: preset('custom', 'Custom OpenAI-compatible', 'custom', '', 'bearer', '', 'Any OpenAI-compatible endpoint.'),
      anthropic: preset('anthropic', 'Anthropic', 'cloud', 'https://api.anthropic.com/v1', 'bearer', 'https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk', "Claude via Anthropic's OpenAI-compatible layer. Intended for testing and comparison; thinking output and prompt caching are not available here.", {
        tools: true,
        capabilities: ['chat', 'tools'],
        default_models: ['claude-sonnet-5-5', 'claude-opus-5-5', 'claude-fable-5-2', 'claude-haiku-5-5'],
        default_model: 'claude-sonnet-5-5',
        rate_limit_headers: { remaining_requests: 'x-ratelimit-remaining-requests', remaining_tokens: 'x-ratelimit-remaining-tokens', retry_after: 'retry-after' },
        models: [
          { id: 'claude-sonnet-5-5', type: 'chat', context: 1000000, price_in: 2.0, price_out: 10.0, price_unit: 'Mtok', recommended: true },
          { id: 'claude-opus-5-5', type: 'chat', context: 1000000, price_in: 4.0, price_out: 20.0, price_unit: 'Mtok', recommended: false },
          { id: 'claude-fable-5-2', type: 'chat', context: 1000000, price_in: 8.0, price_out: 40.0, price_unit: 'Mtok', recommended: false },
          { id: 'claude-haiku-5-5', type: 'chat', context: 1000000, price_in: null, price_out: null, price_unit: 'Mtok', recommended: false },
        ],
      }),
      openai: preset('openai', 'OpenAI', 'cloud', 'https://api.openai.com/v1', 'bearer', 'https://platform.openai.com/api-keys', 'Official OpenAI API.', {
        capabilities: ['chat', 'embed'],
        default_models: ['gpt-5', 'gpt-5-mini', 'gpt-4.1', 'gpt-4o-mini'],
        default_model: 'gpt-5-mini',
        models: [
          { id: 'gpt-5', type: 'chat', context: null, price_in: 1.25, price_out: 10.0, price_unit: 'Mtok', recommended: false },
          { id: 'gpt-5-mini', type: 'chat', context: null, price_in: 0.25, price_out: 2.0, price_unit: 'Mtok', recommended: true },
          { id: 'gpt-4.1', type: 'chat', context: null, price_in: 2.0, price_out: 8.0, price_unit: 'Mtok', recommended: false },
          { id: 'gpt-4o-mini', type: 'chat', context: null, price_in: 0.15, price_out: 0.6, price_unit: 'Mtok', recommended: false },
          { id: 'text-embedding-3-small', type: 'embed', context: null, price_in: 0.02, price_out: null, price_unit: 'Mtok', recommended: false },
        ],
      }),
    }),
  )
  // shape: Record<string, CatalogEntry> (ui/src/api/hooks/useUpstreams.ts:55)
  // over GET /api/providers/catalog (src/hal0/api/routes/providers.py:1037)
  // over the presets (src/hal0/upstreams/presets.py:200, the preset data in
  // src/hal0/config/data/provider_presets.toml)

  // ── /api/upstreams/<id>/models — one provider's model rows ────────────
  await page.route(/\/api\/upstreams\/([^/]+)\/models(\?|$)/, (route) => {
    const name = decodeURIComponent(new URL(route.request().url()).pathname.split('/')[3] || '')
    const now = Date.now()
    const local = name === 'lmstudio'
    return json(route, {
      name,
      curated: true,
      protected: false,
      fetched_at: epoch(now, local ? 4 * MINUTE : 11 * MINUTE),
      stale: false,
      warning: null,
      models: (local ? LOCAL_MODELS : OPENROUTER_MODELS).map((id) => modelRow(id, name)),
    })
  })
  // shape: UpstreamListing / UpstreamModelRow (ui/src/api/hooks/useUpstreams.ts:115)
  // over GET /api/upstreams/{name}/models (src/hal0/api/routes/providers.py:658)

  // ── /api/slots/flm/models — the FLM catalogue, three on disk ───────────
  await page.route(/\/api\/slots\/flm\/models(\?|$)/, (route) =>
    json(route, {
      models: [
        { model: 'gemma3:1b', installed: true, capabilities: ['chat'], family: 'gemma3', size_bytes: 1_000_000_000, in_use_by: [{ role: 'chat', slot: 'agent' }] },
        { model: 'embed-gemma:300m', installed: true, capabilities: ['embed'], family: 'embed-gemma', size_bytes: 350_000_000, in_use_by: [{ role: 'embed', slot: 'embed-npu' }] },
        { model: 'whisper-v3:turbo', installed: true, capabilities: ['stt'], family: 'whisper', size_bytes: 750_000_000, in_use_by: [{ role: 'stt', slot: 'stt-npu' }] },
        { model: 'gemma4-it:e4b', installed: false, capabilities: ['chat', 'stt'], family: 'gemma4', size_bytes: 5_100_000_000, in_use_by: [] },
        { model: 'gemma4-it:e2b', installed: false, capabilities: ['chat', 'stt'], family: 'gemma4', size_bytes: 2_600_000_000, in_use_by: [] },
        { model: 'qwen3.5:9b', installed: false, capabilities: ['chat'], family: 'qwen3.5', size_bytes: 6_400_000_000, in_use_by: [] },
        { model: 'qwen3:8b', installed: false, capabilities: ['chat'], family: 'qwen3', size_bytes: 5_200_000_000, in_use_by: [] },
        { model: 'llama3.2:1b', installed: false, capabilities: ['chat'], family: 'llama3.2', size_bytes: 1_300_000_000, in_use_by: [] },
      ],
      probe_ok: true,
    }),
  )
  // shape: FlmCatalog / FlmModel (ui/src/api/hooks/useFlmCatalog.ts:9) over
  // GET /api/slots/flm/models (src/hal0/api/routes/slots.py:97); the tags are
  // the ones the lane's picker uses (ui/src/dash/npu/npu-models-pure.test.ts:4)

  // ── /api/benchmarks/roster — four measured models ─────────────────────
  await page.route(/\/api\/benchmarks\/roster(\?|$)/, (route) => {
    const now = Date.now()
    const row = (
      id: keyof typeof BENCH_FIGURES,
      name: string,
      repo: string,
      gguf: string,
      caps: string[],
      accept: number | null,
      spec: string | null,
      ttft: number | null,
      runs: number,
    ) => {
      const f = BENCH_FIGURES[id]
      return {
        id,
        gguf,
        name,
        hf_repo: repo,
        decode_ts: f.decode,
        prefill_ts: f.prefill,
        accept,
        caps,
        spec,
        kv: 'q8_0',
        size_gb: f.gb,
        detail: {
          run_id: stamp(now, 6 * DAY),
          measured: stamp(now, 6 * DAY).slice(0, 10),
          lane: 'rocm',
          image: 'ghcr.io/hal0ai/hal0-runner-standard:b12633-r4f071ba',
          llamacpp_build: 'b12633',
          depth: 0,
          sampler: 'greedy',
          reps: 5,
          stddev: null,
          ttft_ms_p50: ttft,
        },
        runs,
        last_run: stamp(now, 6 * DAY).slice(0, 10),
        measured: true,
      }
    }
    return json(route, {
      schema: 1,
      host: { gpu: 'AMD Radeon 8060S', mem_gb: 128, hal0: VERSION },
      models: [
        row('qwen3.6-35b-a3b-crown', 'Qwen3.6-35B-A3B Crown MTP', 'jcbtc/qwen3.6-35b-a3b-crown-halo-mtp-dynamic', '/var/lib/hal0/models/qwen3.6-35b-a3b-crown.gguf', ['chat', 'tool-calling'], 0.909, 'mtp', 210, 4),
        row('qwen3-coder-reap-25b-a3b', 'Qwen3-Coder-REAP-25B-A3B', 'bartowski/cerebras_Qwen3-Coder-REAP-25B-A3B-GGUF', '/var/lib/hal0/models/qwen3-coder-reap-25b-a3b.gguf', ['chat', 'coder', 'tool-calling'], null, null, 160, 3),
        row('gemma-4-26b-a4b', 'gemma-4-26B-A4B-it', 'unsloth/gemma-4-26B-A4B-it-GGUF', '/var/lib/hal0/models/gemma-4-26b-a4b.gguf', ['chat', 'tool-calling', 'vision'], null, null, null, 2),
        row('qwen3.5-0.8b', 'Qwen3.5-0.8B', 'unsloth/Qwen3.5-0.8B-GGUF', '/var/lib/hal0/models/qwen3.5-0.8b.gguf', ['chat'], null, null, null, 1),
      ],
    })
  })
  // shape: RosterResponse / RosterModel (ui/src/dash/Benchmarks.tsx:27) over
  // GET /api/benchmarks/roster (src/hal0/api/routes/benchmarks.py:91); the
  // decode / prefill / size figures are the hal0.dev leaderboard's

  // ── /api/benchmarks/regressions — nothing flagged ─────────────────────
  await page.route(/\/api\/benchmarks\/regressions(\?|$)/, (route) =>
    json(route, { count: 0, flags: [] }),
  )
  // shape: {count, flags: RegressionFlag[]} (ui/src/dash/Benchmarks.tsx:460,
  // RegressionFlag at ui/src/dash/Benchmarks.tsx:75) over
  // GET /api/benchmarks/regressions (src/hal0/api/routes/benchmarks.py:273)

  // ── /api/benchmarks/queue — one run waiting for the worker ────────────
  await page.route(/\/api\/benchmarks\/queue(\?|$)/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const now = Date.now()
    return json(route, {
      control: { state: 'running', exclusive: true },
      active: null,
      updated: iso(now, 3),
      items: [{ id: 'q-2f9c41', label: 'gemma-4-26b-a4b', suite: 'roster', model: 'gemma-4-26b-a4b', enqueued: iso(now, 2 * MINUTE) }],
      failed: [],
    })
  })
  // shape: QueueState / QueueItem (ui/src/dash/Benchmarks.tsx:86) over
  // GET /api/benchmarks/queue (src/hal0/api/routes/benchmarks.py:406)

  // ── /api/benchmarks/runs — three completed sweeps ─────────────────────
  await page.route(/\/api\/benchmarks\/runs(\?|$)/, (route) => {
    const now = Date.now()
    const run = (id: string, model: string, decode: number, prefill: number, trigger: string) => ({
      run_id: id,
      suite: 'roster',
      trigger,
      model,
      lane: 'rocm',
      kind: 'tg',
      depth: 0,
      outcome: 'ok',
      decode_ts_med: decode,
      prefill_ts_med: prefill,
      reps: 5,
      config: 'default',
    })
    const runs = [
      run(stamp(now, 26 * HOUR), 'qwen3.6-35b-a3b-crown', 84.4, 873, 'scheduled'),
      run(stamp(now, 19 * HOUR), 'qwen3-coder-reap-25b-a3b', 54.7, 1368, 'manual'),
      run(stamp(now, 5 * HOUR), 'gemma-4-26b-a4b', 40.9, 1336, 'queue'),
    ]
    return json(route, { count: runs.length, outcomes: { ok: runs.length }, runs })
  })
  // shape: {runs: RunSummary[]} (ui/src/dash/Benchmarks.tsx:80) over
  // GET /api/benchmarks/runs (src/hal0/api/routes/benchmarks.py:316) over
  // _run_summary (src/hal0/api/routes/benchmarks.py:61)

  // ── /api/benchmarks/runs/{id} — one run's full record ─────────────────
  await page.route(/\/api\/benchmarks\/runs\/([^/?]+)(\?|$)/, (route) => {
    const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').pop() || '')
    const model = id.includes('coder') ? 'qwen3-coder-reap-25b-a3b' : 'qwen3.6-35b-a3b-crown'
    const f = BENCH_FIGURES[model as keyof typeof BENCH_FIGURES]
    return json(route, {
      run_id: id,
      suite: 'roster',
      trigger: 'scheduled',
      outcome: 'ok',
      config: 'default',
      cell_key: `rocm/tg/${model}/default`,
      identity: {
        lane: 'rocm',
        model: { id: model, sha256: '3f0c9a2b7d1e4c6f8a5b0d2e9c7f1b3a5d7e9c2f' },
        engine: { kind: 'llama-server', image: 'ghcr.io/hal0ai/hal0-runner-standard:b12633-r4f071ba', llamacpp_build: 'b12633' },
        config: { kv: { main_k: 4096, main_v: 4096 }, ctx: 65536, parallel: 1, spec: null },
        workload: { kind: 'tg', depth: 0, sampler: { mode: 'greedy' } },
      },
      host: { name: 'hal0', platform: 'bare-metal', gpu: 'AMD Radeon 8060S', kernel: 'Linux version 6.19.2-generic', hal0_version: VERSION, exclusive: true },
      summary: {
        decode_ts_med: f.decode,
        prefill_ts_med: f.prefill,
        accept_med: null,
        ttft_ms_p50: model.includes('coder') ? 160 : 210,
        ttft_ms_p95: null,
        decode_ts_stddev: null,
      },
      telemetry: { vram_peak_mb: null, gtt_peak_mb: null, gpu_edge_temp_max_c: null, gpu_power_avg_w: null },
      reps: [],
    })
  })
  // shape: the raw records.jsonl row (identity / host / summary / telemetry /
  // reps) that GET /api/benchmarks/runs/{run_id} returns
  // (src/hal0/api/routes/benchmarks.py:339); the drawer reads it at
  // ui/src/dash/Benchmarks.tsx:1478

  // ── /api/benchmarks/cells — the measured cells per model ──────────────
  await page.route(/\/api\/benchmarks\/cells(\?|$)/, (route) => {
    const model = new URL(route.request().url()).searchParams.get('model') || ''
    const f = BENCH_FIGURES[model as keyof typeof BENCH_FIGURES]
    if (!f) return json(route, { cells: [] })
    return json(route, {
      cells: [
        {
          lane: 'rocm',
          depth: 0,
          kind: 'tg',
          cell_key: `rocm/tg/${model}/default`,
          trigger: 'scheduled',
          config: 'default',
          run_id: stamp(Date.now(), 6 * DAY),
          decode_ts_med: f.decode,
          record: {
            summary: { decode_ts_med: f.decode, prefill_ts_med: f.prefill, accept_med: null, ttft_ms_p50: null, decode_ts_stddev: null },
            config: 'default',
            reps: [],
          },
        },
      ],
    })
  })
  // shape: {cells: CellRow[]} (ui/src/dash/Benchmarks.tsx:56) over
  // GET /api/benchmarks/cells (src/hal0/api/routes/benchmarks.py:246)

  // ── /api/benchmarks/history — the measured sweep per model ────────────
  await page.route(/\/api\/benchmarks\/history(\?|$)/, (route) => {
    const now = Date.now()
    const model = new URL(route.request().url()).searchParams.get('model') || ''
    const f = BENCH_FIGURES[model as keyof typeof BENCH_FIGURES]
    if (!f) return json(route, { points: [] })
    // Only the published figure is used, so a model's history is flat at its
    // measured value — no intermediate measurement is invented.
    const points = [26 * HOUR, 19 * HOUR, 6 * HOUR].map((h) => ({
      ts: iso(now, h),
      decode_ts_med: f.decode,
      prefill_ts_med: f.prefill,
      config: 'default',
      lane: 'rocm',
      depth: 0,
      trigger: 'scheduled',
      cell_key: `rocm/tg/${model}/default`,
    }))
    return json(route, { points })
  })
  // shape: {points: HistoryPoint[]} (ui/src/dash/Benchmarks.tsx:70) over
  // GET /api/benchmarks/history (src/hal0/api/routes/benchmarks.py:286)

  // ── /api/benchmarks/evals — no eval records on this box ───────────────
  await page.route(/\/api\/benchmarks\/evals(\?|$)/, (route) =>
    json(route, { tasks: [], task_order: [], models: [], count: 0 }),
  )
  // shape: the leaderboard body (tasks / task_order / models / count) of
  // GET /api/benchmarks/evals (src/hal0/api/routes/benchmarks.py:361); the
  // empty list renders the tab's own "no eval records yet" state rather than
  // an invented score

  // ── /api/benchmarks/plan — what the next sweep would cover ───────────
  await page.route(/\/api\/benchmarks\/plan(\?|$)/, (route) =>
    json(route, {
      suite: 'roster',
      suites_considered: ['roster'],
      skipped_suites: [],
      stale_count: 1,
      cells: [
        {
          cell_key: 'rocm/tg/gemma-4-26b-a4b/default',
          suite: 'roster',
          model: 'gemma-4-26b-a4b',
          lane: 'rocm',
          kind: 'tg',
          depth: 0,
          reason: 'no measurement within the staleness window',
          priority: 'normal',
          exclusive: true,
        },
      ],
      registry_error: null,
    }),
  )
  // shape: the plan body (suite / suites_considered / skipped_suites /
  // stale_count / cells) of GET /api/benchmarks/plan
  // (src/hal0/api/routes/benchmarks.py:180); read at ui/src/dash/Benchmarks.tsx:1767

  // ── /api/stacks — two stacks, the daily driver applied ────────────────
  await page.route(/\/api\/stacks(\?|$)/, (route) =>
    json(route, {
      active: 'daily-driver',
      drift: 'clean',
      stacks: [
        {
          slug: 'daily-driver',
          name: 'Daily driver',
          description: 'General chat + code + image.',
          author: 'hal0',
          icon: '',
          tags: ['general'],
          seed: true,
          schema_version: 1,
          hal0_version: VERSION,
          active: true,
          drift: 'clean',
          slots: [
            { slot: 'primary', profile: 'rocm', model: 'qwen3.6-35b-a3b-crown', device: 'gpu-rocm', provider: 'llama-server', capabilities: [] },
            { slot: 'coder', profile: 'rocm', model: 'qwen3-coder-reap-25b-a3b', device: 'gpu-rocm', provider: 'llama-server', capabilities: [] },
            { slot: 'embed', profile: 'rocm', model: 'nomic-v1.5', device: 'gpu-rocm', provider: 'llama-server', capabilities: [] },
            { slot: 'img', profile: 'comfyui', model: 'flux1-schnell', device: 'gpu-rocm', provider: 'comfyui', capabilities: [{ child: 'img', device: 'gpu-rocm', provider: 'comfyui', model: 'flux1-schnell', enabled: true }] },
          ],
        },
        {
          slug: 'coding-night',
          name: 'Coding night',
          description: 'Coder model and embeddings only — the chat slot parks.',
          author: 'hal0',
          icon: '',
          tags: ['coding'],
          seed: false,
          schema_version: 1,
          hal0_version: VERSION,
          slots: [
            { slot: 'coder', profile: 'rocm', model: 'qwen3-coder-reap-25b-a3b', device: 'gpu-rocm', provider: 'llama-server', capabilities: [] },
            { slot: 'embed', profile: 'rocm', model: 'nomic-v1.5', device: 'gpu-rocm', provider: 'llama-server', capabilities: [] },
          ],
        },
      ],
    }),
  )
  // shape: StackList / Stack / StackSlotEntry (ui/src/api/hooks/useStacks.ts:17)
  // over GET /api/stacks (src/hal0/api/routes/stacks.py:293); page
  // ui/src/dash/stacks.jsx:736

  // ── /api/mcp/servers — three bundled servers, all running ─────────────
  await page.route(/\/api\/mcp\/servers(\?|$)/, (route) =>
    json(route, {
      servers: [
        mcpServer('hal0-admin', 19, 3, ['claude-code'], [
          { name: 'slot_status', description: "Report a slot's state, model and metrics.", args: 'name?: string, …', read_only: true, destructive: false, idempotent: true, open_world: false, gated: false },
          { name: 'service_action', description: 'Start, stop or restart a managed service.', args: 'id: string, action: string, …', read_only: false, destructive: true, idempotent: false, open_world: false, gated: true },
        ]),
        mcpServer('hal0-memory', 4, 6, ['claude-code', 'openwebui'], [
          { name: 'memory_search', description: 'Search the shared memory banks.', args: 'query: string, top_k?: number, …', read_only: true, destructive: false, idempotent: true, open_world: false, gated: false },
          { name: 'memory_add', description: 'Retain a fact in a memory bank.', args: 'bank: string, text: string, …', read_only: false, destructive: false, idempotent: false, open_world: false, gated: false },
        ]),
        mcpServer('hal0-agents', 12, 1, ['openwebui'], [
          { name: 'agents_list', description: 'List the installed agents and their state.', args: '—', read_only: true, destructive: false, idempotent: true, open_world: false, gated: false },
          { name: 'agent_run', description: 'Start an agent run and follow it to completion.', args: 'agent: string, prompt: string, …', read_only: false, destructive: false, idempotent: false, open_world: true, gated: false },
        ]),
      ],
      count: 3,
    }),
  )
  // shape: {servers: McpServer[], count} (ui/src/api/hooks/useMcp.ts:46) over
  // GET /api/mcp/servers (src/hal0/api/routes/mcp.py:519)

  // ── /api/mcp/clients — the per-agent allow-list ───────────────────────
  await page.route(/\/api\/mcp\/clients(\?|$)/, (route) =>
    json(route, {
      agents: [
        {
          name: 'hermes',
          display: 'Hermes-Agent',
          workspace: '/var/lib/hal0/agents/hermes/workspace',
          servers: [
            { name: 'hal0-admin', url: null, enabled: true, builtin: true, auth: { kind: 'none', env: null, tokenStatus: 'not-needed' }, tools: { allow: ['slot_status', 'service_health'], gated: ['service_action'], blocked: [] }, health: 'green' },
            { name: 'hal0-memory', url: null, enabled: true, builtin: true, auth: { kind: 'none', env: null, tokenStatus: 'not-needed' }, tools: { allow: ['memory_search'], gated: [], blocked: [] }, health: 'green' },
            { name: 'filesystem', url: 'stdio:///usr/lib/hal0/mcp/filesystem-server', enabled: true, builtin: false, auth: { kind: 'none', env: null, tokenStatus: 'not-needed' }, tools: { allow: ['read_file', 'list_directory', 'search_files'], gated: ['write_file'], blocked: [] }, health: 'green' },
          ],
        },
      ],
    }),
  )
  // shape: AgentMCPClientList / MCPClientServer (ui/src/api/hooks/useAgentMcpClients.ts:28)
  // over GET /api/mcp/clients (ENDPOINTS.agentMcpClients, ui/src/api/endpoints.ts:210);
  // the worked example is ADR-0013 §2

  // ── /api/updates/components — every component current ────────────────
  await page.route(/\/api\/updates\/components(\?|$)/, (route) => {
    const now = Date.now()
    const row = (id: string, name: string, kind: string, serviceId: string | null, installed: string, pinned: string, detail: Array<{ key: string; image: string }>) => ({
      id,
      name,
      kind,
      service_id: serviceId,
      installed,
      pinned,
      pin_label: pinned,
      override: null,
      status: 'converged',
      error: null,
      remedy: null,
      note: null,
      ts: iso(now, 2 * HOUR),
      detail,
    })
    return json(route, {
      components: [
        row('openwebui', 'OpenWebUI', 'container-unit', 'openwebui', 'v0.13.5', 'v0.13.5', [{ key: 'image', image: 'ghcr.io/open-webui/open-webui:v0.13.5' }]),
        row('runner-images', 'Runner images', 'slot-images', null, 'b12633-r4f071ba', 'b12633-r4f071ba', [{ key: 'image', image: 'ghcr.io/hal0ai/hal0-runner-standard:b12633-r4f071ba' }]),
        row('hermes', 'Hermes', 'venv', 'hermes', 'v0.23.3', 'v0.23.3', [{ key: 'pin', image: 'v0.23.3' }]),
        row('pi', 'Pi', 'npm-global', null, '1.6.1', '1.6.1', [{ key: 'pin', image: '1.6.1' }]),
        row('memory-decision', 'Memory decision model', 'venv', null, 'unmanaged', 'unknown', []),
        row('hindsight', 'Hindsight', 'venv', 'hindsight', '1.2.3', '1.2.3', [{ key: 'pin', image: '1.2.3' }]),
      ],
      pending: 0,
    })
  })
  // shape: ComponentRow (ui/src/dash/components-pure.ts:14) over
  // GET /api/updates/components (src/hal0/components/status.py:94); the
  // component ids come from src/hal0/components/registry.py:149

  // ── /api/updates/slot-drift — no slot drifted ─────────────────────────
  await page.route(/\/api\/updates\/slot-drift(\?|$)/, (route) =>
    json(route, { count: 0, slots: [] }),
  )
  // shape: SlotDrift {count, slots} (ui/src/api/hooks/useUpdates.ts:132) over
  // GET /api/updates/slot-drift (src/hal0/api/routes/updater.py:1348)

  // ── the journal — 40 lines across four subsystems ────────────────────
  const entries = journalEntries

  // ── /api/journal — the historical backfill ────────────────────────────
  await page.route(/\/api\/journal(\?|$)/, (route) => {
    const rows = entries()
    return json(route, { entries: rows, next_since: rows[rows.length - 1].id })
  })
  // shape: {entries: JournalEntry[], next_since} (ui/src/api/hooks/useLogs.ts:26)
  // over GET /api/journal (src/hal0/api/routes/journal.py:196) over the event
  // projection (src/hal0/api/routes/journal.py:92)

  // /api/journal/stream is not routed: shot.ts installs the e2e SSE harness
  // (ui/tests/e2e/fixtures/sseHarness.ts), which keeps the stream open, and
  // replays journalEntries() into it once the page has subscribed.
}
