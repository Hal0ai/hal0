// hal0 v3 dashboard — mock fixture data + builders (Phase B1, split off #mock-prod-gate).
//
// This module holds every allowlisted mock builder function and the fixture
// data they read (HAL0_DATA passthrough shims, the ~6-week Memory story,
// the FU2 600-node synthetic graph, seed profiles/stacks/chat-templates…).
// It is intentionally NOT imported at the top of `./mock.ts` — `mockFetch`
// only reaches for it via a dynamic `import()` once it has already decided
// a mock substitution is actually going to happen (forced-mock mode, or a
// genuine dev-time 404/network-error fallback). That keeps this file (and
// its fixture payloads) out of the initial module graph for `client.ts`, so
// a production build's main chunk doesn't carry mock data it will never
// serve. See `./mock.ts` for the routing/gating logic and
// `docs/rework/r5-sync-assessment-2026-07-19.md` §1.1 for the blocker this
// split closes.

import { META_ENUMS_FALLBACK } from '../lib/deviceMeta'

export type Builder = (url: string, match: RegExpMatchArray) => unknown

function data(): any {
  return (typeof window !== 'undefined' && window.HAL0_DATA) || {}
}

// ─── Degraded-poll simulation (#1391) ─────────────────────────────
// `config_enrichment` is applied ONLY in the GET /api/slots builder
// (slot_view/__init__.py); routes/health.py builds the /api/status slot
// entries from `_slot_to_dict` alone. So when an /api/slots poll drops,
// `useSlots` falls back to a bare union entry that carries none of the
// TOML-derived config fields.
//
// Forced-mock serves BOTH endpoints from the same HAL0_DATA.slots array, so
// the divergence is invisible — and `page.route` can't fail /api/slots
// either, because the mock short-circuits it first (see ./mock.ts). This
// knob is the harness's only way in: a spec sets
// `window.__hal0MockSlotsDegraded = true` (addInitScript, or mid-test via
// page.evaluate — it is read live on every poll) and gets exactly the real
// degraded shape: /api/slots 404s and /api/status answers with the config
// keys stripped off.
function slotsDegraded(): boolean {
  return (
    typeof window !== 'undefined' &&
    (window as unknown as { __hal0MockSlotsDegraded?: boolean }).__hal0MockSlotsDegraded === true
  )
}

// The exact key set `config_enrichment` lifts onto an /api/slots entry. A
// bare `_slot_to_dict` /api/status entry has none of them.
const CONFIG_ENRICHMENT_KEYS = [
  'type',
  'model_default',
  'labels',
  'pinned',
  'autoload',
  'priority',
  'ctx_max',
  'chat_template',
  'n_gpu_layers',
  'threads',
  'binary',
  'image_pin',
  'rope_freq_base',
  'idle_timeout_s',
  'workers',
  'parallel',
  'llamacpp_args',
  'npu',
  'declared_backend',
] as const

function stripConfigEnrichment(slots: any[]): any[] {
  return (Array.isArray(slots) ? slots : []).map((s) => {
    const bare = { ...s }
    for (const k of CONFIG_ENRICHMENT_KEYS) delete bare[k]
    return bare
  })
}

// ─── Builders — one per endpoint family ───────────────────────────
function buildStatus() {
  const d = data()
  // 0.4 gate: forced-mock dev/preview build keeps the memory surface ON so
  // the Agent → Memory tab stays reachable for layout/screenshot work. The
  // shipped backend defaults this ON too ([memory].enabled in hal0.toml).
  // Because forced-mock short-circuits `page.route`, a spec can't override
  // /api/status the usual way — it sets `window.__hal0MockMemoryEnabled =
  // false` via addInitScript to exercise the disabled path. Default ON.
  const memoryOff =
    typeof window !== 'undefined' &&
    (window as unknown as { __hal0MockMemoryEnabled?: boolean }).__hal0MockMemoryEnabled === false
  // Mirrors the real GET /api/status envelope (health.py:get_status) exactly —
  // no `hostname` key (that was dead: nothing in the client ever reads
  // status.hostname; hardware comes from a dedicated /api/hardware call and
  // /api/status always reports `hardware: null`). `name`/`version`/`status`/
  // `upstreams`/`memory_degraded` were previously missing entirely.
  return {
    name: 'hal0',
    version: '0.3.0-alpha.1',
    status: 'ok',
    hardware: null,
    slots: slotsDegraded() ? stripConfigEnrichment(d.slots ?? []) : (d.slots ?? []),
    upstreams: [],
    memory_enabled: !memoryOff,
    memory_degraded: memoryOff ? null : false,
  }
}

function buildSlots() {
  // null → jsonResponse() answers 404, which apiGet surfaces as a throw —
  // the same soft-fail `fetchSlotsUnion` sees when the real aggregator's
  // heavy per-slot /health probe times out.
  if (slotsDegraded()) return null
  return data().slots ?? []
}

function buildSlotDetail() {
  return null // 404-style — Slot detail not in mock
}

function buildModels() {
  // Forced mock can't be page.route-overridden for /api/models, so a spec
  // opts into HF-update badges via `window.__hal0MockModelUpdates.availableIds`
  // (mirrors the __hal0MockMemoryEnabled pattern). Absent → no badges, exactly
  // as before, so unrelated specs are unaffected.
  const ov =
    typeof window !== 'undefined'
      ? (window as unknown as { __hal0MockModelUpdates?: { availableIds?: string[] } })
          .__hal0MockModelUpdates
      : undefined
  const availableIds = new Set(ov?.availableIds ?? [])
  const models = (data().models ?? []).map((m: any) =>
    availableIds.has(m.id) ? { ...m, update_available: true } : m,
  )
  return { models }
}

function buildBackends() {
  return {
    backends: (data().backends ?? []).map((b: any) => ({
      id: b.name,
      version: b.ver,
      state: b.state,
      usedBy: [],
      recommended: !!b.recommended,
      note: b.note,
      kind: b.kind,
      device: b.device,
    })),
  }
}

// Mirrors the real GET /api/capabilities envelope exactly (orchestrator.py
// get_state / catalogs_by_slot / catalog.available_backends) — the old
// `{capabilities:{chat,embed,voice,img,npu}}` shape was the pre-orchestrator
// envelope and doesn't match what CapabilityOrchestrator ships, so
// Voice/ImageGen e2e rendered empty catalogs in forced-mock. Real shape:
// {backends:[...], catalogs:{embed:{embed,rerank}, voice:{stt,tts},
// img:{img}, vision:{vision}}, selections: same nesting with the live
// {device,backend,provider,model,enabled,slot,status} row per child}.
function capabilityRow(device: string, provider: string, model: string | null, slot: string, status: string) {
  return { device, backend: device, provider, model, enabled: !!model, slot, status }
}

