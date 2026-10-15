// @vitest-environment happy-dom
//
// Profiles view — runtime surface (profile-system tasks U2/U3/U5).
//
// profiles.jsx is a window-globals dash module (bare `FormRow`/`useForm`/
// `FormDrawer`/`ImportDialog`/`Icons` identifiers resolve off `window`), so
// the globals are installed before the dynamic import — same pattern as
// runner-images-view.test.tsx / memoryOverviewV2.smoke.test.tsx.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
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
const { ProfileDrawer, ProfileCard, ImportModal } = await import('../profiles.jsx')

// system-info `backends` fixture — the Task 2 row shape the Runtime select,
// the card chips and the import preview all read.
const BACKENDS = {
  standard: {
    image: 'ghcr.io/hal0ai/hal0-runner-standard:0826',
    runtime_family: 'llama-server',
    device_class: 'gpu',
    backend: 'rocm',
    title: 'Standard',
    blurb: 'llama.cpp for AMD GPUs, on ROCm or Vulkan.',
    tier: 'default',
    supported_backends: ['rocm', 'vulkan'],
    state: 'installed',
  },
  qwen3tts: {
    image: 'ghcr.io/hal0ai/hal0-runner-qwen3tts:v2.3',
    runtime_family: 'llama-server',
    device_class: 'gpu',
    backend: 'rocm',
    title: 'Qwen3 TTS',
    blurb: 'Higher-quality multilingual text-to-speech.',
    supported_backends: ['rocm'],
    state: 'installed',
  },
  cuda: {
    image: 'ghcr.io/ggml-org/llama.cpp:0831',
    runtime_family: 'llama-server',
    device_class: 'gpu',
    backend: 'cuda',
    title: 'NVIDIA CUDA',
    blurb: 'llama.cpp for NVIDIA GPUs.',
    supported_backends: ['cuda'],
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
  { id: 'standard', image: 'ghcr.io/hal0ai/hal0-runner-standard', tag: '0826', available_tags: ['0826'] },
  { id: 'cuda', image: 'ghcr.io/ggml-org/llama.cpp', tag: '0831', available_tags: ['0831'] },
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
// compute_capable/vulkan_capable), translated by hw-cascade.js's shared
// hostHwFlags. Only an explicit `false` from a probed gpus[0] vetoes a lane;
// a payload with no gpus[0] reads as "unknown" and vetoes nothing. The
// default fixture is a dual-capable box; the veto and the two unknown shapes
// each get their own case below.
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
    expect(opts.join('|')).toMatch(/Qwen3 TTS/)
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
    expect(byValue.get('standard')).toBe('Standard · ROCm + Vulkan')
    expect(byValue.get('qwen3tts')).toBe('Qwen3 TTS · ROCm')
    expect(byValue.get('cuda')).toBe('NVIDIA CUDA · CUDA · not pulled')
    act(() => root.unmount())
  })

  it('consequence hint appears only once a runtime is chosen, and names its lane', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    expect(host.querySelector('[data-testid="pf-runtime-consequence"]')).toBeNull()
    pick(select(host), 'qwen3tts')
    const box = host.querySelector('[data-testid="pf-runtime-consequence"]') as HTMLElement
    expect(box).toBeTruthy()
    expect(box.textContent).toContain('Slots applying this profile move to the ROCm lane')
    // The runtime's own blurb rides along with the pick.
    expect(host.textContent).toContain('Higher-quality multilingual text-to-speech.')
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

  // #2186 — the API reads null as "leave the stored runtime unchanged", so the
  // Auto option has to travel as the '' clear sentinel or the save silently
  // keeps the old runtime and the card badge reappears.
  it('picking Auto on an edit sends the clear sentinel, never null', async () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'edit',
        source: { name: 'pinned', flags: '', runner: 'qwen3tts' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    const sel = select(host)
    expect(sel.value).toBe('qwen3tts')
    pick(sel, '')
    const btn = host.querySelector('[data-testid="pf-btn-submit"]') as HTMLButtonElement
    await act(async () => {
      btn.click()
    })
    expect(updateCalls).toHaveLength(1)
    const call = updateCalls[0] as { name: string; body: Record<string, unknown> }
    expect(call.name).toBe('pinned')
    expect(call.body.runner).toBe('')
    act(() => root.unmount())
  })

  it('a create with no runtime picked sends "" too (Auto, nothing to clear)', async () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'clone',
        source: { name: 'plain', flags: '', runner: '' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    expect(select(host).value).toBe('')
    const btn = host.querySelector('[data-testid="pf-btn-submit"]') as HTMLButtonElement
    await act(async () => {
      btn.click()
    })
    expect(createCalls).toHaveLength(1)
    expect((createCalls[0] as Record<string, unknown>).runner).toBe('')
    act(() => root.unmount())
  })

  it('renders the select disabled when forking a seed', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'clone',
        source: { name: 'chat', flags: '', seed: true, runner: 'standard' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    expect(select(host).disabled).toBe(true)
    act(() => root.unmount())
  })

  it('has no Quant or Advanced section any more (quant left the profile)', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'edit',
        source: { name: 'brain', flags: '' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    expect(host.querySelector('[data-testid="pf-advanced-toggle"]')).toBeNull()
    expect(host.querySelector('[data-testid="pf-input-quant"]')).toBeNull()
    act(() => root.unmount())
  })

  it('offers Pull now on a not-pulled runtime and pulls its catalogue row', () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    pick(select(host), 'cuda')
    const btn = host.querySelector('[data-testid="pf-runtime-pull"]') as HTMLButtonElement
    expect(btn).toBeTruthy()
    act(() => btn.click())
    expect(pullStarts).toEqual([['cuda', '0831']])
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

  // Parity with the slot drawer's hardware gate (hw-cascade.js hostHwFlags,
  // covered lane-by-lane in host-hw-flags.test.ts). Both drawers must answer
  // the same feasibility question about the same box; these two cases are the
  // ones the profile drawer used to get wrong with its own private copy.

  it('does not veto ROCm-only runtimes on a kfd-present box with no host rocminfo', () => {
    systemInfoBox.current = {
      backends: BACKENDS,
      hardware: { kfd_present: true, gpus: [{ compute_capable: false, vulkan_capable: false }] },
    }
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    const values = Array.from(select(host).querySelectorAll('option')).map((o) => o.value)
    expect(values).toContain('qwen3tts')
    expect(values).toContain('standard')
    // cuda is still an explicit `false`, so the cuda-only runtime stays hidden
    expect(values).not.toContain('cuda')
    act(() => root.unmount())
  })

  it('treats hardware-present-but-no-gpus[0] as unknown, not as a veto', () => {
    systemInfoBox.current = { backends: BACKENDS, hardware: {} }
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    const values = Array.from(select(host).querySelectorAll('option')).map((o) => o.value)
    expect(values).toEqual(['', 'standard', 'kokoro', 'cuda', 'qwen3tts'])
    act(() => root.unmount())
  })

  it('shows the pull in-flight state and never blocks Save on a failed pull', () => {
    pullBox.current = { state: 'running', inFlight: true, error: null, pct: 40 }
    const first = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    pick(select(first.host), 'cuda')
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
    pick(select(second.host), 'cuda')
    expect(second.host.querySelector('[data-testid="pf-runtime-pull-err"]')?.textContent).toContain(
      'registry unreachable',
    )
    const submit = second.host.querySelector('[data-testid="pf-btn-submit"]') as HTMLButtonElement
    expect(submit.disabled).toBe(false)
    act(() => second.root.unmount())
  })
})

