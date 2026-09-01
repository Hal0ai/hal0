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
const { ProfileDrawer, ProfileCard, ImportModal } = await import('../profiles.jsx')

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

  // #2186 — the API reads null as "leave the stored runtime unchanged", so the
  // Auto option has to travel as the '' clear sentinel or the save silently
  // keeps the old runtime and the card badge reappears.
  it('picking Auto on an edit sends the clear sentinel, never null', async () => {
    const { host, root } = mount(
      React.createElement(ProfileDrawer, {
        mode: 'edit',
        source: { name: 'pinned', flags: '', runner: 'promptforge' },
        onClose: () => {},
        onSaved: () => {},
      }),
    )
    const sel = select(host)
    expect(sel.value).toBe('promptforge')
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
    expect(values).toContain('promptforge')
    expect(values).toContain('rocmfpx')
    // vulkan is still an explicit `false`, so the vulkan-only runtime stays hidden
    expect(values).not.toContain('strix')
    act(() => root.unmount())
  })

  it('treats hardware-present-but-no-gpus[0] as unknown, not as a veto', () => {
    systemInfoBox.current = { backends: BACKENDS, hardware: {} }
    const { host, root } = mount(
      React.createElement(ProfileDrawer, { mode: 'create', source: undefined, onClose: () => {}, onSaved: () => {} }),
    )
    const values = Array.from(select(host).querySelectorAll('option')).map((o) => o.value)
    expect(values).toEqual(['', 'rocmfpx', 'kokoro', 'promptforge', 'strix'])
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

// ── Task U3: runtime-aware profile cards ─────────────────────────────────────

const NOOP = { onEdit: () => {}, onClone: () => {}, onDelete: () => {}, onExport: () => {} }

function card(p: Record<string, unknown>) {
  return mount(React.createElement(ProfileCard, { p, index: 0, ...NOOP }))
}

describe('ProfileCard runtime facts (U3)', () => {
  it('a pinned dual-backend runtime renders one chip PER lane, plus state and blurb', () => {
    const { host, root } = card({
      name: 'coding', intent: 'Coder tune', runner: 'rocmfpx',
      runtime_family: 'llama-server', quant: 'Q6_K', flags: '--jinja', used_by: ['agent'],
    })
    const chips = Array.from(
      host.querySelectorAll('[data-testid="pf-runner-badge-coding"] .pf-be'),
    ).map((c) => c.textContent)
    expect(chips).toEqual(['ROCm', 'Vulkan'])
    expect(host.querySelector('[data-testid="pf-runtime-state-coding"]')?.textContent).toContain(
      'installed',
    )
    expect(host.querySelector('[data-testid="pf-runtime-line-coding"]')?.textContent).toBe(
      'Standard — Runs every model type, incl. FPX quants.',
    )
    // No layout regression: the existing rows and footer tags stay put.
    expect(host.textContent).toContain('Q6_K')
    expect(host.textContent).toContain('--jinja')
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
    const first = card({ name: 'strixy', runner: 'strix', runtime_family: 'llama-server' })
    const chip = first.host.querySelector('[data-testid="pf-runtime-state-strixy"]') as HTMLButtonElement
    expect(chip.tagName).toBe('BUTTON')
    expect(chip.textContent).toContain('not pulled')
    act(() => chip.click())
    expect(pullStarts).toEqual([['strix-vulkan', '0831']])
    act(() => first.root.unmount())

    pullBox.current = { state: 'running', inFlight: true, error: null, pct: 12 }
    const busy = card({ name: 'strixy', runner: 'strix', runtime_family: 'llama-server' })
    const spinning = busy.host.querySelector(
      '[data-testid="pf-runtime-state-strixy"]',
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
    const failed = card({ name: 'strixy', runner: 'strix', runtime_family: 'llama-server' })
    const reverted = failed.host.querySelector(
      '[data-testid="pf-runtime-state-strixy"]',
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

describe('ImportModal runtime preview (U5)', () => {
  it('names the envelope runtime by title, with its lane chip and family', async () => {
    const { host, root } = await openImportWith({
      dry_run: true, valid: true, checksum_ok: true, name: 'strixy', schema_version: 1,
      collides: false, runner: 'strix', runner_stripped: false, runtime_family: 'llama-server',
    })
    const line = host.querySelector('[data-testid="pf-import-runtime"]') as HTMLElement
    expect(line.textContent).toContain('Strix')
    expect(Array.from(line.querySelectorAll('.pf-be')).map((c) => c.textContent)).toEqual(['Vulkan'])
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
      dry_run: true, valid: true, checksum_ok: true, name: 'strix-next-tune', schema_version: 1,
      collides: false, runner: 'strix-next', runner_stripped: true, runtime_family: 'llama-server',
    })
    const line = host.querySelector('[data-testid="pf-import-runtime"]') as HTMLElement
    expect(line.textContent).toContain('strix-next')
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
