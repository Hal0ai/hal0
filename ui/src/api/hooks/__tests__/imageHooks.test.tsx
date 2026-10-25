// @vitest-environment happy-dom
//
// Image Gen hooks: the arbiter, the install jobs, install / remove / cancel, the fit check and
// the polling cadences. The URL and cache wiring is pinned here; the panels mock these hooks.
//
//   useArbiter()            -> GET    /api/comfyui/arbiter  (the whole envelope)
//   useImageJobs()          -> GET    /api/comfyui/jobs     ({jobs} unwrapped)
//   useImageModelInstall()  -> POST   /api/comfyui/models/{id}/install
//   useImageModelRemove()   -> DELETE /api/comfyui/models/{id}
//   useImageJobCancel()     -> POST   /api/comfyui/jobs/{model_id}/cancel
//   useModelFit(id, size)   -> GET    /api/comfyui/models/{id}/fit?size=
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiDelete: vi.fn(), apiPost: vi.fn() }
})

const client = await import('@/api/client')
const hooks = await import('@/api/hooks/useComfyui')
const { ENDPOINTS } = await import('@/api/endpoints')

function mountHook<T>(useHook: () => T, qc: QueryClient) {
  let latest!: T
  function Probe() {
    latest = useHook()
    return null
  }
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, React.createElement(Probe)))
  })
  return { root, get: () => latest }
}

async function flush() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
}

const newClient = () => new QueryClient({ defaultOptions: { queries: { retry: false } } })

function job(over: Partial<import('@/api/hooks/useComfyui').ImageJob> = {}): import('@/api/hooks/useComfyui').ImageJob {
  return {
    id: 'j1',
    model_id: 'sdxl',
    state: 'running',
    bytes_downloaded: 10,
    bytes_total: 100,
    started_at: 1,
    finished_at: null,
    error: null,
    error_code: null,
    sha256: null,
    path: null,
    files: [],
    ...over,
  }
}

function arbiterBlock(over: Partial<import('@/api/hooks/useComfyui').ArbiterState> = {}) {
  return {
    mode: 'llm',
    pinned: false,
    saved_llm_slots: [],
    switching: false,
    image_jobs: 0,
    phase: null,
    restore_at: null,
    handoff_until: null,
    drain_in_flight: 0,
    unload_set: [],
    llm_waiters: { count: 0, oldest_s: null },
    ...over,
  } as import('@/api/hooks/useComfyui').ArbiterState
}

describe('image endpoints', () => {
  it('names the routes, with the model id encoded', () => {
    expect(ENDPOINTS.comfyuiArbiter).toBe('/api/comfyui/arbiter')
    expect(ENDPOINTS.comfyuiJobs).toBe('/api/comfyui/jobs')
    expect(ENDPOINTS.v1ImagesGenerations).toBe('/v1/images/generations')
    expect(ENDPOINTS.comfyuiModel('sdxl')).toBe('/api/comfyui/models/sdxl')
    expect(ENDPOINTS.comfyuiModelInstall('sdxl')).toBe('/api/comfyui/models/sdxl/install')
    expect(ENDPOINTS.comfyuiModelFit('sdxl', '1024x1024')).toBe('/api/comfyui/models/sdxl/fit?size=1024x1024')
    expect(ENDPOINTS.comfyuiModelFit('sdxl', null)).toBe('/api/comfyui/models/sdxl/fit')
    // Cancel is keyed by the model id, not the job id.
    expect(ENDPOINTS.comfyuiJobCancel('sdxl')).toBe('/api/comfyui/jobs/sdxl/cancel')
    expect(ENDPOINTS.comfyuiModel('a b')).toBe('/api/comfyui/models/a%20b')
  })
})