// ── Task U3: runtime-aware profile cards ─────────────────────────────────────

const NOOP = { onEdit: () => {}, onClone: () => {}, onDelete: () => {}, onExport: () => {} }

function card(p: Record<string, unknown>) {
  return mount(React.createElement(ProfileCard, { p, index: 0, ...NOOP }))
}

describe('ProfileCard runtime facts (U3)', () => {
  it('a pinned dual-backend runtime renders one chip PER lane, plus state and blurb', () => {
    const { host, root } = card({
      name: 'coding', intent: 'Coder tune', runner: 'standard',
      runtime_family: 'llama-server', flags: '-fa on', ctx_size: 65536,
      enable_thinking: false, vision: 'off', mtp: 'off', used_by: ['agent'],
    })
    const chips = Array.from(
      host.querySelectorAll('[data-testid="pf-runner-badge-coding"] .pf-be'),
    ).map((c) => c.textContent)
    expect(chips).toEqual(['ROCm', 'Vulkan'])
    expect(host.querySelector('[data-testid="pf-runtime-state-coding"]')?.textContent).toContain(
      'installed',
    )
    expect(host.querySelector('[data-testid="pf-runtime-line-coding"]')?.textContent).toBe(
      'Standard — llama.cpp for AMD GPUs, on ROCm or Vulkan.',
    )
    // No layout regression: the existing rows and footer tags stay put.
    expect(host.textContent).toContain('-fa on')
    expect(host.textContent).toContain('used by 1')
    act(() => root.unmount())
  })

  it('an unpinned profile gets the muted AUTO chip, a "not pinned" state and plain words', () => {
    const { host, root } = card({ name: 'chat', intent: 'Generic chat', runtime_family: 'llama-server' })
    const badge = host.querySelector('[data-testid="pf-runner-badge-chat"]') as HTMLElement
    expect(Array.from(badge.querySelectorAll('.pf-be')).map((c) => c.textContent)).toEqual(['AUTO'])
    expect(badge.querySelector('.pf-be')?.getAttribute('style')).toBeNull()
    expect(host.querySelector('[data-testid="pf-runtime-state-chat"]')?.textContent).toBe('not pinned')
    expect(host.querySelector('[data-testid="pf-runtime-line-chat"]')?.textContent).toMatch(
      /Auto — runs on whatever runtime the slot already uses/,
    )
    act(() => root.unmount())
  })

  it('an unpinned singleton-engine profile also carries its runtime family', () => {
    const { host, root } = card({ name: 'comfyui', runtime_family: 'comfyui' })
    expect(
      Array.from(host.querySelectorAll('[data-testid="pf-runner-badge-comfyui"] .pf-be')).map(
        (c) => c.textContent,
      ),
    ).toEqual(['AUTO', 'comfyui'])
    act(() => root.unmount())
  })

  it('a runtime this box has no entry for renders "unknown", never an invented backend', () => {
    const { host, root } = card({ name: 'legacy', runner: 'rocm-dnse', runtime_family: 'llama-server' })
    expect(
      Array.from(host.querySelectorAll('[data-testid="pf-runner-badge-legacy"] .pf-be')).map(
        (c) => c.textContent,
      ),
    ).toEqual(['unknown'])
    act(() => root.unmount())
  })

  it('the not-pulled chip pulls the runtime image, spins while in flight, and reverts on failure', () => {
    const first = card({ name: 'cuda-tune', runner: 'cuda', runtime_family: 'llama-server' })
    const chip = first.host.querySelector('[data-testid="pf-runtime-state-cuda-tune"]') as HTMLButtonElement
    expect(chip.tagName).toBe('BUTTON')
    expect(chip.textContent).toContain('not pulled')
    act(() => chip.click())
    expect(pullStarts).toEqual([['cuda', '0831']])
    act(() => first.root.unmount())

    pullBox.current = { state: 'running', inFlight: true, error: null, pct: 12 }
    const busy = card({ name: 'cuda-tune', runner: 'cuda', runtime_family: 'llama-server' })
    const spinning = busy.host.querySelector(
      '[data-testid="pf-runtime-state-cuda-tune"]',
    ) as HTMLButtonElement
    expect(spinning.disabled).toBe(true)
    expect(spinning.textContent).toMatch(/pulling/i)
    act(() => busy.root.unmount())

    pullBox.current = {
      state: 'failed',
      inFlight: false,
      error: { code: 'runner_image.pull_failed', message: 'registry unreachable' },
      pct: null,
    }
    const failed = card({ name: 'cuda-tune', runner: 'cuda', runtime_family: 'llama-server' })
    const reverted = failed.host.querySelector(
      '[data-testid="pf-runtime-state-cuda-tune"]',
    ) as HTMLButtonElement
    expect(reverted.textContent).toContain('not pulled')
    expect(reverted.disabled).toBe(false)
    expect(reverted.title).toContain('registry unreachable')
    act(() => failed.root.unmount())
  })
})

