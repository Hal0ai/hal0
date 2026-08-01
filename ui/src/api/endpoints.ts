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
  // Read-only aggregate of docker + systemd + ComfyUI HTTP.
  comfyuiStatus: '/api/comfyui/status',
  comfyuiSwitchover: '/api/comfyui/switchover',
  // Pin image mode (disables the arbiter's idle auto-restore).
  comfyuiPin: '/api/comfyui/pin',
  // Render control
  comfyuiRenderCancel: '/api/comfyui/render/cancel',
  comfyuiRestart: '/api/comfyui/restart',
  comfyuiLogs: '/api/comfyui/logs',
  // List launchable workflow .json files discovered on disk (operator-dropped
  // files in the bind-mounted workflows dir show up here without a rebuild).
  comfyuiWorkflows: '/api/comfyui/workflows',
  comfyuiWorkflowLaunch: (name: string) => `/api/comfyui/workflows/${encodeURIComponent(name)}/launch`,
  // Latest output image proxy
  comfyuiPreview: '/api/comfyui/preview',

  // GET /api/system-info (CLIENT) — hardware + features + per-RUNNER_IMAGES
  // backend state (installed | installable | unavailable). Feeds the Runtimes
  // settings page (D3) — the runner/image evidence axis.
  systemInfo: '/api/system-info',
  slot: (name: string) => `/api/slots/${encodeURIComponent(name)}`,
  slotConfig: (name: string) => `/api/slots/${encodeURIComponent(name)}/config`,
  // TTS voice-list proxy — forwards to the slot container's /v1/audio/voices;
  // {voices: [], source: "offline"} when the slot is cold.
  slotVoices: (name: string) => `/api/slots/${encodeURIComponent(name)}/voices`,
  slotDefaults: (name: string) => `/api/slots/${encodeURIComponent(name)}/defaults`,
  slotRestart: (name: string) => `/api/slots/${encodeURIComponent(name)}/restart`,
  slotLoad: (name: string) => `/api/slots/${encodeURIComponent(name)}/load`,
  slotUnload: (name: string) => `/api/slots/${encodeURIComponent(name)}/unload`,
  slotSwap: (name: string) => `/api/slots/${encodeURIComponent(name)}/swap`,
  // POST /api/slots/{name}/rename — body { new_name }. The stable slot id is
  // untouched; the unit is still name-keyed so the slot must be OFFLINE (409
  // while running) until the live-rename migration lands (rework §11.1).
  slotRename: (name: string) => `/api/slots/${encodeURIComponent(name)}/rename`,
  // `backfill` is explicit so a RECONNECT can ask for 0 (#1472): the server
  // replays its 400-line default on every open, and the slot-log ring
  // deliberately does not content-dedup (raw journald repeats progress-bar
  // lines legitimately), so an unqualified reconnect duplicated up to 400
  // lines. Omitted = the server default, which is what the first connect wants.
  slotLogsStream: (name: string, backfill?: number) =>
    `/api/slots/${encodeURIComponent(name)}/logs/stream` +
    (backfill === undefined ? '' : `?backfill=${encodeURIComponent(String(backfill))}`),
  slotPull: (name: string) =>
    `/api/slots/${encodeURIComponent(name)}/pull`,
  slotPullStream: (name: string) =>
    `/api/slots/${encodeURIComponent(name)}/pull/stream`,
  slotResolved: (name: string) =>
    `/api/slots/${encodeURIComponent(name)}/resolved`,

  // ── Models / pull lifecycle ──────────────────────────────────────
  models: '/api/models',
  model: (id: string) => `/api/models/${encodeURIComponent(id)}`,
  modelPull: (id: string) => `/api/models/${encodeURIComponent(id)}/pull`,
  modelPullStatus: (id: string) => `/api/models/${encodeURIComponent(id)}/pull/status`,
  modelPullStream: (id: string) => `/api/models/${encodeURIComponent(id)}/pull/stream`,
  modelPullCancel: (id: string) => `/api/models/${encodeURIComponent(id)}/pull/cancel`,
  modelPulls: '/api/models/pulls',
  modelPullDelete: (id: string) => `/api/models/pulls/${encodeURIComponent(id)}`,
  modelInspect: '/api/models/inspect',
  // Per-type default MODEL marker: POST {default:true} promotes (demoting the
  // current holder of the type), {default:false} clears. Server enforces the
  // single-holder invariant in one chokepoint.
  modelSetDefault: (id: string) => `/api/models/${encodeURIComponent(id)}/default`,
  // HF update check + in-place update. Check is GET (TTL-cached server-side,
  // ?refresh=1 forces); update re-pulls the row's hf_repo/hf_filename over
  // its installed path and reports through the standard pull job surface.
  modelUpdatesCheck: '/api/models/updates/check',
  modelUpdate: (id: string) => `/api/models/${encodeURIComponent(id)}/update`,
  modelScanPreview: '/api/models/scan/preview',
  modelAddFromPath: '/api/models/add-from-path',
  // Issue #311: free-text HF Hub model search backing the dashboard
  // "Search HF" button. Distinct from /api/models/inspect (which
  // resolves a known coord into variants) — this proxies HF's
  // /api/models?search=… and returns a small typed list.
  hfSearch: '/api/hf/search',

  // ── Runner Image catalogue (subpage of Models) ────────────────────
  // Mirrors the model-pull surface above: /api/runner-images (list) +
  // /downloaded (locally-pulled only, for the slot edit drawer's Runner
  // Image field — owned by fix/slot-edit-drawer-cleanup) + per-id
  // pull/status/stream/cancel. Catalogue ids are GHCR repo paths
  // ("hal0ai/hal0-toolbox-cpu") so they're NOT re-encoded per-segment —
  // encodeURIComponent would turn the id's "/" into "%2F", which the
  // backend's :path route converter expects decoded, matching how the
  // browser's fetch/EventSource already send it.
  runnerImages: '/api/runner-images',
  runnerImagesDownloaded: '/api/runner-images/downloaded',
  runnerImagesSync: '/api/runner-images/sync',
  runnerImagesPulls: '/api/runner-images/pulls/list',
  runnerImage: (id: string) => `/api/runner-images/${id}`,
  runnerImagePull: (id: string) => `/api/runner-images/${id}/pull`,
  runnerImagePullStatus: (id: string) => `/api/runner-images/${id}/pull/status`,
  runnerImagePullStream: (id: string) => `/api/runner-images/${id}/pull/stream`,
  runnerImagePullCancel: (id: string) => `/api/runner-images/${id}/pull/cancel`,

  // ── Backends ─────────────────────────────────────────────────────
  // There is NO generic install route — the only install-like operations
  // the server exposes are the NPU load/unload pair below
  // (src/hal0/api/routes/backends.py).
  backends: '/api/backends',
  backend: (id: string) => `/api/backends/${encodeURIComponent(id)}`,
  backendNpuLoad: '/api/backends/npu/load',
  backendNpuUnload: '/api/backends/npu/unload',

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
  // NPU occupancy — AIE column allocation (xrt-smi probe) + per-FLM-slot
  // composition for the NPU occupancy card. Single-tenant: one FLM claims
  // the whole 8-column array.
  npuOccupancy: '/api/npu/occupancy',
  statsThroughputHistory: '/api/stats/throughput/history',
  // W6 opt-in cards: power/thermal (§5 spike confirmed amdgpu hwmon).
  statsPower: '/api/stats/power',
  // Dashboard-redesign Requests widget: dispatcher-side /v1 rollup
  // (req/min, p50/p95, per-endpoint counts over 60s). Live
  // (src/hal0/api/routes/hardware.py); useRequestsRollup still fails soft
  // to "—" on 404/network error as a defensive floor, not because the
  // route is missing.
  statsRequests: '/api/stats/requests',
  // W6: agent approvals SSE stream (polled list hook is primary; SSE for future).
  agentApprovalsStream: '/api/agent/approvals/events',

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

  // ── Agents — bundled lifecycle + sidebar rollup (v0.3 PR-6) ──────
  // `agents` lives in the catalogue block above (one entry, used by
  // both the bundled-list and sidebar surfaces). The remaining
  // endpoints under this block are surfaces the SidebarAgentBlock
  // calls — all live (src/hal0/api/agents/personas.py,
  // src/hal0/api/agents/restart.py, src/hal0/api/routes/approvals.py).
  // Consuming hooks still fall back to "—" and console.warn once on a
  // 404/network error so the sidebar degrades gracefully on partial
  // deployments, but that's a defensive floor, not the expected state.
  agentPersonas: (id: string) =>
    `/api/agents/${encodeURIComponent(id)}/personas`,
  // Restart the systemd unit backing an agent (POST → {status, detail}).
  // Backend: hal0.api.agents.restart — only "hermes" is a known id in v0.3.
  agentRestart: (id: string) =>
    `/api/agents/${encodeURIComponent(id)}/restart`,
  agentApprovals: '/api/agent/approvals',
  // Approval CRUD — list is an alias of agentApprovals; approve/deny are
  // the action endpoints added by backend-dev task #7 (PR #741 TODOs).
  agentApprovalsList: '/api/agent/approvals',
  agentApprovalApprove: (id: string) =>
    `/api/agent/approvals/${encodeURIComponent(id)}/approve`,
  agentApprovalDeny: (id: string) =>
    `/api/agent/approvals/${encodeURIComponent(id)}/deny`,
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
  memoryBankDocuments: (bank: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/documents`,
  memoryBankDocument: (bank: string, id: string) =>
    `/api/memory/banks/${encodeURIComponent(bank)}/documents/${encodeURIComponent(id)}`,
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

  // ── MCP host introspection ───────────────────────────────────────
  // Read-only list of hosted MCP servers (+ their tool_details), backing
  // the MCP section of the Connections view and the sidebar status pip.
  // The standalone MCP page (clients / catalog / install / SSE stream /
  // lifecycle mutations) was removed, so only the server list remains.
  mcpServers: '/api/mcp/servers',

  // ── Memory (ADR-0023 graph-extraction gate) ──────────────────────
  // status → { enabled, extraction_slot, slot_resolves, available_slots, ... }
  // PUT body → { enabled?, extraction_slot? }
  memoryGraphStatus: '/api/memory/graph/status',
  memoryGraph: '/api/memory/graph',
  // Bulk-requeue every failed extraction/consolidation op across banks.
  memoryGraphRetry: '/api/memory/graph/retry',


  // ── Journal (HTTP backfill + SSE tail) ───────────────────────────
  // Per #322 Phase 1 (PR #330): the ``/api/journal`` surface
  // supersedes ``/api/logs``.
  journal: '/api/journal',
  journalStream: '/api/journal/stream',

  // ── Activity log (durable structured audit trail) ────────────────
  // Backfill + SSE tail + export, all honouring the same filter set
  // (since/category/action/severity/outcome/actor/kind/search/limit).
  // `epoch` rides every payload: a per-process id that, when it CHANGES
  // between polls, means the backend restarted → reset the `since`
  // cursor to 0 (fixes the footer-blank-after-restart bug).
  activity: '/api/activity',
  activityStream: '/api/activity/stream',
  activityExport: '/api/activity/export',

  // ── Auth posture (D4 Security page) ──────────────────────────────
  // GET /api/auth/status (OPEN) → { auth_required, has_admin_key, tier }.
  // Status only — never key values. Client-key status, admin-key fingerprint,
  // last-rotated, and login-throttle counters are NOT reported by this route
  // (D4 flags them as API-lane requests); the page shows disabled-with-reason
  // rather than fabricating them. Key rotation IS live — see `authRotate`
  // below (POST /api/auth/rotate) — this route just doesn't report it.
  authStatus: '/api/auth/status',
  // POST /api/auth/login (OPEN) — body { key }; on success mints the HttpOnly
  // session cookie and returns { ok, tier }. Wrong key → 401 auth.invalid_key;
  // throttled → 429 auth.rate_limited with details.retry_after_s.
  authLogin: '/api/auth/login',
  // POST /api/auth/logout (OPEN) — clears the session cookie (HttpOnly, so JS
  // can't; this route is the only session end the browser has).
  authLogout: '/api/auth/logout',
  // PUT /api/auth/require (ADMIN) — body { require_auth }; persists the
  // [security].require_auth enforcement toggle. Applies live (no restart).
  // Refuses enabling with no admin key configured (400 auth.no_admin_key).
  authRequire: '/api/auth/require',
  // POST /api/auth/rotate (ADMIN) — body { tier: 'admin'|'client' }; mints a
  // fresh box key, writes it to /etc/hal0/api.env (0640, never world-readable),
  // and applies it live in-process (no restart). Returns STATUS ONLY —
  // { tier, rotated_at, key_len, fingerprint, applies_live, restart_required,
  // session_preserved, note } — NEVER the key value. Rate-limited (429).
  authRotate: '/api/auth/rotate',
  // GET /api/auth/exposure (ADMIN) — serializes RULES + OPEN_ALLOWLIST from
  // security/exposure.py: the live per-(method,path) deny-by-default
  // classification table + per-class counts. Backs the Settings ▸ Security
  // exposure table (ExposureTable.jsx) — wired live since Phase 1 wave 2.
  authExposure: '/api/auth/exposure',

  // ── Flag-migration report (D5 migration-resolve) ─────────────────
  // GET /api/migrations/flag-report — MISSING today (API-lane request). The
  // typed client (useMigrationReport) returns an empty report by default and
  // fails soft to empty on 404/network, so the banner + resolution view stay
  // dormant until the migration lane ships the endpoint. Shape is documented
  // in useMigrationReport.ts.
  migrationFlagReport: '/api/migrations/flag-report',

  // ── Doctor diagnoses (D6 diagnostics panel) ──────────────────────
  // GET /api/doctor — LIVE (src/hal0/api/routes/doctor.py): composes the
  // same typed Diagnosis objects (HAL0-* id / severity / evidence /
  // next_steps — src/hal0/diagnostics.py) `hal0 doctor verify --json`
  // prints, over HTTP. ADMIN-classified (aggregates ADMIN-only subsystem
  // detail). Read by useDiagnoses.ts (#1458); a 404 from an older backend
  // falls back to a synthesised GET /api/system-info evidence card.
  doctor: '/api/doctor',

  // ── System health (honest degraded probe) ───────────────────────
  // {status:"ok"|"degraded", checks:{...}} — drives the runtime chip
  // colour + a tooltip listing failing checks (B12).
  healthSystem: '/api/health/system',

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
  // Revert to the retained previous version (/var/lib/hal0/hal0.previous).
  updateRollback: '/api/updates/rollback',
  // Channel (stable | nightly) — GET reads hal0.toml telemetry.channel;
  // PUT persists the choice back so subsequent /check calls honour it.
  updateChannel: '/api/updates/channel',
  // Post-update drift (WS-J, #1111): slots whose running process still uses
  // the pre-update launch command. GET reports; POST restarts only those.
  updateSlotDrift: '/api/updates/slot-drift',
  updateRestartSlots: '/api/updates/restart-slots',
  // Secrets
  secrets: '/api/secrets',
  secret: (name: string) => `/api/secrets/${encodeURIComponent(name)}`,

  // ── Upstream providers (external LLM endpoints) ──────────────────
  // GET list / POST create; PATCH settings / DELETE per name; POST test
  // probes reachability. providersCatalog feeds the "Add upstream" form;
  // providerCredentials writes ONE api-key to api.env ({key, value} —
  // the secret never transits the upstream CRUD surface).
  upstreams: '/api/upstreams',
  upstream: (name: string) => `/api/upstreams/${encodeURIComponent(name)}`,
  upstreamTest: (name: string) => `/api/upstreams/${encodeURIComponent(name)}/test`,
  providersCatalog: '/api/providers/catalog',
  providerCredentials: (name: string) =>
    `/api/providers/${encodeURIComponent(name)}/credentials`,
  // Service URL discovery — the dashboard reads this to resolve the
  // reachable hostnames for sibling services (OpenWebUI, Hermes) from the
  // request host, so links work on any install (localhost / LAN IP /
  // hal0.local / custom domain) without hardcoding. See routes/config.py.
  configUrls: '/api/config/urls',

  // ── Services health (§2d — NEW endpoint, fail soft on 404) ─────
  servicesHealth: '/api/services/health',

  // ── Services management (dedicated Services page) ────────────────
  // GET services → { services:[{id,name,up,detail,unit,unit_state,url,
  // mdns_url,actions,...}], mdns:{...} }. Actions run allow-listed
  // systemctl verbs (registry-driven — see hal0/services/registry.py).
  // mdns GET/POST → avahi discovery status / advertise-withdraw toggle.
  // Per-service logs reuse the generic journald tail: logsUnit(unit).
  services: '/api/services',
  serviceAction: (id: string) => `/api/services/${encodeURIComponent(id)}/action`,
  servicesMdns: '/api/services/mdns',
  logsUnit: (unit: string, n = 120) =>
    `/api/logs?unit=${encodeURIComponent(unit)}&n=${n}`,

  // ── ComfyUI native queue + history (proxy-reachable via :8188) ──
  // These are NOT under /api — ComfyUI's own HTTP server at :8188 is
  // reachable directly from the browser (same LAN). Build the base URL
  // with comfyNativeBase() helper in the consuming component.
  // comfyNativeQueue and comfyNativeHistory are path suffixes only:
  comfyNativeQueue: '/queue',
  comfyNativeHistory: '/history',

  // ── Dashboard layout persistence (§2c, DashLayout store) ────────
  // GET → 200 DashLayout | 200 {} (no saved layout yet)
  // PUT <DashLayout> → 204
  // Fail-soft: 404 treated as "no layout saved" by useDashLayout hook.
  dashboardLayout: '/api/user/dashboard-layout',

  // ── Meta enums (static per-release taxonomy) ─────────────────────
  // GET /api/meta/enums → devices / backends / device_classes / slot_types /
  // model_capabilities (+ aliases) / model_backends / runtime_families +
  // backend_to_device / device_default_profiles maps. Consumed by useMeta;
  // useMetaEnums() falls back to META_ENUMS_FALLBACK when absent.
  metaEnums: '/api/meta/enums',

  // ── Chat templates (per-model default template catalogue) ────────
  // GET /api/chat-templates → [{id, label}] list of known template ids
  // that can be pinned as model.defaults.chat_template. The "auto"
  // sentinel (use the GGUF's embedded template) is not emitted by the
  // backend — the UI prepends it as the first <option>.
  chatTemplates: '/api/chat-templates',

  // ── Profiles (container slot templates) ─────────────────────────
  profiles: '/api/profiles',
  profile: (name: string) => `/api/profiles/${encodeURIComponent(name)}`,
  // POST export (envelope) | import (collection-level POST: dry-run, then create).
  profileExport: (name: string) => `/api/profiles/${encodeURIComponent(name)}/export`,
  profileImport: '/api/profiles/import',

  // ── Stacks (named, portable slot+profile+model bundles) ─────────
  // GET list (+ active + drift) | POST create. Per-stack: GET detail | PUT |
  // DELETE | POST apply (?dry_run=true → diff) | POST export (envelope).
  // import/snapshot are collection-level POSTs.
  stacks: '/api/stacks',
  stack: (slug: string) => `/api/stacks/${encodeURIComponent(slug)}`,
  stackApply: (slug: string) => `/api/stacks/${encodeURIComponent(slug)}/apply`,
  stackExport: (slug: string) => `/api/stacks/${encodeURIComponent(slug)}/export`,
  stackImport: '/api/stacks/import',
  stackSnapshot: '/api/stacks/snapshot',

  // One-click unit repair/restart (design D5). Whitelisted units only —
  // includes hal0-api.service itself, which is how the dashboard offers an
  // API restart (the request connection drops mid-restart by design; callers
  // poll /api/health until the service is back).
  installServiceRepair: (unit: string) =>
    `/api/install/services/${encodeURIComponent(unit)}/repair`,

  // Install state — backs the post-install banner and passive install-state hook.
  // Retained for useInstallState.ts (banner/status surface). The FirstRun picker
  // endpoints (apply, complete, curated-models, pick-default, services, etc.) were
  // removed when the web FirstRun wizard was folded into `hal0 setup` CLI/TUI.
  installState: '/api/install/state',

  // ── Operator Board (#board) — FROZEN FE↔BE contract (SPEC §4) ─────
  // hal0-api thin audited proxy → Hermes kanban
  // ({HERMES_DASHBOARD_BASE_URL or 127.0.0.1:9119}/api/plugins/kanban/*).
  // `?board=<slug>` threads through every task/board-scoped call (omit =
  // current board). Mutations are audited server-side; reads + SSE/WS are not.
  // AUTHORED BY THE UI LEAD and FROZEN — the board hooks (useBoard.ts) consume
  // these; do not diverge from SPEC §4.
  board: '/api/board/board',                 // GET ?tenant=&include_archived=&board=&workflow_template_id=
  boardTasks: '/api/board/tasks',            // POST (CreateTaskBody)
  boardTask: (id: string) => `/api/board/tasks/${encodeURIComponent(id)}`,                 // GET | PATCH | DELETE
  boardTaskComments: (id: string) => `/api/board/tasks/${encodeURIComponent(id)}/comments`, // POST
  boardTaskReassign: (id: string) => `/api/board/tasks/${encodeURIComponent(id)}/reassign`, // POST
  boardTaskSpecify: (id: string) => `/api/board/tasks/${encodeURIComponent(id)}/specify`,   // POST
  boardTaskDecompose: (id: string) => `/api/board/tasks/${encodeURIComponent(id)}/decompose`,// POST
  boardTaskReclaim: (id: string) => `/api/board/tasks/${encodeURIComponent(id)}/reclaim`,    // POST
  boardTaskLog: (id: string) => `/api/board/tasks/${encodeURIComponent(id)}/log`,            // GET ?tail= (pull-only)
  boardLinks: '/api/board/links',            // POST {parent_id, child_id} | DELETE {parent_id, child_id}
  boardTasksBulk: '/api/board/tasks/bulk',   // POST
  boardDispatch: '/api/board/dispatch',      // POST ?max=N (one-shot nudge)
  boards: '/api/board/boards',               // GET ?include_archived= | POST (CreateBoardBody)
  boardBySlug: (slug: string) => `/api/board/boards/${encodeURIComponent(slug)}`,            // PATCH | DELETE ?delete=
  boardSwitch: (slug: string) => `/api/board/boards/${encodeURIComponent(slug)}/switch`,     // POST
  boardProfiles: '/api/board/profiles',      // GET
  boardAssignees: '/api/board/assignees',    // GET ?board=
  boardStats: '/api/board/stats',            // GET ?board=
  boardWorkersActive: '/api/board/workers/active', // GET
  boardRun: (id: string) => `/api/board/runs/${encodeURIComponent(id)}`,                     // GET
  boardConfig: '/api/board/config',          // GET (read-only orchestration knobs)
  boardOrchestration: '/api/board/orchestration', // GET | PUT (4 knobs)
  boardEvents: '/api/board/events',          // WS ?since=&board= (local BoardStore poll — no token/tenant, see board_ws.py)
  boardChat: '/api/board/chat',              // POST (SSE)
} as const
