// The Generate tab's pure parts: the form's starting values, the request it becomes, the
// number rules and the phase strip. Rendering is image-generate-panel.test.tsx's.
import { describe, expect, it, vi } from 'vitest'
import type { ArbiterState, ComfyuiProgress } from '@/api/hooks/useComfyui'
import {
  clampCfg,
  clampN,
  clampSeed,
  clampSteps,
  initialForm,
  isReusableSeed,
  phaseStrip,
  rollSeed,
  snapDim,
  stripArbiter,
  toRequest,
} from '../image/generate-pure'

const lightning = {
  id: 'sdxl-lightning',
  sizes: ['1024x1024', '1344x768'],
  defaults: { steps: 8, cfg: 1, sampler_name: 'euler' },
}

describe('initialForm', () => {
  it('initialForm prefers slot defaults then model defaults', () => {
    // The slot's own defaults win: the server applies them the same way when a request has none.
    expect(initialForm(lightning, { default_size: '768x768', default_steps: 12 })).toMatchObject({
      model: 'sdxl-lightning',
      size: '768x768',
      steps: 12,
      // There is no default cfg on the slot: it comes from the model.
      cfg: 1,
    })
    // An empty size and a zero step count mean "the model's own".
    expect(initialForm(lightning, { default_size: '', default_steps: 0 })).toMatchObject({
      size: '1024x1024',
      steps: 8,
      cfg: 1,
    })
    expect(initialForm(lightning, null)).toMatchObject({ size: '1024x1024', steps: 8, cfg: 1 })
    expect(initialForm(lightning, undefined)).toMatchObject({ size: '1024x1024', steps: 8 })
  })

  it('ignores a slot default it cannot read', () => {
    expect(initialForm(lightning, { default_size: 'huge', default_steps: -3 })).toMatchObject({
      size: '1024x1024',
      steps: 8,
    })
    expect(initialForm(lightning, { default_size: 640, default_steps: '30' })).toMatchObject({
      size: '1024x1024',
      steps: 8,
    })
    // A slot size is snapped the way the server snaps it (down to a multiple of 8).
    expect(initialForm(lightning, { default_size: '1030x770' }).size).toBe('1024x768')
  })

  it('leaves steps and cfg empty for a model that names none (a user checkpoint)', () => {
    const form = initialForm({ id: 'ckpt/my/model', sizes: ['1024x1024'], defaults: {} }, null)
    expect(form).toMatchObject({ model: 'ckpt/my/model', size: '1024x1024', steps: null, cfg: null })
    // No size at all: the request leaves it to the server.
    expect(initialForm({ id: 'ckpt/x' }, null).size).toBe('')
  })

  it('starts with an empty prompt, a random seed and one image', () => {
    expect(initialForm(lightning, null)).toMatchObject({
      prompt: '',
      negative_prompt: '',
      seed: null,
      n: 1,
    })
  })
})

describe('toRequest', () => {
  const form = () => ({ ...initialForm(lightning, null), prompt: '  a cat in a hat  ' })

  it('toRequest omits null seed', () => {
    const req = toRequest(form())
    expect('seed' in req).toBe(false)
    expect(req).toEqual({
      model: 'sdxl-lightning',
      prompt: 'a cat in a hat',
      n: 1,
      size: '1024x1024',
      steps: 8,
      cfg: 1,
    })
  })

  it('sends a locked seed, 0 included', () => {
    expect(toRequest({ ...form(), seed: 42 }).seed).toBe(42)
    expect(toRequest({ ...form(), seed: 0 }).seed).toBe(0)
  })

  it('leaves out what is empty, so the server uses the template own', () => {
    const req = toRequest({
      ...form(),
      steps: null,
      cfg: null,
      negative_prompt: '   ',
      size: '',
    })
    expect(Object.keys(req).sort()).toEqual(['model', 'n', 'prompt'])
  })

  it('sends a negative prompt trimmed', () => {
    expect(toRequest({ ...form(), negative_prompt: ' blurry ' }).negative_prompt).toBe('blurry')
  })

  it('clamps what the form can hold out of range', () => {
    const req = toRequest({ ...form(), n: 9, steps: 500, cfg: 99 })
    expect(req).toMatchObject({ n: 4, steps: 100, cfg: 20 })
    expect(toRequest({ ...form(), n: 0, steps: 0, cfg: -2 })).toMatchObject({ n: 1, steps: 1, cfg: 0 })
  })
})

