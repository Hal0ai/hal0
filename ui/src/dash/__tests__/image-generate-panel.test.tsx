// @vitest-environment happy-dom
//
// The Image Gen pane's Generate tab: the form, the fit panel, the cost line, a run (its phase
// strip and the two cancels), the errors it shows and the results it keeps. The hooks are mocked
// at the module boundary, as image-models-panel.test.tsx does, so what is pinned is what the
// panel shows and what it calls, not the polling (imageHooks.test.tsx has that).
//
// The panel renders inside the pane's `.comfy-page` wrapper (Task 6 provides it), so the tests
// wrap it the same way.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>
type GenVars = Json & { signal?: AbortSignal }

// The shared query client, for the mocks below (they run inside hoisted factories).
const { held } = vi.hoisted(() => ({ held: { qc: null as import('@tanstack/react-query').QueryClient | null } }))
const cache = () => held.qc!

const { box, calls } = vi.hoisted(() => ({
  box: {
    models: undefined as Json | undefined,
    modelsError: undefined as Error | undefined,
    files: undefined as Json | undefined,
    caps: undefined as Json | undefined,
    slots: [] as Json[],
    slotCfg: undefined as Json | undefined,
    arbiter: undefined as Json | undefined,
    status: undefined as Json | undefined,
    memory: undefined as Json | undefined,
    // The /fit answer per size; a size not listed is judged "fits" at the size asked.
    fitBySize: {} as Record<string, Json | null>,
    // The size the server says it judged, when it is not the one asked (it clamps and floors).
    judgedAs: {} as Record<string, string>,
    // Where the /fit query for the current model and size is: 'ready' (its verdict is in),
    // 'first' (asked, nothing to show yet) or 'stale' (the last model/size's verdict is on screen
    // as a placeholder, `staleFit`). The real hook is disabled for a `ckpt/` id and answers none
    // of these; the mock answers them for a checkpoint too, to pin that the panel never waits.
    fitState: 'ready' as 'ready' | 'first' | 'stale',
    staleFit: null as Json | null,
    // What generateImage does with a request. Set per test.
    generate: undefined as ((vars: GenVars) => Promise<unknown>) | undefined,
    cancelFail: undefined as Error | undefined,
  },
  calls: [] as [string, unknown][],
}))

vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useComfyuiModels: () => ({
    data: box.models,
    isPending: !box.models && !box.modelsError,
    isError: !!box.modelsError,
    error: box.modelsError ?? null,
  }),
  useComfyuiFiles: () => ({ data: box.files }),
  // The store's Cancel reads the phase and progress off the query cache, so the mocked hooks
  // seed it with what they answer, as the real ones would.
  useArbiter: () => {
    if (box.arbiter) cache().setQueryData(['comfyui', 'arbiter'], box.arbiter)
    return { data: box.arbiter }
  },
  useComfyui: (opts?: unknown) => {
    calls.push(['status-hook', opts])
    if (box.status) cache().setQueryData(['comfyui', 'status'], box.status)
    return { data: box.status }
  },
  // The real hook is disabled for a `ckpt/` id and answers nothing.
  useModelFit: (id: string, size: string) => {
    calls.push(['fit', [id, size]])
    if (box.fitState === 'first') return { data: undefined, isLoading: true, isPlaceholderData: false }
    if (box.fitState === 'stale') {
      return { data: { fit: box.staleFit, size: '1024x1024' }, isLoading: false, isPlaceholderData: true }
    }
    if (!id || id.startsWith('ckpt/')) return { data: undefined, isLoading: false, isPlaceholderData: false }
    const hit = size in box.fitBySize ? box.fitBySize[size] : fit('fits')
    const judged = box.judgedAs[size] ?? size
    return { data: { fit: hit, size: judged }, isLoading: false, isPlaceholderData: false }
  },
  postRenderCancel: async () => {
    calls.push(['render-cancel', null])
    if (box.cancelFail) throw box.cancelFail
    return { status: 'ok' }
  },
  generateImage: (vars: GenVars) => {
    calls.push(['generate', vars])
    return (box.generate ?? (async () => result()))(vars)
  },
}))
vi.mock('@/api/hooks/useCapabilities', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useCapabilities: () => ({ data: box.caps }),
}))
vi.mock('@/api/hooks/useSlots', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useSlots: () => ({ data: box.slots }),
  useSlotConfig: (name: string | null) => {
    calls.push(['slot-config', name])
    return { data: name ? box.slotCfg : undefined }
  },
}))
vi.mock('@/api/hooks/useStatsHardware', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useMemoryBudget: () => ({ data: box.memory }),
}))

const { ImageGeneratePanel } = await import('../image/ImageGeneratePanel.jsx')
const { resetGenerateStore } = await import('../image/generate-store')
const { queryClient } = await import('@/lib/queryClient')
held.qc = queryClient
const { useImageRunStore } = await import('@/stores/useImageRunStore')

// ── fixtures ─────────────────────────────────────────────────────────────────

const REASON = 'needs ~47.9 GB of RAM to load, 45.8 GB free (48.0 GB container limit)'

function fit(verdict: 'fits' | 'tight' | 'wont_fit', over: Json = {}) {
  return {
    verdict,
    reason: verdict === 'fits' ? null : REASON,
    need: { gpu_gb: 10.2, ram_gb: 12.4, gpu_estimated: false, ram_estimated: false },
    render_free_gb: 50,
    ram_free_gb: 44,
    ...over,
  }
}

const row = (over: Json = {}) => ({
  id: 'sdxl-lightning',
  label: 'SDXL Lightning (8-step)',
  task: 'txt2img',
  state: 'complete',
  api_renderable: true,
  recommended: true,
  license: 'CreativeML-OpenRAIL++',
  est_seconds: 6,
  sizes: ['1024x1024', '1344x768'],
  notes: '',
  defaults: { steps: 8, cfg: 1, sampler_name: 'euler' },
  approx_gb: 6.9,
  missing_gb: 0,
  missing: [],
  workflow: 'sdxl-lightning.json',
  job: null,
  fit: fit('fits'),
  ...over,
})
const qwen = (over: Json = {}) =>
  row({
    id: 'qwen-image',
    label: 'Qwen-Image (20-step)',
    recommended: false,
    license: 'Apache-2.0',
    est_seconds: 360,
    sizes: ['1328x1328', '1664x928'],
    defaults: { steps: 20, cfg: 2.5 },
    approx_gb: 47.9,
    ...over,
  })
const turbo = () =>
  row({ id: 'sdxl-turbo', label: 'SDXL Turbo', recommended: false, state: 'none', missing_gb: 6.5 })
const ltx = () =>
  row({
    id: 'ltx2-t2v',
    label: 'LTX-2 Text to Video (fp8)',
    task: 'txt2video',
    api_renderable: false,
    recommended: false,
    sizes: [],
    defaults: {},
  })

const catalog = (models: Json[], over: Json = {}) => ({
  models,
  engine: { available: true, reason: null },
  store: { free_gb: 812.4, path: '/srv/models/comfyui/models' },
  recommendation: { id: 'sdxl-lightning', pool_gb: 48, limited_by: 'ram' },
  ...over,
})

