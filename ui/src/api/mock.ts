// hal0 v3 dashboard — mock fetch harness (Phase B1).
//
// Two activation modes (mirrors ui-vue.bak/src/composables/useMock.js):
//   1. Forced mock: `VITE_MOCK_LEMONADE=1` at build/dev time. Every
//      allowlisted URL returns baked data from `HAL0_DATA` without
//      touching the network.
//   2. Per-endpoint fallback: when a live fetch fails (404 / network
//      error), allowlisted URLs swap in the mock so the UI never crashes
//      on absent endpoints. Real 2xx / 5xx pass through.
//
// `HAL0_DATA` is installed on `window` by `dash/data.jsx` at module
// load. We read it lazily so this file doesn't depend on the dash
// import order. If for any reason it's missing we fall back to an
// empty shape so build doesn't blow up.
//
// Ambient typing lives in `src/types/globals.d.ts` — no local
// `declare global` here (it would conflict on `HAL0_DATA` modifiers).

const FORCED = !!(import.meta.env && (import.meta.env as any).VITE_MOCK_LEMONADE === '1')

export function isMockForced() {
  return FORCED
}

function data(): any {
  return (typeof window !== 'undefined' && window.HAL0_DATA) || {}
}

// ─── Builders — one per endpoint family ───────────────────────────
function buildHealth() {
  const d = data()
  const L = d.lemond || {}
  return {
    loaded: (d.slots || [])
      .filter((s: any) => s.state === 'serving' || s.state === 'ready')
      .map((s: any) => ({ model_name: s.model, backend_url: `http://localhost:${s.port}` })),
    max_loaded: L.budget ?? 4,
    version: L.version ?? 'v10.6.0',
    throughput_mbps: L.throughput ?? null,
    // #221 — `queued` + `coresident` round-trip via the mock so the demo
    // chips keep rendering. Lemonade itself does not surface these on
    // /v1/health today; production hides the chips when they're absent.
    queued: typeof L.queued === 'number' ? L.queued : null,
    coresident: typeof L.coresident === 'boolean' ? L.coresident : null,
  }
}

function buildStats() {
  // /v1/stats fallback (Phase 4, #326). Lemonade's real /v1/stats does
  // not include `throughput_mbps` — throughput is sourced exclusively
  // from /v1/health (see useLemondRollup). DO NOT synthesize
  // `throughput_mbps: 0.0` here as a "completeness" gesture — the
  // footer chip is gated to hide when null/0 and a synthetic zero
  // would re-introduce the misleading "0.0 MB/s" the chip is hiding.
  return {
    time_to_first_token: 0.22,
    tokens_per_second: 45.0,
    prompt_tokens: 312,
    output_tokens: 188,
    input_tokens: 312,
  }
}

function buildStatus() {
  const d = data()
  return {
    hostname: d.host?.name ?? 'halo-strix.local',
    hardware: d.host ?? null,
    slots: d.slots ?? [],
  }
}

function buildSlots() {
  return data().slots ?? []
}

function buildModels() {
  return { models: data().models ?? [] }
}

function buildBackends() {
  const d = data()
  return {
    backends: (d.backends ?? []).map((b: any) => ({
      id: b.name,
      version: b.ver,
      state: b.state,
      usedBy: [],
      recommended: !!b.recommended,
      note: b.note,
      kind: b.kind,
      device: b.device,
    })),
    lemonade: { version: d.lemond?.version, pinned: true, channel: 'stable' },
  }
}

function buildCapabilities() {
  // Capabilities-toml rollup. Mock just lists the design's groups.
  return {
    capabilities: {
      chat: { provider: 'llamacpp:rocm', model: 'qwen3.6-27b-mtp-q4_k_m' },
      embed: { provider: 'llamacpp:rocm', model: 'nomic-embed-text-v1.5' },
      voice: { provider: 'kokoro', model: 'kokoro-v1' },
      img: { provider: 'sdcpp:rocm', model: 'sd-turbo' },
      npu: { provider: 'flm:npu', model: 'gemma3:1b' },
    },
  }
}

function buildHardware() {
  return data().host ?? {}
}

function buildLogs() {
  const d = data()
  return { entries: d.journal ?? [] }
}

