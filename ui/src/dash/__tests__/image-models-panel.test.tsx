// @vitest-environment happy-dom
//
// The Image Gen Models tab: the catalog by task, each card's fit meter and install lifecycle,
// the filters, the remove confirm, set-as-default and the first-run setup block. The hooks are
// mocked at the module boundary (as image-files-tab.test.tsx and image-status-line.test.tsx do),
// so what is pinned is what the panel shows and what each button calls, not the polling.
//
// The panel renders inside the pane's `.comfy-page` wrapper (Task 6 provides it), so the tests
// wrap it the same way.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { React: typeof React }).React = React
// primitives.jsx (ConfirmDialog, Modal) reads `Icons` off the window at render time.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

const { box, calls } = vi.hoisted(() => ({
  box: {
    models: undefined as Json | undefined,
    modelsError: undefined as (Error & { code?: string; details?: Json }) | undefined,
    jobs: undefined as Json[] | undefined,
    jobsAt: 1_000_000 as number,
    files: undefined as Json | undefined,
    // 'loading' = the scan is running, 'error' = it failed; anything else answers box.files.
    filesState: 'ok' as 'ok' | 'loading' | 'error',
    memory: undefined as Json | undefined,
    selection: {} as Json,
    installFail: undefined as Error | undefined,
    removeFail: undefined as Error | undefined,
  },
  calls: [] as [string, unknown][],
}))

vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useComfyuiModels: (opts?: unknown) => {
    calls.push(['models-hook', opts])
    return {
    data: box.models,
    isPending: !box.models && !box.modelsError,
    isError: !!box.modelsError,
    error: box.modelsError ?? null,
    refetch: () => {
      calls.push(['models-refetch', null])
    },
    }
  },
  useImageJobs: () => ({ data: box.jobs, dataUpdatedAt: box.jobsAt }),
  useComfyuiFiles: (opts?: { enabled?: boolean }) => {
    calls.push(['files-hook', opts?.enabled])
    const on = opts?.enabled !== false
    return {
      data: on && box.filesState === 'ok' ? box.files : undefined,
      isLoading: on && box.filesState === 'loading',
      isError: on && box.filesState === 'error',
    }
  },
  useImageModelInstall: () => ({
    isPending: false,
    mutateAsync: async (id: string) => {
      calls.push(['install', id])
      if (box.installFail) throw box.installFail
      return { job: { id: 'j', model_id: id, state: 'queued' } }
    },
  }),
  useImageModelRemove: () => ({
    isPending: false,
    mutateAsync: async (vars: unknown) => {
      calls.push(['remove', vars])
      if (box.removeFail) throw box.removeFail
      return { removed: [], kept: [], affected: [] }
    },
  }),
  useImageJobCancel: () => ({
    isPending: false,
    mutateAsync: async (id: string) => {
      calls.push(['cancel', id])
      return { job: { id: 'j', model_id: id, state: 'cancelled' } }
    },
  }),
}))
vi.mock('@/api/hooks/useStatsHardware', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useMemoryBudget: () => ({ data: box.memory }),
}))
vi.mock('@/dash/settings/pages/capabilities/useCapabilitySelection.js', () => ({
  useCapabilitySelection: (group: string, child: string, opts: unknown) => {
    calls.push(['selection-hook', [group, child, opts]])
    return {
      selection: box.selection,
      applyCapability: {
        isPending: false,
        mutateAsync: async (vars: unknown) => {
          calls.push(['apply', vars])
          return {}
        },
      },
    }
  },
}))

const { ImageModelsPanel } = await import('../image/ImageModelsPanel.jsx')
const { FitMeter } = await import('../image/FitMeter.jsx')

// ── fixtures ─────────────────────────────────────────────────────────────────

const REASON = 'needs ~47.9 GB of RAM to load, 45.8 GB free (48.0 GB container limit)'

const fit = (verdict: 'fits' | 'tight' | 'wont_fit', over: Json = {}) => ({
  verdict,
  reason: verdict === 'fits' ? null : REASON,
  need: { gpu_gb: 10.2, ram_gb: 12.4, gpu_estimated: false, ram_estimated: false },
  render_free_gb: 50,
  ram_free_gb: 44,
  ...over,
})

const row = (over: Json = {}) => ({
  id: 'sdxl-lightning',
  label: 'SDXL Lightning (8-step)',
  task: 'txt2img',
  state: 'none',
  api_renderable: true,
  recommended: false,
  license: 'CreativeML-OpenRAIL++',
  est_seconds: 6,
  sizes: ['1024x1024'],
  notes: '',
  defaults: {},
  approx_gb: 6.9,
  missing_gb: 6.9,
  missing: [],
  workflow: 'sdxl-lightning.json',
  job: null,
  fit: fit('fits'),
  ...over,
})

const lightning = (over: Json = {}) => row({ recommended: true, state: 'complete', missing_gb: 0, ...over })
const turbo = (over: Json = {}) =>
  row({
    id: 'sdxl-turbo',
    label: 'SDXL Turbo',
    license: 'SAI-NC-Research-Community',
    approx_gb: 6.5,
    missing_gb: 6.5,
    fit: fit('tight'),
    ...over,
  })
const sd15 = (over: Json = {}) =>
  row({ id: 'sd-1.5-pruned-emaonly', label: 'Stable Diffusion 1.5', approx_gb: 4.3, missing_gb: 4.3, ...over })
const qwen = (over: Json = {}) =>
  row({
    id: 'qwen-image',
    label: 'Qwen-Image (20-step)',
    license: 'Apache-2.0',
    est_seconds: 360,
    approx_gb: 47.9,
    missing_gb: 47.9,
    fit: fit('wont_fit'),
    ...over,
  })
const ltx = (over: Json = {}) =>
  row({
    id: 'ltx2-t2v',
    label: 'LTX-2 Text to Video (fp8)',
    task: 'txt2video',
    api_renderable: false,
    license: 'LTX-2-Community',
    est_seconds: 120,
    approx_gb: 28.1,
    missing_gb: 28.1,
    ...over,
  })
