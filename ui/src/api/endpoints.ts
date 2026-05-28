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
  modelScanPreview: '/api/models/scan/preview',
  modelScanCommit: '/api/models/scan',
  modelAddFromPath: '/api/models/add-from-path',

  // ── Backends ─────────────────────────────────────────────────────
  backends: '/api/backends',
  backend: (id: string) => `/api/backends/${encodeURIComponent(id)}`,
  backendInstall: (id: string) => `/api/backends/${encodeURIComponent(id)}/install`,

  // ── Capabilities ─────────────────────────────────────────────────
  capabilities: '/api/capabilities',
  capability: (key: string) => `/api/capabilities/${encodeURIComponent(key)}`,

  // ── Hardware ─────────────────────────────────────────────────────
  hardware: '/api/hardware',

  // ── Agents — list + dashboard catalogues ─────────────────────────
  // ``agents`` is the installed-bundled list (#207). ``agentSkills`` +
  // ``agentPersonaEnums`` back the Skills tab (#227) + the
  // PersonaEditModal selects (#226). Static catalogues sourced from
  // ``hal0.agents.persona`` server-side.
  agents: '/api/agents',
  agentSkills: '/api/agents/skills',
  agentPersonaEnums: '/api/agents/persona-enums',

  // ── Agents — MCP-client allow-list (ADR-0013) ────────────────────
  agentMcpClients: '/api/agents/mcp/clients',
  agentMcpClient: (name: string) =>
    `/api/agents/mcp/clients/${encodeURIComponent(name)}`,

  // ── MCP host introspection (issue #206) ──────────────────────────
  // Read-only view of hosted MCP servers, connected clients, the
  // installable catalog, and an SSE tail of mcp.tool.* events.
  // Lifecycle mutations (install/uninstall/restart/config) stub 501
  // pending ADR-0013 mcp_client.py work.
  mcpServers: '/api/mcp/servers',
  mcpClients: '/api/mcp/clients',
  mcpCatalog: '/api/mcp/catalog',
  mcpStream: '/api/mcp/stream',
  mcpResolve: '/api/mcp/resolve',
  mcpInstall: '/api/mcp/install',
  mcpServer: (id: string) => `/api/mcp/${encodeURIComponent(id)}`,
  mcpServerLogs: (id: string) => `/api/mcp/${encodeURIComponent(id)}/logs`,
  mcpServerAction: (id: string, action: string) =>
    `/api/mcp/${encodeURIComponent(id)}/${encodeURIComponent(action)}`,
  mcpServerConfig: (id: string) =>
    `/api/mcp/${encodeURIComponent(id)}/config`,

  // ── Memory (ADR-0014 graph-extraction gate) ──────────────────────
  memoryGraphStatus: '/api/memory/graph/status',
  memoryGraph: '/api/memory/graph',

  // ── Journal (HTTP backfill + SSE tail) — unified hal0 + lemond ───
  // Per #322 Phase 1 (PR #330): the merged ``/api/journal`` surface
  // supersedes ``/api/logs``. The old constants stay around for the
  // raw lemond WS channel (used by the LogsView's source=lemond mode);
  // historical + SSE consumers should prefer the journal endpoints.
  journal: '/api/journal',
  journalStream: '/api/journal/stream',
  lemondLogsWs: '/logs/stream',

  // ── Settings (hal0.toml read/write) ──────────────────────────────
  settings: '/api/settings',
  settingsReload: '/api/settings/reload',
  settingsSchema: '/api/settings/schema',
  // Single-source-of-truth model storage (Settings → Models + Firstrun → Storage).
  settingsModelsStore: '/api/settings/models/store',
  settingsModelsStoreMigrate: '/api/settings/models/store/migrate',

  // ── Settings ─────────────────────────────────────────────────────
  // Updates
  updateState: '/api/updates/state',
  updateCheck: '/api/updates/check',
  updateApply: '/api/updates/apply',
  // Secrets
  secrets: '/api/secrets',
  secret: (name: string) => `/api/secrets/${encodeURIComponent(name)}`,
  // Install / FirstRun
  installState: '/api/install/state',
  firstrunState: '/api/firstrun/state',
  firstrunCuratedModels: '/api/firstrun/curated-models',
  firstrunPickDefault: '/api/firstrun/pick-default',
  firstrunInstall: '/api/firstrun/install',
  firstrunComplete: '/api/firstrun/complete',
} as const