describe('number rules', () => {
  it('clampN keeps a count of images to 1-4', () => {
    expect(clampN(0)).toBe(1)
    expect(clampN(1)).toBe(1)
    expect(clampN(3)).toBe(3)
    expect(clampN(4)).toBe(4)
    expect(clampN(5)).toBe(4)
    expect(clampN(2.4)).toBe(2)
    expect(clampN(Number.NaN)).toBe(1)
  })

  it('clampSteps keeps whole steps to 1-100', () => {
    expect(clampSteps(0)).toBe(1)
    expect(clampSteps(30.6)).toBe(31)
    expect(clampSteps(101)).toBe(100)
    expect(clampSteps(Number.NaN)).toBeNull()
  })

  it('clampCfg keeps cfg to 0-20 in steps of 0.5', () => {
    expect(clampCfg(-1)).toBe(0)
    expect(clampCfg(7.3)).toBe(7.5)
    expect(clampCfg(7.2)).toBe(7)
    expect(clampCfg(40)).toBe(20)
    expect(clampCfg(Number.NaN)).toBeNull()
  })

  it('snapDim floors a side to a multiple of 8 within 64-2048, as the server does', () => {
    expect(snapDim(1030)).toBe(1024)
    expect(snapDim(1024)).toBe(1024)
    expect(snapDim(30)).toBe(64)
    expect(snapDim(5000)).toBe(2048)
    expect(snapDim(67)).toBe(64)
    expect(snapDim(Number.NaN)).toBe(64)
  })

  it('clampSeed keeps a typed seed to a whole number a browser holds exactly', () => {
    expect(clampSeed(42)).toBe(42)
    expect(clampSeed(0)).toBe(0)
    expect(clampSeed(-5)).toBe(0)
    expect(clampSeed(7.9)).toBe(7)
    expect(clampSeed(1e20)).toBe(Number.MAX_SAFE_INTEGER)
    expect(clampSeed(Number.NaN)).toBeNull()
  })

  it('rollSeed draws a whole number a browser holds exactly', () => {
    const random = vi.spyOn(Math, 'random')
    try {
      random.mockReturnValueOnce(0).mockReturnValueOnce(0.5).mockReturnValueOnce(0.999999999)
      const seeds = [rollSeed(), rollSeed(), rollSeed()]
      expect(seeds[0]).toBe(0)
      expect(seeds[1]).toBe(2 ** 31)
      expect(seeds.every(isReusableSeed)).toBe(true)
    } finally {
      random.mockRestore()
    }
  })

  it('only a seed a browser holds exactly can be sent back', () => {
    expect(isReusableSeed(0)).toBe(true)
    expect(isReusableSeed(123456789)).toBe(true)
    expect(isReusableSeed(Number.MAX_SAFE_INTEGER)).toBe(true)
    // The server draws seeds up to 2**63; JSON.parse rounds those to the nearest double.
    expect(isReusableSeed(5871234567890123000)).toBe(false)
    expect(isReusableSeed(-1)).toBe(false)
    expect(isReusableSeed(1.5)).toBe(false)
    expect(isReusableSeed(null)).toBe(false)
    expect(isReusableSeed(undefined)).toBe(false)
  })
})

