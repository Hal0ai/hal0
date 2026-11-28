/**
 * Showcase overlay: the agents-and-services story.
 *
 * Hermes is installed, running and healthy — it is serving, a persona is
 * active, two delegated runs finished and one is still running, and one
 * command approval is waiting. Pi is installed and healthy. Two bridge
 * clients are paired ("claude-code" and "openwebui"). Every service is up and
 * healthy (hal0-api itself, hindsight-api, Open WebUI v0.13.5, Hermes,
 * ComfyUI), the doctor is green on every row (a few pass, none warn), and the
 * memory engine is clear: the embedder hindsight-ai/hs-embed-1 is recorded
 * AND configured, not mixed, and every health step reads "clear".
 *
 * Everything here is invented for the screenshots; nothing is read from a
 * real box. The in-bundle mock (src/api/mock.ts) answers allowlisted GETs
 * before page.route is consulted, so `/api/memory/health` is opted out of it
 * through `window.__hal0MockPassthrough` — hence AGENTS_SERVICES_PASSTHROUGH.
 * Every payload shape is copied from the hook type, the e2e spec that
 * already routes it, or the backend route; each one is cited below.
 */
import type { Page } from '@playwright/test'

import { json, VERSION } from './core'

/** Paths answered here rather than by the in-bundle mock. */
export const AGENTS_SERVICES_PASSTHROUGH: string[] = ['/api/memory/health']

const MINUTE = 60
const HOUR = 3600
const DAY = 86_400

const PIN_REF = 'caafe5389ae585ef77387a962aee05b1bcf17029' // src/hal0/agents/hermes_provision.py:930

/** ISO-8601, `secondsAgo` before the given instant. */
function iso(nowMs: number, secondsAgo = 0): string {
  return new Date(nowMs - secondsAgo * 1000).toISOString()
}

/** Epoch seconds, `secondsAgo` before the given instant. */
function epoch(nowMs: number, secondsAgo = 0): number {
  return Math.floor(nowMs / 1000) - secondsAgo
}