function buildUpdateState() {
  return {
    hal0: { current: 'v0.2.1', available: 'v0.2.2', channel: 'stable' },
    lemonade: { current: 'v10.6.0', pinned: true, channel: 'stable' },
    flm: { current: 'v0.9.42', source: 'manual-deb' },
    autoCheck: true,
  }
}

function buildAuthToken() {
  return {
    token_masked: 'hal0-•••••••••••••••••••••••••••••••••',
    issued: '2026-04-12',
  }
}

function buildAllowedOrigins() {
  return { origins: ['http://halo-strix.local:8081', 'http://localhost:5174'] }
}

function buildSecrets() {
  return {
    secrets: [
      { name: 'HF_TOKEN', set: true, masked: 'hf_•••••••••••••••••••••' },
      { name: 'OPENAI_API_KEY', set: false },
      { name: 'ANTHROPIC_API_KEY', set: false },
    ],
  }
}

function buildFirstRunState() {
  return { stage: 'pick', bundle: null }
}

function buildFirstRunCurated() {
  return { bundles: data().bundles ?? [], details: data().bundleDetails ?? {} }
}

// ─── Allowlist (first match wins) ─────────────────────────────────
type Builder = (url: string, match: RegExpMatchArray) => unknown

export const MOCK_ALLOWLIST: ReadonlyArray<{ re: RegExp; build: Builder }> = Object.freeze([
  { re: /^\/v1\/health$/, build: buildHealth },
  { re: /^\/v1\/stats$/, build: buildStats },
  { re: /^\/api\/status$/, build: buildStatus },
  { re: /^\/api\/slots$/, build: buildSlots },
  { re: /^\/api\/slots\/[^/]+$/, build: () => null }, // 404-style — Slot detail not in mock
  { re: /^\/api\/models$/, build: buildModels },
  { re: /^\/api\/backends$/, build: buildBackends },
  { re: /^\/api\/capabilities$/, build: buildCapabilities },
  { re: /^\/api\/hardware$/, build: buildHardware },
  { re: /^\/api\/logs$/, build: buildLogs },
  { re: /^\/api\/updates\/state$/, build: buildUpdateState },
  { re: /^\/api\/auth\/token$/, build: buildAuthToken },
  { re: /^\/api\/auth\/allowed-origins$/, build: buildAllowedOrigins },
  { re: /^\/api\/secrets$/, build: buildSecrets },
  { re: /^\/api\/firstrun\/state$/, build: buildFirstRunState },
  { re: /^\/api\/firstrun\/curated-models$/, build: buildFirstRunCurated },
])

function parsePath(url: string | URL | Request): string | null {
  let s: string
  if (typeof url === 'string') s = url
  else if (url instanceof URL) s = url.pathname + url.search
  else {
    try {
      s = (url as Request).url
    } catch {
      return null
    }
  }
  if (s.startsWith('http')) {
    try {
      return new URL(s).pathname
    } catch {
      return null
    }
  }
  const q = s.indexOf('?')
  return q >= 0 ? s.slice(0, q) : s
}

function matchAllowlist(path: string) {
  for (const row of MOCK_ALLOWLIST) {
    const m = path.match(row.re)
    if (m) return { row, match: m }
  }
  return null
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body ?? null), {
    status: body == null ? 404 : status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Drop-in `fetch` replacement. Forced-mock short-circuits any
 * allowlisted URL. Otherwise we let the real fetch run and only
 * substitute on 404 / network failure for allowlisted URLs.
 */
export async function mockFetch(
  url: string | URL | Request,
  options?: RequestInit,
): Promise<Response> {
  const path = parsePath(url)
  if (!path) return fetch(url as any, options)

  const hit = matchAllowlist(path)

  if (FORCED && hit) {
    return jsonResponse(hit.row.build(path, hit.match))
  }

  let res: Response
  try {
    res = await fetch(url as any, options)
  } catch (e) {
    if (hit) {
      // network-level failure on a mocked path — fall back
      return jsonResponse(hit.row.build(path, hit.match))
    }
    throw e
  }
  if (res.status === 404 && hit) {
    return jsonResponse(hit.row.build(path, hit.match))
  }
  return res
}