const ckptFile = (over: Json = {}) => ({
  kind: 'checkpoints',
  subdir: 'checkpoints',
  relpath: 'sdxl/my-model.safetensors',
  size_bytes: 6_900_000_000,
  models: [],
  arch: 'sdxl',
  api_id: 'ckpt/sdxl/my-model',
  symlink: false,
  ...over,
})

const arbiterBlock = (over: Json = {}) => ({
  mode: 'llm',
  pinned: false,
  saved_llm_slots: [],
  switching: false,
  image_jobs: 0,
  phase: null,
  restore_at: null,
  handoff_until: null,
  drain_in_flight: 0,
  unload_set: ['brain', 'coder'],
  llm_waiters: { count: 0, oldest_s: null },
  ...over,
})
const envelope = (over: Json = {}) => ({
  mode: null,
  arbiter: arbiterBlock(over),
  switchover: { active: false, target: null, error: null },
  gate_refusal: null,
})
const statusBody = (over: Json = {}) => ({
  queue: { running: 0, pending: 0 },
  progress: null,
  gate: { state: 'current', version_seen: '1' },
  gate_refusal: null,
  ...over,
})
const progress = (over: Json = {}) => ({
  model: 'sdxl-lightning',
  step: null,
  total: null,
  node: null,
  elapsed_s: 2,
  prompt_id: 'p1',
  ...over,
})
const memoryBody = () => ({
  gpu: {
    kind: 'shared',
    total_gb: 96,
    used_gb: 40,
    this_box: [
      { slot: 'brain', gb: 20.2, state: 'ready' },
      { slot: 'coder', gb: 9.1, state: 'ready' },
    ],
    image_engine_gb: 0,
    image_engine_slot: 'img',
    image_engine_estimated: false,
    other_guests_gb: 0,
    attribution_excess_gb: 0,
    free_gb: 56,
  },
  ram: { limit_gb: 64, used_gb: 20, free_gb: 44, source: 'container' },
  render_free_gb: 50,
})

// What a /v1/images/generations answer carries, for `n` images.
let urlCounter = 0
function result(over: { n?: number; seed?: number | null; model?: string } = {}) {
  const n = over.n ?? 1
  const data = Array.from({ length: n }, () => ({ url: `/api/images/cache/img-${++urlCounter}.png` }))
  return {
    created: 1,
    data,
    _hal0: {
      meta: {
        seed: over.seed === undefined ? 1234567 : over.seed,
        steps: 8,
        cfg: 1,
        width: 1024,
        height: 1024,
        batch_size: n,
        negative_prompt: '',
      },
      prompt_id: `p-${urlCounter}`,
      upstream: 'img',
      model: over.model ?? 'sdxl-lightning',
      template: 'sdxl',
    },
  }
}

class ApiError extends Error {
  code: string
  status: number
  details: Json
  constructor(message: string, code: string, status: number, details: Json = {}) {
    super(message)
    this.code = code
    this.status = status
    this.details = details
  }
}

// A generateImage that stays open until the test settles it, and that rejects the way fetch does
// when its signal is aborted.
function openRun() {
  const run: { vars?: GenVars; resolve?: (v: unknown) => void; reject?: (e: unknown) => void } = {}
  box.generate = (vars) =>
    new Promise((resolve, reject) => {
      run.vars = vars
      run.resolve = resolve
      run.reject = reject
      vars.signal?.addEventListener('abort', () =>
        reject(new DOMException('The operation was aborted.', 'AbortError')),
      )
    })
  return run
}

// ── harness ──────────────────────────────────────────────────────────────────

const mounted: { root: Root; host: HTMLElement }[] = []
const props = {
  initialModelId: undefined as string | undefined,
  pickSeq: undefined as number | undefined,
  onOpenModels: vi.fn(),
}
const tree = () => (
  <div className="comfy-page">
    <ImageGeneratePanel
      initialModelId={props.initialModelId}
      pickSeq={props.pickSeq}
      onOpenModels={props.onOpenModels}
    />
  </div>
)

function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(tree())
  })
  mounted.push({ root, host })
  return {
    host,
    root,
    // A poll landing: render again with whatever the mocks hold now.
    rerender: () =>
      act(() => {
        root.render(tree())
      }),
    unmount: () => {
      act(() => root.unmount())
      host.remove()
      mounted.splice(
        mounted.findIndex((m) => m.root === root),
        1,
      )
    },
  }
}

const byId = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
const allById = (testid: string) =>
  Array.from(document.querySelectorAll(`[data-testid="${testid}"]`)) as HTMLElement[]
const text = (el: ParentNode | null = document.body) => el?.textContent ?? ''
const button = (label: string | RegExp, within: ParentNode = document) =>
  Array.from(within.querySelectorAll('button')).find((b) =>
    typeof label === 'string' ? b.textContent?.trim() === label : label.test(b.textContent ?? ''),
  ) as HTMLButtonElement | undefined

async function click(el: Element | null | undefined) {
  expect(el, 'element to click').toBeTruthy()
  await act(async () => {
    el!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })
}

const inputSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')!.set!
const areaSetter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!.set!
function type(el: HTMLInputElement | HTMLTextAreaElement | null, value: string) {
  expect(el, 'field to type in').toBeTruthy()
  act(() => {
    const set = el instanceof window.HTMLTextAreaElement ? areaSetter : inputSetter
    set.call(el, value)
    el!.dispatchEvent(new Event('input', { bubbles: true }))
  })
}
const blur = (el: Element | null) =>
  act(() => {
    el!.dispatchEvent(new FocusEvent('focusout', { bubbles: true }))
  })

const prompt = () => byId('gen-prompt') as HTMLTextAreaElement
const submit = () => byId('gen-submit') as HTMLButtonElement
const field = (id: string) => byId(id) as HTMLInputElement

async function pickModel(id: string) {
  await click(byId('gen-model'))
  const option = document.querySelector(`[data-option-id="${id}"]`)
  await click(option)
}

// Fill the prompt and press Generate.
async function generate(promptText = 'a cat in a hat') {
  type(prompt(), promptText)
  await click(submit())
}

// Let a pending run settle and the panel re-render.
async function settle(fn: () => void) {
  await act(async () => {
    fn()
  })
}

const generated = () => calls.filter(([name]) => name === 'generate').map(([, vars]) => vars as GenVars)
const fitCalls = () => calls.filter(([name]) => name === 'fit').map(([, args]) => args as [string, string])

beforeEach(() => {
  urlCounter = 0
  box.models = catalog([row(), qwen(), turbo(), ltx()])
  box.modelsError = undefined
  box.files = { root: '/srv/models/comfyui/models', files: [] }
  box.caps = { selections: { img: { img: { model: 'sdxl-lightning', enabled: true } } } }
  box.slots = []
  box.slotCfg = undefined
  box.arbiter = envelope()
  box.status = statusBody()
  box.memory = memoryBody()
  box.fitBySize = {}
  box.judgedAs = {}
  box.fitState = 'ready'
  box.staleFit = null
  box.generate = undefined
  box.cancelFail = undefined
  calls.length = 0
  props.initialModelId = undefined
  props.pickSeq = undefined
  props.onOpenModels.mockReset()
  queryClient.clear()
  useImageRunStore.setState({ run: null })
  resetGenerateStore()
  window.sessionStorage.clear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
  while (mounted.length) {
    const { root, host } = mounted.pop()!
    act(() => root.unmount())
    host.remove()
  }
  document.body.innerHTML = ''
})