function buildCapabilities() {
  return {
    backends: [
      { id: 'gpu-rocm', label: 'GPU · ROCm', short: 'ROCm', provider: 'llamacpp', multiplex: false },
      { id: 'gpu-vulkan', label: 'GPU · Vulkan', short: 'Vulkan', provider: 'llamacpp', multiplex: false },
      { id: 'npu', label: 'NPU', short: 'NPU', provider: 'flm', multiplex: true },
      { id: 'cpu', label: 'CPU', short: 'CPU', provider: 'llamacpp', multiplex: false },
    ],
    catalogs: {
      embed: {
        embed: [
          { id: 'nomic-embed-text-v1.5', capabilities: ['embed'], size_gb: 0.14, backends: [{ id: 'gpu-vulkan', provider: 'llama-server', downloaded: true, pullable: true }, { id: 'cpu', provider: 'llama-server', downloaded: true, pullable: true }] },
        ],
        rerank: [
          { id: 'bge-reranker-v2-m3', capabilities: ['rerank'], size_gb: 0.56, backends: [{ id: 'gpu-vulkan', provider: 'llama-server', downloaded: true, pullable: true }] },
        ],
      },
      voice: {
        stt: [
          { id: 'whisper-large-v3', capabilities: ['stt'], size_gb: 1.5, backends: [{ id: 'npu', provider: 'flm', downloaded: true, pullable: true }] },
        ],
        tts: [
          { id: 'kokoro-v1', capabilities: ['tts'], size_gb: 0.3, backends: [{ id: 'cpu', provider: 'kokoro', downloaded: true, pullable: true }] },
        ],
      },
      img: {
        img: [
          { id: 'sd-turbo', capabilities: ['image'], size_gb: 2.1, backends: [{ id: 'gpu-rocm', provider: 'sdcpp', downloaded: true, pullable: true }] },
        ],
      },
      vision: {
        vision: [],
      },
    },
    selections: {
      embed: {
        embed: capabilityRow('gpu-vulkan', 'llama-server', 'nomic-embed-text-v1.5', 'embed', 'serving'),
        rerank: capabilityRow('', '', null, 'rerank', 'offline'),
      },
      voice: {
        stt: capabilityRow('npu', 'flm', 'whisper-large-v3', 'stt', 'serving'),
        tts: capabilityRow('cpu', 'kokoro', 'kokoro-v1', 'tts', 'serving'),
      },
      img: {
        img: capabilityRow('gpu-rocm', 'sdcpp', 'sd-turbo', 'img', 'offline'),
      },
      vision: {
        vision: capabilityRow('', '', null, 'vision', 'offline'),
      },
    },
  }
}

function buildHardware() {
  return data().host ?? {}
}

// NPU occupancy card — AIE column allocation + per-FLM-slot composition.
// Serves HAL0_DATA.npu_occupancy when present; otherwise synthesises a
// single-tenant "loaded" snapshot from the npu-device slots so the card
// renders in forced-mock without bespoke fixture data.
function buildNpuOccupancy() {
  const d = data()
  if (d.npu_occupancy) return d.npu_occupancy
  const npuSlots = (d.slots ?? []).filter(
    (s: any) => s.device_class === 'npu' || s.device === 'npu',
  )
  const serving = npuSlots.find((s: any) => s.state === 'serving')
  const owner = serving ?? npuSlots[0]
  const used = owner ? 8 : 0
  return {
    present: npuSlots.length > 0,
    rows: 4,
    cols: 8,
    tiles: 32,
    tops_peak: 50,
    cols_total: 8,
    cols_used: used,
    serving: !!serving,
    single_tenant: true,
    columns_available: true,
    slots: npuSlots.map((s: any) => ({
      name: s.name,
      model: String(s.model_id ?? s.model ?? '').replace(/-FLM$/, '') || null,
      state: s.state === 'serving' ? 'serving' : s.state === 'ready' ? 'ready' : 'idle',
      cols: s === owner ? [0, 1, 2, 3, 4, 5, 6, 7] : [],
      gb: typeof s.gb === 'number' ? s.gb : 2.4,
    })),
  }
}

// ── Meta enums — static per-release taxonomy ──────────────────────
// Mirrors GET /api/meta/enums exactly; the payload IS the typed fallback
// (src/lib/deviceMeta.ts) so mock mode and fallback mode can't drift.
function buildMetaEnums() {
  return META_ENUMS_FALLBACK
}

// ── Profiles — mirrors GET /api/profiles (list of ProfileConfig rows) ──
// Seed shapes match profiles.jsx expectations (name/image/flags/mtp/
// resolved_flags/device_class/backend/seed/intent/quant/tps|rtf/used_by),
// grounded in config/schema.py SEED_PROFILES + PROFILE_BENCH, plus one
// custom profile so the "Custom profiles" section renders in mock mode.
function buildProfiles() {
  return [
    {
      name: 'rocm',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      flags: '-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap',
      mtp: false,
      resolved_flags: '-fa on -ctk q8_0 -ctv q8_0 -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap',
      device_class: 'gpu',
      backend: 'rocm',
      seed: true,
      cloned_from: null,
      intent: 'MoE agents',
      quant: 'FP4',
      tps: 52.8,
      rtf: null,
      // `used_by` cross-references slot NAMEs from HAL0_DATA.slots
      // (data.jsx) — kept in sync with that array's fixture names on
      // purpose. This module only ever loads behind the mock-prod-gate
      // (dynamic import, dead-code-eliminated from prod builds — see the
      // file header), so these names can never render against a live
      // backend; see data.jsx's NAMES-stale guard comment for the render
      // path proof.
      used_by: ['primary', 'embed', 'rerank'],
    },
    {
      name: 'vulkan',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server',
      flags: '-fa on -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap',
      mtp: false,
      resolved_flags: '-fa on -b 512 -ub 512 --parallel 1 --threads 8 --no-mmap',
      device_class: 'gpu',
      backend: 'vulkan',
      seed: true,
      cloned_from: null,
      intent: 'Vulkan std · fallback',
      quant: 'Q4_K_M',
      tps: 41.0,
      rtf: null,
      used_by: ['legacy'],
    },
    {
      name: 'flm',
      image: 'ghcr.io/hal0ai/hal0-toolbox-flm:0.9.43',
      flags: '',
      mtp: false,
      resolved_flags: '',
      device_class: 'npu',
      backend: null,
      seed: true,
      cloned_from: null,
      intent: 'FLM NPU inference',
      quant: 'W4ABF16',
      tps: 38.6,
      rtf: null,
      used_by: ['agent', 'stt-npu', 'embed-npu'],
    },
    {
      name: 'tts',
      image: 'ghcr.io/hal0ai/hal0-toolbox-kokoro:v1',
      flags: '--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx',
      mtp: false,
      resolved_flags: '--model_path /mnt/ai-models/local/kokoro-v1/kokoro-onnx',
      device_class: 'cpu',
      backend: null,
      seed: true,
      cloned_from: null,
      intent: 'TTS · Kokoro',
      quant: '',
      tps: null,
      rtf: 0.18,
      used_by: ['tts'],
    },
    {
      name: 'cpu-llm',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:vulkan-radv-server',
      flags: '--threads 4 --threads-batch 8 -b 256 -ub 256 --parallel 1 --no-mmap',
      mtp: false,
      resolved_flags: '--threads 4 --threads-batch 8 -b 256 -ub 256 --parallel 1 --no-mmap',
      device_class: 'cpu',
      backend: null,
      seed: true,
      cloned_from: null,
      intent: 'CPU-only LLM · llama-server',
      quant: 'Q4_K_M',
      tps: null,
      rtf: null,
      used_by: [],
    },
    {
      name: 'comfyui',
      image: 'docker.io/kyuz0/amd-strix-halo-comfyui:latest',
      flags: '--disable-mmap --bf16-vae --cache-none',
      mtp: false,
      resolved_flags: '--disable-mmap --bf16-vae --cache-none',
      device_class: 'img',
      backend: null,
      seed: true,
      cloned_from: null,
      intent: 'Image generation',
      quant: '',
      tps: null,
      rtf: null,
      used_by: ['img'],
    },
    {
      name: 'rocm-fast',
      image: 'ghcr.io/hal0ai/amd-strix-halo-toolboxes:rocm-7.2.4-rocmfp4-server',
      flags: '-fa on -b 8192 -ub 2048 --threads 16 --no-mmap',
      mtp: true,
      resolved_flags: '-fa on -b 8192 -ub 2048 --threads 16 --no-mmap',
      device_class: 'gpu',
      backend: 'rocm',
      seed: false,
      cloned_from: 'rocm',
      intent: 'Dense + MTP · benched on this box',
      quant: 'FP4',
      tps: null,
      rtf: null,
      used_by: [],
    },
  ]
}