// ── Task U5: import preview runtime line ─────────────────────────────────────

async function openImportWith(report: Record<string, unknown>) {
  importResult.current = report
  const m = mount(
    React.createElement(ImportModal, { existing: [], onClose: () => {}, onImported: () => {} }),
  )
  const input = m.host.querySelector('[data-testid="pf-import-file"]') as HTMLInputElement
  // ImportDialog only calls file.text() — a duck-typed stand-in avoids
  // happy-dom's read-only FileList.
  Object.defineProperty(input, 'files', {
    configurable: true,
    value: [{ text: async () => JSON.stringify({ kind: 'hal0.profile' }) }],
  })
  await act(async () => {
    input.dispatchEvent(new Event('change', { bubbles: true }))
    await Promise.resolve()
    await Promise.resolve()
    await Promise.resolve()
  })
  return m
}

function confirmBtn(host: HTMLElement) {
  return host.querySelector('[data-testid="pf-import-confirm"]') as HTMLButtonElement
}

// ── ADR-0029: the profile owns the whole tune ───────────────────────────────

const field = (host: HTMLElement, testid: string) =>
  host.querySelector(`[data-testid="${testid}"]`) as HTMLElement

function setValue(el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, value: string) {
  const proto =
    el instanceof window.HTMLSelectElement
      ? window.HTMLSelectElement.prototype
      : el instanceof window.HTMLTextAreaElement
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype
  const setter = Object.getOwnPropertyDescriptor(proto, 'value')!.set!
  act(() => {
    setter.call(el, value)
    el.dispatchEvent(new Event(el instanceof window.HTMLSelectElement ? 'change' : 'input', { bubbles: true }))
  })
}