const ltxBf16 = (over: Json = {}) =>
  ltx({ id: 'ltx2-t2v-bf16', label: 'LTX-2 Text to Video (bf16)', approx_gb: 40.2, missing_gb: 40.2, ...over })
const esrgan = (over: Json = {}) =>
  row({
    id: 'esrgan-4x',
    label: 'ESRGAN 4x Upscale',
    task: 'upscale',
    api_renderable: false,
    license: 'CC-BY-NC-SA-4.0',
    est_seconds: 4,
    approx_gb: 0.1,
    state: 'complete',
    missing_gb: 0,
    ...over,
  })

const catalog = (models: Json[], over: Json = {}) => ({
  models,
  engine: { available: true, reason: null },
  store: { free_gb: 812.4, path: '/srv/models/comfyui/models' },
  recommendation: { id: 'sdxl-lightning', pool_gb: 48, limited_by: 'ram' },
  ...over,
})

const ramBudget = (source: 'container' | 'system' = 'container') => ({
  gpu: null,
  ram: { limit_gb: 48, used_gb: 2, free_gb: 46, source },
  render_free_gb: null,
})

const job = (over: Json = {}) => ({
  id: 'job-1',
  model_id: 'sdxl-turbo',
  state: 'running',
  bytes_downloaded: 1_000_000_000,
  bytes_total: 4_000_000_000,
  started_at: 1,
  finished_at: null,
  error: null,
  error_code: null,
  sha256: null,
  path: null,
  files: [],
  ...over,
})

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

// ── harness ──────────────────────────────────────────────────────────────────

const mounted: { root: Root; host: HTMLElement }[] = []
const props = {
  onGenerate: vi.fn(),
  onOpenEditor: vi.fn(),
  visible: undefined as boolean | undefined,
  focusModelId: undefined as string | undefined,
  focusSeq: undefined as number | undefined,
}
const tree = () => (
  <div className="comfy-page">
    <ImageModelsPanel
      onGenerate={props.onGenerate}
      onOpenEditor={props.onOpenEditor}
      visible={props.visible}
      focusModelId={props.focusModelId}
      focusSeq={props.focusSeq}
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
    // A poll landing: render again with whatever the mocks hold now.
    rerender: () =>
      act(() => {
        root.render(tree())
      }),
  }
}

const byId = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
const text = (el: ParentNode = document.body) => el.textContent ?? ''
const card = (id: string) => byId(`imp-card-${id}`) as HTMLElement
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

beforeEach(() => {
  box.models = catalog([lightning(), turbo(), qwen(), ltx(), ltxBf16(), esrgan()])
  box.modelsError = undefined
  box.jobs = []
  box.jobsAt = 1_000_000
  box.files = { root: '/srv/models/comfyui/models', files: [] }
  box.filesState = 'ok'
  box.memory = ramBudget('container')
  box.selection = {}
  box.installFail = undefined
  box.removeFail = undefined
  calls.length = 0
  props.onGenerate.mockReset()
  props.onOpenEditor.mockReset()
  props.visible = undefined
  props.focusModelId = undefined
  props.focusSeq = undefined
})

afterEach(() => {
  while (mounted.length) {
    const { root, host } = mounted.pop()!
    act(() => root.unmount())
    host.remove()
  }
  document.body.innerHTML = ''
})

// ── tests ────────────────────────────────────────────────────────────────────