// ── Stacks — mirrors GET /api/stacks ({stacks, active, drift}) ─────
// Slot entries reference HAL0_DATA model ids where possible so the
// available/missing affordances exercise both paths in mock mode.
function buildStacksList() {
  return {
    active: null,
    drift: 'none',
    stacks: [
      {
        slug: 'coding',
        name: 'Coding',
        description: 'Fast coder + repo retrieval',
        author: 'hal0',
        icon: '',
        tags: ['coding', 'fast'],
        seed: true,
        slots: [
          { slot: 'primary', model: 'qwen3-coder-30b', device: 'gpu-rocm', profile: 'rocm', mtp: false, capabilities: [] },
          { slot: 'embed', model: 'nomic-v1.5', device: 'gpu-rocm', profile: 'rocm', mtp: false, capabilities: [] },
        ],
      },
      {
        slug: 'daily-driver',
        name: 'Daily driver',
        description: 'General chat + voice + image',
        author: 'hal0',
        icon: '',
        tags: ['general'],
        seed: false,
        slots: [
          { slot: 'primary', model: 'qwen3.6-27b-mtp', device: 'gpu-rocm', profile: 'rocm', mtp: true, capabilities: [] },
          { slot: 'tts', model: 'kokoro-v1', device: 'cpu', profile: 'tts', mtp: false, capabilities: [] },
          { slot: 'img', model: 'sd-turbo', device: 'img', profile: 'comfyui', mtp: false, capabilities: [] },
          { slot: 'scribe', model: 'whisper-large-v3-missing', device: 'npu', profile: 'flm', mtp: false, capabilities: [] },
        ],
      },
    ],
  }
}

// ── Chat templates — mirrors GET /api/chat-templates ([{id,label,valid}]) ──
function buildChatTemplates() {
  return [
    { id: 'chatml', label: 'ChatML', valid: true, error: null },
    { id: 'llama3', label: 'Llama 3', valid: true, error: null },
    { id: 'mistral', label: 'Mistral / [INST]', valid: true, error: null },
    { id: 'gemma', label: 'Gemma', valid: true, error: null },
  ]
}

function buildJournal() {
  // Phase 3 of #322: HAL0_DATA.journal is gone — the dashboard streams
  // /api/journal/stream and renders an empty-state placeholder when the
  // ring is empty. The forced-mock surface returns an empty envelope so
  // dev runs honour the same "no synthetic copy" rule the live build
  // does; tests that need to drive specific entries either use
  // `page.route('/api/journal*')` or push frames via the SSE harness.
  return { entries: [], next_since: null }
}

function buildUpdateState() {
  // Tests (and dev) can override the forced-mock payload by setting
  // `window.__hal0UpdateStateOverride` before any fetch fires. This is
  // the seam used by Phase 2's update-banner-v3.spec.ts AND Phase 3's
  // footer-update-chip-v3.spec.ts to exercise the "no available
  // release" + "current === available" branches without ripping the
  // forced-mock short-circuit out of mockFetch. A dedicated window key
  // is used so that the override survives data.jsx replacing
  // `window.HAL0_DATA` wholesale at mount.
  if (typeof window !== 'undefined') {
    const override = (window as any).__hal0UpdateStateOverride
    if (override) return override
  }
  // Seed: realistic-looking pair so the dev demo's footer chip + banner
  // render against current-ish release strings. Tests override via the
  // window seam above; the literals here are only used in dev. Keep the
  // pair in sync with pyproject.toml's version when bumping major.
  // `revoked`/`revoked_reason`/`revoked_version` mirror the real
  // GET /api/updates/state envelope (updater.py:update_state) — previously
  // absent here, which left the revocation banner untestable in forced-mock
  // (a spec had nothing to flip via __hal0UpdateStateOverride). Default
  // unrevoked so existing render paths are unaffected; a spec exercising the
  // banner sets `{revoked: true, revoked_reason, revoked_version}` via the
  // override seam.
  return {
    hal0: {
      current: '0.3.0-alpha.1',
      available: '0.3.0-alpha.2',
      channel: 'stable',
      revoked: false,
      revoked_reason: '',
      revoked_version: null,
    },
    flm: { current: 'v0.9.42', source: 'manual-deb' },
    autoCheck: true,
  }
}

// HF model update check — a "nothing checked yet" snapshot so mock/dev
// renders without hammering the (absent) backend with 500-retries. A live
// backend or a page.route override stays authoritative via networkFirst.
function buildModelUpdatesCheck() {
  // Mirrors buildModels' opt-in: a spec sets availableIds (+ optional
  // checked_at) to exercise the "last checked / N available" surface.
  const ov =
    typeof window !== 'undefined'
      ? (
          window as unknown as {
            __hal0MockModelUpdates?: { availableIds?: string[]; checked_at?: number }
          }
        ).__hal0MockModelUpdates
      : undefined
  const ids = ov?.availableIds ?? []
  const models: Record<string, unknown> = {}
  for (const id of ids) {
    models[id] = {
      hf_repo: 'org/repo',
      hf_filename: `${id}.gguf`,
      local_sha256: 'a'.repeat(64),
      remote_sha256: 'b'.repeat(64),
      update_available: true,
      reason: null,
    }
  }
  return {
    checked_at: ov?.checked_at ?? 0,
    checked: ids.length,
    updates_available: ids.length,
    models,
  }
}

function buildSecrets() {
  // `protected` mirrors the real payload (#1450): api.env holds hal0's own
  // HAL0_* service config and auth keys alongside operator credentials, and
  // the route refuses to mutate the former. HAL0_ADMIN_KEY is here so the
  // forced-mock dashboard renders the locked row rather than a Remove button
  // the server would 403 — a mock that omits it teaches the UI a shape the
  // server never sends.
  return {
    secrets: [
      { name: 'HF_TOKEN', set: true, masked: 'hf_•••••••••••••••••••••', protected: false },
      { name: 'OPENAI_API_KEY', set: false, protected: false },
      { name: 'ANTHROPIC_API_KEY', set: false, protected: false },
      { name: 'HAL0_ADMIN_KEY', set: true, masked: '••••••••', protected: true },
    ],
  }
}

// ─── Memory (Hindsight) forced-mock dataset ───────────────────────
//
// A coherent ~6-week story of operating strix-halo-01 so the Memory
// graph/timeseries/console layouts carry MEANING (causal chains, a real
// timeline, semantic topic clusters, entity co-occurrence) instead of
// random points. Ported from the hand-authored prototype
// (hal0-design/memory_overhaul/mem-data.jsx). The graph builders below
// emit the live Hindsight Cytoscape shape the UI consumes via
// `normalizeGraph`: nodes/edges are wrapped in `{ data: { … } }` and node
// ids are stable so edges resolve.
//
// These builders are pure + self-contained (they don't read HAL0_DATA) so
// the memory surface renders identically in forced-mock and on 404
// fallback without depending on dash/data.jsx seeding anything.

type MemFactType = 'world' | 'experience' | 'observation'
type MemLinkType = 'semantic' | 'temporal' | 'causal'

interface MemFact {
  id: string
  date: string // local-ISO (no tz) — drives the timeline
  topic: string
  type: MemFactType
  label: string
  text: string
  ents: string[]
}

const MEM_TOPIC_COLORS: Record<string, string> = {
  reliability: '#5B9BD5',
  performance: '#E69F00',
  storage: '#009E73',
  models: '#CC79A7',
  operator: '#D55E00',
  setup: '#B39DDB',
}

const MEM_ENTITIES: { id: string; label: string; kind: string }[] = [
  { id: 'box', label: 'strix-halo-01', kind: 'host' },
  { id: 'lemond', label: 'lemond', kind: 'service' },
  { id: 'halo', label: 'halo (operator)', kind: 'person' },
  { id: 'qwen', label: 'Qwen3-Coder-30B', kind: 'model' },
  { id: 'comfyui', label: 'ComfyUI', kind: 'service' },
  { id: 'npu', label: 'XDNA NPU', kind: 'device' },
  { id: 'outage', label: 'power outage', kind: 'event' },
  { id: 'ups', label: 'CyberPower UPS', kind: 'hardware' },
  { id: 'proxmox', label: 'Proxmox', kind: 'service' },
  { id: 'hf', label: 'HuggingFace', kind: 'service' },
  { id: 'gemma', label: 'gemma3 / llama-3.2', kind: 'model' },
  { id: 'openwebui', label: 'OpenWebUI', kind: 'service' },
  { id: 'disk', label: '/var volume', kind: 'resource' },
  { id: 'nut', label: 'NUT daemon', kind: 'service' },
  { id: 'ryzenadj', label: 'ryzenadj', kind: 'tool' },
  { id: 'igpu', label: 'Radeon 8060S', kind: 'device' },
]

