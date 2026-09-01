// profileApplyPreview (slot-modals.jsx) — the slot drawer's profile apply
// preview, the `slot-profile-preview` box (mockup panel 12).
//
// Every consequence of "profile wins" enumerated in one place, BEFORE Save:
// which runtime the profile pins, which lane the slot lands on, how many
// flags the profile's tune replaces, and that the slot restarts. The box is
// the operator's only chance to see the server-side reconcile (Task 5) before
// it happens, so the lines must be derived from the SAME hw-cascade the
// Hardware group drives (runnerOptions/applyRunnerChoice) rather than
// re-guessed — and a line with nothing true to say is OMITTED, never faked.
//
// slot-modals.jsx is a window-globals dash module (`const {...} = React` at
// module top, `Object.assign(window, {...})` at the bottom), so the globals
// must be installed before the dynamic import — same pattern as
// host-hw-flags.test.ts / runner-images-view.test.tsx.
import React from 'react'
import { describe, expect, it } from 'vitest'
import { runnerOptions } from '../hw-cascade.js'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { profileApplyPreview } = await import('../slot-modals.jsx')

// system-info `backends` rows (Task 2 shape) — a dual-lane default, a
// single-lane ROCm runtime and a single-lane Vulkan one, so both the
// "device follows the runner" and "runner has a lane choice" branches are
// reachable.
const backends = {
  rocmfpx: {
    title: 'Standard', blurb: 'Runs everything.', is_default: true,
    supported_backends: ['rocm', 'vulkan'], device_class: 'gpu',
    runtime_family: 'llama-server', state: 'installed',
  },
  promptforge: {
    title: 'PromptForge', blurb: 'Accelerated PF.', is_default: false,
    supported_backends: ['rocm'], device_class: 'gpu',
    runtime_family: 'llama-server', state: 'installed',
  },
  strix: {
    title: 'Strix', blurb: 'Qwen4 + MTP.', is_default: false,
    supported_backends: ['vulkan'], device_class: 'gpu',
    runtime_family: 'llama-server', state: 'installed',
  },
}

const optionsFor = (device: string) =>
  runnerOptions({ backends, device, slotType: 'llm', hw: {} }).options

type Preview = {
  runtime: { unchanged: boolean; title: string | null; lanes: string[] }
  lane: null | { unchanged: boolean; from: string; to: string }
  flags: number
  restart: boolean
}

type PreviewArgs = {
  profile: { name: string; runner?: string | null; flags?: string } | null | undefined
  baselineRunner: string
  currentDevice: string
  modelFlags?: string
  currentProfileFlags?: string
}

const preview = (args: PreviewArgs): Preview | null =>
  profileApplyPreview({
    profile: args.profile,
    backends,
    options: optionsFor(args.currentDevice),
    baselineRunner: args.baselineRunner,
    currentDevice: args.currentDevice,
    modelFlags: args.modelFlags || '',
    currentProfileFlags: args.currentProfileFlags || '',
  }) as Preview | null