describe('ImageModelsPanel', () => {
  it('models panel renders groups and badges', () => {
    box.selection = { model: 'sdxl-lightning', enabled: true, provider: 'comfyui' }
    mount()

    const heads = Array.from(document.querySelectorAll('[data-testid^="imp-group-"]')).map((g) =>
      g.getAttribute('data-testid'),
    )
    expect(heads).toEqual(['imp-group-txt2img', 'imp-group-txt2video', 'imp-group-upscale'])
    expect(text(byId('imp-group-txt2img')!)).toContain('Text → Image')
    expect(text(byId('imp-group-txt2video')!)).toContain('Text → Video')
    expect(text(byId('imp-group-upscale')!)).toContain('Upscale')

    // The header reads the store's free space.
    expect(text(byId('imp-store')!)).toBe('Model store · 812.4 GB free')

    const ok = card('sdxl-lightning')
    expect(text(ok)).toContain('SDXL Lightning (8-step)')
    expect(text(ok)).toContain('CreativeML-OpenRAIL++')
    expect(text(ok)).toContain('6.9 GB')
    expect(text(ok)).toContain('~6 s per image')
    expect(text(ok)).toContain('Recommended')
    expect(text(ok)).toContain('Default')

    expect(text(card('sdxl-turbo'))).toContain('Non-commercial')
    expect(text(card('qwen-image'))).toContain('Large')
    expect(text(card('qwen-image'))).toContain('~360 s per image')
    expect(text(card('ltx2-t2v'))).toContain('Editor only')
    expect(text(card('ltx2-t2v'))).toContain('~120 s per clip')
    expect(text(card('esrgan-4x'))).toContain('Non-commercial')
    expect(text(card('esrgan-4x'))).toContain('Editor only')
    // A badge a model has not earned is not shown.
    expect(text(card('sdxl-turbo'))).not.toContain('Recommended')
    expect(text(card('sdxl-turbo'))).not.toContain('Default')
  })

  it('puts the full-precision variants behind a More toggle in their group', async () => {
    mount()
    expect(card('ltx2-t2v-bf16')).toBeNull()

    await click(button(/^More/, byId('imp-group-txt2video')!))
    expect(card('ltx2-t2v-bf16')).not.toBeNull()

    await click(button(/^Fewer/, byId('imp-group-txt2video')!))
    expect(card('ltx2-t2v-bf16')).toBeNull()
  })

  it('keeps an installed full-precision variant in the list', () => {
    box.models = catalog([lightning(), ltx(), ltxBf16({ state: 'complete', missing_gb: 0 })])
    mount()
    expect(card('ltx2-t2v-bf16')).not.toBeNull()
  })

  it('fit meter shows the verdict and reason from the row', () => {
    box.models = catalog([
      lightning(),
      turbo({ fit: fit('tight', { reason: 'needs 6.5 GB of RAM; 6.9 GB free' }) }),
      qwen({
        fit: fit('wont_fit', {
          need: { gpu_gb: 47.9, ram_gb: 47.9, gpu_estimated: true, ram_estimated: false },
          render_free_gb: 88.8,
          ram_free_gb: 45.8,
        }),
      }),
    ])
    mount()

    // fits: the chip says how much is to spare, from the two server figures (min(50 - 10.2,
    // 44 - 12.4) = 31.6), and there is no reason to give.
    expect(text(byId('fit-chip-sdxl-lightning')!)).toBe('Fits · 31.6 GB to spare')
    expect(byId('fit-reason-sdxl-lightning')).toBeNull()

    // tight and won't fit: the server's sentence, verbatim.
    expect(text(byId('fit-chip-sdxl-turbo')!)).toBe('Tight')
    expect(text(byId('fit-reason-sdxl-turbo')!)).toBe('needs 6.5 GB of RAM; 6.9 GB free')
    expect(text(byId('fit-chip-qwen-image')!)).toBe("Won't fit")
    expect(text(byId('fit-reason-qwen-image')!)).toBe(REASON)

    // Two bars with their own labels; an estimated need says so, a measured one does not.
    const meter = byId('fit-meter-qwen-image')!
    expect(text(byId('fit-bar-gpu-qwen-image')!)).toContain('GPU pool')
    expect(text(byId('fit-bar-gpu-qwen-image')!)).toContain('47.9 GB est.')
    expect(text(byId('fit-bar-ram-qwen-image')!)).toContain('RAM · this box')
    expect(text(byId('fit-bar-ram-qwen-image')!)).not.toContain('est.')
    // The bars are the ruler's own, with the new dashed ghost for the need.
    expect(meter.querySelectorAll('.th-ruler-bar')).toHaveLength(2)
    expect(meter.querySelectorAll('.th-ruler-bar .fm-need')).toHaveLength(2)
  })

  it('fits filter hides wont_fit cards', async () => {
    mount()
    expect(card('qwen-image')).not.toBeNull()

    await click(button('Fits this box'))
    expect(card('qwen-image')).toBeNull()
    expect(card('sdxl-lightning')).not.toBeNull()
    expect(card('sdxl-turbo')).not.toBeNull() // tight is not "won't fit"
    expect(card('ltx2-t2v')).not.toBeNull()

    await click(button('Installed'))
    expect(card('sdxl-turbo')).toBeNull()
    expect(card('sdxl-lightning')).not.toBeNull()
    expect(card('esrgan-4x')).not.toBeNull()

    await click(button('All'))
    expect(card('qwen-image')).not.toBeNull()
  })

  it('says so when a filter matches nothing', async () => {
    box.models = catalog([qwen()])
    mount()
    await click(button('Installed'))
    expect(text(byId('imp-empty')!)).toContain('No models match this filter.')
  })

  it('install click posts and shows progress with speed', async () => {
    box.models = catalog([lightning(), turbo({ state: 'none' })])
    const view = mount()

    expect(button('Install', card('sdxl-turbo'))).toBeTruthy()
    await click(button('Install', card('sdxl-turbo')))
    expect(calls).toContainEqual(['install', 'sdxl-turbo'])

    // The first poll shows how far it is, with no speed yet.
    box.jobs = [job({ bytes_downloaded: 1_000_000_000, bytes_total: 4_000_000_000 })]
    box.jobsAt = 1_000_000
    view.rerender()
    const prog = byId('imp-progress-sdxl-turbo')!
    expect(text(prog)).toContain('0.9 / 3.7 GB')
    expect(text(prog)).not.toContain('MB/s')
    expect(button('Cancel', card('sdxl-turbo'))).toBeTruthy()
    expect(button('Install', card('sdxl-turbo'))).toBeUndefined()

    // Two seconds later 1 GB more has landed: 500 MB/s, and the 2 GB left take 4 s at that rate.
    box.jobs = [job({ bytes_downloaded: 2_000_000_000, bytes_total: 4_000_000_000 })]
    box.jobsAt = 1_002_000
    view.rerender()
    expect(text(byId('imp-progress-sdxl-turbo')!)).toContain('1.9 / 3.7 GB')
    expect(text(byId('imp-progress-sdxl-turbo')!)).toContain('500.0 MB/s')
    expect(text(byId('imp-progress-sdxl-turbo')!)).toContain('4 s left')
    const bar = byId('imp-progress-sdxl-turbo')!.querySelector('[role="progressbar"]') as HTMLElement
    expect(bar.getAttribute('aria-valuenow')).toBe('50')
  })

  it('cancels a running download by model id', async () => {
    box.models = catalog([lightning(), turbo()])
    box.jobs = [job()]
    mount()

    await click(button('Cancel', card('sdxl-turbo')))
    expect(calls).toContainEqual(['cancel', 'sdxl-turbo'])
  })

  it('reads a partial model without a job as the GB still to download, never Resume', () => {
    box.models = catalog([lightning(), turbo({ state: 'partial', missing_gb: 4.2 })])
    mount()

    expect(button('Install · 4.2 GB to download', card('sdxl-turbo'))).toBeTruthy()
    expect(text(card('sdxl-turbo'))).not.toContain('Resume')
  })

  it('shows a failed job\'s error with Retry, which installs again', async () => {
    box.models = catalog([lightning(), turbo({ state: 'partial', missing_gb: 2 })])
    box.jobs = [job({ state: 'failed', error: 'sha256 mismatch for sdxl_vae.safetensors' })]
    mount()

    expect(text(byId('imp-job-error-sdxl-turbo')!)).toContain('sha256 mismatch for sdxl_vae.safetensors')
    await click(button('Retry', card('sdxl-turbo')))
    expect(calls).toContainEqual(['install', 'sdxl-turbo'])
  })

  it('507 shows server message', async () => {
    box.models = catalog([lightning(), turbo()])
    box.installFail = new ApiError(
      'not enough disk: sdxl-turbo needs 3.1 GB more, 1.2 GB free',
      'comfyui.insufficient_disk',
      507,
    )
    mount()

    await click(button('Install', card('sdxl-turbo')))
    const err = byId('imp-install-error-sdxl-turbo')!
    expect(err.getAttribute('role')).toBe('alert')
    expect(text(err)).toBe('not enough disk: sdxl-turbo needs 3.1 GB more, 1.2 GB free')

    // Another attempt clears it first.
    box.installFail = undefined
    await click(button('Install', card('sdxl-turbo')))
    expect(byId('imp-install-error-sdxl-turbo')).toBeNull()
  })

  it('models panel disables install when engine unavailable', () => {
    const REASON_NO_ENGINE = 'no ROCm lane on this host (ComfyUI needs /dev/kfd and a render node)'
    // Every model is uninstalled, so this would be the first run: with no engine it is not,
    // because nothing can be set up. `fit` is deliberately non-null: a non-ROCm GPU host has a
    // fit and still no engine.
    box.models = catalog([row({ recommended: true, fit: fit('wont_fit') }), turbo({ fit: fit('wont_fit') })], {
      engine: { available: false, reason: REASON_NO_ENGINE },
    })
    mount()

    // The reason is shown once.
    expect(text().split(REASON_NO_ENGINE).length - 1).toBe(1)
    // The catalog still lists, with every Install off.
    const installs = Array.from(document.querySelectorAll('button')).filter((b) =>
      /^Install( ·.*)?$/.test(b.textContent ?? ''),
    ) as HTMLButtonElement[]
    expect(installs.length).toBeGreaterThan(0)
    expect(installs.every((b) => b.disabled)).toBe(true)
    // No verdict anywhere, and never "Won't fit".
    expect(document.querySelector('[data-testid^="fit-chip-"]')).toBeNull()
    expect(document.querySelector('[data-testid^="fit-meter-"]')).toBeNull()
    expect(text()).not.toContain("Won't fit")
    // No setup block, and the filter that depends on the verdict is not offered.
    expect(text()).not.toContain('Set up image generation')
    expect(button('Fits this box')).toBeUndefined()
  })

  it('shows no chip when the row has no fit', () => {
    box.models = catalog([lightning({ fit: null }), turbo({ fit: null })])
    mount()
    expect(document.querySelector('[data-testid^="fit-chip-"]')).toBeNull()
    expect(text()).not.toContain("Won't fit")
  })

  it('remove confirm lists kept shared files', async () => {
    const f = (subdir: string, relpath: string, models: string[], size_bytes: number) => ({
      kind: subdir,
      subdir,
      relpath,
      size_bytes,
      models,
      symlink: false,
    })
    box.models = catalog([
      row({ id: 'qwen-image-lightning', label: 'Qwen-Image (4-step Lightning)', state: 'complete', missing_gb: 0, workflow: 'qwen-image-lightning.json' }),
      qwen({ state: 'complete', missing_gb: 0, fit: fit('fits') }),
      sd15({ state: 'partial', missing_gb: 1 }),
    ])
    box.files = {
      root: '/srv/models/comfyui/models',
      files: [
        f('loras', 'qwen_lightning_4step.safetensors', ['qwen-image-lightning'], 1_700_000_000),
        f('diffusion_models', 'qwen_image_fp8.safetensors', ['qwen-image-lightning', 'qwen-image'], 20_400_000_000),
        f('vae', 'qwen_vae.safetensors', ['qwen-image-lightning', 'qwen-image'], 254_000_000),
        f('checkpoints', 'other.safetensors', ['sd-1.5-pruned-emaonly'], 100),
      ],
    }
    mount()

    await click(button('Remove', card('qwen-image-lightning')))

    const doomed = byId('imp-remove-delete')!
    expect(text(doomed)).toContain('loras/qwen_lightning_4step.safetensors')
    expect(text(doomed)).toContain('1.58 GB')
    expect(text(doomed)).toContain('qwen-image-lightning.json')
    expect(text(doomed)).not.toContain('qwen_image_fp8')

    const kept = byId('imp-remove-kept')!
    expect(text(kept)).toContain('diffusion_models/qwen_image_fp8.safetensors')
    expect(text(kept)).toContain('vae/qwen_vae.safetensors')
    expect(text(kept)).toContain('Qwen-Image (20-step)')
    expect(text(kept)).not.toContain('qwen_lightning_4step')
    // Nothing of a model this one does not touch.
    expect(text(document.body)).not.toContain('other.safetensors')

    // Nothing is sent until the confirm.
    expect(calls.some(([name]) => name === 'remove')).toBe(false)
    await click(button('Remove model'))
    expect(calls).toContainEqual(['remove', { id: 'qwen-image-lightning' }])
    expect(byId('imp-remove-delete')).toBeNull() // closed
  })

  describe('the file listing', () => {
    const lastFilesEnabled = () => [...calls].reverse().find(([name]) => name === 'files-hook')?.[1]

    it('is not read until a removal is being confirmed', async () => {
      box.models = catalog([lightning()])
      mount()
      expect(lastFilesEnabled()).toBe(false)

      await click(button('Remove', card('sdxl-lightning')))
      expect(lastFilesEnabled()).toBe(true)

      await click(button('Cancel'))
      expect(lastFilesEnabled()).toBe(false)
    })

    it('holds the confirm while the files are being read', async () => {
      box.models = catalog([lightning()])
      box.filesState = 'loading'
      mount()

      await click(button('Remove', card('sdxl-lightning')))
      expect(text()).toContain('Reading the file list')
      expect((button('Remove model') as HTMLButtonElement).disabled).toBe(true)
    })

    it('says the list is unavailable when the read fails, and still lets the removal go ahead', async () => {
      box.models = catalog([lightning()])
      box.filesState = 'error'
      mount()

      await click(button('Remove', card('sdxl-lightning')))
      expect(text()).toContain('The file list could not be read')
      expect((button('Remove model') as HTMLButtonElement).disabled).toBe(false)
    })
  })

  it('keeps the remove confirm open and shows the server message when the removal fails', async () => {
    box.models = catalog([lightning()])
    box.removeFail = new ApiError('could not delete vae/x.safetensors', 'comfyui.remove_failed', 409)
    mount()

    await click(button('Remove', card('sdxl-lightning')))
    await click(button('Remove model'))
    expect(text(byId('imp-remove-error')!)).toContain('could not delete vae/x.safetensors')
    expect(byId('imp-remove-delete')).not.toBeNull()
  })

  it('set default calls capability apply with comfyui provider', async () => {
    box.models = catalog([lightning(), turbo({ state: 'complete', missing_gb: 0 })])
    box.selection = { model: 'sdxl-lightning', enabled: true, provider: 'comfyui' }
    mount()

    // The hook is the capabilities page's own, for the img slot, with the provider.
    expect(calls).toContainEqual(['selection-hook', ['img', 'img', { withProvider: true }]])
    // The current default has no button; another complete renderable model has.
    expect(button('Set as default', card('sdxl-lightning'))).toBeUndefined()
    await click(button('Set as default', card('sdxl-turbo')))
    expect(calls).toContainEqual([
      'apply',
      { slot: 'img', child: 'img', body: { model: 'sdxl-turbo', enabled: true, provider: 'comfyui' } },
    ])
  })

  it('offers Generate on a complete renderable card and Open in ComfyUI on an editor-only one', async () => {
    box.models = catalog([lightning(), turbo(), ltx({ state: 'complete', missing_gb: 0 }), ltxBf16()])
    mount()

    await click(button('Generate', card('sdxl-lightning')))
    expect(props.onGenerate).toHaveBeenCalledWith('sdxl-lightning')

    await click(button('Open in ComfyUI', card('ltx2-t2v')))
    expect(props.onOpenEditor).toHaveBeenCalledWith('ltx2-t2v')

    // Neither on a model that is not installed.
    expect(button('Generate', card('sdxl-turbo'))).toBeUndefined()
    expect(button('Set as default', card('sdxl-turbo'))).toBeUndefined()
    // Nor Generate / Set as default on an editor-only model.
    expect(button('Generate', card('ltx2-t2v'))).toBeUndefined()
    expect(button('Set as default', card('ltx2-t2v'))).toBeUndefined()
  })

  describe('first run', () => {
    const fresh = (over: Json = {}) =>
      catalog(
        [
          row({ recommended: true }),
          turbo(),
          sd15(),
          qwen(),
          ltx(),
        ],
        over,
      )

    it('first run block shows recommended with reason and cta', async () => {
      box.models = fresh()
      mount()

      const block = byId('imp-first-run')!
      expect(text(block)).toContain('Set up image generation')
      // The recommended model as the large card, with the reason from the server's figure and
      // the RAM source (the container's limit).
      const big = byId('imp-first-run-recommended')!
      expect(text(big)).toContain('SDXL Lightning (8-step)')
      expect(text(big)).toContain('chosen from 48 GB of RAM (container limit)')
      expect(text(byId('fit-chip-fr-sdxl-lightning')!)).toContain('Fits')
      const cta = button('Install recommended — SDXL Lightning (8-step) (~6.9 GB)', big)
      expect(cta).toBeTruthy()
      // The two smallest other renderable models; not the editor-only video model, not Qwen.
      const small = Array.from(block.querySelectorAll('[data-testid^="imp-first-run-small-"]')).map((e) =>
        e.getAttribute('data-testid'),
      )
      expect(small).toEqual([
        'imp-first-run-small-sd-1.5-pruned-emaonly',
        'imp-first-run-small-sdxl-turbo',
      ])
      // A link to the full list below.
      expect(button('Browse all models', block)).toBeTruthy()

      await click(cta)
      expect(calls).toContainEqual(['install', 'sdxl-lightning'])
    })

    it('says GPU memory when the GPU limited the pick, and the system when no container limits it', () => {
      box.models = fresh({ recommendation: { id: 'sdxl-lightning', pool_gb: 96, limited_by: 'gpu' } })
      mount()
      expect(text(byId('imp-first-run-recommended')!)).toContain('chosen from 96 GB of GPU memory')
    })

    it('names the system as the RAM source when the box has no container limit', () => {
      box.models = fresh({ recommendation: { id: 'sdxl-lightning', pool_gb: 105.5, limited_by: 'ram' } })
      box.memory = ramBudget('system')
      mount()
      expect(text(byId('imp-first-run-recommended')!)).toContain('chosen from 105.5 GB of RAM (system)')
    })

    it('gives no reason when the server has no recommendation', () => {
      box.models = fresh({ recommendation: null })
      mount()
      expect(text(byId('imp-first-run-recommended')!)).not.toContain('chosen from')
      expect(button(/^Install recommended/)).toBeTruthy()
    })

    // The picker and the fit check can disagree (#3076). A recommended model the server says won't
    // fit must not lead the block under an "Install recommended" button.
    it("leads with the largest model that fits when the recommended one won't fit", async () => {
      box.models = fresh({
        models: [
          row({ recommended: true, fit: fit('wont_fit') }),
          turbo(),
          sd15({ fit: fit('fits') }),
          qwen(),
          ltx(),
        ],
      })
      mount()

      expect(byId('imp-first-run-recommended')).toBeNull()
      const lead = byId('imp-first-run-alternative')!
      expect(text(lead)).toContain('SDXL Turbo')
      // The button does not call it recommended, and the reason is the recommended model's own.
      expect(button(/^Install recommended/)).toBeUndefined()
      expect(text(lead)).not.toContain('chosen from')
      expect(text(byId('imp-first-run')!)).toContain('does not fit this box')
      expect(byId('fit-chip-fr-sdxl-turbo')).not.toBeNull()

      // The recommended model is a small card with the server's reason; then the smallest other.
      const small = Array.from(byId('imp-first-run')!.querySelectorAll('[data-testid^="imp-first-run-small-"]'))
      expect(small.map((e) => e.getAttribute('data-testid'))).toEqual([
        'imp-first-run-small-sdxl-lightning',
        'imp-first-run-small-sd-1.5-pruned-emaonly',
      ])
      expect(text(small[0])).toContain('Recommended')
      expect(text(byId('imp-first-run-wont-fit')!)).toBe(`Won't fit · ${REASON}`)

      await click(button('Install SDXL Turbo (~6.5 GB)', lead))
      expect(calls).toContainEqual(['install', 'sdxl-turbo'])
      expect(calls).not.toContainEqual(['install', 'sdxl-lightning'])
    })

    it("keeps the recommended model in the lead when it won't fit and nothing else does", () => {
      box.models = fresh({
        models: [row({ recommended: true, fit: fit('wont_fit') }), turbo({ fit: fit('wont_fit') }), qwen()],
      })
      mount()
      expect(byId('imp-first-run-alternative')).toBeNull()
      expect(text(byId('imp-first-run-recommended')!)).toContain('SDXL Lightning (8-step)')
      expect(button(/^Install recommended/)).toBeTruthy()
    })

    it('names the GB still to fetch on the button for a partly installed recommended model', () => {
      box.models = fresh({
        models: [row({ recommended: true, state: 'partial', approx_gb: 6.9, missing_gb: 2.5 }), turbo(), sd15()],
      })
      mount()
      // Not the whole 6.9 GB: the files already on disk are not fetched again.
      expect(button('Install recommended — SDXL Lightning (8-step) (~2.5 GB)')).toBeTruthy()
      expect(button(/~6\.9 GB/)).toBeUndefined()
    })

    it('names the whole size on the button when nothing is on disk', () => {
      box.models = fresh()
      mount()
      expect(button('Install recommended — SDXL Lightning (8-step) (~6.9 GB)')).toBeTruthy()
    })

    it('is not shown once a renderable model is installed', () => {
      box.models = catalog([lightning(), turbo()])
      mount()
      expect(byId('imp-first-run')).toBeNull()
    })

    it('is not shown while an install runs', () => {
      box.models = fresh()
      box.jobs = [job({ model_id: 'sdxl-lightning' })]
      mount()
      expect(byId('imp-first-run')).toBeNull()
    })

    it('shows an install refusal in the block', async () => {
      box.models = fresh()
      box.installFail = new ApiError('not enough disk: 3.1 GB more needed', 'comfyui.insufficient_disk', 507)
      mount()
      await click(button(/^Install recommended/))
      expect(text(byId('imp-first-run-error')!)).toBe('not enough disk: 3.1 GB more needed')
    })
  })

  it('unreadable tree shows server message and fix commands', async () => {
    const message =
      'the hal0 user cannot read the ComfyUI models folder: set its group to the hal0 group and chmod 2775'
    box.models = undefined
    box.modelsError = new ApiError(message, 'comfyui.models_dir_not_writable', 409, {
      path: '/srv/models/comfyui/models',
    })
    mount()

    const panel = byId('imp-unreadable')!
    expect(text(panel)).toContain(message)
    expect(text(byId('imp-fix-commands')!)).toContain('sudo chgrp -R <hal0 group> /srv/models/comfyui/models')
    expect(text(byId('imp-fix-commands')!)).toContain('sudo chmod -R 2775 /srv/models/comfyui/models')
    // The panel is replaced, not annotated.
    expect(document.querySelector('[data-testid^="imp-card-"]')).toBeNull()
    expect(byId('imp-store')).toBeNull()

    await click(button('Retry'))
    expect(calls).toContainEqual(['models-refetch', null])
  })

  describe('Copy commands', () => {
    const unreadable = () => {
      box.models = undefined
      box.modelsError = new ApiError('the models folder is not readable', 'comfyui.models_dir_not_writable', 409, {
        path: '/srv/models/comfyui/models',
      })
    }
    afterEach(() => {
      vi.unstubAllGlobals()
      delete (window as unknown as { __hal0Toast?: unknown }).__hal0Toast
    })

    // hal0 is mostly served over plain http on a LAN, where there is no navigator.clipboard.
    it('copies on a plain-http page, where navigator.clipboard is undefined', async () => {
      unreadable()
      const toast = vi.fn()
      ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
      Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
      const exec = vi.fn(() => true)
      ;(document as unknown as { execCommand: unknown }).execCommand = exec
      mount()

      await click(button('Copy commands'))
      expect(exec).toHaveBeenCalledWith('copy')
      expect(toast).toHaveBeenCalledWith('Commands copied', 'ok')
    })

    it('uses the clipboard when there is one, and says so when nothing could copy', async () => {
      unreadable()
      const toast = vi.fn()
      ;(window as unknown as { __hal0Toast: unknown }).__hal0Toast = toast
      const writeText = vi.fn(async () => {})
      Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
      mount()
      await click(button('Copy commands'))
      expect(writeText).toHaveBeenCalledWith(
        'sudo chgrp -R <hal0 group> /srv/models/comfyui/models\nsudo chmod -R 2775 /srv/models/comfyui/models',
      )
      expect(toast).toHaveBeenLastCalledWith('Commands copied', 'ok')

      Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })
      ;(document as unknown as { execCommand: unknown }).execCommand = vi.fn(() => false)
      await click(button('Copy commands'))
      expect(toast).toHaveBeenLastCalledWith('Could not copy: select the commands and copy them by hand', 'err')
    })
  })

  it('shows the message and Retry, but no commands, when the server names no path', async () => {
    box.models = undefined
    box.modelsError = new ApiError('the models folder is not readable', 'comfyui.models_dir_not_writable', 409)
    mount()

    expect(text(byId('imp-unreadable')!)).toContain('the models folder is not readable')
    // No command is built from a made-up path, and nothing offers to copy one.
    expect(byId('imp-fix-commands')).toBeNull()
    expect(text()).not.toContain('sudo')
    expect(button('Copy commands')).toBeUndefined()
    await click(button('Retry'))
    expect(calls).toContainEqual(['models-refetch', null])
  })

  it('shows another load failure as a message with Retry', async () => {
    box.models = undefined
    box.modelsError = new ApiError('the API is down', 'system.unknown', 502)
    mount()

    expect(text(byId('imp-load-error')!)).toContain('the API is down')
    expect(byId('imp-fix-commands')).toBeNull()
    await click(button('Retry'))
    expect(calls).toContainEqual(['models-refetch', null])
  })

  it('keeps the catalog on screen when a refetch fails for another reason', () => {
    box.modelsError = new ApiError('the API is down', 'system.unknown', 502)
    mount()

    expect(card('sdxl-lightning')).not.toBeNull()
    expect(byId('imp-load-error')).toBeNull()
  })

  it('replaces a catalog that is on screen when the tree turns unreadable', () => {
    box.modelsError = new ApiError('cannot read it', 'comfyui.models_dir_not_writable', 409, { path: '/m' })
    mount()

    expect(byId('imp-unreadable')).not.toBeNull()
    expect(document.querySelector('[data-testid^="imp-card-"]')).toBeNull()
  })

  it('wont fit card suggests the recommended model', async () => {
    box.models = catalog([lightning(), turbo(), qwen()])
    mount()

    // Only the card that does not fit offers a replacement.
    const use = button('Use SDXL Lightning (8-step)', card('qwen-image'))
    expect(use).toBeTruthy()
    expect(button(/^Use /, card('sdxl-lightning'))).toBeUndefined()
    expect(button(/^Use /, card('sdxl-turbo'))).toBeUndefined()

    // It is installed, so using it opens it on the Generate tab.
    await click(use)
    expect(props.onGenerate).toHaveBeenCalledWith('sdxl-lightning')
  })

  it('names the download on the suggestion when the model is not installed, and installs it', async () => {
    box.models = catalog([lightning({ state: 'none', missing_gb: 6.9 }), turbo(), qwen()])
    mount()

    // Not "Use": the button says what it will do and how much it fetches.
    const suggest = byId('imp-suggest-qwen-image')!
    expect(button(/^Use /, suggest)).toBeUndefined()
    const install = button('Install SDXL Lightning (8-step) · 6.9 GB to download', suggest)
    expect(install).toBeTruthy()

    await click(install)
    expect(calls).toContainEqual(['install', 'sdxl-lightning'])
    expect(props.onGenerate).not.toHaveBeenCalled()
  })

  it('shows the suggestion disabled as Installing while its target downloads', async () => {
    box.models = catalog([lightning({ state: 'none', missing_gb: 6.9 }), turbo(), qwen()])
    box.jobs = [job({ model_id: 'sdxl-lightning', state: 'running' })]
    mount()

    const suggest = byId('imp-suggest-qwen-image')!
    // Not a second Install for a download that is already running.
    expect(button(/^Install /, suggest)).toBeUndefined()
    const busy = button('Installing SDXL Lightning (8-step)…', suggest)
    expect(busy).toBeTruthy()
    expect(busy!.disabled).toBe(true)
    await click(busy)
    expect(calls).not.toContainEqual(['install', 'sdxl-lightning'])

    // The job ends: the suggestion offers what it did before.
    box.jobs = [job({ model_id: 'sdxl-lightning', state: 'failed' })]
    document.body.innerHTML = ''
    mount()
    expect(button('Install SDXL Lightning (8-step) · 6.9 GB to download', byId('imp-suggest-qwen-image')!)).toBeTruthy()
  })

  it('reads the GB still to fetch for a partly installed suggestion, and the full size without it', () => {
    box.models = catalog([lightning({ state: 'partial', missing_gb: 2.5 }), turbo(), qwen()])
    mount()
    expect(button('Install SDXL Lightning (8-step) · 2.5 GB to download', byId('imp-suggest-qwen-image')!)).toBeTruthy()
    document.body.innerHTML = ''

    // No missing figure: the model's own size.
    box.models = catalog([lightning({ state: 'none', missing_gb: null, approx_gb: 6.9 }), turbo(), qwen()])
    mount()
    expect(button('Install SDXL Lightning (8-step) · 6.9 GB to download', byId('imp-suggest-qwen-image')!)).toBeTruthy()
  })

  it('suggests the largest renderable model that fits when the recommended one does not', () => {
    box.models = catalog([
      lightning({ state: 'none', missing_gb: 6.9, fit: fit('wont_fit') }),
      turbo({ fit: fit('fits'), approx_gb: 6.5 }),
      sd15({ fit: fit('fits') }),
      qwen(),
    ])
    mount()

    expect(button('Install SDXL Turbo · 6.5 GB to download', card('qwen-image'))).toBeTruthy()
    // The recommended model suggests the largest one that fits, not itself.
    expect(button('Install SDXL Turbo · 6.5 GB to download', card('sdxl-lightning'))).toBeTruthy()
  })

  it('shows the loading state before the catalog answers', () => {
    box.models = undefined
    mount()
    expect(text()).toContain('Loading models')
  })

  // The verdicts ride on the catalog, and they move with what the box has free: the catalog is
  // re-read while this tab is the one showing.
  it('re-reads the catalog while it is showing, and not while another tab is', () => {
    mount()
    expect(calls.filter(([n]) => n === 'models-hook').map(([, o]) => o)).toEqual([{ poll: true }])
    document.body.innerHTML = ''
    calls.length = 0
    props.visible = false
    mount()
    expect(calls.filter(([n]) => n === 'models-hook').map(([, o]) => o)).toEqual([{ poll: false }])
  })

  describe('focusing a model', () => {
    let scrolled: Element[]
    beforeEach(() => {
      scrolled = []
      ;(Element.prototype as unknown as { scrollIntoView: unknown }).scrollIntoView = function (this: Element) {
        scrolled.push(this)
      }
    })
    afterEach(() => {
      delete (Element.prototype as unknown as { scrollIntoView?: unknown }).scrollIntoView
    })

    // The 409 "not installed" link on the Generate tab names the model it wants.
    it('scrolls the asked-for card into view and focuses it', () => {
      props.focusModelId = 'qwen-image'
      props.focusSeq = 1
      mount()
      expect(document.activeElement).toBe(card('qwen-image'))
      expect(scrolled).toEqual([card('qwen-image')])
    })

    it('opens the More group of a full-precision variant that waits behind it', () => {
      box.models = catalog([lightning(), ltx(), ltxBf16()])
      const { rerender } = mount()
      expect(card('ltx2-t2v-bf16')).toBeNull()
      props.focusModelId = 'ltx2-t2v-bf16'
      props.focusSeq = 1
      rerender()
      expect(document.activeElement).toBe(card('ltx2-t2v-bf16'))
    })

    it('clears a filter that hides the card', async () => {
      const { rerender } = mount()
      await click(button('Installed'))
      expect(card('qwen-image')).toBeNull()
      props.focusModelId = 'qwen-image'
      props.focusSeq = 1
      rerender()
      expect(document.activeElement).toBe(card('qwen-image'))
    })

    it('does it again for a new request, even for the model asked for before', () => {
      props.focusModelId = 'qwen-image'
      props.focusSeq = 1
      const { rerender } = mount()
      ;(document.activeElement as HTMLElement).blur()
      expect(document.activeElement).not.toBe(card('qwen-image'))

      props.focusSeq = 2
      rerender()
      expect(document.activeElement).toBe(card('qwen-image'))

      props.focusModelId = 'sdxl-turbo'
      props.focusSeq = 3
      rerender()
      expect(document.activeElement).toBe(card('sdxl-turbo'))
    })

    it('waits for the catalog to arrive', () => {
      box.models = undefined
      props.focusModelId = 'qwen-image'
      props.focusSeq = 1
      const { rerender } = mount()
      expect(document.activeElement).toBe(document.body)
      box.models = catalog([lightning(), turbo(), qwen()])
      rerender()
      expect(document.activeElement).toBe(card('qwen-image'))
    })

    it('does nothing when the model is not in the catalog, and nothing without a request', () => {
      props.focusModelId = 'no-such-model'
      props.focusSeq = 1
      mount()
      expect(scrolled).toEqual([])
      document.body.innerHTML = ''
      props.focusModelId = undefined
      mount()
      expect(scrolled).toEqual([])
    })
  })
})