// ── tests ────────────────────────────────────────────────────────────────────

describe('ImageGeneratePanel: the form', () => {
  it('starts on the img selection with the model own steps, cfg and native size', async () => {
    mount()
    expect(text(byId('gen-model'))).toContain('SDXL Lightning (8-step)')
    // The options are folded away until asked for.
    expect(byId('gen-options')).toBeNull()
    await click(byId('gen-more'))
    expect(field('gen-steps').value).toBe('8')
    expect(field('gen-cfg').value).toBe('1')
    expect(byId('gen-size-1024x1024')?.getAttribute('aria-pressed')).toBe('true')
    expect(byId('gen-size-1344x768')?.getAttribute('aria-pressed')).toBe('false')
  })

  it('puts the img selection first, then the other installed models and the user checkpoints', async () => {
    box.caps = { selections: { img: { img: { model: 'qwen-image', enabled: true } } } }
    box.files = {
      root: '/m',
      files: [
        ckptFile(),
        // Owned by a catalog model, or one the sniffer could not place: not offered.
        ckptFile({ relpath: 'unknown.safetensors', api_id: undefined, arch: undefined }),
        ckptFile({ relpath: 'sd15/old.ckpt', api_id: 'ckpt/sd15/old', arch: 'sd15' }),
        { kind: 'vae', subdir: 'vae', relpath: 'x.safetensors', size_bytes: 1, models: [], symlink: false },
      ],
    }
    mount()
    expect(text(byId('gen-model'))).toContain('Qwen-Image (20-step)')
    await click(byId('gen-model'))
    const options = Array.from(document.querySelectorAll('[role="option"]')) as HTMLElement[]
    expect(options.map((o) => o.getAttribute('data-option-id'))).toEqual([
      'qwen-image',
      'sdxl-lightning',
      'ckpt/sdxl/my-model',
      'ckpt/sd15/old',
      // The footer row.
      expect.any(String),
    ])
    // Not installed, or only the editor runs it: not offered.
    expect(options.map((o) => o.textContent).join('|')).not.toMatch(/SDXL Turbo|LTX-2/)
    expect(text(options[2])).toContain('sdxl/my-model.safetensors (your checkpoint, sdxl)')
    expect(text(options[3])).toContain('sd15/old.ckpt (your checkpoint, sd15)')
    // The last row says where the other tasks run, and cannot be picked.
    const footer = options[options.length - 1]
    expect(text(footer)).toContain('Video, edit and upscale run in the ComfyUI editor')
    expect(footer.getAttribute('aria-disabled')).toBe('true')
    await click(footer)
    expect(text(byId('gen-model'))).toContain('Qwen-Image (20-step)')
  })

  it('opens on the model the Models tab asked for', async () => {
    props.initialModelId = 'qwen-image'
    mount()
    expect(text(byId('gen-model'))).toContain('Qwen-Image (20-step)')
    await click(byId('gen-more'))
    expect(field('gen-steps').value).toBe('20')
    expect(field('gen-cfg').value).toBe('2.5')
    expect(byId('gen-size-1328x1328')?.getAttribute('aria-pressed')).toBe('true')
  })

  it('opens on a model asked for after it has mounted', async () => {
    const view = mount()
    expect(text(byId('gen-model'))).toContain('SDXL Lightning (8-step)')
    props.initialModelId = 'qwen-image'
    view.rerender()
    expect(text(byId('gen-model'))).toContain('Qwen-Image (20-step)')
    expect(byId('gen-size-1328x1328')?.getAttribute('aria-pressed')).toBe('true')
  })

  it('opens again on the same model when it is asked for again, after another was picked', async () => {
    props.initialModelId = 'qwen-image'
    props.pickSeq = 1
    const view = mount()
    expect(text(byId('gen-model'))).toContain('Qwen-Image (20-step)')

    // The viewer picks another model here...
    await pickModel('sdxl-lightning')
    expect(text(byId('gen-model'))).toContain('SDXL Lightning (8-step)')
    // ...and a render of the same props changes nothing: it is not a new request.
    view.rerender()
    expect(text(byId('gen-model'))).toContain('SDXL Lightning (8-step)')

    // Generate on the Models tab again: same model, next sequence number.
    props.pickSeq = 2
    view.rerender()
    expect(text(byId('gen-model'))).toContain('Qwen-Image (20-step)')
    expect(byId('gen-size-1328x1328')?.getAttribute('aria-pressed')).toBe('true')
  })

  it('takes size and steps from the img slot before the model', async () => {
    box.slots = [{ name: 'img', type: 'image', group: 'img' }]
    box.slotCfg = { image: { default_size: '1344x768', default_steps: 12 } }
    mount()
    expect(calls).toContainEqual(['slot-config', 'img'])
    expect(byId('gen-size-1344x768')?.getAttribute('aria-pressed')).toBe('true')
    await click(byId('gen-more'))
    expect(field('gen-steps').value).toBe('12')
    // No default cfg on the slot: the model's.
    expect(field('gen-cfg').value).toBe('1')
    await generate()
    expect(generated()[0]).toMatchObject({ size: '1344x768', steps: 12, cfg: 1 })
  })

  it('keeps the prompt and the options when the model changes, and refills steps, cfg and size', async () => {
    mount()
    type(prompt(), 'a fox')
    await click(byId('gen-more'))
    type(field('gen-negative'), 'blurry')
    type(field('gen-steps'), '30')
    await pickModel('qwen-image')
    expect(prompt().value).toBe('a fox')
    expect(field('gen-negative').value).toBe('blurry')
    expect(field('gen-steps').value).toBe('20')
    expect(field('gen-cfg').value).toBe('2.5')
    expect(byId('gen-size-1328x1328')?.getAttribute('aria-pressed')).toBe('true')
  })

  it('submits with Ctrl+Enter, and not with an empty prompt', async () => {
    mount()
    const press = (init: KeyboardEventInit) =>
      act(async () => {
        prompt().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, ...init }))
      })
    type(prompt(), '   ')
    await press({ ctrlKey: true })
    expect(generated()).toHaveLength(0)
    expect(submit().disabled).toBe(true)

    type(prompt(), 'a cat')
    await press({}) // a plain Enter is a new line
    expect(generated()).toHaveLength(0)
    await press({ metaKey: true })
    expect(generated()).toHaveLength(1)
    expect(generated()[0].prompt).toBe('a cat')
  })

  it('sends the options it holds', async () => {
    mount()
    type(prompt(), 'a dog')
    await click(byId('gen-more'))
    type(field('gen-negative'), ' blurry ')
    type(field('gen-steps'), '25')
    type(field('gen-cfg'), '3.5')
    type(field('gen-n'), '3')
    await click(byId('gen-seed-locked'))
    type(field('gen-seed'), '99')
    await click(submit())
    expect(generated()[0]).toMatchObject({
      model: 'sdxl-lightning',
      prompt: 'a dog',
      n: 3,
      size: '1024x1024',
      steps: 25,
      cfg: 3.5,
      seed: 99,
      negative_prompt: 'blurry',
    })
  })

  it('draws a fresh seed for a random run, and the dice rolls one and locks it', async () => {
    const random = vi.spyOn(Math, 'random')
    random.mockReturnValueOnce(0.25).mockReturnValueOnce(0.75)
    mount()
    await click(byId('gen-more'))
    expect(field('gen-seed').disabled).toBe(true)
    expect(field('gen-seed').value).toBe('')
    await generate()
    await click(submit())
    // Each random run has a seed of its own.
    expect(generated().map((v) => v.seed)).toEqual([2 ** 30, 3 * 2 ** 30])

    random.mockReturnValueOnce(0.5)
    await click(byId('gen-seed-dice'))
    expect(byId('gen-seed-mode')?.getAttribute('data-locked')).toBe('true')
    expect(field('gen-seed').disabled).toBe(false)
    expect(field('gen-seed').value).toBe(String(2 ** 31))
    // Locked, it is the same seed every time.
    await click(submit())
    await click(submit())
    expect(generated().slice(2).map((v) => v.seed)).toEqual([2 ** 31, 2 ** 31])

    random.mockReturnValueOnce(0.125)
    await click(byId('gen-seed-random'))
    expect(field('gen-seed').disabled).toBe(true)
    expect(field('gen-seed').value).toBe('')
    await click(submit())
    expect(generated()[4].seed).toBe(2 ** 29)
  })

  it('will not run with a locked seed that has been cleared, and says what to do', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    mount()
    type(prompt(), 'a cat')
    await click(byId('gen-more'))
    await click(byId('gen-seed-locked'))
    expect(submit().disabled).toBe(false)

    type(field('gen-seed'), '')
    expect(field('gen-seed').disabled).toBe(false)
    expect(submit().disabled).toBe(true)
    expect(text(byId('gen-blocked'))).toBe('Enter a seed or switch to Random.')
    // Ctrl+Enter does not get round it.
    await act(async () => {
      prompt().dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true }))
    })
    expect(generated()).toHaveLength(0)

    // A seed typed in, or switching back to Random, lets it run.
    type(field('gen-seed'), '7')
    expect(submit().disabled).toBe(false)
    expect(byId('gen-blocked')).toBeNull()
    type(field('gen-seed'), '')
    expect(submit().disabled).toBe(true)
    await click(byId('gen-seed-random'))
    expect(submit().disabled).toBe(false)
    expect(byId('gen-blocked')).toBeNull()
    await click(submit())
    expect(generated()).toHaveLength(1)
    expect(generated()[0].seed).toBe(2 ** 31)
  })

  it('locking starts from a rolled seed, and a seed too big for a browser is held to what it holds', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    mount()
    await click(byId('gen-more'))
    await click(byId('gen-seed-locked'))
    expect(field('gen-seed').value).toBe(String(2 ** 31))

    type(field('gen-seed'), '99999999999999999999')
    await generate()
    expect(generated()[0].seed).toBe(Number.MAX_SAFE_INTEGER)
    blur(field('gen-seed'))
    expect(field('gen-seed').value).toBe(String(Number.MAX_SAFE_INTEGER))
  })

  it('custom size snaps to multiple of 8', async () => {
    mount()
    await click(byId('gen-size-custom'))
    expect(byId('gen-size-custom')?.getAttribute('aria-pressed')).toBe('true')
    // Starts from the size that was chosen.
    expect(field('gen-custom-w').value).toBe('1024')
    expect(field('gen-custom-h').value).toBe('1024')

    type(field('gen-custom-w'), '1030')
    type(field('gen-custom-h'), '30')
    // Typing does not move the fit: it moves when the field is left.
    expect(fitCalls().at(-1)).toEqual(['sdxl-lightning', '1024x1024'])
    blur(field('gen-custom-w'))
    blur(field('gen-custom-h'))
    expect(field('gen-custom-w').value).toBe('1024')
    expect(field('gen-custom-h').value).toBe('64')
    expect(fitCalls().at(-1)).toEqual(['sdxl-lightning', '1024x64'])

    type(field('gen-custom-w'), '9000')
    blur(field('gen-custom-w'))
    expect(field('gen-custom-w').value).toBe('2048')

    type(prompt(), 'a cat')
    await click(submit())
    expect(generated()[0]).toMatchObject({ size: '2048x64' })
    // A listed size takes the chip back.
    await click(byId('gen-size-1344x768'))
    expect(byId('gen-size-1344x768')?.getAttribute('aria-pressed')).toBe('true')
    expect(byId('gen-size-custom')?.getAttribute('aria-pressed')).toBe('false')
  })
})

