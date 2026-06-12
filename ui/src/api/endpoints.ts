// hal0 v3 dashboard — endpoint constants (Phase B1).
//
// One file so a Cmd+Shift+F surfaces every URL the dashboard touches.
// Add new endpoints here BEFORE adding hooks, so the catalogue stays
// authoritative when we reconcile against the backend (ADR-0004 for
// agent surface, etc).

export const ENDPOINTS = {
  // ── Slots / status (hal0-api) ────────────────────────────────────
  status: '/api/status',
  slots: '/api/slots',

  // ── ComfyUI generation engine (slots-page Image-Gen tab) ─────────
  // Read-only aggregate of docker + systemd + ComfyUI HTTP; the
  // switchover write-path is feature-gated server-side.
  comfyuiStatus: '/api/comfyui/status',
  comfyuiSwitchover: '/api/comfyui/switchover',
  // Pin image mode (disables the arbiter's idle auto-restore). 501 when the
  // switchover gate is off.
  comfyuiPin: '/api/comfyui/pin',

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
  slotPull: (name: string) =>
    `/api/slots/${encodeURIComponent(name)}/pull`,
  slotPullStream: (name: string) =>
    `/api/slots/${encodeURIComponent(name)}/pull/stream`,

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
  // Issue #311: free-text HF Hub model search backing the dashboard
  // "Search HF" button. Distinct from /api/models/inspect (which
  // resolves a known coord into variants) — this proxies HF's
  // /api/models?search=… and returns a small typed list.
  hfSearch: '/api/hf/search',

  // ── Backends ─────────────────────────────────────────────────────
  backends: '/api/backends',
  backend: (id: string) => `/api/backends/${encodeURIComponent(id)}`,
  backendInstall: (id: string) => `/api/backends/${encodeURIComponent(id)}/install`,

  // ── Capabilities ─────────────────────────────────────────────────
  capabilities: '/api/capabilities',
  capability: (key: string) => `/api/capabilities/${encodeURIComponent(key)}`,
  // POST /api/capabilities/{slot}/{child} — apply a partial selection update
  // (model/provider/enabled). Whitelisted keys only; 400 on unknown fields.
  capabilityApply: (slot: string, child: string) =>
    `/api/capabilities/${encodeURIComponent(slot)}/${encodeURIComponent(child)}`,

  // ── Hardware ─────────────────────────────────────────────────────
  hardware: '/api/hardware',
  statsHardware: '/api/stats/hardware',

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

  // ── Agents — bundled lifecycle + sidebar rollup (v0.3 PR-6) ──────
  // `agents` lives in the catalogue block above (one entry, used by
  // both the bundled-list and sidebar surfaces). The remaining
  // endpoints under this block are surfaces the SidebarAgentBlock
  // calls — most are NEW in v0.3 and may 404 against an older
  // hal0-api; the consuming hooks fall back to "—" and console.warn
  // once when a particular path returns 404 / network error so the
  // sidebar degrades gracefully on partial deployments.
  agentPersonas: (id: string) =>
    `/api/agents/${encodeURIComponent(id)}/personas`,
  // Per-persona spending-cap primitive (Phase 0 OpenRouter prereq).
  // GET/PUT/check/charge — the V1 OpenRouter upstream + V2 fusion MCP
  // both call ``check`` pre-flight and ``charge`` post-response.
  agentPersonaBudget: (id: string, pid: string) =>
    `/api/agents/${encodeURIComponent(id)}/personas/${encodeURIComponent(pid)}/budget`,
  agentPersonaBudgetCheck: (id: string, pid: string) =>
    `/api/agents/${encodeURIComponent(id)}/personas/${encodeURIComponent(pid)}/budget/check`,
  agentPersonaBudgetCharge: (id: string, pid: string) =>
    `/api/agents/${encodeURIComponent(id)}/personas/${encodeURIComponent(pid)}/budget/charge`,
  agentActivity: (id: string) =>
    `/api/agents/${encodeURIComponent(id)}/activity`,
  agentApprovals: '/api/agent/approvals',
  // The path below DOES NOT exist yet in any merged backend PR (the
  // sidebar component degrades gracefully with "—" + warn). Recorded
  // here so the wiring is single-place when the route lands.
  agentMemoryStats: '/api/agents/hermes/memory/stats',

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

  // ── Journal (HTTP backfill + SSE tail) ───────────────────────────
  // Per #322 Phase 1 (PR #330): the ``/api/journal`` surface
  // supersedes ``/api/logs``.
  journal: '/api/journal',
  journalStream: '/api/journal/stream',

  // ── Settings (hal0.toml read/write) ──────────────────────────────
  settings: '/api/settings',
  settingsReload: '/api/settings/reload',
  settingsSchema: '/api/settings/schema',
  // Apply-plan registry — key→{apply_class, services} for all settings (#552).
  settingsApplyPlan: '/api/settings/apply-plan',
  // Single-source-of-truth model storage (Settings → Storage).
  settingsModelsStore: '/api/settings/models/store',
  settingsModelsStoreMigrate: '/api/settings/models/store/migrate',
  // Full-shape Proxmox status — includes tenants[] stripped by the
  // /api/stats/hardware slim projection (see pve.py:_SLIM_DROP_KEYS).
  proxmoxSettings: '/api/settings/proxmox',

  // ── Settings ─────────────────────────────────────────────────────
  // Updates
  updateState: '/api/updates/state',
  updateCheck: '/api/updates/check',
  updateApply: '/api/updates/apply',
  updateStatus: (jobId: string) => `/api/updates/status/${encodeURIComponent(jobId)}`,
  // Channel (stable | nightly) — GET reads hal0.toml telemetry.channel;
  // PUT persists the choice back so subsequent /check calls honour it.
  updateChannel: '/api/updates/channel',
  // Secrets
  secrets: '/api/secrets',
  secret: (name: string) => `/api/secrets/${encodeURIComponent(name)}`,
  // Service URL discovery — the dashboard reads this to resolve the
  // reachable hostnames for sibling services (OpenWebUI, Hermes) from the
  // request host, so links work on any install (localhost / LAN IP /
  // hal0.local / custom domain) without hardcoding. See routes/config.py.
  configUrls: '/api/config/urls',

  // ── Connections (issue #549) — providers + upstreams + reachability test
  // ``/api/providers`` is the alias of ``/api/upstreams`` filtered to remote
  // (kind != "slot"); ``/api/upstreams`` returns every routing target. The
  // POST /test probe is what the dashboard's per-upstream Test button calls.
  providers: '/api/providers',
  providersCatalog: '/api/providers/catalog',
  upstreams: '/api/upstreams',
  upstream: (name: string) =>
    `/api/upstreams/${encodeURIComponent(name)}`,
  upstreamTest: (name: string) =>
    `/api/upstreams/${encodeURIComponent(name)}/test`,

  // ── Profiles (container slot templates) ─────────────────────────
  profiles: '/api/profiles',
  profile: (name: string) => `/api/profiles/${encodeURIComponent(name)}`,

  // Install / FirstRun
  installState: '/api/install/state',
  firstrunState: '/api/firstrun/state',
  firstrunCuratedModels: '/api/firstrun/curated-models',
  firstrunPickDefault: '/api/firstrun/pick-default',
  firstrunInstall: '/api/firstrun/install',
  firstrunComplete: '/api/firstrun/complete',
} as const