describe('FitMeter', () => {
  const render = (el: React.ReactElement) => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    act(() => root.render(<div className="comfy-page">{el}</div>))
    mounted.push({ root, host })
    return host
  }

  it('shows nothing without a fit or without the engine', () => {
    const none = render(<FitMeter fit={null} engineAvailable />)
    expect(none.textContent).toBe('')
    const off = render(<FitMeter fit={fit('wont_fit') as never} engineAvailable={false} />)
    expect(off.textContent).toBe('')
  })

  it('says which size it judged when that is not the size asked for', () => {
    const host = render(
      <FitMeter fit={fit('fits') as never} engineAvailable size="1024x1024" requestedSize="1030x1030" />,
    )
    expect(host.textContent).toContain('judged at 1024x1024')
  })

  it('says nothing about the size when it is the one asked for, or none was asked', () => {
    const same = render(
      <FitMeter fit={fit('fits') as never} engineAvailable size="1024x1024" requestedSize="1024x1024" />,
    )
    expect(same.textContent).not.toContain('judged at')
    const bare = render(<FitMeter fit={fit('fits') as never} engineAvailable size="1024x1024" />)
    expect(bare.textContent).not.toContain('judged at')
  })

  it('labels the GPU figure as free for a render, and RAM as plain free', () => {
    const host = render(
      <FitMeter
        fit={
          fit('fits', {
            need: { gpu_gb: 10.2, ram_gb: 12.4, gpu_estimated: false, ram_estimated: false },
            render_free_gb: 58.2,
            ram_free_gb: 52.9,
          }) as never
        }
        engineAvailable
        id="x"
      />,
    )
    const gpu = text(host.querySelector('[data-testid="fit-bar-gpu-x"]')!)
    const ram = text(host.querySelector('[data-testid="fit-bar-ram-x"]')!)
    expect(gpu).toContain('58.2 GB free for a render')
    expect(ram).toContain('52.9 GB free')
    expect(ram).not.toContain('for a render')
  })

  it('says the verdict is for one image when asked to', () => {
    const on = render(<FitMeter fit={fit('fits') as never} engineAvailable perImage />)
    expect(on.textContent).toContain('judged for one image')
    const off = render(<FitMeter fit={fit('fits') as never} engineAvailable />)
    expect(off.textContent).not.toContain('judged for one image')
  })

  it('draws the need as a share of what is free, capped at the whole bar', () => {
    const host = render(
      <FitMeter
        fit={
          fit('wont_fit', {
            need: { gpu_gb: 20, ram_gb: 60, gpu_estimated: false, ram_estimated: false },
            render_free_gb: 40,
            ram_free_gb: 45,
          }) as never
        }
        engineAvailable
      />,
    )
    const ghosts = Array.from(host.querySelectorAll('.fm-need')) as HTMLElement[]
    expect(ghosts.map((g) => g.style.width)).toEqual(['50%', '100%'])
  })
})