describe('ImageGeneratePanel: fit and the cost line', () => {
  it('shows the fit meter for the chosen size', () => {
    mount()
    expect(byId('gen-fit')).toBeTruthy()
    expect(text(byId('fit-chip-gen'))).toBe('Fits · 31.6 GB to spare')
    expect(byId('gen-blocked')).toBeNull()
  })

  it('size change refetches fit', async () => {
    box.fitBySize = { '1344x768': fit('tight') }
    mount()
    expect(fitCalls().at(-1)).toEqual(['sdxl-lightning', '1024x1024'])
    expect(byId('fit-meter-gen')?.getAttribute('data-verdict')).toBe('fits')

    await click(byId('gen-size-1344x768'))
    expect(fitCalls().at(-1)).toEqual(['sdxl-lightning', '1344x768'])
    expect(byId('fit-meter-gen')?.getAttribute('data-verdict')).toBe('tight')
    expect(text(byId('fit-chip-gen'))).toBe('Tight')
    // Tight does not stop a render.
    type(prompt(), 'a cat')
    expect(submit().disabled).toBe(false)

    await pickModel('qwen-image')
    expect(fitCalls().at(-1)).toEqual(['qwen-image', '1328x1328'])
  })

  it('says where a verdict was judged when the server moved the size', async () => {
    box.judgedAs = { '1344x768': '1344x760' }
    mount()
    expect(text(byId('gen-fit'))).not.toContain('judged at')
    await click(byId('gen-size-1344x768'))
    expect(text(byId('gen-fit'))).toContain('judged at 1344x760')
  })

  it('generate disabled with reason when fit is wont_fit', async () => {
    box.fitBySize = { '1024x1024': fit('wont_fit') }
    mount()
    type(prompt(), 'a cat')
    expect(submit().disabled).toBe(true)
    // The reason sits beside the button, as the server wrote it.
    expect(text(byId('gen-blocked'))).toContain(REASON)
    expect(text(byId('fit-chip-gen'))).toBe("Won't fit")
    await click(submit())
    expect(generated()).toHaveLength(0)
    // Another size fits again.
    await click(byId('gen-size-1344x768'))
    expect(submit().disabled).toBe(false)
    expect(byId('gen-blocked')).toBeNull()
  })

  describe('while the verdict for the current model and size is loading', () => {
    it('holds Generate on the first look, saying so', () => {
      box.fitState = 'first'
      const { rerender } = mount()
      type(prompt(), 'a cat')
      expect(submit().disabled).toBe(true)
      expect(text(byId('gen-fit-loading'))).toBe('Checking fit…')

      box.fitState = 'ready'
      rerender()
      expect(submit().disabled).toBe(false)
      expect(byId('gen-fit-loading')).toBeNull()
    })

    // The last verdict stays on screen while the next one is read (no flash), but it is another
    // model's or size's: it neither lets a render through nor stops one.
    it('holds Generate while the last verdict is only a placeholder, and does not act on it', () => {
      box.fitState = 'stale'
      box.staleFit = fit('fits')
      const { rerender } = mount()
      type(prompt(), 'a cat')
      expect(submit().disabled).toBe(true)
      expect(text(byId('gen-fit-loading'))).toBe('Checking fit…')
      // The old meter is still there, marked as out of date.
      expect(byId('fit-meter-gen')).not.toBeNull()
      expect(byId('gen-fit')?.getAttribute('data-stale')).toBe('true')

      // A placeholder that says "won't fit" blocks nothing by its own sentence either.
      box.staleFit = fit('wont_fit')
      rerender()
      expect(submit().disabled).toBe(true)
      expect(byId('gen-blocked')).toBeNull()

      box.fitState = 'ready'
      rerender()
      expect(submit().disabled).toBe(false)
      expect(byId('gen-fit')?.getAttribute('data-stale')).toBeNull()
    })

    it('never holds a user checkpoint, which has no /fit', async () => {
      box.files = { root: '/m', files: [ckptFile()] }
      const { rerender } = mount()
      await pickModel('ckpt/sdxl/my-model')
      type(prompt(), 'a cat')
      expect(submit().disabled).toBe(false)
      for (const state of ['first', 'stale'] as const) {
        box.fitState = state
        box.staleFit = fit('wont_fit')
        rerender()
        expect(submit().disabled, state).toBe(false)
        expect(text(byId('gen-fit-skipped'))).toBe('Fit is checked when you generate.')
        expect(byId('gen-blocked')).toBeNull()
      }
    })
  })

  it('says the verdict is for one image when more than one is asked for', async () => {
    mount()
    await click(byId('gen-more'))
    expect(text(byId('gen-fit'))).not.toContain('judged for one image')
    type(field('gen-n'), '3')
    expect(text(byId('gen-fit'))).toContain('judged for one image')
    type(field('gen-n'), '1')
    expect(text(byId('gen-fit'))).not.toContain('judged for one image')
    // Nothing to qualify when there is no verdict.
    box.fitBySize = { '1024x1024': null }
    type(field('gen-n'), '4')
    expect(byId('fit-meter-gen')).toBeNull()
    expect(text(byId('gen-fit'))).not.toContain('judged for one image')
  })

  it('shows no verdict, and blocks nothing, when the box budget is unknown', () => {
    box.fitBySize = { '1024x1024': null }
    mount()
    type(prompt(), 'a cat')
    expect(byId('fit-meter-gen')).toBeNull()
    expect(submit().disabled).toBe(false)
  })

  it('checks a user checkpoint when it is generated, not before', async () => {
    box.files = { root: '/m', files: [ckptFile()] }
    mount()
    await pickModel('ckpt/sdxl/my-model')
    expect(text(byId('gen-fit-skipped'))).toBe('Fit is checked when you generate.')
    expect(byId('fit-meter-gen')).toBeNull()
    // The size is the family's native one; nothing is known of steps and cfg.
    expect(byId('gen-size-1024x1024')?.getAttribute('aria-pressed')).toBe('true')
    await click(byId('gen-more'))
    expect(field('gen-steps').value).toBe('')
    await generate()
    const vars = generated()[0]
    expect(vars).toMatchObject({ model: 'ckpt/sdxl/my-model', size: '1024x1024' })
    expect('steps' in vars).toBe(false)
    expect('cfg' in vars).toBe(false)
  })

  it('shows the engine note and no verdict on a host with no image engine', () => {
    box.models = catalog([row()], { engine: { available: false, reason: 'no ROCm lane on this host' } })
    mount()
    type(prompt(), 'a cat')
    expect(byId('fit-meter-gen')).toBeNull()
    expect(submit().disabled).toBe(true)
    expect(text(byId('gen-blocked'))).toContain('no ROCm lane on this host')
    expect(text(document.body)).not.toContain("Won't fit")
  })

  it('names what the next image pauses, from the arbiter and the memory budget', () => {
    mount()
    expect(text(byId('gen-cost'))).toBe(
      'Pauses brain and coder (29.3 GB). They reload about 10 s after the last image.',
    )
  })

  it('says nothing about pausing when the unload set is unknown', () => {
    box.arbiter = envelope({ unload_set: null })
    mount()
    expect(byId('gen-cost')).toBeNull()
  })

  it('says there is nothing to pause, and that a chat reply finishes first', () => {
    box.arbiter = envelope({ unload_set: [], drain_in_flight: 1 })
    mount()
    expect(text(byId('gen-cost'))).toBe('No GPU slots to pause on this box. 1 chat reply is finishing first.')
  })

  it('grace shows generate again', async () => {
    const view = mount()
    expect(text(submit())).toBe('Generate')
    box.arbiter = envelope({ mode: 'img', phase: 'grace', restore_at: Date.now() / 1000 + 8 })
    view.rerender()
    expect(text(submit())).toBe('Generate again')
    expect(text(byId('gen-cost'))).toBe('Image mode is still on, so this one starts at once.')
  })
})

