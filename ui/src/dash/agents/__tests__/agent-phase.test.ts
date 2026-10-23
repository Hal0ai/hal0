// Pure card-model derivation for the agent cards (agent-phase.ts). Fixtures are
// literal objects shaped like GET /api/agents/{name}/overview (AgentOverview).
import { describe, expect, it } from 'vitest'
import type { AgentOverview } from '@/api/hooks/useAgents'
import type { ConfigUrls } from '@/api/hooks/useConfigUrls'
import { deriveCardModel, type CardModelInput } from '../agent-phase'

const READY: CardModelInput = {
  record: { name: 'hermes', status: 'installed', unit_active: true },
  overview: null,
  slot: null,
  job: null,
  caps: null,
  urls: null,
}

const REF = '0123456789abcdef0123456789abcdef01234567'
const PREV_REF = 'aaaaaaaabbbbbbbbccccccccddddddddeeeeeeee'
const PIN_REF = 'fedcba9876543210fedcba9876543210fedcba98'

const READY_OVERVIEW: AgentOverview = {
  name: 'hermes',
  kind: 'daemon',
  installed: true,
  status: 'installed',
  unit_active: true,
  generated_at: '2026-10-22T12:00:00Z',
  phase: 'ready',
  degraded: [],
  job: null,
  version: {
    live: { id: 'r1', ref: REF, describe: 'v0.22.2', python_minor: '3.14', hal0_version: '1.6.0' },
    previous: { id: 'r0', ref: PREV_REF, describe: 'v0.22.1' },
    pin: { ref: PIN_REF, describe: null },
    component: { installed: 'v0.22.2', pinned: 'v0.22.2', status: 'current' },
    update_available: false,
  },
  units: { agent: 'active', gateway: 'active', gateway_enabled: true },
  api_server: { endpoint: '127.0.0.1:8642', healthy: true, profiles: ['default', 'research'] },
  platforms: ['api_server', 'telegram'],
  provision: {
    present: true,
    stale: false,
    phases: [{ name: 'voice_wire', status: 'ok' }],
    memory_provider: 'hindsight',
    persona: 'ghost',
    terminal_tool: 'on',
    approvals_mode: 'smart',
  },
  approvals: { mode: 'smart', effective: 'smart', in_sync: true, pending: 0 },
  memory: { available: true, writes: 12 },
  links: { desktop: null },
}

const DEGRADED_OVERVIEW: AgentOverview = {
  ...READY_OVERVIEW,
  phase: 'degraded',
  units: { agent: 'active', gateway: 'inactive', gateway_enabled: true },
  version: { ...READY_OVERVIEW.version, update_available: true },
  degraded: [
    { reason: 'gateway_down', detail: 'hermes-gateway.service is not active', fix: 'restart_gateway' },
    { reason: 'provision_warn', detail: 'one phase warned', fix: 'none' },
    { reason: 'approvals_drift', detail: 'mode differs', fix: 'resync_approvals' },
    { reason: 'doctor_drift', detail: 'ownership', fix: 'cli', command: 'sudo hal0 agent repair hermes' },
  ],
}

const PI_OVERVIEW: AgentOverview = {
  name: 'pi',
  kind: 'cli',
  installed: true,
  status: 'installed',
  phase: 'installed',
  version: {
    installed: { pi: '0.84.4', adapter: '2.31.0' },
    pin: { pi: '1.2.0', adapter: '5.3.0' },
    update_available: true,
  },
  profile: {
    home: '/root',
    home_readable: true,
    checks: [
      { id: 'binary', ok: true },
      { id: 'provider_ext', ok: true },
      { id: 'hindsight_ext', ok: true },
    ],
  },
  memory: { mcp_entry: true, hindsight_config: true, hindsight_api_reachable: true },
  commands: {
    install: 'sudo hal0 agent install pi',
    upgrade: 'sudo hal0 agent upgrade pi',
    uninstall: 'sudo hal0 agent uninstall pi',
  },
}

const PI: CardModelInput = {
  record: { name: 'pi', status: 'installed', unit_active: null },
  overview: PI_OVERVIEW,
  slot: null,
  job: null,
  caps: null,
  urls: null,
}

function urls(over: Partial<ConfigUrls>): ConfigUrls {
  return {
    api: 'http://hal0.test',
    openwebui: '',
    openwebui_enabled: false,
    hermes: '',
    hermes_enabled: false,
    comfyui: '',
    ...over,
  }
}

