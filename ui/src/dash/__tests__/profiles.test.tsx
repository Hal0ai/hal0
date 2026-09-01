// @vitest-environment happy-dom
//
// Profiles view — runtime surface (profile-system tasks U2/U3/U5).
//
// profiles.jsx is a window-globals dash module (bare `FormRow`/`useForm`/
// `FormDrawer`/`ImportDialog`/`Icons` identifiers resolve off `window`), so
// the globals are installed before the dynamic import — same pattern as
// runner-images-view.test.tsx / memoryOverviewV2.smoke.test.tsx.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const {
  profilesBox,
  systemInfoBox,
  runnerImagesBox,
  pullBox,
  pullStarts,
  createCalls,
  updateCalls,
  importCalls,
  importResult,
} = vi.hoisted(() => ({
  profilesBox: { current: [] as Record<string, unknown>[] },
  systemInfoBox: { current: null as Record<string, unknown> | null },
  runnerImagesBox: { current: [] as Record<string, unknown>[] },
  // Mutable snapshot the mocked useRunnerImagePullJob returns — lets a test
  // render the in-flight / failed states without an EventSource.
  pullBox: {
    current: {
      state: 'idle',
      inFlight: false,
      error: null as { code: string; message: string } | null,
      pct: null as number | null,
    },
  },
  pullStarts: [] as Array<[string, string | undefined]>,
  createCalls: [] as unknown[],
  updateCalls: [] as unknown[],
  importCalls: [] as unknown[],
  importResult: { current: {} as Record<string, unknown> },
}))

vi.mock('@/api/hooks/useProfiles', () => ({
  useProfiles: () => ({ data: profilesBox.current, isLoading: false, isError: false, error: null }),
  useProfileCreate: () => ({
    mutateAsync: async (b: unknown) => {
      createCalls.push(b)
      return b
    },
  }),
  useProfileUpdate: () => ({
    mutateAsync: async (b: unknown) => {
      updateCalls.push(b)
      return b
    },
  }),
  useProfileDelete: () => ({ mutateAsync: async () => undefined }),
  useProfileExport: () => ({ mutateAsync: async () => ({}) }),
  useProfileImport: () => ({
    mutateAsync: async (p: unknown) => {
      importCalls.push(p)
      return importResult.current
    },
  }),
}))

// useMetaEnums resolves the static device taxonomy over the live payload; the
// fallback is all this suite needs, and mocking it keeps the run off the
// network (the real hook fires a fetch at localhost).
vi.mock('@/api/hooks/useMeta', async () => {
  const actual = await vi.importActual<typeof import('@/api/hooks/useMeta')>('@/api/hooks/useMeta')
  return { ...actual, useMeta: () => ({ data: undefined }), useMetaEnums: () => actual.resolveMetaEnums(undefined) }
})

vi.mock('@/api/hooks/useRuntimes', () => ({
  useSystemInfo: () => ({ data: systemInfoBox.current, isLoading: false, isError: false }),
}))

vi.mock('@/api/hooks/useRunnerImages', () => ({
  useRunnerImages: () => ({ data: { images: runnerImagesBox.current, families: [] } }),
  useRunnerImagePullJob: () => ({
    ...pullBox.current,
    start: (id: string, tag?: string) => {
      pullStarts.push([id, tag])
      return Promise.resolve({})
    },
    cancel: async () => undefined,
    reset: () => {},
    reattach: async () => undefined,
  }),
}))

await import('../primitives.jsx')
const { ProfileDrawer } = await import('../profiles.jsx')