describe('ImageGeneratePanel: a run', () => {
  it('generate panel submits and renders result tiles', async () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const { host } = mount()
    expect(byId('gen-results')).toBeNull()
    type(prompt(), 'a cat in a hat')
    await click(submit())

    const vars = generated()[0]
    expect(vars).toMatchObject({
      model: 'sdxl-lightning',
      prompt: 'a cat in a hat',
      n: 1,
      size: '1024x1024',
      steps: 8,
      cfg: 1,
    })
    // "Random" is drawn here, so the result's seed reaches the browser exactly.
    expect(vars.seed).toBe(2 ** 31)
    // The only thing handed to the call besides the request is the way to abort it.
    expect(vars.signal).toBeInstanceOf(AbortSignal)
    expect(Object.keys(vars).sort()).toEqual(['cfg', 'model', 'n', 'prompt', 'seed', 'signal', 'size', 'steps'])

    const tiles = allById('gen-tile')
    expect(tiles).toHaveLength(1)
    const img = tiles[0].querySelector('img') as HTMLImageElement
    expect(img.getAttribute('src')).toBe('/api/images/cache/img-1.png')
    expect(img.getAttribute('alt')).toBe('a cat in a hat')
    const open = tiles[0].querySelector('[data-testid="gen-open"]') as HTMLAnchorElement
    expect(open.getAttribute('href')).toBe('/api/images/cache/img-1.png')
    expect(open.getAttribute('target')).toBe('_blank')
    expect(open.getAttribute('rel')).toContain('noopener')
    const download = tiles[0].querySelector('[data-testid="gen-download"]') as HTMLAnchorElement
    expect(download.getAttribute('href')).toBe('/api/images/cache/img-1.png')
    expect(download.hasAttribute('download')).toBe(true)
    expect(tiles[0].querySelector('[data-testid="gen-reuse"]')).toBeTruthy()
    // The form is as it was left, ready for the next one.
    expect(prompt().value).toBe('a cat in a hat')
    expect(submit().disabled).toBe(false)
    expect(host.querySelector('[data-testid="gen-run"]')).toBeNull()

    // Newest first.
    await click(submit())
    const urls = allById('gen-tile').map((t) => t.querySelector('img')!.getAttribute('src'))
    expect(urls).toEqual(['/api/images/cache/img-2.png', '/api/images/cache/img-1.png'])
  })

  it('keeps the last 12 results for the session, across tabs', async () => {
    box.generate = async () => result({ n: 3 })
    const first = mount()
    type(prompt(), 'a cat')
    for (let i = 0; i < 5; i++) await click(submit())
    // 15 made, 12 kept: the newest, in the order the last run listed them.
    expect(allById('gen-tile')).toHaveLength(12)
    const first12 = allById('gen-tile').map((t) => t.querySelector('img')!.getAttribute('src'))
    expect(first12[0]).toBe('/api/images/cache/img-13.png')
    expect(first12).not.toContain('/api/images/cache/img-1.png')
    expect(first12).not.toContain('/api/images/cache/img-3.png')
    expect(first12).toContain('/api/images/cache/img-4.png')

    // Switching tabs unmounts the panel; coming back finds them.
    first.unmount()
    mount()
    expect(allById('gen-tile').map((t) => t.querySelector('img')!.getAttribute('src'))).toEqual(first12)
  })

  it('reads storage failures as an empty history, not an error', async () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => {
        throw new Error('storage blocked')
      },
      setItem: () => {
        throw new Error('storage blocked')
      },
      removeItem: () => {
        throw new Error('storage blocked')
      },
    })
    try {
      mount()
      await generate()
      expect(allById('gen-tile')).toHaveLength(1)
      expect(byId('gen-error')).toBeNull()
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('reuse seed locks the seed field', async () => {
    box.generate = async () => result({ seed: 4242 })
    mount()
    await generate()
    await click(byId('gen-more'))
    expect(field('gen-seed').disabled).toBe(true)
    expect(field('gen-seed').value).toBe('')

    // The seed the render used is `_hal0.meta.seed`.
    await click(byId('gen-reuse'))
    expect(byId('gen-seed-mode')?.getAttribute('data-locked')).toBe('true')
    expect(field('gen-seed').value).toBe('4242')
    expect(field('gen-seed').disabled).toBe(false)
    // The options are open for the viewer to see it, even if they had been closed.
    await click(submit())
    expect(generated()[1]).toMatchObject({ seed: 4242 })
  })

  it('opens the options when a seed is reused from a closed panel', async () => {
    box.generate = async () => result({ seed: 77 })
    mount()
    await generate()
    expect(byId('gen-seed')).toBeNull()
    await click(byId('gen-reuse'))
    expect(field('gen-seed').value).toBe('77')
  })

  it('cannot reuse a seed a browser would round', async () => {
    // The server draws seeds up to 2**63. JSON.parse keeps 53 bits of them, so sending one back
    // would render a different image: the button says so instead of lying.
    box.generate = async () => result({ seed: 5871234567890123000 })
    mount()
    await generate()
    const reuse = byId('gen-reuse') as HTMLButtonElement
    expect(reuse.disabled).toBe(true)
    expect(reuse.getAttribute('title')).toMatch(/too large/i)
    await click(reuse)
    expect(byId('gen-seed')).toBeNull()
  })

  it('hides reuse seed for a result that names no seed', async () => {
    box.generate = async () => result({ seed: null })
    mount()
    await generate()
    expect(allById('gen-tile')).toHaveLength(1)
    expect(byId('gen-reuse')).toBeNull()
  })

  it('shows the phase strip, the elapsed time and Cancel while it runs', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval', 'Date'] })
    const run = openRun()
    box.arbiter = envelope({ phase: 'unloading', unload_set: ['brain', 'coder'] })
    const view = mount()
    type(prompt(), 'a cat')
    await click(submit())

    // The strip replaces the button.
    expect(byId('gen-submit')).toBeNull()
    expect(byId('gen-run')).toBeTruthy()
    // The cost of the next image is not news mid-run: the line goes until the run is over.
    expect(byId('gen-cost')).toBeNull()
    // Only the step labels are a live region; the ticking timer and the bar are not in one.
    expect(byId('gen-steps')?.getAttribute('aria-live')).toBe('polite')
    expect(byId('gen-elapsed')?.closest('[aria-live]')).toBeNull()
    expect(byId('gen-progress')?.closest('[aria-live]')).toBeNull()
    expect(byId('gen-run')?.hasAttribute('aria-live')).toBe(false)
    expect(byId('gen-run')?.hasAttribute('role')).toBe(false)
    const steps = allById('gen-step')
    expect(steps.map((s) => s.textContent)).toEqual([
      'Chat reply finishing',
      'Unload 2 GPU slots',
      'Engine ready',
      'Load model',
      'Render',
      'Save',
    ])
    expect(steps.map((s) => s.getAttribute('data-state'))).toEqual([
      'done',
      'now',
      'todo',
      'todo',
      'todo',
      'todo',
    ])
    expect(text(byId('gen-elapsed'))).toBe('0:00')
    expect(button('Cancel')).toBeTruthy()
    // The status poll runs fast while a run is in flight (S6).
    expect(calls.filter(([n]) => n === 'status-hook').at(-1)![1]).toMatchObject({ active: true, busy: true })

    // A render under way: the step count and the bar.
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({ progress: progress({ step: 5, total: 20 }) })
    await act(async () => {
      vi.advanceTimersByTime(65_000)
    })
    view.rerender()
    expect(text(byId('gen-elapsed'))).toBe('1:05')
    expect(allById('gen-step')[4].textContent).toBe('Render 5/20')
    expect(allById('gen-step').map((s) => s.getAttribute('data-state'))).toEqual([
      'done',
      'done',
      'done',
      'done',
      'now',
      'todo',
    ])
    expect(byId('gen-progress')?.getAttribute('aria-valuenow')).toBe('25')

    // Mid-run the box is in image mode: still no cost line.
    expect(byId('gen-cost')).toBeNull()

    await settle(() => run.resolve!(result()))
    expect(byId('gen-run')).toBeNull()
    expect(calls.filter(([n]) => n === 'status-hook').at(-1)![1]).toMatchObject({ busy: false })
    // Done: the next image's cost is back.
    expect(byId('gen-cost')).toBeTruthy()
  })

  it('reports the run to the dock store: running while it is in flight, null once it ends', async () => {
    const run = openRun()
    mount()
    expect(useImageRunStore.getState().run).toBeNull()
    type(prompt(), 'a cat')
    await click(submit())
    expect(useImageRunStore.getState().run).toEqual({ running: true, cancel: expect.any(Function) })

    await settle(() => run.resolve!(result()))
    expect(useImageRunStore.getState().run).toBeNull()
  })

  it('a refused request and an abort also clear the dock store', async () => {
    box.generate = async () => {
      throw new ApiError('nope', 'image.gate_refused', 503)
    }
    mount()
    type(prompt(), 'a cat')
    await click(submit())
    expect(useImageRunStore.getState().run).toBeNull()

    const run = openRun()
    await click(submit())
    expect(useImageRunStore.getState().run?.running).toBe(true)
    await click(button('Cancel'))
    expect(run.vars!.signal!.aborted).toBe(true)
    expect(useImageRunStore.getState().run).toBeNull()
  })

  it('cancel aborts before queue, calls render/cancel after', async () => {
    // Before the prompt is queued in ComfyUI (the switch phases, or nothing yet) and no progress:
    // only the request is aborted. The server drops the render after the switch, and the switch
    // lands and restores as normal.
    for (const phase of ['draining', 'unloading', 'starting_engine', null]) {
      calls.length = 0
      urlCounter = 0
      resetGenerateStore()
      const run = openRun()
      box.arbiter = envelope({ mode: phase ? 'llm' : 'img', phase })
      box.status = statusBody()
      const view = mount()
      type(prompt(), 'a cat')
      await click(submit())
      expect(run.vars!.signal!.aborted).toBe(false)

      await click(button('Cancel'))
      expect(run.vars!.signal!.aborted, `abort in ${phase}`).toBe(true)
      expect(calls.some(([n]) => n === 'render-cancel'), `no render/cancel in ${phase}`).toBe(false)
      // A cancel is not an error, and the form is as it was left.
      expect(byId('gen-error')).toBeNull()
      expect(byId('gen-run')).toBeNull()
      expect(prompt().value, `prompt kept after cancel in ${phase}`).toBe('a cat')
      expect(submit().disabled).toBe(false)
      view.unmount()
    }

    // Once queued (hand-off, rendering, or a progress entry): render/cancel too, and the abort.
    const queued: [string | null, string, Json | null][] = [
      ['rendering', 'img', null],
      ['handoff', 'img', null],
      ['rendering', 'img', progress({ step: 3, total: 20 })],
      [null, 'img', progress()],
      ['unloading', 'llm', progress()],
    ]
    for (const [phase, mode, prog] of queued) {
      calls.length = 0
      resetGenerateStore()
      const run = openRun()
      box.arbiter = envelope({ mode, phase })
      box.status = statusBody({ progress: prog })
      const view = mount()
      type(prompt(), 'a cat')
      await click(submit())

      await click(button('Cancel'))
      expect(run.vars!.signal!.aborted, `abort in ${phase}/${prog ? 'progress' : 'none'}`).toBe(true)
      expect(
        calls.filter(([n]) => n === 'render-cancel'),
        `render/cancel in ${phase}/${prog ? 'progress' : 'none'}`,
      ).toHaveLength(1)
      expect(byId('gen-error')).toBeNull()
      expect(byId('gen-run')).toBeNull()
      expect(prompt().value, `prompt kept after cancel in ${phase}/${prog ? 'progress' : 'none'}`).toBe('a cat')
      view.unmount()
    }
  })

  it('the cancel it hands the status line takes the phase it has now', async () => {
    const run = openRun()
    box.arbiter = envelope({ phase: 'unloading' })
    const view = mount()
    type(prompt(), 'a cat')
    await click(submit())
    const { cancel } = useImageRunStore.getState().run!

    // The line's Cancel only exists while switching, but the function must not assume it: the
    // render may have been queued since the line last drew.
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({ progress: progress({ step: 1, total: 8 }) })
    view.rerender()
    await act(async () => {
      cancel()
    })
    expect(run.vars!.signal!.aborted).toBe(true)
    expect(calls.filter(([n]) => n === 'render-cancel')).toHaveLength(1)
  })

  it('says so when render/cancel itself fails, and still lets go of the request', async () => {
    const toast = vi.fn()
    ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
    try {
      const run = openRun()
      box.cancelFail = new Error('ComfyUI did not answer')
      box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
      mount()
      type(prompt(), 'a cat')
      await click(submit())
      await click(button('Cancel'))
      expect(run.vars!.signal!.aborted).toBe(true)
      expect(toast).toHaveBeenCalledWith(expect.stringContaining('ComfyUI did not answer'), 'err')
    } finally {
      delete (window as unknown as { __hal0Toast?: unknown }).__hal0Toast
    }
  })

  it('lets a run finish after the panel is gone, keeps what it made, and keeps it cancellable', async () => {
    const run = openRun()
    const view = mount()
    type(prompt(), 'a cat')
    await click(submit())
    view.unmount()

    // Leaving the tab does not cancel the render, and the dock still has its Cancel.
    expect(run.vars!.signal!.aborted).toBe(false)
    expect(useImageRunStore.getState().run).toEqual({ running: true, cancel: expect.any(Function) })

    // Coming back while it runs shows the run, not a button that would start a second one.
    const back = mount()
    expect(byId('gen-run')).toBeTruthy()
    expect(byId('gen-submit')).toBeNull()
    expect(useImageRunStore.getState().run?.running).toBe(true)

    await settle(() => run.resolve!(result()))
    expect(allById('gen-tile')).toHaveLength(1)
    expect(byId('gen-run')).toBeNull()
    expect(useImageRunStore.getState().run).toBeNull()
    back.unmount()
  })

  it('the dock store cancels a queued render with the panel gone', async () => {
    const run = openRun()
    box.arbiter = envelope({ mode: 'img', phase: 'rendering' })
    box.status = statusBody({ progress: progress({ step: 2, total: 8 }) })
    const view = mount()
    type(prompt(), 'a cat')
    await click(submit())
    view.unmount()

    await act(async () => {
      useImageRunStore.getState().run!.cancel()
    })
    expect(run.vars!.signal!.aborted).toBe(true)
    expect(calls.filter(([n]) => n === 'render-cancel')).toHaveLength(1)
  })
})