describe('deriveCardModel: phase', () => {
  it('derives ready from agents list when overview is null', () => {
    const m = deriveCardModel(READY)
    expect(m.phase).toBe('ready')
    expect(m.statusCls).toBe('stale')
    expect(m.reasons).toEqual([])
    expect(m.version.label).toBe('—')
  })

  it('upgrades ready to serving when the slot generates', () => {
    const m = deriveCardModel({ ...READY, slot: { state: 'serving', metrics: { toks: 12 } } })
    expect(m.phase).toBe('serving')
    expect(m.statusCls).toBe('serving')
    expect(m.statusLabel).toBe('serving')
  })

  it('upgrades ready to serving on throughput alone', () => {
    expect(deriveCardModel({ ...READY, slot: { state: 'ready', metrics: { toks: 3 } } }).phase).toBe('serving')
    expect(deriveCardModel({ ...READY, slot: { state: 'ready', metrics: { toks: 0 } } }).phase).toBe('ready')
  })

  it('never upgrades degraded to serving', () => {
    const m = deriveCardModel({
      ...READY,
      overview: DEGRADED_OVERVIEW,
      slot: { state: 'serving', metrics: { toks: 12 } },
    })
    expect(m.phase).toBe('degraded')
  })

  it('takes phase, reasons and version from the overview', () => {
    const m = deriveCardModel({ ...READY, overview: DEGRADED_OVERVIEW })
    expect(m.phase).toBe('degraded')
    expect(m.reasonsTop.map((r) => r.reason)).toEqual(['gateway_down', 'provision_warn', 'approvals_drift'])
    expect(m.reasons).toHaveLength(4)
    expect(m.version.label).toBe('v0.22.2')
    expect(m.updateAvailable).toBe(true)
    expect(m.lifecycle.primary).toBe('upgrade')
    expect(m.statusCls).toBe('stale')
    expect(m.statusLabel).toBe('degraded')
    expect(m.degradedCount).toBe(4)
  })

  it('working wins while a job runs', () => {
    expect(
      deriveCardModel({ ...READY, job: { id: 'j', state: 'running', phase: 'converge:hermes' } }).phase,
    ).toBe('working')
    expect(deriveCardModel({ ...READY, job: { id: 'j', state: 'queued' } }).phase).toBe('working')
    const m = deriveCardModel({ ...READY, overview: DEGRADED_OVERVIEW, job: { id: 'j', state: 'running' } })
    expect(m.phase).toBe('working')
    expect(m.statusCls).toBe('working')
    expect(m.statusLabel).toBe('working')
    expect(m.job?.id).toBe('j')
  })

  it('working also follows the overview job', () => {
    const m = deriveCardModel({
      ...READY,
      overview: { ...READY_OVERVIEW, job: { id: 'o', kind: 'repair:hermes', state: 'running' } },
    })
    expect(m.phase).toBe('working')
  })

  it('a finished job does not force working', () => {
    const m = deriveCardModel({ ...READY, job: { id: 'j', state: 'failed', error: 'boom' } })
    expect(m.phase).toBe('ready')
    expect(m.job?.state).toBe('failed')
  })

  it('never renders null liveness as healthy', () => {
    const m = deriveCardModel({
      ...READY,
      record: { name: 'hermes', status: 'installed', unit_active: null },
      overview: null,
    })
    expect(m.phase).toBe('unknown')
    expect(m.statusCls).toBe('offline')
    expect(m.statusLabel).toBe('unknown')
  })

  it('maps a stopped unit to down and a broken bundle to broken', () => {
    const down = deriveCardModel({ ...READY, record: { name: 'hermes', status: 'installed', unit_active: false } })
    expect([down.phase, down.statusCls, down.statusLabel]).toEqual(['down', 'error', 'down'])
    const broken = deriveCardModel({ ...READY, record: { name: 'hermes', status: 'broken', unit_active: true } })
    expect([broken.phase, broken.statusCls, broken.statusLabel]).toEqual(['broken', 'error', 'broken'])
  })

  it('no record and no overview phase is not installed, pin from the overview', () => {
    const m = deriveCardModel({
      ...READY,
      record: null,
      overview: { name: 'hermes', kind: 'daemon', installed: false, pin: { ref: PIN_REF } },
    })
    expect(m.phase).toBe('not_installed')
    expect(m.statusCls).toBe('offline')
    expect(m.statusLabel).toBe('not installed')
    expect(m.version.pinLabel).toBe('fedcba9')
    expect(m.commands).toEqual({ install: 'sudo hal0 agent install hermes', upgrade: null, uninstall: null })
  })

  it('an agent outside the bundled set is a roadmap card', () => {
    const m = deriveCardModel({ ...READY, record: null, agent: 'qwen' })
    expect(m.phase).toBe('roadmap')
    expect(m.statusCls).toBe('soon')
    expect(m.statusLabel).toBe('on the roadmap')
  })

  it('gateway off on purpose is not degraded', () => {
    const m = deriveCardModel({
      ...READY,
      overview: {
        ...READY_OVERVIEW,
        units: { agent: 'active', gateway: 'inactive', gateway_enabled: false },
        degraded: [],
        phase: 'ready',
      },
    })
    expect(m.phase).toBe('ready')
    expect(m.reasons).toEqual([])
    expect(m.degradedCount).toBe(0)
    expect(m.gatewayOff).toBe(true)
  })

  it('an enabled or unknown gateway is not gatewayOff', () => {
    expect(deriveCardModel({ ...READY, overview: READY_OVERVIEW }).gatewayOff).toBe(false)
    const unknown = { ...READY_OVERVIEW, units: { agent: 'active', gateway: 'unknown', gateway_enabled: null } }
    expect(deriveCardModel({ ...READY, overview: unknown }).gatewayOff).toBe(false)
  })
})

