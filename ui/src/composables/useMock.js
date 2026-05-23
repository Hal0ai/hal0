/**
 * useMock — runtime mock harness for the v2 dashboard.
 *
 * Backs the gap between today's backend (Lemonade migration mid-flight)
 * and the v2/v0.3 views. Two activation modes:
 *
 *   1. Forced mock — `VITE_MOCK_LEMONADE=1` at build/dev time. Every
 *      whitelisted URL returns a baked response without ever touching
 *      the network. Lets the dashboard boot with NO backend running
 *      (acceptance criterion of issue #166).
 *   2. Per-endpoint fallback — when the real fetch returns 404 for a
 *      whitelisted URL the mock response is substituted with a
 *      `console.warn` so the UI never crashes on absent endpoints.
 *      Real responses (200, 4xx≠404, 5xx) pass through untouched so
 *      contract drift surfaces fast.
 *
 * Stores opt in: `import { mockFetch } from '@/composables/useMock'`
 * and swap `fetch()` → `mockFetch()`. There is **no** global
 * monkey-patch — `window.fetch` stays vanilla so non-opted-in code,
 * Playwright `page.route()`, and the existing `useApi()` wrapper all
 * behave exactly as before.
 *
 * `MOCK_DATA` is the single source of truth — Playwright fixtures
 * re-export the same constants (`ui/tests/e2e/fixtures/mock-data.ts`)
 * so dev + e2e share one shape vocabulary.
 *
 * Retirement plan — every allowlist row maps to a tracking issue. When
 * the real endpoint ships, drop the matcher from `MOCK_ALLOWLIST` and
 * the per-endpoint dispatch case below. See `docs/dev/web-ui-mocks.md`.
 */
import { computed } from 'vue'

/* ─── MOCK_DATA — port of /tmp/hal0-design-v3/dash/{data,mcp-data}.jsx ── */

/**
 * Top-level keys mirror what the v2 views consume. The shapes are the
 * contract — when backend issues land they MUST emit these fields (or
 * we update both ends in lockstep). See ADR-0008 §4 and the per-issue
 * mapping in `docs/dev/web-ui-mocks.md`.
 */