describe('ImageGeneratePanel: errors', () => {
  const cases: [string, ApiError][] = [
    [
      '409 image.model_not_installed',
      new ApiError('Image model qwen-image is not installed (47.9 GB to download).', 'image.model_not_installed', 409, {
        model: 'qwen-image',
        install: 'POST /api/comfyui/models/qwen-image/install',
        missing_gb: 47.9,
      }),
    ],
    [
      '422 image.wont_fit',
      new ApiError(REASON, 'image.wont_fit', 422, {
        need: {},
        render_free_gb: 50,
        ram_free_gb: 45.8,
        model: 'sdxl-lightning',
        size: '1024x1024',
      }),
    ],
    ['502 upstream', new ApiError('ComfyUI did not answer the render request.', 'upstream.unavailable', 502)],
    [
      '503 image.gate_refused',
      new ApiError('The GPU gate refused: <b>chat is busy</b>', 'image.gate_refused', 503),
    ],
  ]

  it.each(cases)('generate panel shows server error and keeps prompt (%s)', async (_label, error) => {
    box.generate = async () => {
      throw error
    }
    mount()
    type(prompt(), 'a cat in a hat')
    await click(byId('gen-more'))
    type(field('gen-negative'), 'blurry')
    type(field('gen-steps'), '25')
    await click(byId('gen-size-1344x768'))
    await click(submit())

    // The server's own sentence, as text.
    expect(text(byId('gen-error'))).toContain(error.message)
    expect(byId('gen-error')?.querySelector('b')).toBeNull()
    // The form is as it was left.
    expect(prompt().value).toBe('a cat in a hat')
    expect(field('gen-negative').value).toBe('blurry')
    expect(field('gen-steps').value).toBe('25')
    expect(byId('gen-size-1344x768')?.getAttribute('aria-pressed')).toBe('true')
    expect(allById('gen-tile')).toHaveLength(0)
    // The button is back, and the run is over.
    expect(byId('gen-run')).toBeNull()
    expect(submit().disabled).toBe(false)
    expect(useImageRunStore.getState().run).toBeNull()
    // The install route in the details is a route, never a link.
    expect(document.querySelector('a[href*="install"]')).toBeNull()

    if (error.code === 'image.model_not_installed') {
      await click(byId('gen-error-models'))
      expect(props.onOpenModels).toHaveBeenCalledWith('qwen-image')
    } else {
      expect(byId('gen-error-models')).toBeNull()
    }

    // The next try starts clean.
    box.generate = async () => result()
    await click(submit())
    expect(byId('gen-error')).toBeNull()
    expect(allById('gen-tile')).toHaveLength(1)
  })

  it('drops an error about one model when another is picked', async () => {
    box.generate = async () => {
      throw new ApiError('Image model qwen-image is not installed.', 'image.model_not_installed', 409, {
        model: 'qwen-image',
      })
    }
    mount()
    await generate()
    expect(byId('gen-error')).toBeTruthy()
    await pickModel('qwen-image')
    expect(byId('gen-error')).toBeNull()
    expect(prompt().value).toBe('a cat in a hat')
  })

  it('says so when a 200 comes back with no image in it', async () => {
    const empty = { created: 1, data: [], _hal0: result()._hal0 }
    const unsafe = {
      created: 1,
      // Not ours to show: another origin, a script, and a non-string.
      data: [{ url: '//evil.example/x.png' }, { url: 'javascript:alert(1)' }, { b64_json: 'AAAA' }, {}],
      _hal0: result()._hal0,
    }
    for (const body of [empty, unsafe, null, { created: 1 }]) {
      resetGenerateStore()
      box.generate = async () => body
      const view = mount()
      await generate()
      expect(text(byId('gen-error'))).toBe('No image came back.')
      expect(allById('gen-tile')).toHaveLength(0)
      expect(byId('gen-run')).toBeNull()
      expect(submit().disabled).toBe(false)
      expect(prompt().value).toBe('a cat in a hat')
      // Not a 409: no link to the Models tab.
      expect(byId('gen-error-models')).toBeNull()
      view.unmount()
    }

    // The next run starts clean and can succeed.
    box.generate = async () => result()
    const view = mount()
    await generate()
    expect(byId('gen-error')).toBeNull()
    expect(allById('gen-tile')).toHaveLength(1)
    view.unmount()
  })

  it('shows the images that did come back, and no error, when only some are dropped', async () => {
    const body = result({ n: 2 })
    body.data.push({ url: '//evil.example/x.png' })
    box.generate = async () => body
    mount()
    await generate()
    expect(byId('gen-error')).toBeNull()
    expect(allById('gen-tile')).toHaveLength(2)
  })

  it('links a 409 to the Models tab without a model when the details name none', async () => {
    box.generate = async () => {
      throw new ApiError('Image model is not installed.', 'image.model_not_installed', 409, {})
    }
    mount()
    await generate()
    await click(byId('gen-error-models'))
    expect(props.onOpenModels).toHaveBeenCalledWith(undefined)
  })

  it('shows a failure that is not an API error too, with a message of its own', async () => {
    box.generate = async () => {
      throw new TypeError('Failed to fetch')
    }
    mount()
    await generate()
    expect(text(byId('gen-error'))).toContain('Failed to fetch')
    box.generate = async () => {
      throw 'boom'
    }
    await click(submit())
    expect(text(byId('gen-error'))).not.toBe('')
  })

  it('is silent for an abort and for a 499', async () => {
    box.generate = async () => {
      throw new DOMException('The operation was aborted.', 'AbortError')
    }
    mount()
    await generate()
    expect(byId('gen-error')).toBeNull()
    expect(submit().disabled).toBe(false)

    box.generate = async () => {
      throw new ApiError('API error 499', 'system.unknown', 499)
    }
    await click(submit())
    expect(byId('gen-error')).toBeNull()
    expect(allById('gen-tile')).toHaveLength(0)
  })

  it('keeps an error when the panel was away as it came in', async () => {
    const run = openRun()
    const view = mount()
    await generate()
    view.unmount()
    await settle(() => run.reject!(new ApiError('ComfyUI did not answer.', 'upstream.unavailable', 502)))
    mount()
    expect(text(byId('gen-error'))).toContain('ComfyUI did not answer.')
  })
})