describe('profileApplyPreview — runtime flip', () => {
  it('a runner-carrying profile names the runtime it pins and the lanes it serves', () => {
    const p = preview({
      profile: { name: 'pf-tune', runner: 'promptforge', flags: '-fa on -b 512' },
      baselineRunner: 'strix',
      currentDevice: 'gpu-vulkan',
      modelFlags: '',
      currentProfileFlags: '',
    })
    expect(p).not.toBeNull()
    expect(p!.runtime).toEqual({ unchanged: false, title: 'PromptForge', lanes: ['rocm'] })
    expect(p!.restart).toBe(true)
  })

  it('a single-lane runner moves the slot off its current lane (applyRunnerChoice truth)', () => {
    const p = preview({
      profile: { name: 'pf-tune', runner: 'promptforge', flags: '' },
      baselineRunner: 'strix',
      currentDevice: 'gpu-vulkan',
    })
    expect(p!.lane).toEqual({ unchanged: false, from: 'Vulkan', to: 'ROCm' })
  })

  it('an out-of-catalog runner key still names itself and never invents a lane', () => {
    const p = preview({
      profile: { name: 'ghost', runner: 'strix-next', flags: '' },
      baselineRunner: '',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.runtime).toEqual({ unchanged: false, title: 'strix-next', lanes: [] })
    // No lane claim can be derived, and the device cannot follow an unknown
    // key — the lane line says "unchanged" rather than inventing a move.
    expect(p!.lane).toEqual({ unchanged: true, from: 'ROCm', to: 'ROCm' })
  })
})

describe('profileApplyPreview — same runtime', () => {
  it('a profile pinning the runtime the slot already runs says so instead of faking a flip', () => {
    const p = preview({
      profile: { name: 'pf-tune', runner: 'promptforge', flags: '' },
      baselineRunner: 'promptforge',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.runtime).toEqual({ unchanged: true, title: 'PromptForge', lanes: ['rocm'] })
  })

  it('omits the lane line entirely when a single-lane runtime is already on its lane', () => {
    const p = preview({
      profile: { name: 'pf-tune', runner: 'promptforge', flags: '' },
      baselineRunner: 'promptforge',
      currentDevice: 'gpu-rocm',
    })
    expect(p!.lane).toBeNull()
  })

  it('a multi-lane runtime keeps its lane line — the lane is a live choice that is not moving', () => {
    const p = preview({
      profile: { name: 'std-tune', runner: 'rocmfpx', flags: '' },
      baselineRunner: 'strix',
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
      baselineRunner: 'promptforge',
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
      modelFlags: '',
      currentProfileFlags: '',
    })
    expect(p!.flags).toBe(3)
    expect(p!.restart).toBe(true)
  })
})

describe('profileApplyPreview — flag delta', () => {
  it('counts a flag the model tune already carries at the same value as NOT replaced', () => {
    const p = preview({
      profile: { name: 'chat', runner: '', flags: '-fa on -b 512' },
      baselineRunner: '',
      currentDevice: 'cpu',
      modelFlags: '-fa on',
      currentProfileFlags: '',
    })
    expect(p!.flags).toBe(1)
  })

  it('counts a flag the model tune carries at a DIFFERENT value as replaced', () => {
    const p = preview({
      profile: { name: 'chat', runner: '', flags: '-fa on -b 512' },
      baselineRunner: '',
      currentDevice: 'cpu',
      modelFlags: '-fa off -b 512',
      currentProfileFlags: '',
    })
    expect(p!.flags).toBe(1)
  })

  it('diffs against the effective current tune — the outgoing profile overlay counts too', () => {
    const p = preview({
      profile: { name: 'next', runner: '', flags: '-b 2048' },
      baselineRunner: '',
      currentDevice: 'cpu',
      modelFlags: '',
      currentProfileFlags: '-b 512',
    })
    expect(p!.flags).toBe(1)
  })

  it('a tune identical to what the slot already launches replaces nothing (line omitted)', () => {
    const p = preview({
      profile: { name: 'same', runner: '', flags: '-fa on -b 512' },
      baselineRunner: '',
      currentDevice: 'cpu',
      modelFlags: '-fa on -b 512',
      currentProfileFlags: '',
    })
    expect(p!.flags).toBe(0)
  })

  it('quoted values survive (shlex-lite tokenizer, not a whitespace split)', () => {
    const p = preview({
      profile: { name: 'q', runner: '', flags: `--chat-template-kwargs '{"enable_thinking":false}'` },
      baselineRunner: '',
      currentDevice: 'cpu',
      modelFlags: `--chat-template-kwargs '{"enable_thinking":false}'`,
      currentProfileFlags: '',
    })
    expect(p!.flags).toBe(0)
  })
})

describe('profileApplyPreview — nothing selected', () => {
  it('returns null with no profile (the box never renders on an empty pick)', () => {
    expect(preview({ profile: null, baselineRunner: '', currentDevice: 'gpu-rocm' })).toBeNull()
    expect(preview({ profile: undefined, baselineRunner: '', currentDevice: 'gpu-rocm' })).toBeNull()
  })
})
