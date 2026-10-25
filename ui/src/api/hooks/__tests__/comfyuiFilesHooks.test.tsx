// @vitest-environment happy-dom
//
// ComfyUI file-inventory hooks (Models → Image).
//
//   useComfyuiFiles()       -> GET    /api/comfyui/files
//   useComfyuiFileDelete()  -> DELETE /api/comfyui/files/{subdir}/{relpath}
//                              then invalidates the files and status queries
//   useComfyuiModels()      -> GET    /api/comfyui/models
//
// The component tests mock these hooks, so this is the one place the real URL and
// cache wiring is pinned. Harness mirrors personaHooks.test.tsx.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
const { useComfyuiFiles, useComfyuiFileDelete, useComfyuiModels } = await import('@/api/hooks/useComfyui')
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

describe('comfyuiFile endpoint', () => {
  it('keeps the / between relpath segments and encodes each segment', () => {
    expect(ENDPOINTS.comfyuiFile('checkpoints', 'a.safetensors')).toBe('/api/comfyui/files/checkpoints/a.safetensors')
    // The route takes a nested path: the separators must survive, the rest must not.
    expect(ENDPOINTS.comfyuiFile('loras', 'styles/my lora#1.safetensors')).toBe(
      '/api/comfyui/files/loras/styles/my%20lora%231.safetensors',
    )
    expect(ENDPOINTS.comfyuiFile('loras', 'a%2Fb?.pt')).toBe('/api/comfyui/files/loras/a%252Fb%3F.pt')
  })
})

describe('ComfyUI file hooks', () => {
  beforeEach(() => {
    vi.mocked(client.apiGet).mockReset()
    vi.mocked(client.apiDelete).mockReset()
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('useComfyuiFiles reads the files route', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ root: '/m', files: [{ kind: 'vae', subdir: 'vae', relpath: 'a.pt' }] })
    const probe = mountHook(() => useComfyuiFiles(), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/comfyui/files')
    expect(probe.get().data?.files).toHaveLength(1)
    act(() => probe.root.unmount())
  })

  it('useComfyuiModels reads the catalog route', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({ models: [{ id: 'flux-dev', label: 'FLUX.1 dev' }], engine: {} })
    const probe = mountHook(() => useComfyuiModels(), newClient())
    await flush()
    expect(client.apiGet).toHaveBeenCalledWith('/api/comfyui/models')
    expect(probe.get().data?.models[0]?.label).toBe('FLUX.1 dev')
    act(() => probe.root.unmount())
  })

  it('useComfyuiFileDelete deletes the nested path and refreshes files and status', async () => {
    vi.mocked(client.apiDelete).mockResolvedValue({ deleted: 'loras/styles/a.safetensors' })
    const qc = newClient()
    qc.setQueryData(['comfyui', 'files'], { root: '/m', files: [] })
    qc.setQueryData(['comfyui', 'status'], {})
    qc.setQueryData(['comfyui', 'models'], { models: [] })
    const probe = mountHook(() => useComfyuiFileDelete(), qc)

    await act(async () => {
      await probe.get().mutateAsync({ subdir: 'loras', relpath: 'styles/a.safetensors' })
    })

    expect(client.apiDelete).toHaveBeenCalledWith('/api/comfyui/files/loras/styles/a.safetensors')
    expect(qc.getQueryState(['comfyui', 'files'])?.isInvalidated).toBe(true)
    expect(qc.getQueryState(['comfyui', 'status'])?.isInvalidated).toBe(true)
    // A delete never changes the catalog, so its listing is left alone.
    expect(qc.getQueryState(['comfyui', 'models'])?.isInvalidated).toBe(false)
    act(() => probe.root.unmount())
  })

  it('useComfyuiFileDelete leaves the caches alone when the server refuses', async () => {
    vi.mocked(client.apiDelete).mockRejectedValue(new client.Hal0Error('catalog owned', { code: 'comfyui.file_catalog_owned', status: 409 }))
    const qc = newClient()
    qc.setQueryData(['comfyui', 'files'], { root: '/m', files: [] })
    const probe = mountHook(() => useComfyuiFileDelete(), qc)

    await act(async () => {
      await expect(probe.get().mutateAsync({ subdir: 'checkpoints', relpath: 'a.safetensors' })).rejects.toThrow('catalog owned')
    })

    expect(qc.getQueryState(['comfyui', 'files'])?.isInvalidated).toBe(false)
    act(() => probe.root.unmount())
  })
})
