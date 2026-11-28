/**
 * Showcase overlay: the box itself.
 *
 * A healthy, busy Ryzen AI Max+ 395 box on hal0 1.8.2: chat, coder and NPU
 * slots serving, live gauges, a throughput history with traffic in it, and no
 * update offer. Nothing here is read from a box at capture time.
 *
 * Where a figure has a published measurement, the showcase uses it:
 *   - decode / prefill tok/s, MTP accept rate and memory of the two GPU chat
 *     models: the hal0.dev/benchmarks leaderboard, sweep of 2026-06-19 on the
 *     reference box, rocm lane (README.md "Benchmarks" quotes the same sweep);
 *   - the NPU chat model: README.md "Benchmarks", gemma3:1b ~38.6 tok/s in ~2 GB.
 * Everything else (gauge readings, the shape of the traffic charts, 24 h usage
 * counts, embed/rerank latency and the small slots' memory) is illustrative.
 * docs/screenshots/README.md lists which images carry illustrative figures.
 *
 * The in-bundle mock (src/api/mock.ts) answers some paths before page.route is
 * consulted, so the paths this file owns are opted out of it through
 * `window.__hal0MockPassthrough` and then routed here.
 */
import type { Page, Route } from '@playwright/test'

import { MOCK_DATA } from '../../../tests/e2e/fixtures/mock-data'

export const VERSION = '1.8.2'

export function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/** A smooth, deterministic wave so charts look like real traffic, not noise. */
export function wave(n: number, base: number, amp: number, phase = 0, period = 9): number[] {
  return Array.from({ length: n }, (_, i) => {
    const v =
      base +
      amp * Math.sin((i + phase) / (period / (2 * Math.PI))) +
      (amp / 3) * Math.sin((i + phase * 2) / 2.3)
    return Math.max(0, Math.round(v * 10) / 10)
  })
}

const slotBase = {
  runtime: 'container',
  container_status: 'running',
  container_health: true,
  image_status: 'present',
}

export const HOST = {
  ...MOCK_DATA.host,
  hostname: 'hal0',
  name: 'hal0',
  kernel: 'Linux version 6.19.2-generic',
  distro: 'Ubuntu 26.04 LTS',
  platform: 'bare-metal',
  platform_label: 'Bare metal',
  cpu_model: 'AMD Ryzen AI Max+ 395',
  cpu: 'AMD Ryzen AI Max+ 395',
  cpu_cores: 16,
  cpu_threads: 32,
  ram_mb: 131072,
  ram_total_mb: 131072,
  ram_available_mb: 71680,
  unified_memory_mb: 131072,
  gtt_total_mb: 118784,
  gpus: [
    {
      vendor: 'amd',
      name: 'AMD Radeon 8060S (Strix Halo)',
      vram_mb: 512,
      driver: 'amdgpu',
      compute_capable: true,
      vulkan_capable: true,
    },
  ],
  kfd_present: true,
}

/** Installed backends: versions from the runner image tags pinned in
 *  manifest.json (llama.cpp standard b12633, FLM 1.0.14, Kokoro 0.4.9,
 *  ComfyUI 0.30.0). Shape: HAL0_DATA.backends (ui/src/dash/data.jsx). */
export const BACKENDS = [
  { name: 'llamacpp:rocm', kind: 'llamacpp', device: 'rocm', ver: 'b12633', state: 'installed', recommended: true },
  { name: 'llamacpp:vulkan', kind: 'llamacpp', device: 'vulkan', ver: 'b12633', state: 'installed' },
  { name: 'llamacpp:cpu', kind: 'llamacpp', device: 'cpu', ver: 'b12633', state: 'installed' },
  { name: 'flm:npu', kind: 'flm', device: 'npu', ver: '1.0.14', state: 'installed', recommended: true },
  { name: 'kokoro', kind: 'kokoro', device: 'cpu', ver: '0.4.9', state: 'installed' },
  { name: 'comfyui', kind: 'comfyui', device: 'rocm', ver: '0.30.0', state: 'installed' },
]