describe('phaseStrip', () => {
  const arbiter = (over: Partial<ArbiterState> = {}): ArbiterState => ({
    mode: 'llm',
    pinned: false,
    saved_llm_slots: [],
    switching: false,
    image_jobs: 0,
    phase: null,
    restore_at: null,
    handoff_until: null,
    drain_in_flight: 0,
    unload_set: ['brain', 'coder', 'tts'],
    llm_waiters: { count: 0, oldest_s: null },
    ...over,
  })
  const progress = (over: Partial<ComfyuiProgress> = {}): ComfyuiProgress => ({
    model: 'sdxl-lightning',
    step: null,
    total: null,
    node: null,
    elapsed_s: 1,
    prompt_id: 'p1',
    ...over,
  })
  const states = (steps: { state: string }[]) => steps.map((s) => s.state).join(' ')
  const labels = (steps: { label: string }[]) => steps.map((s) => s.label)

  it('phaseStrip marks done, now and todo from arbiter phase and progress', () => {
    const a = (phase: string | null, over: Partial<ArbiterState> = {}) => arbiter({ phase, ...over })

    expect(labels(phaseStrip(a('draining'), null, 'running'))).toEqual([
      'Chat reply finishing',
      'Unload 3 GPU slots',
      'Engine ready',
      'Load model',
      'Render',
      'Save',
    ])
    expect(states(phaseStrip(a('draining'), null, 'running'))).toBe('now todo todo todo todo todo')
    expect(states(phaseStrip(a('unloading'), null, 'running'))).toBe('done now todo todo todo todo')
    expect(states(phaseStrip(a('starting_engine'), null, 'running'))).toBe('done done now todo todo todo')

    // Image mode, the render queued but no step yet: the model is loading.
    const img = { mode: 'img' as const }
    expect(states(phaseStrip(a('rendering', img), null, 'running'))).toBe('done done done now todo todo')
    expect(states(phaseStrip(a('rendering', img), progress(), 'running'))).toBe(
      'done done done now todo todo',
    )
    expect(states(phaseStrip(a('handoff', img), null, 'running'))).toBe('done done done now todo todo')

    // The first step has landed: rendering, with its count.
    const stepping = phaseStrip(a('rendering', img), progress({ step: 7, total: 20 }), 'running')
    expect(states(stepping)).toBe('done done done done now todo')
    expect(labels(stepping)[4]).toBe('Render 7/20')

    // The render is over and hal0 is writing the files: grace on the arbiter, the run still going.
    expect(states(phaseStrip(a('grace', img), progress({ step: 20, total: 20 }), 'running'))).toBe(
      'done done done done done now',
    )
  })

  it('reads the progress ahead of a phase that has not caught up', () => {
    // /status and /arbiter are two polls: a step can arrive while the arbiter still says handoff.
    expect(states(phaseStrip(arbiter({ phase: 'handoff', mode: 'img' }), progress({ step: 1, total: 8 }), 'running'))).toBe(
      'done done done done now todo',
    )
  })

  it('starts at the first step before the arbiter has said anything', () => {
    expect(states(phaseStrip(arbiter(), null, 'running'))).toBe('now todo todo todo todo todo')
    expect(states(phaseStrip(null, null, 'running'))).toBe('now todo todo todo todo todo')
    // Image mode already on and idle: there is nothing to switch.
    expect(states(phaseStrip(arbiter({ mode: 'img', pinned: true }), null, 'running'))).toBe(
      'done done done now todo todo',
    )
  })

  it('names what it unloads, and says so when it cannot', () => {
    const label = (set: string[] | null) =>
      phaseStrip(arbiter({ unload_set: set, phase: 'unloading' }), null, 'running')[1].label
    expect(label(['brain'])).toBe('Unload 1 GPU slot')
    expect(label(['brain', 'coder'])).toBe('Unload 2 GPU slots')
    expect(label([])).toBe('Unload GPU slots')
    expect(label(null)).toBe('Unload GPU slots')
  })

  it('is all to do when nothing is running', () => {
    expect(states(phaseStrip(arbiter({ phase: 'rendering', mode: 'img' }), progress({ step: 3, total: 8 }), 'idle'))).toBe(
      'todo todo todo todo todo todo',
    )
  })
})

describe('stripArbiter', () => {
  const grace = {
    mode: 'img',
    pinned: false,
    saved_llm_slots: [],
    switching: false,
    image_jobs: 0,
    phase: 'grace',
    restore_at: 1,
    handoff_until: null,
    drain_in_flight: 0,
    unload_set: ['brain'],
    llm_waiters: { count: 0, oldest_s: null },
  } satisfies ArbiterState

  it('does not take the last run grace for this run own, for the first few seconds', () => {
    const early = stripArbiter(grace, { graceAtStart: true, elapsedMs: 800 })
    expect(early?.phase).toBeNull()
    // Image mode is still on, so the model is what loads first.
    expect(phaseStrip(early, null, 'running').map((s) => s.state).join(' ')).toBe(
      'done done done now todo todo',
    )
  })

  it('believes a grace that is the run own: it began after the run did, or lasted past the lag', () => {
    expect(stripArbiter(grace, { graceAtStart: false, elapsedMs: 800 })).toBe(grace)
    expect(stripArbiter(grace, { graceAtStart: true, elapsedMs: 3500 })).toBe(grace)
    const rendering = { ...grace, phase: 'rendering' }
    expect(stripArbiter(rendering, { graceAtStart: true, elapsedMs: 100 })).toBe(rendering)
    expect(stripArbiter(null, { graceAtStart: true, elapsedMs: 100 })).toBeNull()
  })
})