export const MOCK_DATA = Object.freeze({
  host: {
    name: 'halo-strix.local',
    uptime: '14d 02:11',
    cpu: 'AMD Ryzen AI Max+ PRO 395',
    cores: '16c · 32t',
    gpu: 'Radeon Graphics (gfx1151, Strix Halo)',
    ram: { total: 128, free: 74, used: 54 },
    npu: { present: true, columns: 8, ctx: 1 },
  },

  lemonade: {
    status: 'up',
    version: 'v10.6.0',
    loaded: 3,
    budget: 4,
    throughput: 12.4,
    queued: 0,
    coresident: true,
  },

  slots: [
    {
      name: 'primary', type: 'llm', device: 'gpu-rocm',
      model: 'qwen3.6-27b-mtp-q4_k_m', model_id: 'qwen3.6-27b-mtp',
      modelLong: 'unsloth/Qwen3.6-27B-A3B-MTP-GGUF',
      group: 'chat', state: 'serving', isDefault: true, port: 8092, pid: 28471,
      metrics: { toks: 45, ttft: 220, ctx: 8192, kv: null, mem: 18.8 },
      spark: [3, 5, 7, 6, 8, 9, 10, 8, 9, 11, 12, 9, 10, 11, 13, 10],
    },
    {
      name: 'agent', type: 'llm', device: 'npu',
      model: 'gemma3:1b', model_id: 'gemma3-1b-npu',
      modelLong: 'google/gemma-3-1b-it-flm',
      group: 'npu', state: 'ready', isDefault: true, coresident: true,
      port: 8093, pid: 28482,
      metrics: { toks: 40, ttft: 280, ctx: 4096, kv: 66, mem: 1.0 },
      spark: [2, 3, 4, 3, 5, 4, 6, 5, 4, 6, 5, 7, 6, 5, 4, 5],
    },
    {
      name: 'coder', type: 'llm', device: 'gpu-rocm',
      model: 'qwen3-coder-30b-a3b', model_id: 'qwen3-coder-30b',
      modelLong: 'unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF',
      group: 'chat', state: 'idle', port: 8094, pid: 28491,
      metrics: { toks: 0, ttft: null, ctx: 32768, kv: null, mem: 18.6 },
      spark: [0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    },
    {
      name: 'embed', type: 'embedding', device: 'gpu-rocm',
      model: 'nomic-embed-text-v1.5', model_id: 'nomic-v1.5',
      modelLong: 'nomic-ai/nomic-embed-text-v1.5-GGUF',
      group: 'embed', state: 'ready', isDefault: true, port: 8095, pid: 28498,
      metrics: { rpm: 124, lat: 18, dim: 768, mem: 0.35 },
      spark: [4, 5, 5, 6, 5, 7, 8, 6, 7, 8, 9, 7, 8, 9, 8, 7],
    },
    {
      name: 'rerank', type: 'reranking', device: 'gpu-rocm',
      model: 'bge-reranker-v2-m3', model_id: 'bge-reranker-v2',
      modelLong: 'BAAI/bge-reranker-v2-m3-q4_k_m',
      group: 'embed', state: 'idle', isDefault: true, port: 8096, pid: 28502,
      metrics: { rpm: 22, lat: 32, maxDocs: 200, mem: 0.4 },
      spark: [1, 0, 2, 1, 1, 0, 1, 2, 1, 0, 1, 1, 2, 1, 0, 1],
    },
    {
      name: 'stt-npu', type: 'transcription', device: 'npu',
      model: 'whisper-v3-turbo', model_id: 'whisper-v3-turbo-npu',
      modelLong: 'openai/whisper-large-v3-turbo-flm',
      group: 'npu', state: 'ready', isDefault: true, coresident: true,
      port: 8093, pid: 28482,
      metrics: { rpm: 8, xrt: 0.18, precision: 'Q4_K_M', mem: 0.4 },
    },
    {
      name: 'embed-npu', type: 'embedding', device: 'npu',
      model: 'embed-gemma-300m', model_id: 'embed-gemma-300m-npu',
      modelLong: 'google/embed-gemma-300m-flm',
      group: 'npu', state: 'ready', coresident: true, port: 8093, pid: 28482,
      metrics: { rpm: 0, lat: null, dim: 768, mem: 0.35 },
    },
    {
      name: 'tts', type: 'tts', device: 'cpu',
      model: 'kokoro-v1', model_id: 'kokoro-v1',
      modelLong: 'hexgrad/Kokoro-82M',
      group: 'voice', state: 'ready', isDefault: true, cpuOnly: true,
      port: 8097, pid: 28510,
      metrics: { rpm: 4, secs: 47, voice: 'af_heart', mem: 0.4 },
    },
    {
      name: 'img', type: 'image', device: 'gpu-rocm',
      model: 'sd-turbo', model_id: 'sd-turbo',
      modelLong: 'stabilityai/sd-turbo',
      group: 'img', state: 'idle', isDefault: true, port: 8098, pid: 28518,
      metrics: { rpm: 2, avg: 4.1, res: '512×512', mem: 1.2 },
    },
  ],

  bundles: [
    { id: 'lite', name: 'Lite', ram: 16, sizeGB: 1.2,
      desc: 'Chat only — a small LLM on CPU/GPU.',
      includes: [
        { label: 'chat (1.2B params)', active: true },
        { label: 'embed', active: false },
        { label: 'voice', active: false },
        { label: 'image', active: false },
      ] },
    { id: 'default', name: 'Default', ram: 32, sizeGB: 8.4,
      desc: 'Mainstream chat + embed + transcription + TTS.',
      includes: [
        { label: 'chat (qwen3.5-9b)', active: true },
        { label: 'embed (nomic-v1.5)', active: true },
        { label: 'voice (whisper-base + kokoro)', active: true },
        { label: 'image', active: false },
      ] },
    { id: 'pro', name: 'Pro', ram: 64, sizeGB: 38,
      desc: 'Chat + coder + rerank + full A/V + image.',
      includes: [
        { label: 'chat + coder (qwen3.6-27b, qwen3-coder-30b)', active: true },
        { label: 'embed + rerank', active: true },
        { label: 'voice', active: true },
        { label: 'image (sd-turbo)', active: true },
      ] },
    { id: 'max', name: 'Max', ram: 100, sizeGB: 75,
      desc: 'Pro + NPU trio + bigger models.', recommended: true,
      includes: [
        { label: 'chat + coder + NPU agent', active: true },
        { label: 'embed + rerank + embed-npu', active: true },
        { label: 'voice (whisper-large + kokoro + stt-npu)', active: true },
        { label: 'image (flux-2-klein)', active: true },
      ] },
  ],

  journal: [
    { ts: '14:02:11.117', source: 'lemond', level: 'ok',   msg: "loaded model 'qwen3.6-27b-mtp' via llamacpp:rocm" },
    { ts: '14:02:11.514', source: 'hal0',   level: 'ok',   msg: 'slot:primary state idle → ready' },
    { ts: '14:02:14.230', source: 'hal0',   level: 'ok',   msg: 'slot:agent coresident with stt-npu, embed-npu' },
    { ts: '14:02:18.443', source: 'lemond', level: 'warn', msg: 'nuclear evict-all candidate avoided (file-not-found)' },
    { ts: '14:02:20.117', source: 'lemond', level: 'ok',   msg: '/v1/chat/completions primary 45 tok/s TTFT 220ms' },
    { ts: '14:02:22.290', source: 'hal0',   level: 'ok',   msg: "omnirouter dispatched 'generate_image' → slot img" },
  ],

  models: [
    { id: 'qwen3.6-27b-mtp', longName: 'Qwen3.6-27B-MTP', repo: 'unsloth/Qwen3.6-27B-A3B-MTP-GGUF:Q4_K_M', params: '27B', size: '18.8 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
    { id: 'qwen3-coder-30b', longName: 'Qwen3-Coder-30B-A3B', repo: 'unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q4_K_M', params: '30B', size: '18.6 GB', labels: ['chat', 'coder', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
    { id: 'qwen3.5-9b', longName: 'Qwen3.5-9B', repo: 'Qwen/Qwen3.5-9B-Instruct-GGUF:Q4_K_M', params: '9B', size: '5.4 GB', labels: ['chat', 'tool-calling', 'vision'], type: 'llm', device: 'rocm', ns: 'blessed', installed: false, runtime: 'llamacpp' },
    { id: 'gemma3-1b-npu', longName: 'gemma3:1b (NPU)', repo: 'google/gemma-3-1b-it-flm', params: '1B', size: '1.0 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'npu', ns: 'blessed', installed: true, runtime: 'flm' },
    { id: 'llama-3.2-3b-npu', longName: 'Llama-3.2-3B (NPU)', repo: 'meta-llama/llama-3.2-3b-flm', params: '3B', size: '2.4 GB', labels: ['chat'], type: 'llm', device: 'npu', ns: 'blessed', installed: false, runtime: 'flm' },
    { id: 'nomic-v1.5', longName: 'nomic-embed-text-v1.5', repo: 'nomic-ai/nomic-embed-text-v1.5-GGUF', params: '350M', size: '350 MB', labels: ['embeddings'], type: 'embedding', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
    { id: 'bge-reranker-v2', longName: 'bge-reranker-v2-m3', repo: 'BAAI/bge-reranker-v2-m3-q4_k_m', params: '400M', size: '400 MB', labels: ['reranking'], type: 'reranking', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
    { id: 'whisper-large-v3', longName: 'whisper-large-v3', repo: 'openai/whisper-large-v3', params: '1.5B', size: '3.0 GB', labels: ['transcription'], type: 'transcription', device: 'cpu', ns: 'blessed', installed: false, runtime: 'whispercpp' },
    { id: 'whisper-base', longName: 'whisper-base', repo: 'openai/whisper-base', params: '74M', size: '150 MB', labels: ['transcription'], type: 'transcription', device: 'cpu', ns: 'blessed', installed: true, runtime: 'whispercpp' },
    { id: 'kokoro-v1', longName: 'kokoro-v1', repo: 'hexgrad/Kokoro-82M', params: '82M', size: '400 MB', labels: ['tts'], type: 'tts', device: 'cpu', ns: 'blessed', installed: true, runtime: 'kokoro' },
    { id: 'sd-turbo', longName: 'sd-turbo', repo: 'stabilityai/sd-turbo', params: '1.2B', size: '1.2 GB', labels: ['image'], type: 'image', device: 'rocm', ns: 'blessed', installed: true, runtime: 'sdcpp' },
    { id: 'flux-2-klein-9b', longName: 'Flux-2-Klein-9B', repo: 'black-forest-labs/flux-2-klein-9b', params: '9B', size: '12 GB', labels: ['image', 'edit'], type: 'image', device: 'rocm', ns: 'blessed', installed: false, runtime: 'sdcpp' },
    { id: 'user.phi-4-mini', longName: 'user.Phi-4-Mini', repo: 'microsoft/Phi-4-mini-instruct-GGUF', params: '3.8B', size: '2.3 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'pulled', installed: true, runtime: 'llamacpp' },
    { id: 'user.bert-base', longName: 'user.bert-base', repo: 'google-bert/bert-base-uncased', params: '110M', size: '440 MB', labels: ['embeddings'], type: 'embedding', device: 'cpu', ns: 'pulled', installed: true, runtime: 'llamacpp' },
  ],

  /**
   * Same shape `useBackendsStore` expects (slice #165). Keep this and
   * the store's `MOCK_BACKENDS` in sync until #142/#145 lands a real
   * `/api/backends`.
   */
  backends: [
    { id: 'llamacpp:rocm',   version: 'v1.0 (b9253)', state: 'installed', usedBy: ['primary', 'coder', 'embed', 'rerank'], recommended: true },
    { id: 'llamacpp:vulkan', version: 'v1.0 (b9253)', state: 'installed', usedBy: [] },
    { id: 'llamacpp:cpu',    version: 'v1.0 (b9253)', state: 'installed', usedBy: [] },
    { id: 'flm:npu',         version: 'v0.9.42 (deb)', state: 'installed', usedBy: ['agent', 'stt-npu', 'embed-npu'], recommended: true, note: 'manual deb' },
    { id: 'whispercpp',      version: 'v1.0 (vulkan)', state: 'installed', usedBy: [] },
    { id: 'sdcpp',           version: 'v1.0 (rocm)',  state: 'installed', usedBy: ['img'] },
    { id: 'kokoro',          version: 'builtin · cpu', state: 'installed', usedBy: ['tts'] },
    { id: 'ryzenai-server',  version: '—', state: 'unavailable', usedBy: [], note: 'Windows-only' },
  ],

  personas: [
    { id: 'default', label: 'Default', model: 'qwen3.6-27b-mtp' },
    { id: 'coder',   label: 'Coder',   model: 'qwen3-coder-30b' },
    { id: 'agent',   label: 'Agent',   model: 'gemma3-1b-npu' },
  ],

  /* ─── MCP (v0.3 surface — slice #14 #180 owns the store) ─── */

  mcpServers: [
    { id: 'hal0-admin', name: 'hal0-admin',
      description: 'Inspect and supervise this hal0 box — list slots, search models, restart lemond, read journal.',
      provider: 'hal0', bundled: true, state: 'running', since: '14d 02:11', pid: 31204,
      transport: 'streamable-http', url: 'https://halo-strix.local/mcp/admin',
      tools: 11, resources: 4, prompts: 2, version: '0.3.0',
      clients: ['claude-code', 'cursor'], activity: { rpm: 14, lastCall: 2 } },
    { id: 'hal0-memory', name: 'hal0-memory',
      description: "Cognee-backed recall, write, namespace ops over the operator's personal graph.",
      provider: 'hal0', bundled: true, state: 'running', since: '14d 02:11', pid: 31218,
      transport: 'streamable-http', url: 'https://halo-strix.local/mcp/memory',
      tools: 8, resources: 0, prompts: 1, version: '0.3.0',
      clients: ['claude-code', 'cursor', 'claude-desktop'], activity: { rpm: 32, lastCall: 0 } },
    { id: 'filesystem', name: 'filesystem',
      description: 'Read, write, and search files inside an allowlisted root. Scoped to /home/operator/projects.',
      provider: 'modelcontextprotocol', bundled: false, state: 'running', since: '4d 18:42', pid: 41882,
      transport: 'stdio → http bridge', url: 'https://halo-strix.local/mcp/filesystem',
      tools: 9, resources: 1, prompts: 0, version: '1.2.4',
      clients: ['claude-code'], env: { ROOT: '/home/operator/projects', MAX_READ_BYTES: '2000000' },
      activity: { rpm: 8, lastCall: 4 } },
    { id: 'github', name: 'github',
      description: 'Repo browsing, issue + PR ops, code search. OAuth via gh-cli token in keychain.',
      provider: 'modelcontextprotocol', bundled: false, state: 'running', since: '11d 09:14', pid: 38440,
      transport: 'streamable-http', url: 'https://halo-strix.local/mcp/github',
      tools: 27, resources: 0, prompts: 3, version: '0.9.1',
      clients: ['claude-code'], env: { GH_HOST: 'github.com' },
      activity: { rpm: 3, lastCall: 41 } },
    { id: 'postgres', name: 'postgres',
      description: 'Read-only SQL over the lab.db dev cluster. Schema-aware completions, EXPLAIN ANALYZE.',
      provider: 'modelcontextprotocol', bundled: false, state: 'running', since: '2d 04:55', pid: 47102,
      transport: 'stdio → http bridge', url: 'https://halo-strix.local/mcp/postgres',
      tools: 5, resources: 4, prompts: 0, version: '0.6.0',
      clients: [], env: { DSN: 'postgres://reader@10.0.0.4:5432/lab' },
      activity: { rpm: 0, lastCall: null } },
    { id: 'obsidian-vault', name: 'obsidian-vault',
      description: 'Search + create notes in the Obsidian vault at /mnt/vault. Renders wikilinks as resources.',
      provider: 'community', bundled: false, state: 'installing',
      progress: 67, progressLabel: 'pulling deps · uv pip install',
      transport: 'stdio', url: null, tools: null, version: '0.4.2',
      clients: [], activity: { rpm: 0, lastCall: null } },
    { id: 'brave-search', name: 'brave-search',
      description: 'Web search + summarisation via the Brave Search API.',
      provider: 'modelcontextprotocol', bundled: false, state: 'failed', since: '—',
      transport: 'streamable-http', url: 'https://halo-strix.local/mcp/brave-search',
      tools: 2, version: '0.7.0', clients: [],
      lastError: { ts: '2026-05-23 09:14:22', code: 'BRAVE_API_KEY_MISSING',
        msg: 'Required env var BRAVE_API_KEY is unset. Server exited with code 78 (config error).',
        attempts: 3 },
      env: { BRAVE_API_KEY: '' }, activity: { rpm: 0, lastCall: null } },
    { id: 'timed-reminders', name: 'timed-reminders',
      description: 'Create one-shot or recurring reminders that the agent can schedule and trigger.',
      provider: 'community', bundled: false, state: 'stopped', since: 'stopped 3h ago',
      transport: 'stdio', url: 'https://halo-strix.local/mcp/timed-reminders',
      tools: 4, version: '0.2.1', clients: [], note: 'Manually disabled · auto-start off',
      activity: { rpm: 0, lastCall: null } },
  ],

  mcpClients: [
    { id: 'claude-code', name: 'Claude Code', host: 'ramekin.lan', role: 'CLI', since: 'today 09:22',
      servers: ['hal0-admin', 'hal0-memory', 'filesystem', 'github'],
      activity: { rpm: 22, lastCall: 0 } },
    { id: 'cursor', name: 'Cursor', host: 'tritium.lan', role: 'IDE', since: 'today 08:01',
      servers: ['hal0-admin', 'hal0-memory'],
      activity: { rpm: 9, lastCall: 41 } },
    { id: 'claude-desktop', name: 'Claude Desktop', host: 'ramekin.lan', role: 'App', since: 'today 13:45',
      servers: ['hal0-memory'],
      activity: { rpm: 4, lastCall: 12 } },
  ],

  mcpCatalog: [
    { id: 'puppeteer', name: 'puppeteer', author: 'modelcontextprotocol', verified: true,
      description: 'Headless-browser automation. Navigate, scrape, screenshot.',
      tools: 9, stars: 2840, category: 'browser' },
    { id: 'sqlite', name: 'sqlite', author: 'modelcontextprotocol', verified: true,
      description: 'Read-only SQL over a single sqlite database file.',
      tools: 4, stars: 1820, category: 'data' },
    { id: 'gdrive', name: 'google-drive', author: 'modelcontextprotocol', verified: true,
      description: 'Browse and read documents from a Google Drive account.',
      tools: 6, stars: 1410, category: 'files' },
    { id: 'slack', name: 'slack', author: 'modelcontextprotocol', verified: true,
      description: 'Channel + DM read, message send, thread fetch.',
      tools: 8, stars: 990, category: 'comms' },
    { id: 'linear', name: 'linear', author: 'linear-app', verified: true,
      description: 'Issue + project ops backed by the Linear GraphQL API.',
      tools: 14, stars: 720, category: 'issues' },
    { id: 'exa-search', name: 'exa-search', author: 'exa-labs', verified: false,
      description: 'Neural web search and similarity-based document retrieval.',
      tools: 3, stars: 540, category: 'search' },
    { id: 'homeassistant', name: 'home-assistant', author: 'community', verified: false,
      description: 'Control Home Assistant entities — lights, sensors, scenes, automations.',
      tools: 11, stars: 480, category: 'iot' },
    { id: 'pi-hole', name: 'pi-hole', author: 'community', verified: false,
      description: 'Inspect blocked queries and toggle groups on your Pi-hole instance.',
      tools: 5, stars: 220, category: 'iot' },
    { id: 'kagi', name: 'kagi', author: 'community', verified: false,
      description: 'Search via the Kagi API. Universal, summarize, FastGPT.',
      tools: 4, stars: 180, category: 'search' },
    { id: 'kubernetes', name: 'kubernetes', author: 'manusa', verified: false,
      description: 'kubectl-flavoured read access to a cluster. Logs, describe, get.',
      tools: 16, stars: 920, category: 'ops' },
    { id: 'datadog', name: 'datadog', author: 'winor30', verified: false,
      description: 'Query metrics, monitors, and logs from a Datadog tenant.',
      tools: 7, stars: 310, category: 'ops' },
    { id: 'todoist', name: 'todoist', author: 'abhiz123', verified: false,
      description: 'Create, update, complete tasks in Todoist.',
      tools: 6, stars: 240, category: 'productivity' },
  ],

  mcpCategories: [
    { id: 'all',          label: 'All' },
    { id: 'files',        label: 'Files' },
    { id: 'data',         label: 'Data' },
    { id: 'search',       label: 'Search' },
    { id: 'browser',      label: 'Browser' },
    { id: 'comms',        label: 'Comms' },
    { id: 'issues',       label: 'Issues' },
    { id: 'ops',          label: 'Ops' },
    { id: 'iot',          label: 'IoT' },
    { id: 'productivity', label: 'Productivity' },
  ],
})

/* ─── MOCK_ALLOWLIST — patterns mocked under #166 ──────────────────────
 *
 * Each row is `[issue, regex, builder]`. The regex tests `pathname`
 * (or `pathname + search`) so query strings don't break matching. Drop
 * a row when its backend issue lands.
 */

/**
 * `/v1/stats` — last-request snapshot Lemonade exposes natively. PR-12
 * (#179) on main consumes it for `MetricsShim`. Shape mirrors what
 * `metrics_shim.py` parses; the dashboard's lemonade store polls the
 * same endpoint for derived metrics. When the worktree rebases on PR-12
 * the same payload comes from the live Lemonade — flip is free.
 */
function buildV1Stats() {
  return {
    time_to_first_token: 0.220,
    tokens_per_second: 45.0,
    prompt_tokens: 312,
    output_tokens: 188,
    input_tokens: 312,
  }
}

function buildBackends() {
  return { backends: MOCK_DATA.backends, lemonade: { version: MOCK_DATA.lemonade.version } }
}

function buildBackendById(id) {
  const b = MOCK_DATA.backends.find((x) => x.id === id)
  if (!b) return null
  // Backend snapshots include loaded models so the UI can render them inline.
  return {
    ...b,
    loaded: MOCK_DATA.slots
      .filter((s) => {
        if (id.startsWith('llamacpp')) return s.device.startsWith('gpu') || s.device === 'cpu'
        if (id === 'flm:npu') return s.device === 'npu'
        return false
      })
      .map((s) => ({ model_name: s.model, slot: s.name })),
  }
}

function buildMcpServers() {
  return { servers: MOCK_DATA.mcpServers }
}

function buildMcpClients() {
  return { clients: MOCK_DATA.mcpClients }
}

function buildMcpCatalog() {
  return { entries: MOCK_DATA.mcpCatalog, categories: MOCK_DATA.mcpCategories }
}

function buildMcpServerById(id) {
  const s = MOCK_DATA.mcpServers.find((x) => x.id === id)
  return s || null
}

/**
 * Order matters — first match wins. Specific paths before wildcards.
 */
export const MOCK_ALLOWLIST = Object.freeze([
  // /v1/stats — PR-12 #179 on main; not yet on feat/dash-v2-rework.
  { issue: '#145', re: /^\/v1\/stats$/, build: buildV1Stats },

  // /api/backends — slice #142/#145 backend track.
  { issue: '#142', re: /^\/api\/backends$/, build: buildBackends },
  { issue: '#142', re: /^\/api\/backends\/([^/]+)$/, build: (_url, m) => buildBackendById(m[1]) },

  // /api/mcp/* — slice #14 #180 backend track. Slice #166 ships shapes only.
  { issue: '#180', re: /^\/api\/mcp\/servers$/, build: buildMcpServers },
  { issue: '#180', re: /^\/api\/mcp\/clients$/, build: buildMcpClients },
  { issue: '#180', re: /^\/api\/mcp\/catalog$/, build: buildMcpCatalog },
  { issue: '#180', re: /^\/api\/mcp\/servers\/([^/]+)$/, build: (_url, m) => buildMcpServerById(m[1]) },

  // /api/capabilities/* — shapes for surfaces not covered by #142 yet.
  // The bare `/api/capabilities` endpoint is already live; mock only
  // child endpoints that may 404 in dev.
  { issue: '#142', re: /^\/api\/capabilities\/personas$/, build: () => ({ personas: MOCK_DATA.personas }) },
])

/* ─── mockFetch — drop-in fetch() replacement ──────────────────────── */

const FORCED = typeof import.meta !== 'undefined'
  && import.meta.env
  && import.meta.env.VITE_MOCK_LEMONADE === '1'

function parsePath(url) {
  if (typeof url !== 'string') {
    // Request object — extract URL string.
    try { url = String(url?.url ?? url) } catch { return null }
  }
  // Strip origin if present; we only match against pathname.
  try {
    if (url.startsWith('http')) return new URL(url).pathname
  } catch {
    // fall through
  }
  // Strip query / fragment from path-only URLs.
  const q = url.indexOf('?')
  return q >= 0 ? url.slice(0, q) : url
}

function matchAllowlist(path) {
  for (const row of MOCK_ALLOWLIST) {
    const m = path.match(row.re)
    if (m) return { row, match: m }
  }
  return null
}

function jsonResponse(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/**
 * Drop-in `fetch()` replacement. Forced-mock returns immediately for
 * any allowlisted URL; otherwise the real fetch runs and we only
 * substitute on 404 + allowlisted.
 */
export async function mockFetch(url, options = {}) {
  const path = parsePath(url)
  if (!path) return fetch(url, options)

  const hit = matchAllowlist(path)

  if (FORCED && hit) {
    const body = hit.row.build(url, hit.match)
    return jsonResponse(body == null ? null : body, body == null ? 404 : 200)
  }

  let res
  try {
    res = await fetch(url, options)
  } catch (e) {
    // Network-level failure — if the URL is allowlisted, fall back to
    // mock data so the dashboard renders. Otherwise re-throw so
    // callers get the original error.
    if (hit) {
      console.warn(`[useMock] network error for ${path}; using mock (${hit.row.issue})`, e)
      const body = hit.row.build(url, hit.match)
      return jsonResponse(body == null ? null : body, body == null ? 404 : 200)
    }
    throw e
  }

  if (res.status === 404 && hit) {
    console.warn(`[useMock] 404 for ${path}; using mock (${hit.row.issue})`)
    const body = hit.row.build(url, hit.match)
    return jsonResponse(body == null ? null : body, body == null ? 404 : 200)
  }

  return res
}

/* ─── useMock composable ───────────────────────────────────────────── */

export function useMock() {
  const isMockActive = computed(() => FORCED)
  return { mockFetch, MOCK_DATA, MOCK_ALLOWLIST, isMockActive }
}

/* ─── Test-only export ─────────────────────────────────────────────── */

/**
 * Exported for unit/Playwright introspection. `true` when the build was
 * compiled with `VITE_MOCK_LEMONADE=1`. Do NOT branch on this in view
 * code — use the `isMockActive` ref so future runtime toggles still work.
 */
export const __MOCK_FORCED = FORCED
