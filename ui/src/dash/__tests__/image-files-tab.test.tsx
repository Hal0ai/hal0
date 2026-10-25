// @vitest-environment happy-dom
//
// Models → Image: the file inventory (GET /api/comfyui/files). The tab shows what
// is on disk under the ComfyUI models tree, grouped by component kind, with the
// catalog models that load each file. It deletes only a file nobody owns, behind a
// confirm; a catalog-owned file is removed by removing its model, and a symlink is
// removed through its target.
//
// ImageFilesTab is an ES module (no window-global components of its own), but
// ConfirmDialog/Modal come from primitives.jsx, which resolves `Icons` off the
// window, so that one global is installed before the dynamic import — same pattern
// as runner-images-confirm-flow.test.tsx. The hooks are mocked at the module
// boundary, the way model-drawer.test.tsx mocks its own.
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

const { filesBox, modelsBox, deleteCalls, deleteError, toasts } = vi.hoisted(() => ({
  filesBox: {
    current: {
      state: 'ok' as 'ok' | 'pending' | 'error',
      root: '/models/comfyui',
      files: [] as Record<string, unknown>[],
    },
  },
  modelsBox: { current: [] as Record<string, unknown>[] },
  deleteCalls: [] as unknown[],
  deleteError: { current: undefined as Error | undefined },
  toasts: [] as unknown[][],
}))

vi.mock('@/api/hooks/useComfyui', () => ({
  useComfyuiFiles: () => ({
    data:
      filesBox.current.state === 'ok'
        ? { root: filesBox.current.root, files: filesBox.current.files }
        : undefined,
    isPending: filesBox.current.state === 'pending',
    isError: filesBox.current.state === 'error',
    error: filesBox.current.state === 'error' ? new Error('files unreachable') : null,
  }),
  useComfyuiModels: () => ({ data: { models: modelsBox.current, engine: { available: true, reason: null } } }),
  useComfyuiFileDelete: () => ({
    isPending: false,
    mutateAsync: async (vars: unknown) => {
      deleteCalls.push(vars)
      if (deleteError.current) throw deleteError.current
      return { deleted: 'ok' }
    },
  }),
}))

const { ImageFilesTab } = await import('../image/ImageFilesTab.jsx')

const CKPT_OWNED = {
  kind: 'checkpoint',
  subdir: 'checkpoints',
  relpath: 'flux-dev.safetensors',
  size_bytes: 23_800_000_000,
  models: ['flux-dev'],
  symlink: false,
}
const CKPT_MINE = {
  kind: 'checkpoint',
  subdir: 'checkpoints',
  relpath: 'mine/sdxl-custom.safetensors',
  size_bytes: 6_900_000_000,
  models: [],
  symlink: false,
  arch: 'sdxl',
  api_id: 'ckpt/mine/sdxl-custom',
}
const LORA_LINK = {
  kind: 'lora',
  subdir: 'loras',
  relpath: 'linked.safetensors',
  size_bytes: 144_000_000,
  models: [],
  symlink: true,
}
const CONTROLNET = {
  kind: 'controlnet',
  subdir: 'controlnet',
  relpath: 'canny.safetensors',
  size_bytes: 1_400_000_000,
  models: [],
  symlink: false,
}

function mount() {
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  act(() => {
    root.render(React.createElement(ImageFilesTab))
  })
  return { host, root }
}

const byId = (testid: string) => document.querySelector(`[data-testid="${testid}"]`) as HTMLElement | null
const button = (label: string) =>
  Array.from(document.querySelectorAll('button')).find((b) => b.textContent === label) as HTMLButtonElement | undefined

beforeEach(() => {
  filesBox.current = { state: 'ok', root: '/models/comfyui', files: [CKPT_OWNED, CKPT_MINE, LORA_LINK, CONTROLNET] }
  modelsBox.current = [{ id: 'flux-dev', label: 'FLUX.1 dev', task: 'txt2img', state: 'complete' }]
  deleteCalls.length = 0
  deleteError.current = undefined
  toasts.length = 0
  ;(window as unknown as { __hal0Toast: (...a: unknown[]) => void }).__hal0Toast = (...a) => {
    toasts.push(a)
  }
})
afterEach(() => {
  document.body.innerHTML = ''
})

