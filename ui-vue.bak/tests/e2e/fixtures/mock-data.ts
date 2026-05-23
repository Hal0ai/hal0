/**
 * mock-data — Playwright-side mirror of `ui/src/composables/useMock.js`
 * `MOCK_DATA`.
 *
 * Why a duplicate? `useMock.js` is a Vite-transformed ESM module that
 * touches `import.meta.env` — Playwright (Node, plain tsx) can't import
 * it without a Vite shim. Rather than wire that up we keep the shape
 * constants here as the e2e source of truth and treat divergence as a
 * lint failure (see `tests/e2e/fixtures/mock-data.spec-helpers.test.ts`
 * style guard if added later — issue #166 ships the duplicate).
 *
 * Contract: every top-level key here MUST stay structurally identical
 * to `MOCK_DATA` in `useMock.js`. Slice #166 ships them together; future
 * slices that mutate one MUST mutate the other in the same PR.
 *
 * Retirement: when a backend endpoint lands (#142, #145, #146, MCP
 * #180), the matching block here moves to a per-spec live-mode override
 * or is deleted outright. See `docs/dev/web-ui-mocks.md`.
 */

export const MOCK_DATA = {
  lemonade: {
    status: 'up',
    version: 'v10.6.0',
    loaded: 3,
    budget: 4,
    throughput: 12.4,
    queued: 0,
    coresident: true,
  },

  /** Subset relevant to e2e — full slot list lives in `useMock.js`. */
  slots: [
    {
      name: 'primary', type: 'llm', device: 'gpu-rocm',
      model: 'qwen3.6-27b-mtp-q4_k_m', model_id: 'qwen3.6-27b-mtp',
      group: 'chat', state: 'serving', port: 8092,
    },
    {
      name: 'agent', type: 'llm', device: 'npu',
      model: 'gemma3:1b', model_id: 'gemma3-1b-npu',
      group: 'npu', state: 'ready', port: 8093,
    },
  ],

  backends: [
    { id: 'llamacpp:rocm',   version: 'v1.0 (b9253)', state: 'installed', usedBy: ['primary'], recommended: true },
    { id: 'llamacpp:vulkan', version: 'v1.0 (b9253)', state: 'installed', usedBy: [] },
    { id: 'llamacpp:cpu',    version: 'v1.0 (b9253)', state: 'installed', usedBy: [] },
    { id: 'flm:npu',         version: 'v0.9.42 (deb)', state: 'installed', usedBy: ['agent'], recommended: true, note: 'manual deb' },
    { id: 'whispercpp',      version: 'v1.0 (vulkan)', state: 'installed', usedBy: [] },
    { id: 'sdcpp',           version: 'v1.0 (rocm)',  state: 'installed', usedBy: [] },
    { id: 'kokoro',          version: 'builtin · cpu', state: 'installed', usedBy: [] },
    { id: 'ryzenai-server',  version: '—', state: 'unavailable', usedBy: [], note: 'Windows-only' },
  ],

  v1Stats: {
    time_to_first_token: 0.220,
    tokens_per_second: 45.0,
    prompt_tokens: 312,
    output_tokens: 188,
    input_tokens: 312,
  },

  /**
   * Subset of the model catalog needed by Models v2 specs (slice #171).
   * Mirrors a subset of `useMock.js` MOCK_DATA.models — keep id +
   * type + device + installed + runtime in sync.
   */
  models: [
    { id: 'qwen3.6-27b-mtp', longName: 'Qwen3.6-27B-MTP', repo: 'unsloth/Qwen3.6-27B-A3B-MTP-GGUF:Q4_K_M', params: '27B', size: '18.8 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
    { id: 'qwen3-coder-30b', longName: 'Qwen3-Coder-30B-A3B', repo: 'unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:Q4_K_M', params: '30B', size: '18.6 GB', labels: ['chat', 'coder', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
    { id: 'qwen3.5-9b', longName: 'Qwen3.5-9B', repo: 'Qwen/Qwen3.5-9B-Instruct-GGUF:Q4_K_M', params: '9B', size: '5.4 GB', labels: ['chat', 'tool-calling', 'vision'], type: 'llm', device: 'rocm', ns: 'blessed', installed: false, runtime: 'llamacpp' },
    { id: 'gemma3-1b-npu', longName: 'gemma3:1b (NPU)', repo: 'google/gemma-3-1b-it-flm', params: '1B', size: '1.0 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'npu', ns: 'blessed', installed: true, runtime: 'flm' },
    { id: 'nomic-v1.5', longName: 'nomic-embed-text-v1.5', repo: 'nomic-ai/nomic-embed-text-v1.5-GGUF', params: '350M', size: '350 MB', labels: ['embeddings'], type: 'embedding', device: 'rocm', ns: 'blessed', installed: true, runtime: 'llamacpp' },
    { id: 'kokoro-v1', longName: 'kokoro-v1', repo: 'hexgrad/Kokoro-82M', params: '82M', size: '400 MB', labels: ['tts'], type: 'tts', device: 'cpu', ns: 'blessed', installed: true, runtime: 'kokoro' },
    { id: 'sd-turbo', longName: 'sd-turbo', repo: 'stabilityai/sd-turbo', params: '1.2B', size: '1.2 GB', labels: ['image'], type: 'image', device: 'rocm', ns: 'blessed', installed: true, runtime: 'sdcpp' },
    { id: 'user.phi-4-mini', longName: 'user.Phi-4-Mini', repo: 'microsoft/Phi-4-mini-instruct-GGUF', params: '3.8B', size: '2.3 GB', labels: ['chat', 'tool-calling'], type: 'llm', device: 'rocm', ns: 'pulled', installed: true, runtime: 'llamacpp' },
  ],

  mcpServers: [
    { id: 'hal0-admin', name: 'hal0-admin', provider: 'hal0', bundled: true, state: 'running',
      transport: 'streamable-http', tools: 11, resources: 4, prompts: 2, version: '0.3.0',
      clients: ['claude-code', 'cursor'], activity: { rpm: 14, lastCall: 2 } },
    { id: 'hal0-memory', name: 'hal0-memory', provider: 'hal0', bundled: true, state: 'running',
      transport: 'streamable-http', tools: 8, resources: 0, prompts: 1, version: '0.3.0',
      clients: ['claude-code', 'cursor', 'claude-desktop'], activity: { rpm: 32, lastCall: 0 } },
    { id: 'filesystem', name: 'filesystem', provider: 'modelcontextprotocol', bundled: false,
      state: 'running', transport: 'stdio → http bridge', tools: 9, resources: 1, prompts: 0,
      version: '1.2.4', clients: ['claude-code'], activity: { rpm: 8, lastCall: 4 } },
    { id: 'github', name: 'github', provider: 'modelcontextprotocol', bundled: false,
      state: 'running', transport: 'streamable-http', tools: 27, resources: 0, prompts: 3,
      version: '0.9.1', clients: ['claude-code'], activity: { rpm: 3, lastCall: 41 } },
    { id: 'postgres', name: 'postgres', provider: 'modelcontextprotocol', bundled: false,
      state: 'running', transport: 'stdio → http bridge', tools: 5, resources: 4, prompts: 0,
      version: '0.6.0', clients: [], activity: { rpm: 0, lastCall: null } },
    { id: 'obsidian-vault', name: 'obsidian-vault', provider: 'community', bundled: false,
      state: 'installing', progress: 67, progressLabel: 'pulling deps · uv pip install',
      transport: 'stdio', tools: null, version: '0.4.2',
      clients: [], activity: { rpm: 0, lastCall: null } },
    /* Slice #14 (#180): brave-search carries the `lastError` shape the
       failed-state row block consumes (code pill + body), plus an empty
       BRAVE_API_KEY env var so EditConfigModal's empty-input red-border
       branch has a row to render. */
    { id: 'brave-search', name: 'brave-search', provider: 'modelcontextprotocol', bundled: false,
      state: 'failed', since: '—', transport: 'streamable-http', tools: 2, version: '0.7.0',
      clients: [],
      lastError: {
        ts: '2026-05-23 09:14:22',
        code: 'BRAVE_API_KEY_MISSING',
        msg: 'Required env var BRAVE_API_KEY is unset. Server exited with code 78 (config error).',
        attempts: 3,
      },
      env: { BRAVE_API_KEY: '' },
      activity: { rpm: 0, lastCall: null } },
    { id: 'timed-reminders', name: 'timed-reminders', provider: 'community', bundled: false,
      state: 'stopped', since: 'stopped 3h ago', transport: 'stdio',
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
      tools: 9, stars: 2840, category: 'browser' },
    { id: 'sqlite', name: 'sqlite', author: 'modelcontextprotocol', verified: true,
      tools: 4, stars: 1820, category: 'data' },
    { id: 'gdrive', name: 'google-drive', author: 'modelcontextprotocol', verified: true,
      tools: 6, stars: 1410, category: 'files' },
    { id: 'slack', name: 'slack', author: 'modelcontextprotocol', verified: true,
      tools: 8, stars: 990, category: 'comms' },
    { id: 'linear', name: 'linear', author: 'linear-app', verified: true,
      tools: 14, stars: 720, category: 'issues' },
    { id: 'exa-search', name: 'exa-search', author: 'exa-labs', verified: false,
      tools: 3, stars: 540, category: 'search' },
    { id: 'homeassistant', name: 'home-assistant', author: 'community', verified: false,
      tools: 11, stars: 480, category: 'iot' },
    { id: 'pi-hole', name: 'pi-hole', author: 'community', verified: false,
      tools: 5, stars: 220, category: 'iot' },
    { id: 'kagi', name: 'kagi', author: 'community', verified: false,
      tools: 4, stars: 180, category: 'search' },
    { id: 'kubernetes', name: 'kubernetes', author: 'manusa', verified: false,
      tools: 16, stars: 920, category: 'ops' },
    { id: 'datadog', name: 'datadog', author: 'winor30', verified: false,
      tools: 7, stars: 310, category: 'ops' },
    { id: 'todoist', name: 'todoist', author: 'abhiz123', verified: false,
      tools: 6, stars: 240, category: 'productivity' },
  ],
}