describe('deriveCardModel: reasons', () => {
  it('passes a fix: none reason through untouched', () => {
    const m = deriveCardModel({ ...READY, overview: DEGRADED_OVERVIEW })
    expect(m.reasonsTop[1]).toEqual({ reason: 'provision_warn', detail: 'one phase warned', fix: 'none' })
  })

  it('carries provisionStale and generatedAt', () => {
    const stale = {
      ...READY_OVERVIEW,
      provision: { ...READY_OVERVIEW.provision, stale: true, reported_at: '2026-10-20T00:00:00Z' },
    }
    const m = deriveCardModel({ ...READY, overview: stale })
    expect(m.provisionStale).toBe(true)
    expect(m.generatedAt).toBe('2026-10-22T12:00:00Z')
    const fresh = deriveCardModel({ ...READY, overview: READY_OVERVIEW })
    expect(fresh.provisionStale).toBe(false)
    expect(deriveCardModel(READY).generatedAt).toBeNull()
  })
})

describe('deriveCardModel: version', () => {
  it('labels the live release and its pin', () => {
    const m = deriveCardModel({ ...READY, overview: READY_OVERVIEW })
    expect(m.version).toEqual({ label: 'v0.22.2', sub: 'py 3.14 · 0123456', pinLabel: 'fedcba9' })
    expect(m.updateAvailable).toBe(false)
    expect(m.lifecycle).toEqual({ primary: 'repair', previous: 'v0.22.1' })
  })

  it('falls back to the short ref when describe is absent', () => {
    const live = { id: 'r1', ref: REF, describe: null, python_minor: '3.14' }
    const m = deriveCardModel({
      ...READY,
      overview: { ...READY_OVERVIEW, version: { ...READY_OVERVIEW.version, live, previous: { id: 'r0', ref: PREV_REF } } },
    })
    expect(m.version.label).toBe('0123456')
    expect(m.lifecycle.previous).toBe('aaaaaaa')
  })

  it('shows dashes for missing facts', () => {
    const m = deriveCardModel({ ...READY, overview: { ...READY_OVERVIEW, version: undefined } })
    expect(m.version).toEqual({ label: '—', sub: '—', pinLabel: '—' })
    expect(m.lifecycle.previous).toBeNull()
  })
})