const MEM_FACTS: MemFact[] = [
  // setup
  { id: 'f1', date: '2026-04-28T10:12', topic: 'setup', type: 'experience', label: 'Installed hal0 on Debian 13', text: 'Installed hal0 on Debian 13 via the one-line installer; lemond came up first try.', ents: ['box', 'lemond'] },
  { id: 'f2', date: '2026-04-28T11:40', topic: 'setup', type: 'world', label: 'Proxmox iGPU passthrough', text: 'Configured Proxmox LXC privileged passthrough for the Radeon 8060S iGPU and XDNA NPU.', ents: ['proxmox', 'igpu', 'npu', 'box'] },
  { id: 'f3', date: '2026-04-29T09:05', topic: 'models', type: 'world', label: 'Default chat = Qwen3-Coder-30B', text: 'Set the default chat slot to Qwen3-Coder-30B-A3B-Instruct-Q4_K_M on gpu-vulkan.', ents: ['qwen', 'lemond'] },
  { id: 'f4', date: '2026-05-01T14:22', topic: 'setup', type: 'world', label: 'Enabled NPU coresident FLM', text: 'Enabled the NPU coresident FLM stack — chat + ASR + embed boot together on XDNA.', ents: ['npu', 'gemma'] },
  { id: 'f5', date: '2026-05-01T15:00', topic: 'setup', type: 'experience', label: 'Wired OpenWebUI tab', text: 'Pointed OpenWebUI at the /v1 gateway; chat works from the dashboard tab.', ents: ['openwebui', 'lemond'] },
  // operator prefs
  { id: 'f6', date: '2026-05-02T21:18', topic: 'operator', type: 'observation', label: 'Prefers terse technical answers', text: 'Operator consistently trims verbose replies — prefers terse, technical, lowercase answers.', ents: ['halo'] },
  { id: 'f7', date: '2026-05-25T23:40', topic: 'operator', type: 'observation', label: 'Works evenings 8pm–1am', text: 'Inference load peaks 20:00–01:00; operator works late, box idle most mornings.', ents: ['halo', 'box'] },
  { id: 'f8', date: '2026-06-02T22:05', topic: 'operator', type: 'observation', label: 'Asks for sources inline', text: 'Operator routinely asks for citations inline — values traceability over prose.', ents: ['halo'] },
  // reliability — power → UPS chain
  { id: 'f9', date: '2026-05-03T02:14', topic: 'reliability', type: 'experience', label: 'Power outage 02:14', text: 'Grid power dropped at 02:14; strix-halo-01 hard-powered off mid-generation.', ents: ['outage', 'box'] },
  { id: 'f10', date: '2026-05-03T02:55', topic: 'reliability', type: 'experience', label: 'Lost 40 min of queue', text: 'Cold boot lost ~40 minutes of queued inference + one in-flight ComfyUI job.', ents: ['outage', 'comfyui', 'lemond'] },
  { id: 'f11', date: '2026-05-06T18:30', topic: 'reliability', type: 'experience', label: 'Bought a CyberPower UPS', text: 'Operator bought a CyberPower 1500VA UPS after the outage; ~22 min runtime at idle.', ents: ['ups', 'halo', 'outage'] },
  { id: 'f12', date: '2026-05-08T12:10', topic: 'reliability', type: 'world', label: 'NUT graceful shutdown', text: 'Configured the NUT daemon for graceful shutdown at 20% battery; tested with a pull.', ents: ['nut', 'ups', 'box'] },
  // performance — thermal → undervolt chain
  { id: 'f13', date: '2026-05-12T20:48', topic: 'performance', type: 'observation', label: 'iGPU hit 95°C', text: 'iGPU touched 95°C under sustained ComfyUI batch load — fans maxed, case warm.', ents: ['igpu', 'comfyui'] },
  { id: 'f14', date: '2026-05-12T21:02', topic: 'performance', type: 'observation', label: 'Img throughput −30%', text: 'Thermal throttling cut sdxl-turbo throughput ~30% during the hot window.', ents: ['comfyui', 'igpu'] },
  { id: 'f15', date: '2026-05-14T16:25', topic: 'performance', type: 'world', label: 'Applied −30mV undervolt', text: 'Applied a −30mV iGPU undervolt via ryzenadj; added it to the boot unit.', ents: ['ryzenadj', 'igpu'] },
  { id: 'f16', date: '2026-05-15T19:10', topic: 'performance', type: 'observation', label: 'Throughput recovered', text: 'Sustained ComfyUI throughput back to baseline; peak temp now ~84°C.', ents: ['comfyui', 'igpu'] },
  // storage — disk full → prune chain
  { id: 'f17', date: '2026-05-20T13:33', topic: 'storage', type: 'observation', label: '/var at 92%', text: '/var hit 92% from accumulated HuggingFace model pulls and old quants.', ents: ['disk', 'hf'] },
  { id: 'f18', date: '2026-05-20T13:34', topic: 'storage', type: 'experience', label: 'Pulls auto-paused', text: 'hal0 auto-paused HuggingFace downloads at the 90% disk threshold.', ents: ['hf', 'disk', 'lemond'] },
  { id: 'f19', date: '2026-05-21T10:02', topic: 'storage', type: 'experience', label: 'Pruned 3 quants, freed 41 GB', text: 'Operator pruned 3 unused GGUF quants; freed 41 GB and resumed pulls.', ents: ['disk', 'halo', 'hf'] },
  // models — npu swap
  { id: 'f20', date: '2026-05-17T20:15', topic: 'models', type: 'experience', label: 'Swapped NPU chat model', text: 'Swapped NPU chat gemma3:1b → llama-3.2-3b-npu; voice + embed paused ~14s on FLM restart.', ents: ['gemma', 'npu'] },
  { id: 'f21', date: '2026-05-18T20:40', topic: 'models', type: 'observation', label: 'llama-3.2 better at tools', text: 'llama-3.2-3b follows tool-call schemas more reliably than gemma3:1b on the NPU.', ents: ['gemma', 'npu'] },
  { id: 'f22', date: '2026-06-05T11:20', topic: 'models', type: 'world', label: 'Pinned Qwen quant', text: 'Pinned the Q4_K_M Qwen quant after testing Q5 — Q5 spilled into shared RAM.', ents: ['qwen', 'disk'] },
]

// explicit "led to" chains (directed)
const MEM_CAUSAL: [string, string][] = [
  ['f9', 'f10'], ['f10', 'f11'], ['f11', 'f12'], // outage → UPS → NUT
  ['f13', 'f14'], ['f14', 'f15'], ['f15', 'f16'], // thermal → undervolt → recovery
  ['f17', 'f18'], ['f18', 'f19'], // disk full → pause → prune
  ['f20', 'f21'], // swap → observation
  ['f1', 'f3'], ['f2', 'f4'], // install → default model; passthrough → FLM
]

const ISO = (d: string) => new Date(d).toISOString()

/**
 * FACT graph in live Cytoscape shape: nodes/edges wrapped in `{ data }`.
 * Edges are deliberately sparse + meaningful so it never becomes a hairball:
 *   causal   — explicit chains above (directed)
 *   temporal — consecutive facts WITHIN a topic (the local timeline)
 *   semantic — same-topic neighbours (capped to nearest-in-time pairs) plus
 *              a few cross-topic bridges.
 */
