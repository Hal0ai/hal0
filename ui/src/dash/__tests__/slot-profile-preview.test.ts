// profileApplyPreview (profile-apply-preview.js) — the profile apply preview
// BOTH profile surfaces speak: the slot drawer's `slot-profile-preview` box
// (mockup panel 12) and the slot card's `infer-profile-preview` apply confirm
// (panel 02).
//
// Every consequence of switching a slot's profile enumerated in one place,
// BEFORE the write: which runtime the profile pins, which lane the slot lands
// on, how many flags and which typed tune fields (context / thinking / vision
// / mtp) differ from the outgoing profile, and that the slot restarts. The
// profile IS the tune (ADR-0029) — there is no model flag layer under it. It is
// the operator's only chance to see the server-side reconcile (Task 5) before
// it happens, so the lines must be derived from the SAME hw-cascade the
// Hardware group drives (runnerOptions/applyRunnerChoice) rather than
// re-guessed — and a line with nothing true to say is OMITTED, never faked.
//
// The function is pure and lives in its own module, so this file needs no
// window/React globals shim.
import { describe, expect, it } from 'vitest'
import { runnerOptions } from '../hw-cascade.js'
import { profileApplyPreview, profileFitsSlot } from '../profile-apply-preview.js'

// system-info `backends` rows (Task 2 shape) — a dual-lane default, a
// single-lane ROCm runtime and a single-lane Vulkan one, so both the
// "device follows the runner" and "runner has a lane choice" branches are
// reachable.
const backends = {
  standard: {
    title: 'Standard', blurb: 'Runs everything.', tier: 'default',
    supported_backends: ['rocm', 'vulkan'], device_class: 'gpu',
    runtime_family: 'llama-server', state: 'installed',
  },
  qwen3tts: {
    title: 'Qwen3 TTS', blurb: 'Higher-quality TTS.', tier: 'optional',
    supported_backends: ['rocm'], device_class: 'gpu',
    runtime_family: 'llama-server', state: 'installed',
  },
  cuda: {
    title: 'NVIDIA CUDA', blurb: 'llama.cpp for NVIDIA.', tier: 'optional',
    supported_backends: ['cuda'], device_class: 'gpu',
    runtime_family: 'llama-server', state: 'installed',
  },
}

const optionsFor = (device: string) =>
  runnerOptions({ backends, device, slotType: 'llm', hw: {} }).options

type Preview = {
  runtime: { unchanged: boolean; title: string | null; lanes: string[] }
  lane: null | { unchanged: boolean; from: string; to: string }
  flags: number
  tune: { label: string; from: string; to: string }[]
  restart: boolean
}

type ProfileRow = {
  name: string
  runner?: string | null
  flags?: string
  ctx_size?: number | null
  enable_thinking?: boolean | null
  vision?: string
  speculation?: string
}

type PreviewArgs = {
  profile: ProfileRow | null | undefined
  baselineRunner: string
  currentDevice: string
  currentProfile?: ProfileRow | null
}

const preview = (args: PreviewArgs): Preview | null =>
  profileApplyPreview({
    profile: args.profile,
    backends,
    options: optionsFor(args.currentDevice),
    baselineRunner: args.baselineRunner,
    currentDevice: args.currentDevice,
    currentProfile: args.currentProfile ?? null,
  }) as Preview | null

