// hal0 v3 dashboard — endpoint constants (Phase B1).
//
// One file so a Cmd+Shift+F surfaces every URL the dashboard touches.
// Add new endpoints here BEFORE adding hooks, so the catalogue stays
// authoritative when we reconcile against the backend (PRs #137+ for
// Lemonade migration, ADR-0004 for agent surface, etc).

export const ENDPOINTS = {
  // ── Lemonade runtime ─────────────────────────────────────────────
  lemonade: {
    health: '/v1/health',
    stats: '/v1/stats',
    chatCompletions: '/v1/chat/completions',
    load: '/v1/load',
    unload: '/v1/unload',
  },

  // ── Slots / status (hal0-api) ────────────────────────────────────
  status: '/api/status',
  slots: '/api/slots',
  slotMetrics: '/api/slots/metrics',
  slot: (name: string) => `/api/slots/${encodeURIComponent(name)}`,
  slotConfig: (name: string) => `/api/slots/${encodeURIComponent(name)}/config`,
  slotDefaults: (name: string) => `/api/slots/${encodeURIComponent(name)}/defaults`,
  slotBackend: (name: string) => `/api/slots/${encodeURIComponent(name)}/backend`,
  slotRestart: (name: string) => `/api/slots/${encodeURIComponent(name)}/restart`,
  slotLoad: (name: string) => `/api/slots/${encodeURIComponent(name)}/load`,
  slotUnload: (name: string) => `/api/slots/${encodeURIComponent(name)}/unload`,
  slotSwap: (name: string) => `/api/slots/${encodeURIComponent(name)}/swap`,
  slotStateStream: (name: string) =>
    `/api/slots/${encodeURIComponent(name)}/state/stream`,
  slotLogsStream: (name: string) =>
    `/api/slots/${encodeURIComponent(name)}/logs/stream`,

  // ── Models / pull lifecycle ──────────────────────────────────────
  models: '/api/models',
  model: (id: string) => `/api/models/${encodeURIComponent(id)}`,
  modelPull: (id: string) => `/api/models/${encodeURIComponent(id)}/pull`,
  modelPullStatus: (id: string) => `/api/models/${encodeURIComponent(id)}/pull/status`,
  modelPullStream: (id: string) => `/api/models/${encodeURIComponent(id)}/pull/stream`,
  modelPullCancel: (id: string) => `/api/models/${encodeURIComponent(id)}/pull/cancel`,
  modelInspect: '/api/models/inspect',

  // ── Backends ─────────────────────────────────────────────────────
  backends: '/api/backends',
  backend: (id: string) => `/api/backends/${encodeURIComponent(id)}`,
  backendInstall: (id: string) => `/api/backends/${encodeURIComponent(id)}/install`,

  // ── Capabilities ─────────────────────────────────────────────────
  capabilities: '/api/capabilities',
  capability: (key: string) => `/api/capabilities/${encodeURIComponent(key)}`,

  // ── Hardware ─────────────────────────────────────────────────────
  hardware: '/api/hardware',

  // ── Memory (ADR-0014 graph-extraction gate) ──────────────────────
  memoryGraphStatus: '/api/memory/graph/status',
  memoryGraph: '/api/memory/graph',

  // ── Logs (HTTP historical + SSE tail + WS lemond) ────────────────
  logs: '/api/logs',
  logsStream: '/api/logs/stream',
  lemondLogsWs: '/logs/stream',

  // ── Settings ─────────────────────────────────────────────────────
  // Updates
  updateState: '/api/updates/state',
  updateCheck: '/api/updates/check',
  updateApply: '/api/updates/apply',
  // Secrets
  secrets: '/api/secrets',
  secret: (name: string) => `/api/secrets/${encodeURIComponent(name)}`,
  // FirstRun
  firstrunState: '/api/firstrun/state',
  firstrunCuratedModels: '/api/firstrun/curated-models',
  firstrunPickDefault: '/api/firstrun/pick-default',
  firstrunInstall: '/api/firstrun/install',
  firstrunComplete: '/api/firstrun/complete',
} as const