async function submit(host: HTMLElement) {
  await act(async () => {
    field(host, 'pf-btn-submit').click()
  })
}

describe('ProfileDrawer tune fields (ADR-0029)', () => {
  const drawer = (mode: string, source?: Record<string, unknown>) =>
    mount(React.createElement(ProfileDrawer, { mode, source, onClose: () => {}, onSaved: () => {} }))

  it('a new profile defaults to context native, thinking auto, vision auto, MTP auto', () => {
    const { host, root } = drawer('create')
    expect((field(host, 'pf-input-ctx') as HTMLInputElement).value).toBe('')
    expect((field(host, 'pf-select-thinking') as HTMLSelectElement).value).toBe('auto')
    expect((field(host, 'pf-select-vision') as HTMLSelectElement).value).toBe('auto')
    expect((field(host, 'pf-select-mtp') as HTMLSelectElement).value).toBe('auto')
    act(() => root.unmount())
  })

  it('labels unset thinking as off unless the request asks (the server default)', () => {
    const { host, root } = drawer('create')
    const sel = field(host, 'pf-select-thinking') as HTMLSelectElement
    expect(sel.options[sel.selectedIndex].text).toBe('Default — off unless the request asks')
    expect(field(host, 'pf-drawer-preview')?.textContent).toContain('thinking default (off)')
    act(() => root.unmount())
  })

  it('prefills every tune field on edit', () => {
    const { host, root } = drawer('edit', {
      name: 'tuned', flags: '-fa on', ctx_size: 65536, enable_thinking: true, vision: 'off', mtp: 'off',
    })
    expect((field(host, 'pf-input-ctx') as HTMLInputElement).value).toBe('65536')
    expect((field(host, 'pf-select-thinking') as HTMLSelectElement).value).toBe('on')
    expect((field(host, 'pf-select-vision') as HTMLSelectElement).value).toBe('off')
    expect((field(host, 'pf-select-mtp') as HTMLSelectElement).value).toBe('off')
    act(() => root.unmount())
  })

  it('maps the fields onto the wire shape: ctx_size int, enable_thinking tri-state, vision/mtp auto|off', async () => {
    const { host, root } = drawer('create')
    setValue(field(host, 'pf-input-name') as HTMLInputElement, 'my-tune')
    setValue(field(host, 'pf-input-ctx') as HTMLInputElement, '32768')
    setValue(field(host, 'pf-select-thinking') as HTMLSelectElement, 'off')
    setValue(field(host, 'pf-select-vision') as HTMLSelectElement, 'off')
    setValue(field(host, 'pf-select-mtp') as HTMLSelectElement, 'off')
    setValue(field(host, 'pf-input-flags') as HTMLTextAreaElement, '-fa on -b 2048')
    await submit(host)
    expect(createCalls).toHaveLength(1)
    expect(createCalls[0]).toEqual({
      name: 'my-tune',
      flags: '-fa on -b 2048',
      ctx_size: 32768,
      enable_thinking: false,
      vision: 'off',
      mtp: 'off',
      intent: '',
      runner: '',
    })
    act(() => root.unmount())
  })

  it('never sends the removed fields (backend, device_class, quant, boolean mtp)', async () => {
    const { host, root } = drawer('create')
    setValue(field(host, 'pf-input-name') as HTMLInputElement, 'plain')
    await submit(host)
    const body = createCalls[0] as Record<string, unknown>
    for (const k of ['backend', 'device_class', 'quant']) expect(body).not.toHaveProperty(k)
    expect(typeof body.mtp).toBe('string')
    // An empty context on a create is simply omitted (native window).
    expect(body).not.toHaveProperty('ctx_size')
    act(() => root.unmount())
  })

  it('clearing the context on an edit sends ctx_size: null; thinking Auto sends null', async () => {
    const { host, root } = drawer('edit', {
      name: 'tuned', flags: '', ctx_size: 65536, enable_thinking: true, vision: 'auto', mtp: 'auto',
    })
    setValue(field(host, 'pf-input-ctx') as HTMLInputElement, '')
    setValue(field(host, 'pf-select-thinking') as HTMLSelectElement, 'auto')
    await submit(host)
    const call = updateCalls[0] as { name: string; body: Record<string, unknown> }
    expect(call.body.ctx_size).toBeNull()
    expect(call.body.enable_thinking).toBeNull()
    act(() => root.unmount())
  })

  it.each([
    ['64', 'between 128 and 16,777,216'],
    ['16777217', 'between 128 and 16,777,216'],
    ['32k', 'whole number'],
  ])('rejects ctx %s inline and does not save', async (value, msg) => {
    const { host, root } = drawer('create')
    setValue(field(host, 'pf-input-name') as HTMLInputElement, 'bad-ctx')
    setValue(field(host, 'pf-input-ctx') as HTMLInputElement, value)
    await submit(host)
    expect(createCalls).toHaveLength(0)
    expect(host.textContent).toContain(msg)
    act(() => root.unmount())
  })

  it('accepts the bounds 128 and 16777216', async () => {
    for (const v of ['128', '16777216']) {
      createCalls.length = 0
      const { host, root } = drawer('create')
      setValue(field(host, 'pf-input-name') as HTMLInputElement, 'edge')
      setValue(field(host, 'pf-input-ctx') as HTMLInputElement, v)
      await submit(host)
      expect((createCalls[0] as Record<string, unknown>).ctx_size).toBe(Number(v))
      act(() => root.unmount())
    }
  })

  it.each([
    ['--jinja', '--jinja'],
    ['--chat-template chatml', '--chat-template'],
    ['--chat-template-file /x/t.jinja', '--chat-template-file'],
    ['--mmproj /x/m.gguf', '--mmproj'],
    ['--no-mmproj', '--no-mmproj'],
    ['--spec-draft-device ROCm0', '--spec-draft-device'],
    ['--reasoning-format none', '--reasoning-format none'],
  ])('rejects the model-fact flag %s inline (profiles.model_fact_flag_denied)', async (flags, named) => {
    const { host, root } = drawer('create')
    setValue(field(host, 'pf-input-name') as HTMLInputElement, 'bad-flags')
    setValue(field(host, 'pf-input-flags') as HTMLTextAreaElement, `-fa on ${flags}`)
    await submit(host)
    expect(createCalls).toHaveLength(0)
    expect(host.textContent).toContain(`${named} can't be set on a profile`)
    act(() => root.unmount())
  })

  it.each([
    ['-m /etc/shadow', 'computed by hal0'],
    ['-m=/etc/shadow', 'computed by hal0'],
    ['--model=/etc/shadow', 'computed by hal0'],
    ['-a evil', 'computed by hal0'],
    ['--gpu-layers 99', 'belongs on the slot'],
    ['-hf org/model', 'hal0 manages all of that for the slot'],
    ['--api_key secret', 'hal0 manages all of that for the slot'],
    ['-ag', 'hal0 manages all of that for the slot'],
    ['/etc/shadow', 'has no flag in front of it'],
    ['--temp=0.7 /etc/shadow', 'has no flag in front of it'],
    ['-mu https://example.com/model.gguf', 'hal0 manages all of that for the slot'],
    ['--gpt-oss-20b-default', 'hal0 manages all of that for the slot'],
    ['--metrics /etc/shadow', 'has no flag in front of it'],
    ['--reasoning_format=NONE', "can't be set on a profile"],
  ])('rejects %s inline and does not save (every llama.cpp spelling)', async (flags, msg) => {
    const { host, root } = drawer('create')
    setValue(field(host, 'pf-input-name') as HTMLInputElement, 'bad-spelling')
    setValue(field(host, 'pf-input-flags') as HTMLTextAreaElement, `-fa on ${flags}`)
    await submit(host)
    expect(createCalls).toHaveLength(0)
    expect(host.textContent).toContain(msg)
    act(() => root.unmount())
  })

  it('still rejects hardware and managed flags, and allows a reasoning format other than none', async () => {
    const { host, root } = drawer('create')
    setValue(field(host, 'pf-input-name') as HTMLInputElement, 'hw')
    setValue(field(host, 'pf-input-flags') as HTMLTextAreaElement, '-ngl 99')
    await submit(host)
    expect(createCalls).toHaveLength(0)
    expect(host.textContent).toContain('belongs on the slot')
    setValue(field(host, 'pf-input-flags') as HTMLTextAreaElement, '--ctx-size 4096')
    await submit(host)
    expect(createCalls).toHaveLength(0)
    expect(host.textContent).toContain('computed by hal0')
    setValue(field(host, 'pf-input-flags') as HTMLTextAreaElement, '--reasoning-format deepseek')
    await submit(host)
    expect(createCalls).toHaveLength(1)
    act(() => root.unmount())
  })

  it('a stored hardware flag is refused on save too — no grandfathering', async () => {
    const { host, root } = drawer('edit', { name: 'old', flags: '-fa on --threads 8' })
    await submit(host)
    expect(updateCalls).toHaveLength(0)
    expect(host.textContent).toContain('belongs on the slot')
    act(() => root.unmount())
  })

  it('a stored model-fact flag is refused on save too — no grandfathering', async () => {
    const { host, root } = drawer('edit', { name: 'old', flags: '-fa on --jinja' })
    setValue(field(host, 'pf-input-ctx') as HTMLInputElement, '8192')
    await submit(host)
    expect(updateCalls).toHaveLength(0)
    expect(host.textContent).toContain("--jinja can't be set on a profile")
    // Removing the flag releases the save.
    setValue(field(host, 'pf-input-flags') as HTMLTextAreaElement, '-fa on')
    await submit(host)
    expect(updateCalls).toHaveLength(1)
    act(() => root.unmount())
  })

  it('reports the saved name to onSaved so the slot drawer can point at a fork', async () => {
    const saved: unknown[] = []
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'clone',
        source: { name: 'chat', flags: '-fa on', seed: true },
        onClose: () => {},
        onSaved: (n: unknown) => saved.push(n),
      }),
    )
    await submit(host)
    expect(saved).toEqual(['chat-custom'])
    act(() => root.unmount())
  })
})