// system-info `backends` fixture — the Task 2 row shape the Runtime select,
// the card chips and the import preview all read.
const BACKENDS = {
  rocmfpx: {
    image: 'ghcr.io/hal0ai/hal0-combined:0826',
    runtime_family: 'llama-server',
    device_class: 'gpu',
    backend: 'rocm',
    title: 'Standard',
    blurb: 'Runs every model type, incl. FPX quants.',
    is_default: true,
    supported_backends: ['rocm', 'vulkan'],
    state: 'installed',
  },
  promptforge: {
    image: 'ghcr.io/hal0ai/hal0-promptforge:v2.3',
    runtime_family: 'llama-server',
    device_class: 'gpu',
    backend: 'rocm',
    title: 'PromptForge',
    blurb: 'Accelerated PromptForge model distributions.',
    supported_backends: ['rocm'],
    state: 'installed',
  },
  strix: {
    image: 'ghcr.io/hal0ai/hal0-strix-vulkan:0831',
    runtime_family: 'llama-server',
    device_class: 'gpu',
    backend: 'vulkan',
    title: 'Strix',
    blurb: 'Qwen4 experimental + MTP speculative decode.',
    supported_backends: ['vulkan'],
    state: 'installable',
  },
  kokoro: {
    image: 'ghcr.io/hal0ai/hal0-kokoro:v1',
    runtime_family: 'kokoro',
    device_class: 'cpu',
    backend: 'cpu',
    title: 'Kokoro',
    blurb: 'Kokoro ONNX voice server · CPU only.',
    supported_backends: ['cpu'],
    state: 'installed',
  },
}

const IMAGE_ROWS = [
  { id: 'rocmfpx-combined', image: 'ghcr.io/hal0ai/hal0-combined', tag: '0826', available_tags: ['0826'] },
  { id: 'strix-vulkan', image: 'ghcr.io/hal0ai/hal0-strix-vulkan', tag: '0831', available_tags: ['0831'] },
]

function mount(el: React.ReactElement) {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, el))
  })
  return { host, root }
}

// hw flags ride on the RAW system-info `hardware` payload (gpus[0]
// compute_capable/vulkan_capable). A PRESENT-but-empty hardware object reads
// as "no GPU", which vetoes every GPU runtime — so the default fixture is a
// dual-capable box and the veto gets its own case below.
const GPU_HARDWARE = { gpus: [{ compute_capable: true, vulkan_capable: true }] }

beforeEach(() => {
  systemInfoBox.current = { backends: BACKENDS, hardware: GPU_HARDWARE }
  runnerImagesBox.current = IMAGE_ROWS
  pullBox.current = { state: 'idle', inFlight: false, error: null, pct: null }
  pullStarts.length = 0
  createCalls.length = 0
  updateCalls.length = 0
  importCalls.length = 0
  importResult.current = {}
})

afterEach(() => {
  document.body.innerHTML = ''
})

function select(host: HTMLElement) {
  return host.querySelector('[data-testid="profile-runner"]') as HTMLSelectElement
}

function pick(sel: HTMLSelectElement, value: string) {
  act(() => {
    sel.value = value
    sel.dispatchEvent(new Event('change', { bubbles: true }))
  })
}

// ── Task U2: drawer runtime select ───────────────────────────────────────────