function buildMemFactGraph(facts: MemFact[] = MEM_FACTS) {
  const byId = Object.fromEntries(facts.map((f) => [f.id, f]))
  const nodes = facts.map((f) => ({
    data: {
      id: f.id,
      label: f.label,
      text: f.text,
      type: f.type,
      topic: f.topic,
      date: ISO(f.date),
      entities: f.ents,
      color: MEM_TOPIC_COLORS[f.topic] ?? '#7FB8FF',
    },
  }))
  const edges: { data: Record<string, unknown> }[] = []
  const seen = new Set<string>()
  const add = (s: string, t: string, linkType: MemLinkType, weight = 1) => {
    if (s === t || !byId[s] || !byId[t]) return
    const id = `${linkType}:${s}>${t}`
    if (seen.has(id)) return
    seen.add(id)
    edges.push({ data: { id, source: s, target: t, linkType, weight } })
  }
  MEM_CAUSAL.forEach(([s, t]) => add(s, t, 'causal', 2))
  const topics: Record<string, MemFact[]> = {}
  facts.forEach((f) => {
    ;(topics[f.topic] = topics[f.topic] || []).push(f)
  })
  Object.values(topics).forEach((group) => {
    group.sort((a, b) => +new Date(a.date) - +new Date(b.date))
    for (let i = 1; i < group.length; i++) add(group[i - 1].id, group[i].id, 'temporal', 1)
    for (let i = 0; i < group.length; i++) {
      for (let j = i + 1; j <= Math.min(i + 2, group.length - 1); j++) {
        add(group[i].id, group[j].id, 'semantic', 1)
      }
    }
  })
  // cross-topic semantic bridges (the interesting connections)
  ;([['f10', 'f13'], ['f11', 'f7'], ['f17', 'f22'], ['f6', 'f8'], ['f5', 'f20']] as [string, string][]).forEach(
    ([s, t]) => add(s, t, 'semantic', 1),
  )
  return { nodes, edges, total_units: nodes.length }
}

/** ENTITY co-occurrence graph in live Cytoscape shape. */
function buildMemEntityGraph(facts: MemFact[] = MEM_FACTS, minCount = 1) {
  const count: Record<string, number> = {}
  const co: Record<string, number> = {}
  facts.forEach((f) => {
    f.ents.forEach((e) => {
      count[e] = (count[e] || 0) + 1
    })
    for (let i = 0; i < f.ents.length; i++)
      for (let j = i + 1; j < f.ents.length; j++) {
        const k = [f.ents[i], f.ents[j]].sort().join('|')
        co[k] = (co[k] || 0) + 1
      }
  })
  const palette = ['#5B9BD5', '#E69F00', '#009E73', '#CC79A7', '#F0E442', '#D55E00', '#56B4E9', '#B39DDB']
  const kinds = [...new Set(MEM_ENTITIES.map((e) => e.kind))]
  const kept = MEM_ENTITIES.filter((e) => (count[e.id] || 0) >= minCount && count[e.id])
  const nodes = kept.map((e) => ({
    data: {
      id: e.id,
      label: e.label,
      mentionCount: count[e.id] || 0,
      color: palette[kinds.indexOf(e.kind) % palette.length],
    },
  }))
  const have = new Set(nodes.map((n) => n.data.id))
  const edges = Object.entries(co)
    .filter(([k]) => {
      const [a, b] = k.split('|')
      return have.has(a) && have.has(b)
    })
    .map(([k, w]) => {
      const [a, b] = k.split('|')
      return { data: { id: `co:${k}`, source: a, target: b, linkType: 'cooccurrence', weight: w } }
    })
  return { nodes, edges, total_entities: nodes.length }
}

// ─── FU2: large synthetic bank + server-side subgraph port ──────────
// `big` exercises the subgraph endpoint: ~600 fact nodes, a handful of
// high-degree hubs, varied timestamps. The wrapper swaps to the subgraph
// hook when nodeCount > 240, so this bank must clear that threshold.
const BIG_NODE_COUNT = 600
const BIG_HUBS = ['big-hub-0', 'big-hub-1', 'big-hub-2', 'big-hub-3']
const BIG_TOPICS = ['power', 'thermal', 'storage', 'models', 'network', 'agents']
const BIG_LINK_TYPES: MemLinkType[] = ['semantic', 'causal', 'temporal', 'semantic']

let _bigGraphCache: { nodes: { data: Record<string, unknown> }[]; edges: { data: Record<string, unknown> }[] } | null = null

function buildBigMemFactGraph() {
  if (_bigGraphCache) return _bigGraphCache
  const nodes: { data: Record<string, unknown> }[] = []
  // hubs first (high degree), then leaves. Timestamps fan across ~120 days
  // so recency ranking has something to sort.
  const baseTs = Date.parse('2026-02-01T00:00:00Z')
  for (let i = 0; i < BIG_NODE_COUNT; i++) {
    const id = i < BIG_HUBS.length ? BIG_HUBS[i] : `big-n${i}`
    const topic = BIG_TOPICS[i % BIG_TOPICS.length]
    // spread timestamps: newer ids are more recent (and hubs are oldest)
    const ts = new Date(baseTs + i * 6 * 3600 * 1000).toISOString()
    nodes.push({
      data: {
        id,
        label: `${topic} fact ${i}`,
        text: `Synthetic ${topic} memory unit ${i} for subgraph scale testing.`,
        type: ['world', 'experience', 'observation'][i % 3],
        topic,
        date: ts,
        created_at: ts,
        entities: [topic],
        color: MEM_TOPIC_COLORS[topic] ?? '#7FB8FF',
      },
    })
  }
  const edges: { data: Record<string, unknown> }[] = []
  const seen = new Set<string>()
  const add = (s: string, t: string, linkType: MemLinkType, weight = 1) => {
    if (s === t) return
    const id = `${linkType}:${s}>${t}`
    if (seen.has(id)) return
    seen.add(id)
    edges.push({ data: { id, source: s, target: t, linkType, weight } })
  }
  // each non-hub leaf attaches to one hub (round-robin) → hubs get huge degree
  for (let i = BIG_HUBS.length; i < BIG_NODE_COUNT; i++) {
    const hub = BIG_HUBS[i % BIG_HUBS.length]
    add(hub, `big-n${i}`, BIG_LINK_TYPES[i % BIG_LINK_TYPES.length], 1 + (i % 3))
  }
  // a temporal chain through every 10th leaf so depth-2 ego has reach
  for (let i = BIG_HUBS.length + 10; i < BIG_NODE_COUNT; i += 10) {
    add(`big-n${i - 10}`, `big-n${i}`, 'temporal', 1)
  }
  // hub-to-hub causal bridges (keep the hubs salient)
  for (let i = 1; i < BIG_HUBS.length; i++) add(BIG_HUBS[i - 1], BIG_HUBS[i], 'causal', 3)
  _bigGraphCache = { nodes, edges }
  return _bigGraphCache
}

// JS port of the backend graph-math (degree / recency / ego / induce).
const _SUB_TYPE_WEIGHT: Record<string, number> = { causal: 4, temporal: 3, cooccurrence: 2, semantic: 1 }
const _subTypeWeight = (t: string | undefined) => _SUB_TYPE_WEIGHT[t ?? 'semantic'] ?? 1
const _edgeEnds = (e: { data: Record<string, unknown> }) => {
  const d = e.data
  return {
    s: String(d.source ?? d.from ?? ''),
    t: String(d.target ?? d.to ?? ''),
    lt: String(d.linkType ?? d.type ?? 'semantic'),
    w: typeof d.weight === 'number' ? d.weight : 1,
  }
}
const _nid = (n: { data: Record<string, unknown> }) => String(n.data.id)

function _subAdjacency(graph: { nodes: { data: Record<string, unknown> }[]; edges: { data: Record<string, unknown> }[] }) {
  const ids = new Set(graph.nodes.map(_nid))
  const adj = new Map<string, { t: string; lt: string; w: number }[]>()
  for (const e of graph.edges) {
    const { s, t, lt, w } = _edgeEnds(e)
    if (s === t || !ids.has(s) || !ids.has(t)) continue
    ;(adj.get(s) ?? adj.set(s, []).get(s)!).push({ t, lt, w })
    ;(adj.get(t) ?? adj.set(t, []).get(t)!).push({ t: s, lt, w })
  }
  return adj
}