describe('ProfileCard tune rows (ADR-0029)', () => {
  it('shows ctx, thinking, vision, MTP, runner and flags', () => {
    const { host, root } = card({
      name: 'tuned', flags: '-fa on', resolved_flags: '-fa on', ctx_size: 65536,
      enable_thinking: false, vision: 'off', mtp: 'off', runner: 'standard',
    })
    const rows = (host.querySelector('[data-testid="pf-tune-tuned"]') as HTMLElement).textContent || ''
    expect(rows).toContain('ctx64K')
    expect(rows).toContain('thinkingoff')
    expect(rows).toContain('visionoff')
    expect(rows).toContain('mtpoff')
    expect(rows).toContain('runnerStandard')
    expect(rows).toContain('-fa on')
    act(() => root.unmount())
  })

  it('a profile with no ctx_size says it defers to the model, unset thinking reads default (off), other auto fields read auto', () => {
    const { host, root } = card({ name: 'plain', flags: '', ctx_size: null, enable_thinking: null, vision: 'auto', mtp: 'auto' })
    const rows = (host.querySelector('[data-testid="pf-tune-plain"]') as HTMLElement).textContent || ''
    expect(rows).toContain('ctxmodel native')
    expect(rows).toContain('thinkingdefault (off)')
    expect(rows).toContain('runnerauto')
    act(() => root.unmount())
  })
})