describe('ImageFilesTab', () => {
  it('groups the files into sections with headers, in component order', () => {
    const { root } = mount()
    const headers = Array.from(document.querySelectorAll('.mdl-section-label')).map((e) => e.textContent)
    expect(headers).toEqual(['Checkpoints · 2', 'LoRAs · 1', 'Controlnet · 1'])
    act(() => root.unmount())
  })

  it('names the catalog model that owns a file, and marks the rest unmanaged', () => {
    const { root } = mount()
    expect(byId('imgf-row-checkpoints/flux-dev.safetensors')!.textContent).toContain('Used by FLUX.1 dev')
    const mine = byId('imgf-row-checkpoints/mine/sdxl-custom.safetensors')!
    expect(mine.textContent).toContain('sdxl-custom.safetensors')
    expect(mine.textContent).toContain('Unmanaged')
    expect(mine.textContent).toContain('6.43 GB')
    act(() => root.unmount())
  })

  it('falls back to the model id when the catalog has no label for it', () => {
    modelsBox.current = []
    const { root } = mount()
    expect(byId('imgf-row-checkpoints/flux-dev.safetensors')!.textContent).toContain('Used by flux-dev')
    act(() => root.unmount())
  })

  it('shows path, size, kind and used-by in the detail pane', () => {
    const { root } = mount()
    act(() => byId('imgf-row-checkpoints/flux-dev.safetensors')!.click())
    const detail = byId('imgf-detail')!
    expect(detail.textContent).toContain('/models/comfyui/checkpoints/flux-dev.safetensors')
    expect(detail.textContent).toContain('22.17 GB')
    expect(detail.textContent).toContain('Checkpoints')
    expect(detail.textContent).toContain('FLUX.1 dev')
    act(() => root.unmount())
  })

  it('hides Delete for catalog-owned files', () => {
    const { root } = mount()
    act(() => byId('imgf-row-checkpoints/flux-dev.safetensors')!.click())
    expect(byId('imgf-delete')).toBeNull()
    // Install and remove are on the Models tab now; the CLI stays as the other way.
    expect(byId('imgf-owned-note')!.textContent).toBe(
      "Remove the model to delete this file: use Remove on its card in the Image Gen pane's Models tab, or run hal0 comfyui models remove flux-dev",
    )
    act(() => root.unmount())
  })

  it('names --include-shared for a file two catalog models load', () => {
    filesBox.current = {
      state: 'ok',
      root: '/models/comfyui',
      files: [{ ...CKPT_OWNED, models: ['flux-dev', 'flux-schnell'] }],
    }
    const { root } = mount()
    expect(byId('imgf-delete')).toBeNull()
    const text = byId('imgf-owned-note')!.textContent!
    expect(text).toContain('hal0 comfyui models remove flux-dev --include-shared')
    expect(text).toContain('flux-dev, flux-schnell')
    // A plain remove from the dashboard keeps the shared file; only the CLI flag deletes it.
    expect(text).toContain('removing one of them keeps it for the others')
    act(() => root.unmount())
  })

  it('hides Delete for a symlink and says to delete its target', () => {
    const { root } = mount()
    act(() => byId('imgf-row-loras/linked.safetensors')!.click())
    expect(byId('imgf-delete')).toBeNull()
    expect(byId('imgf-symlink-note')!.textContent).toContain('link')
    expect(byId('imgf-symlink-note')!.textContent).toContain('target')
    act(() => root.unmount())
  })

  it('confirms then calls delete for unmanaged files', async () => {
    const { root } = mount()
    act(() => byId('imgf-row-checkpoints/mine/sdxl-custom.safetensors')!.click())
    act(() => byId('imgf-delete')!.click())
    // The confirm names the file and nothing has been deleted yet.
    expect(document.body.textContent).toContain('Delete mine/sdxl-custom.safetensors?')
    expect(deleteCalls).toEqual([])
    await act(async () => button('Delete file')!.click())
    expect(deleteCalls).toEqual([{ subdir: 'checkpoints', relpath: 'mine/sdxl-custom.safetensors' }])
    expect(toasts.at(-1)![1]).toBe('ok')
    act(() => root.unmount())
  })

  it('cancelling the confirm deletes nothing', () => {
    const { root } = mount()
    act(() => byId('imgf-row-checkpoints/mine/sdxl-custom.safetensors')!.click())
    act(() => byId('imgf-delete')!.click())
    act(() => button('Cancel')!.click())
    expect(deleteCalls).toEqual([])
    expect(document.body.textContent).not.toContain('Delete mine/sdxl-custom.safetensors?')
    act(() => root.unmount())
  })

  it('toasts the server refusal when the delete fails', async () => {
    deleteError.current = new Error('a catalog model loads this file')
    const { root } = mount()
    act(() => byId('imgf-row-checkpoints/mine/sdxl-custom.safetensors')!.click())
    act(() => byId('imgf-delete')!.click())
    await act(async () => button('Delete file')!.click())
    expect(toasts.at(-1)![1]).toBe('err')
    expect(String(toasts.at(-1)![0])).toContain('a catalog model loads this file')
    act(() => root.unmount())
  })

  it('tells an empty inventory to install with the CLI, not to a dashboard place', () => {
    filesBox.current = { state: 'ok', root: '/models/comfyui', files: [] }
    const { root } = mount()
    const hint = byId('imgf-empty-hint')!
    expect(hint.textContent).toContain('hal0 comfyui models install <id>')
    expect(hint.textContent).toContain('hal0 comfyui models list')
    expect(document.body.textContent).not.toContain('Image Gen')
    expect(document.querySelector('a')).toBeNull()
    act(() => root.unmount())
  })

  it('shows loading and error states instead of an empty inventory', () => {
    filesBox.current = { ...filesBox.current, state: 'pending' }
    const loading = mount()
    expect(document.body.textContent).toContain('Loading')
    expect(byId('imgf-empty-hint')).toBeNull()
    act(() => loading.root.unmount())
    document.body.innerHTML = ''
    filesBox.current = { ...filesBox.current, state: 'error' }
    const failed = mount()
    expect(document.body.textContent).toContain('files unreachable')
    expect(byId('imgf-empty-hint')).toBeNull()
    act(() => failed.root.unmount())
  })
})