function _subRankByDegree(graph: { nodes: { data: Record<string, unknown> }[]; edges: { data: Record<string, unknown> }[] }) {
  const adj = _subAdjacency(graph)
  const ids = graph.nodes.map(_nid)
  const order = new Map(ids.map((id, k) => [id, k]))
  const score = new Map(ids.map((id) => [id, (adj.get(id) ?? []).reduce((a, e) => a + _subTypeWeight(e.lt) * e.w, 0)]))
  return [...ids].sort((a, b) => (score.get(b)! - score.get(a)!) || (order.get(a)! - order.get(b)!))
}

function _subTs(n: { data: Record<string, unknown> }) {
  const d = n.data
  for (const k of ['t', 'created_at', 'timestamp', 'updated_at', 'date']) {
    const v = d[k]
    if (v) return String(v)
  }
  return ''
}

function _subRankByRecency(graph: { nodes: { data: Record<string, unknown> }[]; edges: { data: Record<string, unknown> }[] }) {
  const order = new Map(graph.nodes.map((n, k) => [_nid(n), k]))
  return [...graph.nodes]
    .sort((a, b) => {
      const ta = _subTs(a)
      const tb = _subTs(b)
      if ((ta === '') !== (tb === '')) return ta === '' ? 1 : -1 // missing last
      if (ta !== tb) return ta < tb ? 1 : -1 // newest first
      return order.get(_nid(a))! - order.get(_nid(b))!
    })
    .map(_nid)
}

function _subEgoBfs(
  graph: { nodes: { data: Record<string, unknown> }[]; edges: { data: Record<string, unknown> }[] },
  center: string,
  depth: number,
  limit: number,
) {
  const adj = _subAdjacency(graph)
  const ids = new Set(graph.nodes.map(_nid))
  if (!ids.has(center)) return new Set<string>()
  const reached = new Set([center])
  let frontier = [center]
  for (let d = 0; d < Math.max(1, depth); d++) {
    const nxt: string[] = []
    for (const cur of frontier) {
      const nbrs = [...(adj.get(cur) ?? [])].sort((x, y) => _subTypeWeight(y.lt) * y.w - _subTypeWeight(x.lt) * x.w)
      for (const { t } of nbrs) {
        if (!reached.has(t)) {
          reached.add(t)
          nxt.push(t)
          if (reached.size >= limit) return reached
        }
      }
    }
    frontier = nxt
  }
  return reached
}

function _subInduce(
  graph: { nodes: { data: Record<string, unknown> }[]; edges: { data: Record<string, unknown> }[] },
  keep: Set<string>,
) {
  const nodes = graph.nodes.filter((n) => keep.has(_nid(n)))
  const edges = graph.edges.filter((e) => {
    const { s, t } = _edgeEnds(e)
    return keep.has(s) && keep.has(t) && s !== t
  })
  return { nodes, edges }
}

// Resolve the source graph for a bank+kind. Only `big` uses the large
// synthetic graph; every other bank reuses the existing primary fixtures.
function graphForBank(bank: string, kind: 'memories' | 'entities') {
  if (bank === 'empty') return { nodes: [], edges: [] } // empty bank → empty graph
  if (kind === 'entities') return buildMemEntityGraph()
  // Single source of truth for bank→fact-graph, shared by the full-graph route
  // and the subgraph route so ego/top-K slices stay consistent with the whole.
  if (bank === 'big') return buildBigMemFactGraph() // FU2 scale bank
  if (bank === 'ingest') return buildDenseFactGraph() // #756 FU1 dense star
  return buildMemFactGraph()
}

function buildBankSubgraphRoute(url: string, match: RegExpMatchArray) {
  const bank = bankFrom(match)
  let params = new URLSearchParams()
  try {
    params = new URL(url, 'http://x').searchParams
  } catch {
    /* path-only */
  }
  const kind = params.get('kind') === 'entities' ? 'entities' : 'memories'
  const mode = params.get('mode') === 'ego' ? 'ego' : 'top'
  const limit = Math.min(Number(params.get('limit') ?? 240) || 240, 500)
  const graph = graphForBank(bank, kind)
  const totalNodes = graph.nodes.length
  const totalEdges = graph.edges.length

  let keep: Set<string>
  if (mode === 'ego') {
    const node = params.get('node') ?? ''
    const depth = Math.min(Number(params.get('depth') ?? 1) || 1, 2)
    keep = _subEgoBfs(graph, node, depth, limit)
  } else {
    const by = params.get('by') || (kind === 'entities' ? 'degree' : 'recency')
    const ranked = by === 'degree' ? _subRankByDegree(graph) : _subRankByRecency(graph)
    const topK = Math.min(Number(params.get('top_k') ?? 200) || 200, 500)
    keep = new Set(ranked.slice(0, Math.min(topK, limit)))
  }
  const sub = _subInduce(graph, keep)
  const out: Record<string, unknown> = { nodes: sub.nodes, edges: sub.edges }
  out.total_edges = totalEdges
  out[kind === 'entities' ? 'total_entities' : 'total_units'] = totalNodes
  out.returned_nodes = sub.nodes.length
  out.returned_edges = sub.edges.length
  out.truncated = sub.nodes.length < totalNodes
  out.mode = mode
  out.center = params.get('node')
  return out
}

// banks list — one rich primary + 3 supporting banks + the `big` scale bank
const MEM_BANKS = [
  { bank_id: 'primary', name: 'primary', mission: 'memory of the strix-halo-01 operator', fact_count: MEM_FACTS.length },
  { bank_id: 'big', name: 'big', mission: 'large synthetic bank for subgraph scale testing', fact_count: BIG_NODE_COUNT },
  { bank_id: 'hermes', name: 'hermes', mission: 'the bundled home agent', fact_count: 612 },
  { bank_id: 'scratch', name: 'scratch', mission: 'ephemeral working memory', fact_count: 88 },
  { bank_id: 'ingest', name: 'ingest', mission: 'raw document drop', fact_count: 4310 },
  // a genuinely empty bank — exercises the graph empty-state shell (no facts,
  // no entities) so the explorer's "No graph data" placeholder is testable.
  { bank_id: 'empty', name: 'empty', mission: 'no memories yet', fact_count: 0 },
]

function buildMemoryEngine() {
  return {
    enabled: true,
    engine: 'hindsight',
    reachable: true,
    version: '0.7.2',
    features: { observations: true, mcp: true, mental_models: true },
    banks_total: MEM_BANKS.length,
  }
}

function buildMemoryBanks() {
  return { banks: MEM_BANKS }
}

// bank-id is the 4th path segment: /api/memory/banks/<bank>/…
function bankFrom(match: RegExpMatchArray): string {
  return decodeURIComponent(match[1] ?? 'primary')
}

function buildBankStats(_url: string, match: RegExpMatchArray) {
  const bank = bankFrom(match)
  const graph = buildMemFactGraph()
  const nodesByType = graph.nodes.reduce<Record<string, number>>((acc, n) => {
    const t = String((n.data as { type: string }).type)
    acc[t] = (acc[t] || 0) + 1
    return acc
  }, {})
  const linksByType = graph.edges.reduce<Record<string, number>>((acc, e) => {
    const t = String((e.data as { linkType: string }).linkType)
    acc[t] = (acc[t] || 0) + 1
    return acc
  }, {})
  // non-primary banks: synthesize plausible scaled counts from fact_count
  const meta = MEM_BANKS.find((b) => b.bank_id === bank)
  if (bank !== 'primary' && meta) {
    const n = meta.fact_count ?? 0
    return {
      bank_id: bank,
      total_nodes: n,
      total_links: Math.round(n * 1.6),
      total_documents: Math.round(n / 6),
      nodes_by_fact_type: {
        world: Math.round(n * 0.32),
        experience: Math.round(n * 0.41),
        observation: Math.round(n * 0.27),
      },
      links_by_link_type: {
        semantic: Math.round(n * 0.9),
        temporal: Math.round(n * 0.5),
        causal: Math.round(n * 0.2),
      },
      pending_operations: bank === 'ingest' ? 7 : 0,
      failed_operations: bank === 'ingest' ? 1 : 0,
      operations_by_status: {
        completed: Math.round(n / 6),
        pending: bank === 'ingest' ? 7 : 0,
        failed: bank === 'ingest' ? 1 : 0,
      },
      last_consolidated_at: '2026-06-11T03:00:00.000Z',
      pending_consolidation: bank === 'ingest' ? 2 : 0,
      failed_consolidation: 0,
      total_observations: Math.round(n * 0.27),
    }
  }
  return {
    bank_id: bank,
    total_nodes: graph.nodes.length,
    total_links: graph.edges.length,
    total_documents: 14,
    nodes_by_fact_type: nodesByType,
    links_by_link_type: linksByType,
    pending_operations: 1,
    failed_operations: 0,
    operations_by_status: { completed: 41, pending: 1, processing: 0, failed: 0 },
    last_consolidated_at: '2026-06-12T04:30:00.000Z',
    pending_consolidation: 0,
    failed_consolidation: 0,
    total_observations: nodesByType.observation ?? 0,
  }
}