describe('ImportModal runtime preview (U5)', () => {
  it('names the envelope runtime by title, with its lane chip and family', async () => {
    const { host, root } = await openImportWith({
      dry_run: true, valid: true, checksum_ok: true, name: 'cuda-tune', schema_version: 1,
      collides: false, runner: 'cuda', runner_stripped: false, runtime_family: 'llama-server',
    })
    const line = host.querySelector('[data-testid="pf-import-runtime"]') as HTMLElement
    expect(line.textContent).toContain('NVIDIA CUDA')
    expect(Array.from(line.querySelectorAll('.pf-be')).map((c) => c.textContent)).toEqual(['CUDA'])
    expect(line.textContent).toContain('family: llama-server')
    expect(host.querySelector('[data-testid="pf-import-runtime-warn"]')).toBeNull()
    expect(confirmBtn(host).textContent).toBe('Import')
    act(() => root.unmount())
  })

  it('says Auto for an envelope that pins no runtime', async () => {
    const { host, root } = await openImportWith({
      dry_run: true, valid: true, checksum_ok: true, name: 'chatty', schema_version: 1,
      collides: false, runner: null, runner_stripped: false, runtime_family: 'llama-server',
    })
    const line = host.querySelector('[data-testid="pf-import-runtime"]') as HTMLElement
    expect(line.textContent).toContain('Runtime: Auto')
    expect(Array.from(line.querySelectorAll('.pf-be')).map((c) => c.textContent)).toEqual(['AUTO'])
    act(() => root.unmount())
  })

  it('warns (never blocks) on a runtime this box lacks, and says what Import will do', async () => {
    const { host, root } = await openImportWith({
      dry_run: true, valid: true, checksum_ok: true, name: 'ghost-next-tune', schema_version: 1,
      collides: false, runner: 'ghost-next', runner_stripped: true, runtime_family: 'llama-server',
    })
    const line = host.querySelector('[data-testid="pf-import-runtime"]') as HTMLElement
    expect(line.textContent).toContain('ghost-next')
    expect(line.textContent).toContain('Auto')
    const warn = host.querySelector('[data-testid="pf-import-runtime-warn"]') as HTMLElement
    expect(warn.textContent).toContain('not available on this box')
    expect(warn.textContent).toContain('imports with runtime Auto')
    const btn = confirmBtn(host)
    expect(btn.textContent).toBe('Import as Auto')
    expect(btn.disabled).toBe(false)
    act(() => root.unmount())
  })
})

// ── #2653: quarantined row ───────────────────────────────────────────────────

describe('ProfileCard quarantined row (#2653)', () => {
  it('renders the error and a delete button without any tuning values', () => {
    const onDelete = vi.fn()
    const { host } = mount(
      React.createElement(ProfileCard, {
        p: { name: 'broken', quarantined: true, error: 'not_a_field: Extra inputs are not permitted', seed: false, used_by: [] },
        index: 0,
        onEdit: vi.fn(),
        onClone: vi.fn(),
        onDelete,
        onExport: vi.fn(),
      }),
    )
    expect(host.querySelector('[data-testid="pf-quarantined-error-broken"]')?.textContent).toContain(
      'Extra inputs are not permitted',
    )
    expect(host.querySelector('[data-testid="pf-btn-edit-broken"]')).toBeNull()
    const del = host.querySelector('[data-testid="pf-btn-delete-broken"]') as HTMLButtonElement
    act(() => del.click())
    expect(onDelete).toHaveBeenCalledTimes(1)
  })
})