describe('ImageGeneratePanel: empty and loading', () => {
  it('points at the Models tab when no model is installed', async () => {
    box.models = catalog([turbo(), ltx()])
    mount()
    expect(text(byId('gen-empty'))).toContain('No image model installed yet')
    expect(byId('gen-prompt')).toBeNull()
    await click(byId('gen-empty-models'))
    expect(props.onOpenModels).toHaveBeenCalledWith()
  })

  it('says the host cannot render, rather than to install, when there is no image engine', () => {
    box.models = catalog([turbo()], { engine: { available: false, reason: 'no ROCm lane on this host' } })
    mount()
    expect(text(byId('gen-empty'))).toContain('No image model installed yet')
    expect(text(byId('gen-empty'))).toContain('Image generation is unavailable on this host: no ROCm lane on this host')
    expect(text(byId('gen-empty'))).not.toContain('Install one')
  })

  it('does not call it empty while the catalog loads, and shows a failure to load', () => {
    box.models = undefined
    const view = mount()
    expect(byId('gen-empty')).toBeNull()
    expect(byId('gen-loading')).toBeTruthy()

    box.modelsError = new ApiError('The API did not answer.', 'system.unknown', 502)
    view.rerender()
    expect(byId('gen-empty')).toBeNull()
    expect(text(byId('gen-load-error'))).toContain('The API did not answer.')
  })

  it('offers a user checkpoint when it is all there is', async () => {
    box.models = catalog([turbo()])
    box.files = { root: '/m', files: [ckptFile()] }
    mount()
    expect(byId('gen-empty')).toBeNull()
    expect(text(byId('gen-model'))).toContain('sdxl/my-model.safetensors (your checkpoint, sdxl)')
  })

  it('falls back to a model that is installed when the img selection is not', () => {
    box.caps = { selections: { img: { img: { model: 'sdxl-turbo', enabled: true } } } }
    mount()
    expect(text(byId('gen-model'))).toContain('SDXL Lightning (8-step)')
  })
})