function buildBankTimeseries() {
  // 21 daily buckets ending 2026-06-12, derived from the fact timeline so
  // the stacked area lines up with the graph's real dates. Days without a
  // fact still carry a low baseline so the chart isn't all zeros.
  const days = 21
  const end = new Date('2026-06-12T00:00:00.000Z')
  const factsByDay: Record<string, MemFactType[]> = {}
  MEM_FACTS.forEach((f) => {
    const key = ISO(f.date).slice(0, 10)
    ;(factsByDay[key] = factsByDay[key] || []).push(f.type)
  })
  const buckets = []
  for (let i = days - 1; i >= 0; i--) {
    const d = new Date(end.getTime() - i * 86400000)
    const key = d.toISOString().slice(0, 10)
    const todays = factsByDay[key] ?? []
    const c = (t: MemFactType) => todays.filter((x) => x === t).length
    // light baseline so the area chart reads as continuous activity
    const base = (d.getUTCDate() % 3) // 0..2 deterministic
    buckets.push({
      time: d.toISOString(),
      world: c('world') + (base === 0 ? 1 : 0),
      experience: c('experience') + (base === 1 ? 1 : 0),
      observation: c('observation') + (base === 2 ? 1 : 0),
    })
  }
  return { bucket_size: 'day', buckets }
}

function buildBankDocuments() {
  const items = [
    { id: 'doc-install-log', created_at: '2026-04-28T10:12:00.000Z', memory_unit_count: 5, tags: ['setup', 'install'], original_text: 'Fresh hal0 install on Debian 13. One-line installer; lemond + Proxmox iGPU/NPU passthrough configured; default chat slot set to Qwen3-Coder-30B.' },
    { id: 'doc-outage-postmortem', created_at: '2026-05-03T03:40:00.000Z', memory_unit_count: 4, tags: ['reliability', 'incident'], original_text: 'Grid power dropped 02:14, box hard-off mid-generation. Lost ~40min queue + one ComfyUI job. Action item: buy a UPS, wire NUT graceful shutdown.' },
    { id: 'doc-thermal-notes', created_at: '2026-05-14T16:40:00.000Z', memory_unit_count: 4, tags: ['performance', 'thermal'], original_text: 'iGPU hit 95C under sustained ComfyUI batch; throughput -30%. Applied -30mV undervolt via ryzenadj, added to boot unit. Peak now ~84C, throughput recovered.' },
    { id: 'doc-storage-prune', created_at: '2026-05-21T10:10:00.000Z', memory_unit_count: 3, tags: ['storage'], original_text: '/var hit 92% from HF pulls + old quants; auto-paused at 90%. Pruned 3 GGUF quants, freed 41GB, resumed pulls.' },
    { id: 'doc-npu-eval', created_at: '2026-05-18T21:00:00.000Z', memory_unit_count: 3, tags: ['models', 'npu'], original_text: 'Swapped NPU chat gemma3:1b -> llama-3.2-3b-npu (voice+embed paused ~14s on FLM restart). llama-3.2 follows tool-call schemas more reliably.' },
    { id: 'doc-operator-profile', created_at: '2026-06-02T22:10:00.000Z', memory_unit_count: 3, tags: ['operator'], original_text: 'Operator prefers terse technical lowercase answers, asks for citations inline, works evenings 8pm-1am; box idle most mornings.' },
  ]
  return { items, total: items.length }
}

function buildMentalModels() {
  const items = [
    { id: 'mm-power-resilience', name: 'Power resilience posture', source_query: 'how does the box handle power loss?', content: 'Since the May 3 outage the operator runs a CyberPower 1500VA UPS (~22min idle runtime) with the NUT daemon set to gracefully shut down at 20% battery. Inference queues are now the main loss surface, not cold boots.', tags: ['reliability'], is_stale: false, last_refreshed_at: '2026-06-10T08:00:00.000Z' },
    { id: 'mm-thermal-envelope', name: 'iGPU thermal envelope', source_query: 'what are the iGPU thermal limits?', content: 'Sustained ComfyUI load used to push the Radeon 8060S to 95C and throttle img throughput ~30%. A -30mV ryzenadj undervolt (now in the boot unit) holds peak at ~84C with baseline throughput.', tags: ['performance'], is_stale: false, last_refreshed_at: '2026-06-09T19:30:00.000Z' },
    { id: 'mm-operator-style', name: 'Operator interaction style', source_query: 'how should I talk to the operator?', content: 'Terse, technical, lowercase. Inline citations expected — traceability over prose. Active 20:00–01:00; mornings are safe for heavy maintenance.', tags: ['operator'], is_stale: true, last_refreshed_at: '2026-05-28T23:00:00.000Z' },
  ]
  return { items, total: items.length }
}

function buildDirectives() {
  const items = [
    { id: 'dir-citations', name: 'Always cite sources', content: 'Include inline citations / source references in answers; the operator values traceability over prose.', priority: 1, is_active: true, tags: ['operator'] },
    { id: 'dir-terse', name: 'Keep it terse', content: 'Default to terse, technical, lowercase responses. Expand only when explicitly asked.', priority: 2, is_active: true, tags: ['operator'] },
    { id: 'dir-maintenance-window', name: 'Heavy work in the morning', content: 'Schedule consolidation, pulls, and restarts before noon — the box is idle most mornings and busy 20:00–01:00.', priority: 3, is_active: true, tags: ['operator', 'reliability'] },
    { id: 'dir-disk-guard', name: 'Guard /var headroom', content: 'Keep /var below 85%. Pause HF pulls and surface a prune suggestion before hitting the 90% auto-pause threshold.', priority: 2, is_active: false, tags: ['storage'] },
  ]
  return { items, total: items.length }
}

// Real hindsight-api 0.8.4 envelope: {bank_id, total, limit, offset,
// operations: [{id, task_type, status, ...}]} — NOT {items, operation_id,
// operation_type} (#1645).
function buildBankOperations(_url: string, match: RegExpMatchArray) {
  const bank = bankFrom(match)
  if (bank === 'ingest') {
    const operations = [
      { id: 'op-ing-9012', task_type: 'document_ingest', status: 'processing', created_at: '2026-06-12T22:14:00.000Z', error_message: null, retry_count: 0 },
      { id: 'op-ing-9011', task_type: 'document_ingest', status: 'pending', created_at: '2026-06-12T22:13:30.000Z', error_message: null, retry_count: 0 },
      { id: 'op-ing-8990', task_type: 'consolidation', status: 'failed', created_at: '2026-06-12T19:02:00.000Z', error_message: 'embedding backend timed out after 30s', retry_count: 2 },
      { id: 'op-ing-8975', task_type: 'document_ingest', status: 'completed', created_at: '2026-06-12T18:40:00.000Z', error_message: null, retry_count: 0 },
    ]
    return { bank_id: bank, total: operations.length, limit: 50, offset: 0, operations }
  }
  const operations = [
    { id: 'op-7741', task_type: 'consolidation', status: 'completed', created_at: '2026-06-12T04:30:00.000Z', error_message: null, retry_count: 0 },
    { id: 'op-7742', task_type: 'observation_extract', status: 'pending', created_at: '2026-06-12T22:06:00.000Z', error_message: null, retry_count: 0 },
    { id: 'op-7710', task_type: 'document_ingest', status: 'completed', created_at: '2026-06-05T11:21:00.000Z', error_message: null, retry_count: 0 },
  ]
  return { bank_id: bank, total: operations.length, limit: 50, offset: 0, operations }
}