describe('Image Gen hooks', () => {
  beforeEach(() => {
    vi.mocked(client.apiGet).mockReset()
    vi.mocked(client.apiDelete).mockReset()
    vi.mocked(client.apiPost).mockReset()
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('useArbiter returns the whole envelope', async () => {
    const envelope = {
      mode: 'generation',
      arbiter: arbiterBlock({ mode: 'img', phase: 'grace', restore_at: 5 }),
      switchover: { active: false, target: null, error: null },
      gate_refusal: null,
    }
    vi.mocked(client.apiGet).mockResolvedValue(envelope)
    const probe = mountHook(() => hooks.useArbiter(), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/comfyui/arbiter')
    expect(probe.get().data).toEqual(envelope)
    act(() => probe.root.unmount())
  })

  it('useImageJobs unwraps the list', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ jobs: [job()] })
    const probe = mountHook(() => hooks.useImageJobs({ active: true }), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/comfyui/jobs')
    expect(probe.get().data).toEqual([job()])
    act(() => probe.root.unmount())
  })

  it('useImageJobs does not fetch when it is not active', async () => {
    const probe = mountHook(() => hooks.useImageJobs({ active: false }), newClient())
    await flush()
    expect(client.apiGet).not.toHaveBeenCalled()
    act(() => probe.root.unmount())
  })

  it('useImageJobs refreshes the model list and files when an install ends', async () => {
    const qc = newClient()
    qc.setQueryData(['comfyui', 'jobs'], [job({ state: 'running' })])
    qc.setQueryData(['comfyui', 'models'], { models: [] })
    qc.setQueryData(['comfyui', 'files'], { root: '/m', files: [] })
    vi.mocked(client.apiGet).mockResolvedValue({ jobs: [job({ state: 'completed', finished_at: 5 })] })
    const probe = mountHook(() => hooks.useImageJobs({ active: true }), qc)
    await flush()
    expect(qc.getQueryState(['comfyui', 'models'])?.isInvalidated).toBe(true)
    expect(qc.getQueryState(['comfyui', 'files'])?.isInvalidated).toBe(true)
    act(() => probe.root.unmount())
  })

  it('useImageJobs leaves the model list alone while a job is still running', async () => {
    const qc = newClient()
    qc.setQueryData(['comfyui', 'jobs'], [job({ bytes_downloaded: 10 })])
    qc.setQueryData(['comfyui', 'models'], { models: [] })
    vi.mocked(client.apiGet).mockResolvedValue({ jobs: [job({ bytes_downloaded: 50 })] })
    const probe = mountHook(() => hooks.useImageJobs({ active: true }), qc)
    await flush()
    expect(qc.getQueryState(['comfyui', 'models'])?.isInvalidated).toBe(false)
    act(() => probe.root.unmount())
  })

  it('useImageModelInstall posts the install and puts the job in the list at once', async () => {
    const started = job({ state: 'queued', bytes_downloaded: 0 })
    vi.mocked(client.apiPost).mockResolvedValue({ job: started })
    const qc = newClient()
    qc.setQueryData(['comfyui', 'jobs'], [job({ id: 'old', model_id: 'sdxl', state: 'failed' }), job({ id: 'o', model_id: 'flux' })])
    qc.setQueryData(['comfyui', 'models'], { models: [] })
    const probe = mountHook(() => hooks.useImageModelInstall(), qc)

    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync('sdxl')
    })

    expect(client.apiPost).toHaveBeenCalledWith('/api/comfyui/models/sdxl/install')
    expect(result).toEqual({ job: started })
    // The latest job per model: the new one replaces the model's old one and leaves the rest.
    const list = qc.getQueryData<unknown[]>(['comfyui', 'jobs'])
    expect(list).toEqual([started, job({ id: 'o', model_id: 'flux' })])
    expect(qc.getQueryState(['comfyui', 'models'])?.isInvalidated).toBe(true)
    act(() => probe.root.unmount())
  })

  it('useImageModelInstall leaves the caches alone when the server refuses', async () => {
    vi.mocked(client.apiPost).mockRejectedValue(
      new client.Hal0Error('already installing', { code: 'comfyui.install_running', status: 409 }),
    )
    const qc = newClient()
    qc.setQueryData(['comfyui', 'jobs'], [])
    qc.setQueryData(['comfyui', 'models'], { models: [] })
    const probe = mountHook(() => hooks.useImageModelInstall(), qc)
    await act(async () => {
      await expect(probe.get().mutateAsync('sdxl')).rejects.toThrow('already installing')
    })
    expect(qc.getQueryData(['comfyui', 'jobs'])).toEqual([])
    expect(qc.getQueryState(['comfyui', 'models'])?.isInvalidated).toBe(false)
    act(() => probe.root.unmount())
  })

  it('useImageModelRemove deletes the model and refreshes models, files and status', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue({ removed: ['checkpoints/a.safetensors'], kept: [], affected: [] })
    const qc = newClient()
    qc.setQueryData(['comfyui', 'models'], { models: [] })
    qc.setQueryData(['comfyui', 'files'], { root: '/m', files: [] })
    qc.setQueryData(['comfyui', 'status'], {})
    const probe = mountHook(() => hooks.useImageModelRemove(), qc)

    let result: unknown
    await act(async () => {
      result = await probe.get().mutateAsync({ id: 'sdxl' })
    })
    expect(client.apiDelete).toHaveBeenCalledWith('/api/comfyui/models/sdxl')
    expect(result).toEqual({ removed: ['checkpoints/a.safetensors'], kept: [], affected: [] })
    for (const key of ['models', 'files', 'status']) {
      expect(qc.getQueryState(['comfyui', key])?.isInvalidated).toBe(true)
    }

    await act(async () => {
      await probe.get().mutateAsync({ id: 'sdxl', includeShared: true })
    })
    expect(client.apiDelete).toHaveBeenLastCalledWith('/api/comfyui/models/sdxl?include_shared=true')
    act(() => probe.root.unmount())
  })

  it('useImageJobCancel posts the cancel by model id and refreshes the jobs', async () => {
    vi.mocked(client.apiPost).mockResolvedValue({ job: job({ state: 'running' }) })
    const qc = newClient()
    qc.setQueryData(['comfyui', 'jobs'], [job()])
    const probe = mountHook(() => hooks.useImageJobCancel(), qc)
    await act(async () => {
      await probe.get().mutateAsync('sdxl')
    })
    expect(client.apiPost).toHaveBeenCalledWith('/api/comfyui/jobs/sdxl/cancel')
    expect(qc.getQueryState(['comfyui', 'jobs'])?.isInvalidated).toBe(true)
    act(() => probe.root.unmount())
  })

  it('useModelFit asks for the verdict at a size and returns the size judged', async () => {
    const answer = {
      fit: {
        verdict: 'tight',
        reason: 'close',
        need: { gpu_gb: 10, ram_gb: 8, gpu_estimated: false, ram_estimated: true },
        render_free_gb: 11,
        ram_free_gb: 30,
      },
      size: '1024x1024',
    }
    vi.mocked(client.apiGet).mockResolvedValue(answer)
    const probe = mountHook(() => hooks.useModelFit('sdxl', '1030x1030'), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/comfyui/models/sdxl/fit?size=1030x1030')
    expect(probe.get().data).toEqual(answer)
    act(() => probe.root.unmount())
  })

  it('useModelFit asks nothing for a user checkpoint or without a model', async () => {
    // /fit 404s for ckpt/... ids (the route's {model_id} does not take the slash); those are
    // judged by the /v1 preflight when the render is requested.
    const a = mountHook(() => hooks.useModelFit('ckpt/styles/mine', '1024x1024'), newClient())
    const b = mountHook(() => hooks.useModelFit('', '1024x1024'), newClient())
    const c = mountHook(() => hooks.useModelFit(null, '1024x1024'), newClient())
    await flush()
    expect(client.apiGet).not.toHaveBeenCalled()
    for (const p of [a, b, c]) act(() => p.root.unmount())
  })

  it('useComfyuiModels types the store block', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({
      models: [],
      engine: { available: true, reason: null },
      store: { free_gb: 120.5, path: '/models' },
    })
    const probe = mountHook(() => hooks.useComfyuiModels(), newClient())
    await flush()
    expect(probe.get().data?.store.free_gb).toBe(120.5)
    act(() => probe.root.unmount())
  })
})

