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
  // NOTE: backendInstall has no generic backend route. The only install-
  // like operation available is NPU: POST /api/backends/npu/load (and
  // /api/backends/npu/unload). The UI flow-modals.jsx BackendInstallModal
  // should route the NPU case to backendNpuLoad; remove this constant once
  // ui-sweep-a's BackendInstallModal migration is complete.
  backendInstall: (id: string) => `/api/backends/${encodeURIComponent(id)}/install`,
  backendNpuLoad: '/api/backends/npu/load',
  backendNpuUnload: '/api/backends/npu/unload',
  // Alias spelling used in ui-sweep-a hook files (PR #741 TODOs).
  backendsNpuLoad: '/api/backends/npu/load',
  backendsNpuUnload: '/api/backends/npu/unload',

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
  // Backend: GET /api/mcp/clients (mcp.py:469) — note the prefix is
  // /api/mcp, NOT /api/agents/mcp.  The original constant had the wrong
  // prefix which caused the Clients tab to 404 on every real install.
  agentMcpClients: '/api/mcp/clients',
  agentMcpClient: (name: string) =>
    `/api/mcp/clients/${encodeURIComponent(name)}`,

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
  // Approval CRUD — list is an alias of agentApprovals; approve/deny are
  // the action endpoints added by backend-dev task #7 (PR #741 TODOs).
  agentApprovalsList: '/api/agent/approvals',
  agentApprovalApprove: (id: string) =>
    `/api/agent/approvals/${encodeURIComponent(id)}/approve`,
  agentApprovalDeny: (id: string) =>
    `/api/agent/approvals/${encodeURIComponent(id)}/deny`,
  // Memory list endpoint (ADR-0014, PR #736 backend surface).
  memoryList: '/api/memory/list',
  // ── Hindsight engine admin surface (memory_admin routes) ─────────
  // Fail-soft engine card + allowlisted bank-scoped passthrough.
  memoryEngine: '/api/memory/engine',
  memoryBanks: '/api/memory/banks',
  memoryBank: (bank: string) => `/api/memory/banks/${encodeURIComponent(bank)}`,
  memoryBankStats: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/stats`,
  memoryBankTimeseries: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/stats/timeseries`,
  memoryBankProfile: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/profile`,
  memoryBankGraph: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/graph`,
  // FU2: server-side ego / top-K subgraph slice for large banks.
  memoryBankSubgraph: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/graph/subgraph`,
  memoryBankEntityGraph: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/entities/graph`,
  memoryBankEntities: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/entities`,
  memoryBankEntity: (bank: string, id: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/entities/${encodeURIComponent(id)}`,
  memoryBankMemories: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/memories`,
  memoryBankDocuments: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/documents`,
  memoryBankDocument: (bank: string, id: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/documents/${encodeURIComponent(id)}`,
  memoryBankTags: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/tags`,
  memoryBankRecall: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/recall`,
  memoryBankReflect: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/reflect`,
  memoryBankMentalModels: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/mental-models`,
  memoryBankDirectives: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/directives`,
  memoryBankOperations: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/operations`,
  memoryBankOperationRetry: (bank: string, id: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/operations/${encodeURIComponent(id)}/retry`,
  memoryBankConsolidate: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/consolidate`,
  // Per-agent memory stats — parameterised by agent id. Previously a
  // hardcoded "/api/agents/hermes/memory/stats" placeholder; now generic.
  agentMemoryStats: (id: string) =>
    `/api/agents/${encodeURIComponent(id)}/memory/stats`,
  // Persona update — PATCH/PUT /api/agents/{agentId}/personas/{pid}.
  agentPersonaUpdate: (agentId: string, pid: string) =>
    `/api/agents/${encodeURIComponent(agentId)}/personas/${encodeURIComponent(pid)}`,

  // ── MCP host introspection ───────────────────────────────────────
  // Read-only list of hosted MCP servers (+ their tool_details), backing
  // the MCP section of the Connections view and the sidebar status pip.
  // The standalone MCP page (clients / catalog / install / SSE stream /
  // lifecycle mutations) was removed, so only the server list remains.
  mcpServers: '/api/mcp/servers',

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

  // ── Profiles (container slot templates) ─────────────────────────
  profiles: '/api/profiles',
  profile: (name: string) => `/api/profiles/${encodeURIComponent(name)}`,

  // Install / FirstRun — all routes are under /api/install/* (installer.py).
  // The old /api/firstrun/* prefix was a stale artifact; the backend mounts
  // the router at /api/install (verified in src/hal0/api/routes/installer.py).
  installState: '/api/install/state',
  installCuratedModels: '/api/install/curated-models',
  installPickDefault: '/api/install/pick-default',
  // NOTE: there is no single /api/install/install (bundle-level) endpoint.
  // The wizard "install" step maps to POST /api/install/pick-default per model.
  // The hook (useFirstRunInstall) calls pick-default; the UI handles graceful
  // degradation on any error so progress stage still renders.
  installComplete: '/api/install/complete',
  // PUT /api/install/slots/{slot}/model — assign a model to a slot post-pick.
  installSlotModel: (slot: string) =>
    `/api/install/slots/${encodeURIComponent(slot)}/model`,
} as const
