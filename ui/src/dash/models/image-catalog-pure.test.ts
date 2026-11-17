import { describe, expect, it } from 'vitest'
import type { ComfyuiCatalogModel, ImageJob } from '@/api/hooks/useComfyui'
import {
  fileFolder,
  fileKey,
  fileName,
  fileState,
  filterFiles,
  filterImageModels,
  imageState,
  imageTaskFilters,
  sortFiles,
  sortImageModels,
} from './image-catalog-pure'

const model = (id: string, extra: Partial<ComfyuiCatalogModel> = {}): ComfyuiCatalogModel =>
  ({
    id,
    label: id.toUpperCase(),
    task: 'txt2img',
    state: 'none',
    api_renderable: true,
    recommended: false,
    license: 'Apache-2.0',
    est_seconds: 9,
    sizes: ['1024x1024'],
    notes: '',
    defaults: {},
    approx_gb: 10,
    missing_gb: 10,
    missing: [],
    workflow: `${id}.json`,
    job: null,
    fit: null,
    ...extra,
  }) as ComfyuiCatalogModel
const job = (extra: Partial<ImageJob>): ImageJob =>
  ({ id: 'j', model_id: 'm', state: 'running', bytes_downloaded: 0, bytes_total: 0, files: [], ...extra }) as ImageJob
const wont = { verdict: 'wont_fit' } as ComfyuiCatalogModel['fit']

describe('imageState', () => {
  it('reads an installed default model as in use by the img capability', () => {
    expect(imageState(model('a', { state: 'complete' }), null, 'a', true)).toMatchObject({ installed: true, inUseBy: ['img'] })
    expect(imageState(model('a', { state: 'complete' }), null, 'b', true).inUseBy).toEqual([])
    // A default that is not on disk is not in use.
    expect(imageState(model('a'), null, 'a', true)).toMatchObject({ installed: false, inUseBy: [] })
  })
  it('treats a partly installed model as not installed', () => {
    expect(imageState(model('a', { state: 'partial' }), null, null, true).installed).toBe(false)
  })
  it('gives a running job a percentage only when its total is known', () => {
    expect(imageState(model('a'), job({ bytes_downloaded: 25, bytes_total: 100 }), null, true).download).toEqual({ pct: 25 })
    expect(imageState(model('a'), job({ state: 'queued' }), null, true).download).toEqual({ pct: null })
    expect(imageState(model('a'), job({ state: 'failed' }), null, true).download).toBeNull()
  })
  it("marks the server's won't fit only when the engine can run", () => {
    expect(imageState(model('a', { fit: wont }), null, null, true).wontFit).toBe(true)
    expect(imageState(model('a', { fit: wont }), null, null, false).wontFit).toBe(false)
    expect(imageState(model('a'), null, null, true).wontFit).toBe(false)
  })
})

describe('filterImageModels and sortImageModels', () => {
  const models = [
    model('flux', { state: 'complete', approx_gb: 17 }),
    model('edit', { task: 'img2img', approx_gb: 21 }),
    model('wan', { task: 'txt2video', approx_gb: 32, fit: wont }),
  ]
  const stateOf = (m: ComfyuiCatalogModel) => imageState(m, null, null, true)
  const ids = (f: Partial<Parameters<typeof filterImageModels>[1]>) =>
    filterImageModels(models, { q: '', states: [], tasks: [], fits: false, ...f }, stateOf).map((m) => m.id)
  it('filters by text over label, id and task name', () => {
    expect(ids({ q: 'video' })).toEqual(['wan'])
    expect(ids({ q: 'FLUX' })).toEqual(['flux'])
  })
  it('filters by state, task and fit together', () => {
    expect(ids({ states: ['available'] })).toEqual(['edit', 'wan'])
    expect(ids({ states: ['available'], fits: true })).toEqual(['edit'])
    expect(ids({ tasks: ['txt2img', 'txt2video'] })).toEqual(['flux', 'wan'])
  })
  it('offers one Task chip per task in the catalog, in catalog order', () => {
    expect(imageTaskFilters(models)).toEqual([
      { id: 'txt2img', label: 'Text → Image' },
      { id: 'img2img', label: 'Image edit' },
      { id: 'txt2video', label: 'Text → Video' },
    ])
  })
  it('keeps catalog order by default and sorts by name or size', () => {
    const order = (field: string, dir: 'asc' | 'desc') => sortImageModels(models, field, dir).map((m) => m.id)
    expect(order('catalog', 'asc')).toEqual(['flux', 'edit', 'wan'])
    expect(order('catalog', 'desc')).toEqual(['wan', 'edit', 'flux'])
    expect(order('name', 'asc')).toEqual(['edit', 'flux', 'wan'])
    expect(order('size', 'desc')).toEqual(['wan', 'edit', 'flux'])
  })
})

describe('the Files view helpers', () => {
  const files = [
    { subdir: 'checkpoints', relpath: 'mine/sdxl.safetensors', size_bytes: 7, models: [] as string[] },
    { subdir: 'vae', relpath: 'ae.safetensors', size_bytes: 1, models: ['flux'] },
  ]
  it('names a file, its folder and its key', () => {
    expect(fileKey(files[0])).toBe('checkpoints/mine/sdxl.safetensors')
    expect(fileName(files[0].relpath)).toBe('sdxl.safetensors')
    expect(fileFolder(files[0])).toBe('checkpoints/mine')
    expect(fileFolder(files[1])).toBe('vae')
  })
  it('reads a file as on disk and in use by the models that load it', () => {
    expect(fileState(files[1], (id) => id.toUpperCase())).toEqual({ installed: true, inUseBy: ['FLUX'] })
    expect(fileState(files[0], (id) => id).inUseBy).toEqual([])
  })
  it('filters by text and by owner, and sorts by name or size', () => {
    expect(filterFiles(files, { q: 'MINE', owners: [] })).toEqual([files[0]])
    expect(filterFiles(files, { q: '', owners: ['unmanaged'] })).toEqual([files[0]])
    expect(filterFiles(files, { q: '', owners: ['owned', 'unmanaged'] })).toHaveLength(2)
    expect(sortFiles(files, 'name', 'asc').map((f) => f.relpath)).toEqual(['ae.safetensors', 'mine/sdxl.safetensors'])
    expect(sortFiles(files, 'size', 'desc').map((f) => f.size_bytes)).toEqual([7, 1])
  })
})