/** The model catalogue the in-bundle /api/models answers from (HAL0_DATA.models). */
export const MODELS = [
  { id: 'qwen3.6-35b-a3b-crown', longName: 'Qwen3.6-35B-A3B Crown MTP', repo: 'jcbtc/qwen3.6-35b-a3b-crown-halo-mtp-dynamic', params: '35B-A3B', size: '22.6 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
  { id: 'qwen3-coder-reap-25b-a3b', longName: 'Qwen3-Coder-REAP-25B-A3B', repo: 'bartowski/cerebras_Qwen3-Coder-REAP-25B-A3B-GGUF:Q5_K_M', params: '25B-A3B', size: '17.7 GB', labels: ['chat', 'coder', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
  { id: 'gemma-4-26b-a4b', longName: 'gemma-4-26B-A4B-it', repo: 'unsloth/gemma-4-26B-A4B-it-GGUF:Q4_K_XL', params: '26B-A4B', size: '17.1 GB', labels: ['chat', 'tool-calling', 'vision'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
  { id: 'qwen3.5-9b', longName: 'Qwen3.5-9B', repo: 'unsloth/Qwen3.5-9B-GGUF:Q4_K_XL', params: '9B', size: '6.0 GB', labels: ['chat', 'tool-calling', 'vision'], type: 'llm', device: 'rocm', ns: 'blessed', installed: false, runtime: 'llamacpp' },
  { id: 'qwen3.5-0.8b', longName: 'Qwen3.5-0.8B', repo: 'unsloth/Qwen3.5-0.8B-GGUF:Q4_K_XL', params: '0.8B', size: '0.6 GB', labels: ['chat'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
  { id: 'gemma3-1b-npu', longName: 'gemma3:1b (NPU)', repo: 'google/gemma-3-1b-it-flm', params: '1B', size: '1.0 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'npu', ns: 'blessed', installed: true, runtime: 'flm' },
  { id: 'llama-3.2-3b-npu', longName: 'Llama-3.2-3B (NPU)', repo: 'meta-llama/llama-3.2-3b-flm', params: '3B', size: '2.4 GB', labels: ['chat'], type: 'llm', device: 'npu', ns: 'blessed', installed: false, runtime: 'flm' },
  { id: 'nomic-v1.5', longName: 'nomic-embed-text-v1.5', repo: 'nomic-ai/nomic-embed-text-v1.5-GGUF', params: '137M', size: '350 MB', labels: ['embeddings'], type: 'embedding', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
  { id: 'bge-reranker-v2', longName: 'bge-reranker-v2-m3', repo: 'BAAI/bge-reranker-v2-m3-q4_k_m', params: '568M', size: '400 MB', labels: ['reranking'], type: 'reranking', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
  { id: 'whisper-v3-turbo-npu', longName: 'whisper-large-v3-turbo (NPU)', repo: 'openai/whisper-large-v3-turbo-flm', params: '809M', size: '750 MB', labels: ['transcription'], type: 'transcription', device: 'npu', ns: 'blessed', installed: true, runtime: 'flm' },
  { id: 'embed-gemma-300m-npu', longName: 'embed-gemma-300m (NPU)', repo: 'google/embed-gemma-300m-flm', params: '300M', size: '350 MB', labels: ['embeddings'], type: 'embedding', device: 'npu', ns: 'blessed', installed: true, runtime: 'flm' },
  { id: 'kokoro-v1', longName: 'kokoro-v1', repo: 'hexgrad/Kokoro-82M', params: '82M', size: '400 MB', labels: ['tts'], type: 'tts', device: 'cpu', ns: 'blessed', installed: true, runtime: 'kokoro' },
  { id: 'hermes-4-14b', longName: 'Hermes-4-14B', repo: 'bartowski/NousResearch_Hermes-4-14B-GGUF:Q5_K_M', params: '14B', size: '10.5 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'pulled', installed: true, runtime: 'llamacpp' },
]

/** The slot roster: what /api/slots answers. Names are the seeded ones. */
export const SLOTS = [
  {
    name: 'hal0',
    _synthetic: true,
    _synthetic_reason: 'Composite /v1 endpoint that fronts every chat model — not a lifecycle slot.',
    status: 'serving',
    url: 'http://127.0.0.1:8080/v1',
    advertised_models: 3,
  },
  {
    ...slotBase,
    name: 'primary',
    type: 'llm',
    device: 'gpu-rocm',
    device_class: 'gpu',
    backend: 'rocm',
    profile: 'rocm',
    model: 'qwen3.6-35b-a3b-crown-halo-mtp-dynamic',
    model_id: 'qwen3.6-35b-a3b-crown',
    modelLong: 'jcbtc/qwen3.6-35b-a3b-crown-halo-mtp-dynamic',
    group: 'chat',
    state: 'serving',
    isDefault: true,
    port: 8092,
    pid: 28471,
    mem_mb: 22_600,
    ctx_max: 65536,
    last_used_at: Date.now() / 1000,
    metrics: { toks: 84.4, ttft: 210, ctx: 18432, kv: 28, mem: 22.6 },
    spark: [8, 9, 11, 10, 12, 13, 15, 14, 13, 15, 16, 14, 15, 17, 18, 16],
  },
  {
    ...slotBase,
    name: 'coder',
    type: 'llm',
    device: 'gpu-rocm',
    device_class: 'gpu',
    backend: 'rocm',
    profile: 'rocm',
    model: 'qwen3-coder-reap-25b-a3b-q5km',
    model_id: 'qwen3-coder-reap-25b-a3b',
    modelLong: 'bartowski/cerebras_Qwen3-Coder-REAP-25B-A3B-GGUF',
    group: 'chat',
    state: 'serving',
    port: 8094,
    pid: 28490,
    mem_mb: 17_700,
    ctx_max: 131072,
    last_used_at: Date.now() / 1000,
    metrics: { toks: 54.7, ttft: 160, ctx: 40960, kv: 31, mem: 17.7 },
    spark: [10, 12, 14, 13, 16, 18, 17, 19, 21, 20, 22, 21, 23, 22, 24, 23],
  },
  {
    ...slotBase,
    name: 'agent',
    type: 'llm',
    device: 'npu',
    device_class: 'npu',
    profile: 'flm',
    model: 'gemma3:1b',
    model_id: 'gemma3-1b-npu',
    modelLong: 'google/gemma-3-1b-it-flm',
    group: 'npu',
    state: 'serving',
    isDefault: true,
    coresident: true,
    port: 8093,
    pid: 28482,
    mem_mb: 2_000,
    last_used_at: Date.now() / 1000,
    metrics: { toks: 38.6, ttft: 280, ctx: 4096, kv: 22, mem: 2.0 },
    spark: [2, 3, 4, 3, 5, 4, 6, 5, 4, 6, 5, 7, 6, 5, 6, 7],
  },
  {
    ...slotBase,
    name: 'embed',
    type: 'embedding',
    device: 'gpu-rocm',
    device_class: 'gpu',
    backend: 'rocm',
    profile: 'rocm',
    model: 'nomic-embed-text-v1.5',
    model_id: 'nomic-v1.5',
    modelLong: 'nomic-ai/nomic-embed-text-v1.5-GGUF',
    group: 'embed',
    state: 'ready',
    isDefault: true,
    port: 8095,
    pid: 28498,
    mem_mb: 540,
    metrics: { lat: 14, dim: 768, mem: 0.35 },
    spark: [4, 5, 5, 6, 5, 7, 8, 6, 7, 8, 9, 7, 8, 9, 8, 7],
  },
  {
    ...slotBase,
    name: 'rerank',
    type: 'reranking',
    device: 'gpu-rocm',
    device_class: 'gpu',
    backend: 'rocm',
    profile: 'rocm',
    model: 'bge-reranker-v2-m3',
    model_id: 'bge-reranker-v2',
    modelLong: 'BAAI/bge-reranker-v2-m3-q4_k_m',
    group: 'embed',
    state: 'ready',
    isDefault: true,
    port: 8096,
    pid: 28502,
    mem_mb: 540,
    metrics: { lat: 28, maxDocs: 200, mem: 0.4 },
    spark: [1, 2, 2, 1, 3, 2, 1, 2, 3, 2, 1, 2, 2, 3, 2, 1],
  },
  {
    ...slotBase,
    name: 'stt-npu',
    type: 'transcription',
    device: 'npu',
    device_class: 'npu',
    profile: 'flm',
    model: 'whisper-v3-turbo',
    model_id: 'whisper-v3-turbo-npu',
    modelLong: 'openai/whisper-large-v3-turbo-flm',
    group: 'npu',
    state: 'ready',
    isDefault: true,
    coresident: true,
    port: 8093,
    pid: 28482,
    mem_mb: 750,
    metrics: { precision: 'Q4_K_M', mem: 0.4 },
  },
  {
    ...slotBase,
    name: 'embed-npu',
    type: 'embedding',
    device: 'npu',
    device_class: 'npu',
    profile: 'flm',
    model: 'embed-gemma-300m',
    model_id: 'embed-gemma-300m-npu',
    modelLong: 'google/embed-gemma-300m-flm',
    group: 'npu',
    state: 'ready',
    coresident: true,
    port: 8093,
    pid: 28482,
    mem_mb: 350,
    metrics: { lat: 9, dim: 768, mem: 0.35 },
  },
  {
    ...slotBase,
    name: 'tts',
    type: 'tts',
    device: 'cpu',
    device_class: 'cpu',
    profile: 'tts',
    model: 'kokoro-v1',
    model_id: 'kokoro-v1',
    modelLong: 'hexgrad/Kokoro-82M',
    group: 'utility',
    state: 'ready',
    isDefault: true,
    cpuOnly: true,
    port: 8097,
    pid: 28510,
    mem_mb: 800,
    metrics: { secs: 47, voice: 'af_heart', mem: 0.4 },
  },
  {
    ...slotBase,
    name: 'img',
    type: 'image',
    device: 'gpu-rocm',
    device_class: 'img',
    model: 'flux1-schnell',
    model_id: 'flux1-schnell',
    modelLong: 'black-forest-labs/FLUX.1-schnell',
    group: 'img',
    state: 'idle',
    isDefault: true,
    profile: 'comfyui',
    port: 8188,
    pid: 28518,
    mem_mb: 1_200,
    metrics: { res: '1024×1024', mem: 1.2 },
  },
]

/**
 * One table of per-slot figures; every total on every page is derived from
 * it, so two screenshots of "the same box" cannot disagree.
 *   gb:  resident memory in GB, equal to the size the model catalogue
 *        lists. For the two GPU chat models it is the leaderboard's per-run
 *        memory; the three NPU models add up to README.md's ~2 GB.
 *   tps: steady decode rate (historical figures; see the header).
 */
export const SLOT_FIGURES: Record<string, { gb: number; tps?: number; inflight?: number; kv?: number }> = {
  primary: { gb: 22.6, tps: 84.4, inflight: 2, kv: 280000 },
  coder: { gb: 17.7, tps: 54.7, inflight: 1, kv: 310000 },
  agent: { gb: 1.0, tps: 38.6, inflight: 1, kv: 220000 },
  embed: { gb: 0.35 },
  rerank: { gb: 0.4 },
  'stt-npu': { gb: 0.75 },
  'embed-npu': { gb: 0.35 },
  tts: { gb: 0.4 },
  img: { gb: 1.5 },
}
for (const s of SLOTS as any[]) {
  const f = SLOT_FIGURES[s.name]
  if (!f) continue
  s.mem_mb = Math.round(f.gb * 1024)
  if (s.metrics) {
    s.metrics.mem = f.gb
    if (f.tps != null) s.metrics.toks = f.tps
  }
}

const POOL_GB = 116
const real = (SLOTS as any[]).filter((s) => !s._synthetic)
const isGpu = (s: any) => String(s.device || '').startsWith('gpu-')
const gpuChat = real.filter((s) => isGpu(s) && s.type !== 'image')
const imgSlot = real.find((s) => s.type === 'image')
const serving = real.filter((s) => s.state === 'serving')
const round1 = (v: number) => Math.round(v * 10) / 10
const GPU_USED_GB = round1(gpuChat.reduce((a, s) => a + SLOT_FIGURES[s.name].gb, 0) + SLOT_FIGURES[imgSlot.name].gb)
const GPU_FREE_GB = round1(POOL_GB - GPU_USED_GB)
/** Combined decode rate of the serving slots, and its GPU / NPU split. */
export const TOTAL_TPS = round1(serving.reduce((a, s) => a + (SLOT_FIGURES[s.name].tps ?? 0), 0))

function statsHardware() {
  return {
    ram_total_mb: 131072,
    ram_used_mb: 16794,
    ram_available_mb: 114278,
    gtt_used_mb: Math.round(GPU_USED_GB * 1024),
    vram_used_mb: 512,
    gpu_util: 0.64,
    util_is_forced_high: false,
    gpu_clock_mhz: 2650,
    gpu_temp_c: 61,
    cpu_util: 0.18,
    npu_util: 0.42,
    npu_pmode: 'turbo',
    npu_fclk: 1901,
    npu_status: {
      ok: true,
      model_mb: Math.round(real.filter((s) => s.device === 'npu').reduce((a, s) => a + SLOT_FIGURES[s.name].gb, 0) * 1024),
    },
    per_slot: Object.fromEntries(
      [...gpuChat, imgSlot].map((s) => [
        s.name,
        { mem_mb: Math.round(SLOT_FIGURES[s.name].gb * 1024), state: s.state, model_id: s.model_id },
      ]),
    ),
    memory: {
      gpu: {
        kind: 'shared',
        total_gb: POOL_GB,
        used_gb: GPU_USED_GB,
        this_box: gpuChat.map((s) => ({ slot: s.name, gb: SLOT_FIGURES[s.name].gb, state: s.state })),
        image_engine_gb: SLOT_FIGURES[imgSlot.name].gb,
        image_engine_slot: imgSlot.name,
        image_engine_estimated: false,
        other_guests_gb: 0,
        attribution_excess_gb: 0,
        free_gb: GPU_FREE_GB,
      },
      ram: { limit_gb: 128, used_gb: 16.4, free_gb: 111.6, source: 'system' },
      render_free_gb: round1(GPU_FREE_GB + SLOT_FIGURES[imgSlot.name].gb),
      render_ram_free_gb: 111.6,
    },
    host: { configured: false, detected: false },
  }
}

/** A traffic series for one slot that ends on its steady rate and never exceeds it. */
function series(n: number, tps: number, amp: number, phase: number) {
  const s = wave(n, tps - amp, amp, phase).map((v) => Math.min(v, tps))
  s[n - 1] = tps
  return s
}

function throughputHistory() {
  const now = Math.floor(Date.now() / 1000)
  const n = 20
  const per: Record<string, number[]> = Object.fromEntries(
    serving.map((s, i) => [s.name, series(n, SLOT_FIGURES[s.name].tps ?? 0, 6, i * 3)]),
  )
  return {
    window_s: 100,
    bucket_s: 5,
    samples: Array.from({ length: n }, (_, i) => ({
      ts: now - (n - 1 - i) * 5,
      total_tps: round1(Object.values(per).reduce((a, v) => a + v[i], 0)),
      serving_slots: serving.length,
    })),
    per_slot: per,
    pending_tokens: 412,
  }
}

function activity(window: string) {
  const buckets = 60
  const winS = window === '1h' ? 3600 : window === '5m' ? 300 : 900
  const lane = (tps: number[], requests: number, ttft: number) => ({
    tps,
    peak_tps: Math.max(...tps),
    tokens: Math.round(tps.reduce((a, b) => a + b, 0) * (winS / buckets)),
    requests,
    errors: 0,
    ttft_p50_ms: ttft,
  })
  // Bursty traffic: a working session with pauses, not a flat line.
  const burst = (base: number, amp: number, phase: number) =>
    wave(buckets, base, amp, phase, 14).map((v, i) => ((i + phase) % 17 < 3 ? Math.round(v * 0.15) : v))
  return {
    window,
    window_s: winS,
    bucket_s: winS / buckets,
    buckets,
    end: new Date().toISOString(),
    fleet: {
      gpu_util: wave(buckets, 0.55, 0.2, 2, 14).map((v) => Math.min(1, v)),
      npu_util: wave(buckets, 0.35, 0.15, 5, 14).map((v) => Math.min(1, v)),
    },
    slots: {
      primary: lane(burst(72, 10, 0).map((v) => Math.min(v, SLOT_FIGURES.primary.tps!)), 182, 210),
      coder: lane(burst(46, 8, 6).map((v) => Math.min(v, SLOT_FIGURES.coder.tps!)), 241, 160),
      agent: lane(burst(32, 5, 11).map((v) => Math.min(v, SLOT_FIGURES.agent.tps!)), 96, 280),
      embed: lane(burst(4, 2, 3), 1240, 12),
    },
  }
}

function systemStats() {
  const ts = new Date().toISOString()
  return {
    ts,
    fleet: { gpu_util: 0.64, vram_bytes: null, gtt_bytes: Math.round(GPU_USED_GB * 1024 ** 3), power_w: 96, temp_c: 61 },
    slots: serving.map((s) => ({
      slot_id: s.name,
      state: 'serving',
      vram_bytes: null,
      inflight: SLOT_FIGURES[s.name].inflight ?? 0,
      kv_used: SLOT_FIGURES[s.name].kv ?? 0,
      ts,
    })),
  }
}

function usageStats() {
  const model = (
    id: string,
    ok: number,
    errors: number,
    tps: number,
    ttft: number,
    prefill: number | null,
    accept: number | null,
  ) => ({
    model_id: id,
    ttft_ms: { p50: ttft, p95: Math.round(ttft * 3.1) },
    tps_decode: { avg: tps, p50: tps },
    tps_prefill: { avg: prefill },
    spec_accept_rate: accept,
    ok,
    errors,
  })
  const now = Date.now()
  return {
    window: {
      from: new Date(now - 24 * 3600_000).toISOString(),
      to: new Date(now).toISOString(),
      bucket: '1h',
    },
    totals: { requests: 4182, ok: 4171, errors: 11, tokens_completed: 3_912_400 },
    by_model: [
      model('qwen3-coder-reap-25b-a3b', 1840, 4, 54.7, 160, 1368, null),
      model('qwen3.6-35b-a3b-crown', 1322, 6, 84.4, 210, 873, 0.909),
      model('gemma3-1b-npu', 612, 1, 38.6, 280, null, null),
      model('nomic-v1.5', 408, 0, 0, 12, null, null),
    ],
    buckets: Array.from({ length: 24 }, (_, i) => {
      const hour = new Date(now - (23 - i) * 3600_000)
      const workday = hour.getUTCHours() >= 13 && hour.getUTCHours() <= 23
      const req = workday ? 220 + ((i * 37) % 90) : 30 + ((i * 13) % 25)
      return { ts: hour.toISOString(), requests: req, tokens_completed: req * 940 }
    }),
  }
}

function power() {
  return { gpu_power_w: 96, gpu_temp_c: 61, gpu_sclk_mhz: 2650, cpu_temp_c: 58 }
}

/** Paths answered here rather than by the in-bundle mock. */
export const CORE_PASSTHROUGH = ['/api/status', '/api/slots', '/api/hardware', '/api/updates/state', '/api/backends']

export async function installCore(page: Page) {
  await page.route('**/api/status', (route) =>
    json(route, {
      name: 'hal0',
      version: VERSION,
      status: 'ok',
      hardware: null,
      slots: SLOTS,
      upstreams: [],
      memory_enabled: true,
      memory_degraded: false,
    }),
  )
  await page.route('**/api/slots', (route) => json(route, SLOTS))
  await page.route('**/api/hardware', (route) => json(route, HOST))
  // shape: buildBackends (ui/src/api/mockFixtures.ts:136-149)
  await page.route('**/api/backends', (route) =>
    json(route, {
      backends: BACKENDS.map((b) => ({
        id: b.name,
        version: b.ver,
        state: b.state,
        usedBy: [],
        recommended: !!b.recommended,
        note: undefined,
        kind: b.kind,
        device: b.device,
      })),
    }),
  )
  await page.route('**/api/updates/state', (route) =>
    json(route, {
      hal0: {
        current: VERSION,
        available: null,
        channel: 'stable',
        revoked: false,
        revoked_reason: '',
        revoked_version: null,
      },
      flm: { current: 'v1.0.14', source: 'manual-deb' },
      autoCheck: true,
    }),
  )
  await page.route('**/api/stats/hardware', (route) => json(route, statsHardware()))
  await page.route('**/api/stats/power', (route) => json(route, power()))
  await page.route(/\/api\/stats\/throughput\/history(\?|$)/, (route) => json(route, throughputHistory()))
  await page.route(/\/api\/stats\/activity(\?|$)/, (route) => {
    const w = new URL(route.request().url()).searchParams.get('window') || '15m'
    return json(route, activity(w))
  })
  await page.route('**/api/system-stats', (route) => json(route, systemStats()))
  // Profiles: the e2e seed set, with its per-profile speed figures cleared
  // except the two README.md "Benchmarks" quotes (FLM gemma3:1b ~38.6 tok/s,
  // Kokoro ~0.18 real-time factor); the others have no published source.
  const sourced: Record<string, { tps?: number; rtf?: number }> = { flm: { tps: 38.6 }, tts: { rtf: 0.18 } }
  await page.route('**/api/profiles', (route) =>
    route.request().method() !== 'GET'
      ? route.fallback()
      : json(
          route,
          (MOCK_DATA.profiles ?? []).map((p: any) => ({
            ...p,
            tps: sourced[p.name]?.tps ?? null,
            rtf: sourced[p.name]?.rtf ?? null,
          })),
        ),
  )
  await page.route(/\/api\/stats(\?|$)/, (route) => json(route, usageStats()))
}