// Route wrappers: the allowlist passes the query-stripped path as the first
// arg, so `?type=`/`?q=`/`?min_count=`/`?limit=` aren't visible here. We
// return the full graph for the bank — the UI's normalizeGraph + client-side
// filters handle type/q narrowing, and the prototype keeps every mentioned
// entity (min_count defaults to 1), so the unfiltered payload is the right
// forced-mock shape.
// A deliberately dense star graph for the `ingest` bank: one hub fact with
// 40 semantic neighbours (+ a couple causal/temporal) so the Direction-C ego
// ring-cap (>24 neighbours → "+K more") is exercisable in mock-mode e2e.
function buildDenseFactGraph() {
  const FT = ['world', 'experience', 'observation']
  const nodes = [
    { data: { id: 'hub', label: 'ingest hub note', text: 'Central note that many ingested chunks reference.', type: 'world', date: '2026-06-01T09:00', entities: 'ingest, hal0', color: '#7fb8ff' } },
  ]
  const edges: { data: Record<string, unknown> }[] = []
  for (let i = 0; i < 40; i++) {
    const id = 'leaf' + i
    nodes.push({
      data: {
        id, label: 'ingested chunk ' + i, text: 'Ingested document chunk #' + i + ' referencing the hub.',
        type: FT[i % 3], date: '2026-06-0' + (1 + (i % 7)) + 'T1' + (i % 9) + ':00',
        entities: 'ingest', color: '#7fb8ff',
      },
    })
    // most are semantic (the noisy bulk); a few causal/temporal stay salient.
    const linkType = i < 2 ? 'causal' : i < 5 ? 'temporal' : 'cooccurrence'
    edges.push({ data: { id: 'e' + i, source: 'hub', target: id, linkType: i < 8 ? linkType : 'semantic', weight: i < 8 ? 3 : 1 } })
  }
  return { nodes, edges, total_units: nodes.length }
}

function buildMemFactGraphRoute(_url: string, match: RegExpMatchArray) {
  const bank = bankFrom(match)
  const g = graphForBank(bank, 'memories')
  return { ...g, total_units: g.nodes.length }
}

function buildMemEntityGraphRoute() {
  return buildMemEntityGraph()
}

function buildBankRecall(url: string) {
  // POST body isn't available to builders; filter loosely by a `q=` query
  // param if the caller put one on the URL, else return the strongest hits.
  let q = ''
  try {
    const u = new URL(url, 'http://x')
    q = (u.searchParams.get('q') ?? u.searchParams.get('query') ?? '').toLowerCase()
  } catch {
    /* path-only — no query */
  }
  let hits = MEM_FACTS
  if (q) {
    const terms = q.split(/\s+/).filter(Boolean)
    const filtered = MEM_FACTS.filter((f) =>
      terms.some((t) => (f.text + ' ' + f.label + ' ' + f.topic).toLowerCase().includes(t)),
    )
    if (filtered.length) hits = filtered
  }
  const results = hits.slice(0, 6).map((f) => ({
    id: f.id,
    text: f.text,
    type: f.type,
    entities: f.ents,
    occurred_start: ISO(f.date),
    tags: [f.topic],
  }))
  return { results }
}

function buildBankReflect() {
  return {
    text: 'Over the last six weeks the strix-halo-01 operator hardened a fresh hal0 install into a resilient daily driver. Two incidents drove the biggest changes: the May 3 power outage (now mitigated by a CyberPower UPS + NUT graceful shutdown) and sustained ComfyUI thermal throttling (resolved with a −30mV iGPU undervolt). Storage pressure from HuggingFace pulls was contained by the 90% auto-pause and a 41 GB prune. The operator favours terse, cited answers and works evenings — schedule heavy maintenance for the idle mornings.',
    based_on: { facts: MEM_FACTS.length, documents: 6, mental_models: 3 },
  }
}

// ADR-0023 graph-extraction gate status (served by routes/memory.py, not the
// admin proxy). Mocked so the Agent pointer's "Graph extraction" panel renders
// a clean OFF state in forced-mock mode instead of a 500.
function buildMemoryGraphStatus() {
  return {
    enabled: false,
    extraction_slot: 'utility',
    slot_resolves: true,
    available_slots: ['agent', 'utility'],
    in_flight: 0,
    builds_ok: 0,
    errors: 0,
    last_built_at: null,
    last_error: null,
  }
}

// ─── Runner Images (Models → Runner Images tab) ───────────────────────────
// Shape mirrors /api/runner-images (registry rows synced from
// hal0-runner-images images.json + GHCR). One row is enough for the
// catalog list + card to render; the pulls list must be an ARRAY — the
// downloads pane calls `.filter` on it directly.
function buildRunnerImages() {
  return {
    images: [
      {
        id: 'rocmfpx',
        image: 'ghcr.io/hal0ai/hal0-rocmfpx',
        tag: 'ade07ba',
        digest: 'sha256:0000000000000000000000000000000000000000000000000000000000000000',
        size_bytes: 7_340_032_000,
        manifest_key: null,
        ownership: 'referenced',
        publish: 'manual',
        notes: 'ROCmFPX runner — HIP+Vulkan single build (mock fixture).',
        build: null,
        local_path: null,
        downloaded_at: null,
        discovered_at: '2026-08-08T00:00:00Z',
        updated_at: '2026-08-08T00:00:00Z',
        extra: {},
      },
    ],
  }
}

function buildRunnerImagesDownloaded() {
  return { images: [] }
}

function buildRunnerImagesPulls() {
  return []
}

// ─── Builder lookup — keyed by the AllowRow.key in `./mock.ts`'s
// `MOCK_ALLOWLIST`. The route table there only carries `{re, key,
// networkFirst}` (no function refs), so it stays cheap to construct eagerly;
// this map is only ever touched from behind the dynamic `import()` in
// `mock.ts`'s `buildMockPayload`, once a substitution has already been
// decided. Keys MUST stay in sync with `MOCK_ALLOWLIST` in `./mock.ts`.
export const MOCK_BUILDERS: Record<string, Builder> = {
  status: buildStatus,
  slots: buildSlots,
  slotDetail: buildSlotDetail,
  models: buildModels,
  modelUpdatesCheck: buildModelUpdatesCheck,
  backends: buildBackends,
  capabilities: buildCapabilities,
  hardware: buildHardware,
  npuOccupancy: buildNpuOccupancy,
  journal: buildJournal,
  updatesState: buildUpdateState,
  secrets: buildSecrets,
  metaEnums: buildMetaEnums,
  profiles: buildProfiles,
  stacks: buildStacksList,
  chatTemplates: buildChatTemplates,
  memoryGraphStatus: buildMemoryGraphStatus,
  runnerImages: buildRunnerImages,
  runnerImagesDownloaded: buildRunnerImagesDownloaded,
  runnerImagesPulls: buildRunnerImagesPulls,
  memoryEngine: buildMemoryEngine,
  memoryBanks: buildMemoryBanks,
  bankTimeseries: buildBankTimeseries,
  bankStats: buildBankStats,
  bankSubgraph: buildBankSubgraphRoute,
  bankEntityGraph: buildMemEntityGraphRoute,
  bankGraph: buildMemFactGraphRoute,
  bankDocuments: buildBankDocuments,
  bankMentalModels: buildMentalModels,
  bankDirectives: buildDirectives,
  bankOperations: buildBankOperations,
  bankRecall: buildBankRecall,
  bankReflect: buildBankReflect,
}