describe('deriveCardModel: chips and reach', () => {
  it('reads the identity chips', () => {
    const m = deriveCardModel({ ...READY, overview: READY_OVERVIEW })
    expect(m.chips).toEqual({ persona: 'ghost', approvals: 'smart', terminal: 'on' })
    expect(deriveCardModel(READY).chips).toEqual({ persona: null, approvals: null, terminal: null })
  })

  it('lights platform chips with the gateway and leaves api_server to the api chip', () => {
    const up = deriveCardModel({ ...READY, overview: READY_OVERVIEW })
    expect(up.reach.platforms).toEqual([{ id: 'telegram', lit: true }])
    expect(up.reach.api).toEqual({ lit: true, profiles: ['default', 'research'] })
    const down = deriveCardModel({ ...READY, overview: DEGRADED_OVERVIEW })
    expect(down.reach.platforms).toEqual([{ id: 'telegram', lit: false }])
  })

  it('an unhealthy or absent api is unlit', () => {
    const sick = { ...READY_OVERVIEW, api_server: { endpoint: 'x', healthy: false } }
    expect(deriveCardModel({ ...READY, overview: sick }).reach.api).toEqual({ lit: false, profiles: [] })
    expect(deriveCardModel(READY).reach.api).toEqual({ lit: false, profiles: [] })
  })

  it('desktop link: overview first, then the configured public url, else null', () => {
    const withLink = { ...READY_OVERVIEW, links: { desktop: 'https://hermes.example/desktop' } }
    const on = urls({ hermes: 'https://hermes.example', hermes_enabled: true })
    expect(deriveCardModel({ ...READY, overview: withLink, urls: on }).reach.desktop).toBe(
      'https://hermes.example/desktop',
    )
    expect(deriveCardModel({ ...READY, overview: READY_OVERVIEW, urls: on }).reach.desktop).toBe(
      'https://hermes.example',
    )
    const off = urls({ hermes: 'https://hermes.example', hermes_enabled: false })
    expect(deriveCardModel({ ...READY, overview: READY_OVERVIEW, urls: off }).reach.desktop).toBeNull()
    expect(deriveCardModel(READY).reach.desktop).toBeNull()
  })

  it('carries the last tool when the caller has one', () => {
    expect(deriveCardModel(READY).lastTool).toBeNull()
    const m = deriveCardModel({ ...READY, lastTool: { name: 'terminal', at: 1_700_000_000 } })
    expect(m.lastTool).toEqual({ name: 'terminal', at: 1_700_000_000 })
  })
})

describe('deriveCardModel: lore', () => {
  it('lore unlocks from live wiring', () => {
    const m = deriveCardModel({ ...READY, overview: READY_OVERVIEW })
    expect(m.lore.abilities.map((a) => [a.name, a.unlocked])).toEqual([
      ['Ghost Relay', true],
      ['Engram', true],
      ['Deep Run', true],
    ])
    expect(m.lore.abilities.every((a) => a.note === null)).toBe(true)
  })

  it('ghost relay ignores api_server', () => {
    const m = deriveCardModel({ ...READY, overview: { ...READY_OVERVIEW, platforms: ['api_server'] } })
    expect(m.lore.abilities[0].unlocked).toBe(false)
    expect(m.lore.abilities[0].note).toEqual(expect.any(String))
  })

  it('ghost relay needs the gateway active', () => {
    const m = deriveCardModel({ ...READY, overview: DEGRADED_OVERVIEW })
    expect(m.lore.abilities[0].unlocked).toBe(false)
  })

  it('engram needs memory available, deep run needs terminal on and smart approvals', () => {
    const noMemory = { ...READY_OVERVIEW, memory: { available: false } }
    expect(deriveCardModel({ ...READY, overview: noMemory }).lore.abilities[1].unlocked).toBe(false)
    const manual = {
      ...READY_OVERVIEW,
      approvals: { mode: 'manual', effective: 'manual', in_sync: true },
    }
    const m1 = deriveCardModel({ ...READY, overview: manual })
    expect(m1.lore.abilities[2].unlocked).toBe(false)
    expect(m1.lore.abilities[2].note).toEqual(expect.any(String))
    const termOff = { ...READY_OVERVIEW, provision: { ...READY_OVERVIEW.provision, terminal_tool: 'off' as const } }
    expect(deriveCardModel({ ...READY, overview: termOff }).lore.abilities[2].unlocked).toBe(false)
  })

  it('everything is locked and dim without an overview or caps', () => {
    const m = deriveCardModel(READY)
    expect(m.lore.abilities.every((a) => !a.unlocked && typeof a.note === 'string')).toBe(true)
    expect(m.lore.skills.map((s) => s.label)).toEqual([
      'voice · tts',
      'speech · stt',
      'image-gen',
      'memory',
      'vision',
      'embeddings',
    ])
    expect(m.lore.skills.every((s) => !s.lit && s.note === '· no slot')).toBe(true)
  })

  it('lights skills from provisioning and capabilities', () => {
    const caps = {
      selections: {
        img: { img: { enabled: true } },
        embed: { embed: { enabled: false }, rerank: { enabled: true } },
        vision: { vision: { enabled: false } },
      },
    }
    const m = deriveCardModel({ ...READY, overview: READY_OVERVIEW, caps })
    expect(m.lore.skills.map((s) => [s.label, s.lit])).toEqual([
      ['voice · tts', true],
      ['speech · stt', true],
      ['image-gen', true],
      ['memory', true],
      ['vision', false],
      ['embeddings', true],
    ])
    expect(m.lore.skills.find((s) => s.label === 'vision')?.note).toBe('· no slot')
    expect(m.lore.skills.find((s) => s.label === 'image-gen')?.note).toBeNull()
  })

  it('voice skills need voice_wire ok, memory needs a provider', () => {
    const warned = {
      ...READY_OVERVIEW,
      provision: {
        ...READY_OVERVIEW.provision,
        phases: [{ name: 'voice_wire', status: 'warn' }],
        memory_provider: null,
      },
    }
    const m = deriveCardModel({ ...READY, overview: warned })
    expect(m.lore.skills.filter((s) => s.lit)).toEqual([])
  })
})