describe('polling cadences', () => {
  const status = (over: Record<string, unknown> = {}) =>
    ({
      queue: { running: 0, pending: 0 },
      arbiter: arbiterBlock(),
      ...over,
    }) as unknown as import('@/api/hooks/useComfyui').ComfyuiStatus

  it('arbiterPollMs: 1 s while a phase runs, a switch holds the lock or the queue is busy; else 5 s', () => {
    const env = (over = {}) =>
      ({ mode: null, arbiter: arbiterBlock(over), switchover: {}, gate_refusal: null }) as unknown as import('@/api/hooks/useComfyui').ArbiterEnvelope
    expect(hooks.arbiterPollMs(env(), false)).toBe(5_000)
    expect(hooks.arbiterPollMs(env({ phase: 'grace' }), false)).toBe(1_000)
    expect(hooks.arbiterPollMs(env({ switching: true }), false)).toBe(1_000)
    expect(hooks.arbiterPollMs(env(), true)).toBe(1_000)
    // No answer yet / no arbiter: the slow cadence, unless the queue says otherwise.
    expect(hooks.arbiterPollMs(undefined, false)).toBe(5_000)
    expect(hooks.arbiterPollMs({ mode: null, arbiter: null } as never, false)).toBe(5_000)
  })

  it('comfyuiStatusPollMs: 2 s while the pane is open and work is going on; else the old 4 s / 20 s', () => {
    expect(hooks.comfyuiStatusPollMs(status(), { active: false })).toBe(20_000)
    expect(hooks.comfyuiStatusPollMs(status(), { active: true })).toBe(4_000)
    expect(hooks.comfyuiStatusPollMs(undefined, { active: true })).toBe(4_000)
    // Work in progress, read off the status itself.
    expect(hooks.comfyuiStatusPollMs(status({ arbiter: arbiterBlock({ phase: 'rendering' }) }), { active: true })).toBe(2_000)
    expect(hooks.comfyuiStatusPollMs(status({ arbiter: arbiterBlock({ switching: true }) }), { active: true })).toBe(2_000)
    expect(hooks.comfyuiStatusPollMs(status({ queue: { running: 0, pending: 1 } }), { active: true })).toBe(2_000)
    // A Generate run in flight, which only the panel knows.
    expect(hooks.comfyuiStatusPollMs(status(), { active: true, busy: true })).toBe(2_000)
    // The pane is not mounted: busy or not, the cheap cadence.
    expect(hooks.comfyuiStatusPollMs(status({ queue: { running: 1, pending: 0 } }), { active: false })).toBe(20_000)
  })

  it('imageJobsPollMs: 1 s while a job is queued or running, otherwise off', () => {
    expect(hooks.imageJobsPollMs([job({ state: 'queued' })])).toBe(1_000)
    expect(hooks.imageJobsPollMs([job({ state: 'completed' }), job({ model_id: 'b', state: 'running' })])).toBe(1_000)
    expect(hooks.imageJobsPollMs([job({ state: 'completed' }), job({ model_id: 'b', state: 'failed' })])).toBe(false)
    expect(hooks.imageJobsPollMs([])).toBe(false)
    expect(hooks.imageJobsPollMs(undefined)).toBe(false)
  })
})