/** A systemd `systemctl show` timestamp: "Fri 2026-07-03 09:12:01 UTC". */
function systemdSince(nowMs: number, secondsAgo: number): string {
  const d = new Date(nowMs - secondsAgo * 1000)
  const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${days[d.getUTCDay()]} ${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` +
    ` ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} UTC`
  )
}

/** A healthy systemd unit_state row, up since `secondsAgo`. */
function unitState(nowMs: number, secondsAgo: number) {
  return {
    active_state: 'active',
    sub_state: 'running',
    unit_file_state: 'enabled',
    since: systemdSince(nowMs, secondsAgo),
  }
}

export async function installAgentsServices(page: Page): Promise<void> {
  // ── /api/agents — the installed bundled agents ─────────────────────────
  await page.route(/\/api\/agents(\?|$)/, (route) => {
    const now = Date.now()
    const agents = [
      {
        name: 'hermes',
        installed_at: iso(now, 9 * DAY),
        status: 'installed',
        data_dir: '/var/lib/hal0/agents/hermes',
        config_path: '/etc/hal0/agents/hermes.toml',
        unit_active: true,
      },
      {
        name: 'pi',
        installed_at: iso(now, 21 * DAY),
        status: 'installed',
        data_dir: '/var/lib/hal0/agents/pi',
        config_path: '/etc/hal0/agents/pi.toml',
        unit_active: null,
      },
    ]
    return json(route, { agents, count: agents.length })
  })
  // shape: AgentRecord (ui/src/api/hooks/useAgents.ts:52) over manager.as_dict
  // (src/hal0/agents/manager.py:180); spec ui/tests/e2e/specs/agent-view-v3.spec.ts:32

  // ── /api/agents/hermes/overview — installed, serving, healthy ──────────
  await page.route(/\/api\/agents\/hermes\/overview(\?|$)/, (route) => {
    const now = Date.now()
    return json(route, {
      name: 'hermes',
      kind: 'daemon',
      installed: true,
      status: 'installed',
      unit_active: true,
      generated_at: iso(now),
      job: null,
      // The card upgrades "ready" to "serving" when a slot is actually bound
      // (ui/src/dash/agents/agent-phase.ts:251) — the showcase box has one.
      phase: 'ready',
      degraded: [],
      doctor: [
        { id: 'gateway', status: 'ok', detail: 'hal0-agent@hermes.service is active.' },
        { id: 'api_server', status: 'ok', detail: 'profile default answered on :8642.' },
        { id: 'memory', status: 'ok', detail: 'hindsight-api answered.' },
      ],
      version: {
        live: {
          describe: 'v0.23.3',
          ref: PIN_REF,
          python_minor: '3.14',
          built_at: iso(now, 6 * DAY),
        },
        previous: null,
        pin: { describe: 'v0.23.3', ref: PIN_REF },
        update_available: false,
      },
      units: { agent: 'active', gateway: 'active', gateway_enabled: true },
      api_server: {
        endpoint: 'http://127.0.0.1:8642',
        healthy: true,
        profiles: ['default'],
      },
      profiles: [{ name: 'default', healthy: true }],
      platforms: ['api_server'],
      provision: {
        present: true,
        hal0_version: VERSION,
        hermes_version: 'v0.23.3',
        completed_at: iso(now, 6 * DAY),
        reported_at: iso(now, 6 * DAY),
        stale: false,
        live_built_at: iso(now, 6 * DAY),
        phases: [
          { name: 'install', status: 'ok', at: iso(now, 6 * DAY), failure_count: 0, reason: null },
          { name: 'gateway', status: 'ok', at: iso(now, 6 * DAY), failure_count: 0, reason: null },
          { name: 'profiles', status: 'ok', at: iso(now, 6 * DAY), failure_count: 0, reason: null },
        ],
        ok: 9,
        warn: 0,
        fail: 0,
        primary_model: 'qwen3.6-27b-mtp',
        delegation_model: 'qwen3-coder-30b-a3b',
        chat_slot_count: 2,
        mcp_server_count: 3,
        memory_provider: 'hindsight',
        persona: 'default',
        terminal_tool: 'on',
        approvals_mode: 'smart',
      },
      approvals: { mode: 'smart', effective: 'smart', in_sync: true, pending: 1 },
      memory: { available: true, writes: 1284 },
      links: { desktop: null },
    })
  })
  // shape: AgentOverview (ui/src/api/hooks/useAgents.ts:663); spec
  // ui/tests/e2e/specs/agent-cards-phases-v3.spec.ts:56 (hermesOverview)

  // ── /api/agents/pi/overview — the CLI agent, installed and healthy ─────
  await page.route(/\/api\/agents\/pi\/overview(\?|$)/, (route) => {
    const now = Date.now()
    return json(route, {
      name: 'pi',
      kind: 'cli',
      installed: true,
      status: 'installed',
      generated_at: iso(now),
      job: null,
      phase: 'installed',
      degraded: [],
      doctor: [{ id: 'binary', status: 'ok', detail: 'pi 1.6.1 on PATH.' }],
      version: {
        installed: { pi: '1.6.1', adapter: '7.0.1' },
        pin: { pi: '1.6.1', adapter: '7.0.1' },
        update_available: false,
      },
      profile: {
        home: '~/.pi',
        profiled_at: iso(now, 21 * DAY),
        home_readable: true,
        checks: [
          { id: 'binary', ok: true },
          { id: 'provider_ext', ok: true },
        ],
      },
      memory: { mcp_entry: true, hindsight_config: true, hindsight_api_reachable: true },
      commands: {
        install: 'sudo hal0 agent install pi',
        upgrade: 'sudo hal0 update component pi',
        uninstall: 'sudo hal0 agent uninstall pi',
      },
    })
  })
  // shape: AgentOverview (ui/src/api/hooks/useAgents.ts:663); spec
  // ui/tests/e2e/specs/agent-cards-phases-v3.spec.ts:103 (piOverview)

  // ── /api/agents/hermes/personas — one persona active ───────────────────
  await page.route(/\/api\/agents\/hermes\/personas(\?|$)/, (route) =>
    json(route, {
      agent_id: 'hermes',
      active: 'default',
      personas: [
        {
          id: 'default',
          display_name: 'Default',
          description: 'General assistant persona.',
          active: true,
        },
        {
          id: 'coder',
          display_name: 'Coder',
          description: 'Code-focused persona.',
          active: false,
        },
      ],
    }),
  )
  // shape: PersonaList (ui/src/api/hooks/useAgents.ts:77); spec
  // ui/tests/e2e/specs/agent-cards-phases-v3.spec.ts:437

  // ── /api/agents/hermes/command-approvals — the gate is on "smart" ──────
  await page.route('**/api/agents/hermes/command-approvals', (route) =>
    json(route, {
      agent_id: 'hermes',
      mode: 'smart',
      effective: 'smart',
      source: 'default',
      default: 'smart',
      allowed: ['smart', 'manual'],
      invalid_value: null,
      seed_unreadable: false,
      seed_path: '/etc/hal0/agents/hermes.toml',
      overrides_path: '/etc/hal0/agents/hermes/overrides.yaml',
      rendered: 'smart',
      in_sync: true,
    }),
  )
  // shape: CommandApprovalsStatus (ui/src/api/hooks/useHermesCommandApprovals.ts:22)
  // over the backend projection (src/hal0/agents/hermes_command_approvals.py:245);
  // spec ui/tests/e2e/specs/security-page-v3.spec.ts:264

  // ── /api/agent/approvals — one command approval waiting ─────────────────
  await page.route('**/api/agent/approvals', (route) => {
    const now = Date.now()
    return json(route, {
      approvals: [
        {
          id: 'appr-7f3c21',
          tool: 'terminal',
          args: { command: 'hal0 doctor verify' },
          target_args: ['command'],
          client_id: 'hermes',
          enqueued_at: epoch(now, 95),
          state: 'pending',
          hit_count: 1,
          decided_at: null,
          result: null,
          error: null,
        },
      ],
    })
  })
  // shape: ApprovalEntry.as_dict (src/hal0/mcp/approval_queue.py:186) over
  // ApprovalEntry (ui/src/api/hooks/useAgents.ts:83); spec
  // ui/tests/e2e/specs/ui-sweep-a-v3.spec.ts:214

  // ── /api/bridge/runs — two finished, one running ───────────────────────
  await page.route(/\/api\/bridge\/runs(\?|$)/, (route) => {
    const now = Date.now()
    const items = [
      {
        run_id: 'run_2f8c14',
        client: 'claude-code',
        agent: 'hermes',
        profile: 'default',
        session_key: 'claude-code:default',
        status: 'running',
        created_at: epoch(now, 72),
        updated_at: epoch(now, 5),
        input_preview: 'Summarize the 1.8.2 notes',
        output_preview: 'Drafting the summary from CHANGELOG.md — 14 entries.',
        error: null,
        usage: null,
        pending: null,
        last_seq: 6,
      },
      {
        run_id: 'run_9ab370',
        client: 'claude-code',
        agent: 'hermes',
        profile: 'default',
        session_key: 'claude-code:default',
        status: 'completed',
        created_at: epoch(now, 9 * MINUTE),
        updated_at: epoch(now, 8 * MINUTE),
        input_preview: 'Which slots are serving?',
        output_preview: 'primary and coder are serving; the rest are ready.',
        error: null,
        usage: null,
        pending: null,
        last_seq: 11,
      },
      {
        run_id: 'run_5d70be',
        client: 'openwebui',
        agent: 'hermes',
        profile: 'default',
        session_key: 'openwebui:default',
        status: 'completed',
        created_at: epoch(now, 26 * MINUTE),
        updated_at: epoch(now, 24 * MINUTE),
        input_preview: 'Check memory health',
        output_preview: 'Verdict clear — every step reads clear.',
        error: null,
        usage: null,
        pending: null,
        last_seq: 9,
      },
    ]
    return json(route, { items })
  })
  // shape: BridgeRun (ui/src/api/hooks/useBridge.ts:94) over RunRow.as_dict
  // (src/hal0/bridge/store.py:146) as the route projects it
  // (src/hal0/bridge/output.py:135)

  // ── /api/bridge/clients — two paired clients ───────────────────────────
  await page.route(/\/api\/bridge\/clients(\?|$)/, (route) => {
    const now = Date.now()
    const items = [
      {
        name: 'claude-code',
        note: 'Claude Code, on the desk machine',
        scopes: ['agents', 'memory'],
        created_at: epoch(now, 6 * DAY),
        created_by: 'api:admin',
        revoked_at: null,
        last_seen_at: epoch(now, 2 * MINUTE),
      },
      {
        name: 'openwebui',
        note: 'Open WebUI (managed by hal0)',
        scopes: ['agents', 'memory'],
        created_at: epoch(now, 3 * DAY),
        created_by: 'system:openwebui',
        revoked_at: null,
        last_seen_at: epoch(now, 40),
      },
    ]
    return json(route, { items })
  })
  // shape: BridgeClient (ui/src/api/hooks/useBridge.ts:40) over ClientRow.as_dict
  // (src/hal0/bridge/store.py:89); spec ui/tests/e2e/specs/memory-agents-v3.spec.ts:75

  // ── /api/bridge/me — the operator's own admin record ───────────────────
  await page.route('**/api/bridge/me', (route) =>
    json(route, {
      name: 'admin',
      tier: 'admin',
      scopes: ['agents', 'memory', 'admin'],
      created_at: null,
      last_seen_at: null,
      servers: ['hal0-agents', 'hal0-memory', 'hal0-admin'],
      tools: [
        'agent_run',
        'agent_runs_list',
        'agent_run_status',
        'agent_memory_search',
        'agent_memory_add',
      ],
    }),
  )
  // shape: the admin branch of GET /api/bridge/me (src/hal0/api/routes/bridge.py:561);
  // the hook reads {name, tier} (ui/src/api/hooks/useBridge.ts:159)

  // ── /api/services — every companion service up ─────────────────────────
  await page.route(/\/api\/services(\?|$)/, (route) => {
    const now = Date.now()
    const services = [
      {
        id: 'openwebui',
        name: 'OpenWebUI',
        description: 'Chat UI companion (podman container, LAN :3001).',
        managed: true,
        unit: 'hal0-openwebui.service',
        unit_state: unitState(now, 3 * DAY),
        up: true,
        detail: 'reachable — /health ok',
        stat: { label: 'version', value: 'v0.13.5' },
        url: 'http://hal0.example:3001',
        mdns_url: 'http://hal0.local:3001',
        loopback_port: null,
        actions: ['start', 'stop', 'restart', 'enable', 'disable'],
        mdns_capable: true,
        hints: [],
        wired: {
          chat: true,
          voice: true,
          documents: true,
          images: true,
          web_search: false,
          mcp: true,
          embed_model: 'nomic-embed-text-v1.5',
          image_model: 'flux1-schnell',
          agents: ['hermes-agent'],
        },
      },
      {
        id: 'comfyui',
        name: 'ComfyUI',
        description: 'Image generation engine (img slot container, LAN :8188 by default).',
        managed: true,
        unit: 'hal0-slot@img.service',
        unit_state: unitState(now, 5 * HOUR),
        up: true,
        detail: 'running — 0 job(s) active',
        stat: { label: 'jobs', value: '0 running / 0 queued' },
        url: 'http://hal0.example:8188',
        mdns_url: 'http://hal0.local:8188',
        loopback_port: null,
        actions: ['start', 'restart'],
        mdns_capable: true,
        hints: ['stop is GPU-arbiter managed — switch back to inference in the Image-Gen pane'],
        wired: null,
      },
      {
        id: 'hermes',
        name: 'Hermes',
        description: 'Bundled agent + dashboard (loopback :9119).',
        managed: true,
        unit: 'hal0-agent@hermes.service',
        unit_state: unitState(now, 6 * DAY),
        up: true,
        detail: 'systemd unit active',
        stat: null,
        url: null,
        mdns_url: null,
        loopback_port: 9119,
        actions: ['start', 'stop', 'restart', 'enable', 'disable'],
        mdns_capable: false,
        hints: [],
        wired: null,
      },
      {
        id: 'hindsight',
        name: 'Hindsight',
        description: 'Memory engine (native daemon, loopback :9177).',
        managed: true,
        unit: 'hindsight-api.service',
        unit_state: unitState(now, 9 * DAY),
        up: true,
        detail: 'systemd unit active',
        stat: null,
        url: null,
        mdns_url: null,
        loopback_port: 9177,
        actions: ['start', 'stop', 'restart', 'enable', 'disable'],
        mdns_capable: false,
        hints: [],
        wired: null,
      },
    ]
    return json(route, {
      services,
      mdns: {
        available: true,
        hostname: 'hal0.local',
        base_advertised: true,
        advertised: ['openwebui', 'comfyui'],
      },
    })
  })
  // shape: ServicesPayload / ManagedService (ui/src/api/hooks/useServices.ts:23)
  // over the route's row (src/hal0/api/routes/services.py:210); spec
  // ui/tests/e2e/specs/services-v3.spec.ts:14

  // ── /api/services/health — the same five services, all up ──────────────
  await page.route(/\/api\/services\/health(\?|$)/, (route) =>
    json(route, {
      services: [
        {
          id: 'openwebui',
          name: 'OpenWebUI',
          up: true,
          state: 'up',
          detail: 'reachable — /health ok',
          url: 'http://hal0.example:3001',
          stat: { label: 'version', value: 'v0.13.5' },
        },
        {
          id: 'comfyui',
          name: 'ComfyUI',
          up: true,
          state: 'up',
          detail: 'running — 0 job(s) active',
          url: 'http://hal0.example:8188',
          stat: { label: 'jobs', value: '0 running / 0 queued' },
        },
        {
          id: 'hermes',
          name: 'Hermes',
          up: true,
          state: 'up',
          detail: 'systemd unit active',
          url: null,
          stat: null,
        },
        {
          id: 'hindsight',
          name: 'Hindsight',
          up: true,
          state: 'up',
          detail: 'systemd unit active',
          url: null,
          stat: null,
        },
      ],
    }),
  )
  // shape: ServiceEntry (ui/src/api/hooks/useServicesHealth.ts:20) over the
  // health rows (src/hal0/api/routes/services_health.py:250)

  // ── /api/health/system — every check green ─────────────────────────────
  await page.route('**/api/health/system', (route) =>
    json(route, {
      status: 'ok',
      checks: {
        disk_state: {
          ok: true,
          free_mb: 402_000,
          total_mb: 916_000,
          floor_mb: 2048,
          path: '/var/lib/hal0',
        },
        disk_config: {
          ok: true,
          free_mb: 402_000,
          total_mb: 916_000,
          floor_mb: 2048,
          path: '/etc/hal0',
        },
        slot_manager: { ok: true, slots: 10, errored: [] },
        event_bus: { ok: true },
        mcp_mount: { ok: true, servers: ['admin', 'agents', 'memory'] },
      },
    }),
  )
  // shape: HealthSystem (ui/src/api/hooks/useRuntime.ts:68) over the route's
  // check map (src/hal0/api/routes/health.py:309); spec
  // ui/tests/e2e/specs/footer-health-tooltip-v3.spec.ts:23

  // ── /api/doctor — green on every row ───────────────────────────────────
  await page.route('**/api/doctor', (route) => {
    const rows = [
      { id: 'HAL0-API-UNREACHABLE', summary: 'Dashboard / API', detail: `serving (v${VERSION})` },
      { id: 'HAL0-DNS-LOCAL-UNRESOLVED', summary: 'Hostname', detail: '127.0.0.1 (no mDNS needed)' },
      { id: 'HAL0-RUNNERS-NONE-HEALTHY', summary: 'Runners', detail: '10/10 slot(s) healthy' },
      { id: 'HAL0-CAPABILITIES-NONE', summary: 'Capability slots', detail: '3 active: embed, img, tts' },
      { id: 'HAL0-MEMORY-ENGINE-UNREACHABLE', summary: 'Hindsight / banks', detail: 'reachable — 2 bank(s)' },
      { id: 'HAL0-OPENWEBUI-DOWN', summary: 'OpenWebUI', detail: 'reachable — /health ok' },
      { id: 'HAL0-HERMES-DOWN', summary: 'Hermes', detail: 'systemd unit active' },
    ]
    return json(route, {
      verdict: 'ok',
      diagnoses: rows.map((row) => ({
        id: row.id,
        severity: 'info',
        confidence: 'high',
        summary: row.summary,
        detail: row.detail,
        evidence: [{ kind: 'endpoint', summary: row.detail }],
        next_steps: [],
        fixable: false,
      })),
    })
  })
  // shape: DoctorFeed / Diagnosis (ui/src/api/hooks/useDiagnoses.ts:37) over
  // the check suite (src/hal0/health_report.py:341) — ids from
  // src/hal0/health_report.py:379, all PASS → severity "info"

  // ── /api/install/state — the box is past first run ─────────────────────
  await page.route('**/api/install/state', (route) =>
    json(route, {
      first_run: false,
      has_models: true,
      has_default_slot: true,
      openwebui_running: true,
      sentinel_path: '/var/lib/hal0/.first_run_done',
      bundle: null,
    }),
  )
  // shape: InstallState (ui/src/api/hooks/useInstallState.ts:20) over the
  // route's body (src/hal0/api/routes/installer.py:154)

  // ── /api/system-info — the runner registry, all installed ──────────────
  await page.route('**/api/system-info', (route) =>
    json(route, {
      hardware: {
        platform: 'bare-metal',
        platform_label: 'Bare metal',
        cpu_name: 'AMD Ryzen AI Max+ 395',
        cpu_cores: 16,
        cpu_threads: 32,
        gpu_name: 'AMD Radeon 8060S',
        gpu_vendor: 'AMD',
        unified_memory_mb: 131072,
        ram_total_mb: 131072,
        vram_total_mb: 131072,
        disk_free_mb: 402_000,
        npu_present: true,
        npu_name: 'AMD XDNA NPU',
      },
      features: {
        gpu: true,
        npu: true,
        memory: true,
        openwebui: true,
        hermes: true,
      },
      backends: {
        standard: {
          image: 'ghcr.io/hal0ai/hal0-runner-standard:1.8.2',
          runtime_family: 'llama-server',
          device_class: 'gpu',
          backend: 'rocm',
          title: 'Standard',
          blurb: 'The default runner for the GPU lanes.',
          tier: 'default',
          supported_backends: ['rocm', 'vulkan'],
          state: 'installed',
        },
        cpu: {
          image: 'ghcr.io/hal0ai/hal0-runner-cpu:1.8.2',
          runtime_family: 'llama-server',
          device_class: 'cpu',
          backend: 'cpu',
          title: 'CPU',
          blurb: 'The CPU-only runner for the utility lanes.',
          tier: 'default',
          supported_backends: ['cpu'],
          state: 'installed',
        },
      },
    }),
  )
  // shape: SystemInfo / SystemInfoBackend (ui/src/api/hooks/useRuntimes.ts:49);
  // spec ui/tests/e2e/specs/runtimes-page-v3.spec.ts:16

  // ── /api/config/urls — the reachable links ─────────────────────────────
  await page.route('**/api/config/urls', (route) =>
    json(route, {
      api: 'http://127.0.0.1:8080',
      openwebui: 'http://hal0.example:3001',
      openwebui_enabled: true,
      openwebui_ip: 'http://127.0.0.1:3001',
      hermes: 'https://hermes.example',
      hermes_enabled: true,
      comfyui: 'http://hal0.example:8188',
      comfyui_public: false,
      comfyui_lan: true,
    }),
  )
  // shape: ConfigUrls (ui/src/api/hooks/useConfigUrls.ts:18); spec
  // ui/tests/e2e/specs/services-v3.spec.ts:145

  // ── /api/memory/agents — who reaches memory, and how to install ────────
  await page.route('**/api/memory/agents', (route) =>
    json(route, {
      banks: ['coding-agent::hal0'],
      install: {
        harnesses: [
          'claude-code',
          'codex',
          'opencode',
          'pi',
          'cursor-cli',
          'copilot-cli',
          'qwen-code',
        ],
        command: 'hal0 memory agents install <harness> --bridge-key-file hal0-memory.key',
        mint_command: 'hal0 bridge add <client> --scope memory',
        key_file: 'hal0-memory.key',
        clients: {
          'claude-code': 'claude-code',
          codex: 'codex',
          opencode: 'opencode',
          pi: 'pi-memory',
          'cursor-cli': 'cursor-cli',
          'copilot-cli': 'copilot-cli',
          'qwen-code': 'qwen-code',
        },
      },
      console_url: null,
    }),
  )
  // shape: MemoryAgents (ui/src/api/hooks/useMemory.ts:508) over the route's
  // body (src/hal0/api/routes/memory_lanes.py:365); spec
  // ui/tests/e2e/specs/memory-agents-v3.spec.ts:89

  // ── /api/memory/models — the embedder, recorded and configured ─────────
  await page.route('**/api/memory/models', (route) => {
    const now = Date.now()
    const embedder = 'hindsight-ai/hs-embed-1'
    return json(route, {
      embeddings: {
        provider: 'local',
        model: embedder,
        recorded: embedder,
        recorded_provider: 'local',
        configured: embedder,
        configured_provider: 'local',
        mixed: false,
        needs_switch: false,
        source: 'record',
        recorded_at: iso(now, 12 * DAY),
        effective: { provider: 'local', model: embedder, source: 'process' },
        conflict: null,
        record_error: null,
        store_unavailable: null,
      },
      reranker: {
        provider: 'local',
        model: 'hindsight-ai/hs-rerank-1',
        max_candidates: 300,
        max_candidates_low: 100,
        max_candidates_mid: 200,
        max_candidates_high: 300,
        local_max_concurrent: 4,
        local_batch_size: 32,
        local_timeout_s: 60,
        base_url: null,
        api_model: null,
        timeout_s: 30,
      },
      decision: {
        enabled: false,
        provider: 'local',
        model: 'decider',
        reachable: false,
        installed: false,
        install_state: 'not-installed',
        unit_active: null,
      },
    })
  })
  // shape: MemoryModels (ui/src/api/hooks/useMemory.ts:530) over the route's
  // body (src/hal0/api/routes/memory_lanes.py:390); embedder record fields
  // from src/hal0/memory/embedder_record.py:685; spec
  // ui/tests/e2e/fixtures/memoryArea.ts:32

  // ── /api/memory/refresh-policy — the reconcile ran, all clean ──────────
  await page.route('**/api/memory/refresh-policy', (route) => {
    const now = Date.now()
    return json(route, {
      running: true,
      last_report: {
        dry_run: false,
        policy: { stale_grace_s: 86_400, min_refresh_interval_s: 900 },
        trigger: { kind: 'schedule', at: iso(now, 40 * MINUTE) },
        deferred: null,
        ok: true,
        started_at: epoch(now, 41 * MINUTE),
        finished_at: epoch(now, 40 * MINUTE),
        banks: [
          {
            bank: 'hal0:shared',
            models_seen: 4,
            models_patched: [],
            models_unchanged: 4,
            model_errors: [],
            config_updates: {},
            config_error: null,
            skipped: null,
            list_error: null,
            notes: [],
            ok: true,
          },
        ],
      },
      consolidation: {
        bank: 'hal0:shared',
        facts: 902,
        models_seen: 4,
        models_consolidated: 1,
        errors: [],
        at: iso(now, 2 * HOUR),
      },
      notes: [],
    })
  })
  // shape: MemoryRefreshPolicyStatus (ui/src/api/hooks/useMemory.ts:399) over
  // the route's body (src/hal0/api/routes/memory_admin.py:1372) and
  // PolicyReport.to_dict (src/hal0/memory/refresh_policy.py:296)

  // ── /api/memory/health — verdict clear, every step clear ───────────────
  await page.route('**/api/memory/health', (route) => {
    const now = Date.now()
    const embedder = 'hindsight-ai/hs-embed-1'
    return json(route, {
      enabled: true,
      verdict: 'clear',
      checked_at: iso(now),
      partial: [],
      engine: { reachable: true, version: '1.2.3' },
      extraction: {
        slot: 'memory',
        mode: 'dedicated',
        slot_resolves: true,
        llm_reachable: true,
        concurrency: 2,
        failures_6h: 0,
        top_errors: [],
      },
      embedder: {
        recorded: { provider: 'local', model: embedder },
        configured: { provider: 'local', model: embedder },
        mixed: false,
        needs_switch: false,
        conflict: null,
        informational: false,
      },
      queue: {
        waiting: 0,
        failed: 0,
        oldest_waiting_s: null,
        oldest_waiting_any_s: null,
        consolidation_pending: 0,
        consolidation_failed: 0,
        by_bank: [],
        truncated: false,
      },
      knowledge: {
        pages: 18,
        pages_stale: 0,
        models: 4,
        models_stale: 0,
        refresh_failed: 0,
        last_success_at: iso(now, 15 * MINUTE),
      },
      banks: {
        layout: 'two_bank',
        total: 2,
        facts: 1284,
        retired: 0,
        list: [
          {
            bank_id: 'hal0:shared',
            facts: 902,
            documents: 18,
            retired: false,
            role: 'shared',
          },
          {
            bank_id: 'hal0:private',
            facts: 382,
            documents: 6,
            retired: false,
            role: 'private',
          },
        ],
      },
      writes: null,
      steps: ['engine', 'extraction', 'queue', 'embedder', 'knowledge'].map((id) => ({
        id,
        state: 'clear',
        waits_on: null,
        reason_code: null,
        figures: {},
      })),
    })
  })
  // shape: the route's body (src/hal0/api/routes/memory_health.py:97) over the
  // section collector (src/hal0/api/routes/_memory_health_collect.py:790) and
  // the step shape (src/hal0/memory/health_path.py:109); spec
  // ui/tests/e2e/fixtures/apiMock.ts:219
}