describe('profileApplyPreview — runtime flip', () => {
  it('a runner-carrying profile names the runtime it pins and the lanes it serves', () => {
    const p = preview({
      profile: { name: 'q3-tune', runner: 'qwen3tts', flags: '-fa on -b 512' },
      baselineRunner: 'standard',
      currentDevice: 'gpu-vulkan',
    })
    expect(p).not.toBeNull()
    expect(p!.runtime).toEqual({ unchanged: false, title: 'Qwen3 TTS', lanes: ['rocm'] })
    expect(p!.restart).toBe(true)
  })

  it('a single-lane runner moves the slot off its current lane (applyRunnerChoice truth)', () => {
    const p = preview({
      profile: { name: 'q3-tune', runner: 'qwen3tts', flags: '' },
      baselineRunner: 'standard',
      currentDevice: 'gpu-vulkan',
    })
    expect(p!.lane).toEqual({ unchanged: false, from: 'Vulkan', to: 'ROCm' })
  })

  it('an out-of-catalog runner key still names itself and never invents a lane', () => {
    const p = preview({
      profile: { name: 'ghost', runner: 'ghost-next', flags: '' },
      baselineRunner: '',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.runtime).toEqual({ unchanged: false, title: 'ghost-next', lanes: [] })
    // No lane claim can be derived, and the device cannot follow an unknown
    // key — the lane line says "unchanged" rather than inventing a move.
    expect(p!.lane).toEqual({ unchanged: true, from: 'ROCm', to: 'ROCm' })
  })
})

describe('profileApplyPreview — same runtime', () => {
  it('a profile pinning the runtime the slot already runs says so instead of faking a flip', () => {
    const p = preview({
      profile: { name: 'q3-tune', runner: 'qwen3tts', flags: '' },
      baselineRunner: 'qwen3tts',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.runtime).toEqual({ unchanged: true, title: 'Qwen3 TTS', lanes: ['rocm'] })
  })

  it('omits the lane line entirely when a single-lane runtime is already on its lane', () => {
    const p = preview({
      profile: { name: 'q3-tune', runner: 'qwen3tts', flags: '' },
      baselineRunner: 'qwen3tts',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.lane).toBeNull()
  })

  it('a multi-lane runtime keeps its lane line — the lane is a live choice that is not moving', () => {
    const p = preview({
      profile: { name: 'std-tune', runner: 'standard', flags: '' },
      baselineRunner: 'standard',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.runtime.lanes).toEqual(['rocm', 'vulkan'])
    expect(p!.lane).toEqual({ unchanged: true, from: 'ROCm', to: 'ROCm' })
  })
})

describe('profileApplyPreview — Auto profile (no runtime opinion)', () => {
  it('says "unchanged" for runtime and lane rather than dropping the lines', () => {
    const p = preview({
      profile: { name: 'chat', runner: null, flags: '-fa on' },
      baselineRunner: 'qwen3tts',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.runtime).toEqual({ unchanged: true, title: null, lanes: [] })
    expect(p!.lane).toEqual({ unchanged: true, from: 'ROCm', to: 'ROCm' })
  })

  it('omits the lane line on a slot with no lane to name (empty device enum)', () => {
    const p = preview({
      profile: { name: 'chat', runner: '', flags: '-fa on' },
      baselineRunner: '',
      currentDevice: '',
    })
    expect(p!.lane).toBeNull()
  })

  it('still counts flags and still restarts — applying a tune is a restart', () => {
    const p = preview({
      profile: { name: 'chat', runner: '', flags: '-fa on -b 512 --mlock' },
      baselineRunner: '',
      currentDevice: 'cpu',
    })
    expect(p!.flags).toBe(3)
    expect(p!.restart).toBe(true)
  })
})

describe('profileApplyPreview — flag delta (profile vs outgoing profile, nothing else)', () => {
  it('counts only the pairs the outgoing profile does not already carry at that value', () => {
    const p = preview({
      profile: { name: 'chat', runner: '', flags: '-fa on -b 512' },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', flags: '-fa on' },
    })
    expect(p!.flags).toBe(1)
  })

  it('counts a flag carried at a DIFFERENT value as changed', () => {
    const p = preview({
      profile: { name: 'chat', runner: '', flags: '-fa on -b 512' },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', flags: '-fa off -b 512' },
    })
    expect(p!.flags).toBe(1)
  })

  it('a flag the outgoing profile had and the incoming lacks is dropped, and counts', () => {
    const p = preview({
      profile: { name: 'next', runner: '', flags: '-b 2048' },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', flags: '-b 2048 --mlock' },
    })
    expect(p!.flags).toBe(1)
  })

  it('a tune identical to the outgoing profile changes nothing (line omitted)', () => {
    const p = preview({
      profile: { name: 'same', runner: '', flags: '-fa on -b 512' },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', flags: '-b 512 -fa on' },
    })
    expect(p!.flags).toBe(0)
    expect(p!.tune).toEqual([])
  })

  it('quoted values survive (shlex-lite tokenizer, not a whitespace split)', () => {
    const flags = `--chat-template-kwargs '{"enable_thinking":false}'`
    const p = preview({
      profile: { name: 'q', runner: '', flags },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', flags },
    })
    expect(p!.flags).toBe(0)
  })
})

describe('profileApplyPreview — typed tune fields', () => {
  const base = { runner: '', flags: '' }
  it('lists context, thinking, vision and speculation that differ, in that order', () => {
    const p = preview({
      profile: { name: 'next', ...base, ctx_size: 65536, enable_thinking: false, vision: 'off', speculation: 'off' },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', ...base, ctx_size: 32768, enable_thinking: null, vision: 'auto', speculation: 'auto' },
    })
    expect(p!.tune).toEqual([
      { label: 'context', from: '32K', to: '64K' },
      { label: 'thinking', from: 'auto', to: 'off' },
      { label: 'vision', from: 'auto', to: 'off' },
      { label: 'speculation', from: 'auto', to: 'off' },
    ])
  })

  it('a profile with no ctx_size defers to the model native window', () => {
    const p = preview({
      profile: { name: 'next', ...base, ctx_size: null },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', ...base, ctx_size: 8192 },
    })
    expect(p!.tune).toEqual([{ label: 'context', from: '8K', to: 'model native' }])
  })

  it('omits fields that do not change', () => {
    const p = preview({
      profile: { name: 'next', ...base, ctx_size: 8192, enable_thinking: true },
      baselineRunner: '',
      currentDevice: 'cpu',
      currentProfile: { name: 'old', ...base, ctx_size: 8192, enable_thinking: null },
    })
    expect(p!.tune).toEqual([{ label: 'thinking', from: 'auto', to: 'on' }])
  })
})

describe('profileApplyPreview — nothing selected', () => {
  it('returns null with no profile (the box never renders on an empty pick)', () => {
    expect(preview({ profile: null, baselineRunner: '', currentDevice: 'gpu-rocm' })).toBeNull()
    expect(preview({ profile: undefined, baselineRunner: '', currentDevice: 'gpu-rocm' })).toBeNull()
  })
})

describe('profileFitsSlot — the client twin of resolved_profile_fits_slot', () => {
  const cat = {
    standard: { device_class: 'gpu' },
    kokoro: { device_class: 'cpu' },
    flm: { device_class: 'npu' },
  }
  it('requires the slot type to be one the profile supports', () => {
    const p = { name: 'chat', supported_slot_types: ['llm'] }
    expect(profileFitsSlot(p, { type: 'llm', device: 'gpu-rocm' }, cat)).toBe(true)
    expect(profileFitsSlot(p, { type: 'tts', device: 'cpu' }, cat)).toBe(false)
  })
  it('a profile with no supported_slot_types, or a slot with no type, is not type-fenced', () => {
    expect(profileFitsSlot({ name: 'x' }, { type: 'llm' }, cat)).toBe(true)
    expect(profileFitsSlot({ name: 'x', supported_slot_types: ['llm'] }, {}, cat)).toBe(true)
  })
  it('a pinned runner must match the slot device class (gpu-* is gpu)', () => {
    const p = { name: 'p', supported_slot_types: ['llm'], runner: 'standard' }
    expect(profileFitsSlot(p, { type: 'llm', device: 'gpu-vulkan' }, cat)).toBe(true)
    expect(profileFitsSlot(p, { type: 'llm', device: 'cpu' }, cat)).toBe(false)
    expect(profileFitsSlot({ ...p, runner: 'flm' }, { type: 'llm', device: 'npu' }, cat)).toBe(true)
    expect(profileFitsSlot({ ...p, runner: 'flm' }, { type: 'llm', device: 'gpu-rocm' }, cat)).toBe(false)
  })
  it('a profile pinning no runner fits any device', () => {
    const p = { name: 'p', supported_slot_types: ['llm'] }
    for (const device of ['gpu-rocm', 'cpu', 'npu']) {
      expect(profileFitsSlot(p, { type: 'llm', device }, cat)).toBe(true)
    }
  })
  it('an unloaded catalogue vetoes nothing; a loaded one never fits an unknown runner', () => {
    const p = { name: 'p', supported_slot_types: ['llm'], runner: 'ghost' }
    expect(profileFitsSlot(p, { type: 'llm', device: 'gpu-rocm' }, {})).toBe(true)
    expect(profileFitsSlot(p, { type: 'llm', device: 'gpu-rocm' }, cat)).toBe(false)
  })
  it('no profile never fits', () => {
    expect(profileFitsSlot(null, { type: 'llm' }, cat)).toBe(false)
  })
})