describe('ProfileDrawer runtime select (U2)', () => {
  it('defaults to Auto on create and lists every registry runtime', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    const sel = select(host)
    expect(sel).toBeTruthy()
    expect(sel.value).toBe('')
    const opts = Array.from(sel.querySelectorAll('option')).map((o) => o.textContent || '')
    expect(opts[0]).toMatch(/Auto/)
    expect(opts.join('|')).toMatch(/Standard/)
    expect(opts.join('|')).toMatch(/PromptForge/)
    expect(opts.join('|')).toMatch(/Kokoro/)
    act(() => root.unmount())
  })

  it('option anatomy: title · lane(s) · not-pulled state suffix', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    const byValue = new Map(
      Array.from(select(host).querySelectorAll('option')).map((o) => [o.value, o.textContent || '']),
    )
    expect(byValue.get('rocmfpx')).toBe('Standard · ROCm + Vulkan')
    expect(byValue.get('promptforge')).toBe('PromptForge · ROCm')
    expect(byValue.get('strix')).toBe('Strix · Vulkan · not pulled')
    act(() => root.unmount())
  })

  it('consequence hint appears only once a runtime is chosen, and names its lane', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    expect(host.querySelector('[data-testid="pf-runtime-consequence"]')).toBeNull()
    pick(select(host), 'promptforge')
    const box = host.querySelector('[data-testid="pf-runtime-consequence"]') as HTMLElement
    expect(box).toBeTruthy()
    expect(box.textContent).toContain('Slots applying this profile move to the ROCm lane')
    // The runtime's own blurb rides along with the pick.
    expect(host.textContent).toContain('Accelerated PromptForge model distributions.')
    act(() => root.unmount())
  })

  it('keeps an out-of-vocab stored runtime selectable and says it is unknown', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'edit',
        source: { name: 'legacy', flags: '', runner: 'rocm-dnse' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    const sel = select(host)
    expect(sel.value).toBe('rocm-dnse')
    const own = Array.from(sel.querySelectorAll('option')).find((o) => o.value === 'rocm-dnse')
    expect(own?.textContent).toMatch(/not in this box's registry/)
    expect(host.querySelector('[data-testid="pf-runtime-unknown"]')?.textContent).toMatch(
      /no entry for this runtime/,
    )
    act(() => root.unmount())
  })

  it('renders the select disabled when forking a seed', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'clone',
        source: { name: 'chat', flags: '', seed: true, runner: 'rocmfpx' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    expect(select(host).disabled).toBe(true)
    act(() => root.unmount())
  })

  it('demotes Quant to the Advanced disclosure, prefilled on edit', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'edit',
        source: { name: 'brain', flags: '', quant: 'Q8_0' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    expect(host.querySelector('[data-testid="pf-input-quant"]')).toBeNull()
    const toggle = host.querySelector('[data-testid="pf-advanced-toggle"]') as HTMLButtonElement
    expect(toggle).toBeTruthy()
    act(() => toggle.click())
    const quant = host.querySelector('[data-testid="pf-input-quant"]') as HTMLInputElement
    expect(quant).toBeTruthy()
    expect(quant.value).toBe('Q8_0')
    act(() => root.unmount())
  })

  it('offers Pull now on a not-pulled runtime and pulls its catalogue row', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    pick(select(host), 'strix')
    const btn = host.querySelector('[data-testid="pf-runtime-pull"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    act(() => btn.click())
    expect(pullStarts).toEqual([['strix-vulkan', '0831']])
    act(() => root.unmount())
  })

  it('hides runtimes whose every lane is infeasible on this box', () => {
    systemInfoBox.current = {
      backends: BACKENDS,
      hardware: { gpus: [{ compute_capable: false, vulkan_capable: false }] },
    }
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    const values = Array.from(select(host).querySelectorAll('option')).map((o) => o.value)
    expect(values).toEqual(['', 'kokoro'])
    act(() => root.unmount())
  })

  it('shows the pull in-flight state and never blocks Save on a failed pull', () => {
    pullBox.current = { state: 'running', inFlight: true, error: null, pct: 40 }
    const first = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    pick(select(first.host), 'strix')
    const busy = first.host.querySelector('[data-testid="pf-runtime-pull"]') as HTMLButtonElement
    expect(busy.disabled).toBe(true)
    expect(busy.textContent).toMatch(/Pulling/)
    act(() => first.root.unmount())

    pullBox.current = {
      state: 'failed',
      inFlight: false,
      error: { code: 'runner_image.pull_failed', message: 'registry unreachable' },
      pct: null,
    }
    const second = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    pick(select(second.host), 'strix')
    expect(second.host.querySelector('[data-testid="pf-runtime-pull-err"]')?.textContent).toContain(
      'registry unreachable',
    )
    const submit = second.host.querySelector('[data-testid="pf-btn-submit"]') as HTMLButtonElement
    expect(submit.disabled).toBe(false)
    act(() => second.root.unmount())
  })
})