describe('deriveCardModel: Pi', () => {
  it('pi phases and labels', () => {
    const m = deriveCardModel(PI)
    expect(m.phase).toBe('installed')
    expect(m.statusCls).toBe('stale')
    expect(m.statusLabel).toBe('installed')
    expect(m.updateAvailable).toBe(true)
    expect(m.lifecycle.primary).toBe('upgrade')
  })

  it('pi never reads ready or serving', () => {
    const live = { ...PI, record: { name: 'pi', status: 'installed', unit_active: true }, slot: { state: 'serving' } }
    expect(deriveCardModel(live).phase).toBe('installed')
    expect(deriveCardModel({ ...live, overview: { ...PI_OVERVIEW, phase: 'ready' } }).phase).toBe('installed')
    expect(deriveCardModel({ ...live, overview: null }).phase).toBe('installed')
  })

  it('pi broken comes from the overview or the record', () => {
    expect(deriveCardModel({ ...PI, overview: { ...PI_OVERVIEW, phase: 'broken' } }).phase).toBe('broken')
    expect(deriveCardModel({ ...PI, overview: null, record: { name: 'pi', status: 'broken' } }).phase).toBe('broken')
  })

  it('pi versions come from the probe and the pins', () => {
    const m = deriveCardModel(PI)
    expect(m.version).toEqual({ label: 'v0.84.4', sub: 'adapter 2.31.0', pinLabel: 'v1.2.0 · adapter 5.3.0' })
    expect(m.commands).toEqual({
      install: 'sudo hal0 agent install pi',
      upgrade: 'sudo hal0 agent upgrade pi',
      uninstall: 'sudo hal0 agent uninstall pi',
    })
  })

  it('pi commands default when the overview is missing, and prefer the overview when present', () => {
    expect(deriveCardModel({ ...PI, overview: null }).commands).toEqual({
      install: 'sudo hal0 agent install pi',
      upgrade: 'sudo hal0 agent upgrade pi',
      uninstall: 'sudo hal0 agent uninstall pi',
    })
    const custom = { ...PI_OVERVIEW, commands: { install: 'a', upgrade: 'b', uninstall: 'c' } }
    expect(deriveCardModel({ ...PI, overview: custom }).commands).toEqual({
      install: 'a',
      upgrade: 'b',
      uninstall: 'c',
    })
  })

  it('pi not installed shows the install command', () => {
    const m = deriveCardModel({
      ...PI,
      record: null,
      overview: { name: 'pi', kind: 'cli', installed: false, pin: { pi: '1.2.0', adapter: '5.3.0' } },
    })
    expect(m.phase).toBe('not_installed')
    expect(m.commands.install).toBe('sudo hal0 agent install pi')
    expect(m.version.pinLabel).toBe('v1.2.0 · adapter 5.3.0')
  })

  it('pi lore unlocks from the profile checks and memory wiring', () => {
    const m = deriveCardModel(PI)
    expect(m.lore.abilities.map((a) => [a.name, a.unlocked])).toEqual([
      ['Slot Sync', true],
      ['Dual Memory', true],
      ['Delegate', true],
    ])
    expect(m.lore.skills).toEqual([])
    const partial = {
      ...PI_OVERVIEW,
      profile: { checks: [{ id: 'provider_ext', ok: false }] },
      memory: { mcp_entry: true, hindsight_config: null, hindsight_api_reachable: false },
    }
    const p = deriveCardModel({ ...PI, overview: partial })
    expect(p.lore.abilities.map((a) => a.unlocked)).toEqual([false, false, true])
  })
})
