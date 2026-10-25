// Models → Image: grouping, install-state actions and download progress. Pure.
import { describe, expect, it } from 'vitest'
import type { ComfyuiCatalogModel, Fit, ImageJob } from '@/api/hooks/useComfyui'
import {
  fitSpareGb,
  groupByTask,
  jobProgress,
  jobRate,
  parseSize,
  renderableInstalled,
  stateAction,
} from '../image/image-models-pure'

function model(over: Partial<ComfyuiCatalogModel> & { id: string }): ComfyuiCatalogModel {
  return {
    label: over.id,
    task: 'txt2img',
    state: 'none',
    api_renderable: true,
    recommended: false,
    license: 'apache-2.0',
    est_seconds: 30,
    sizes: ['1024x1024'],
    notes: '',
    defaults: {},
    approx_gb: 6.5,
    missing_gb: 6.5,
    missing: [],
    workflow: `${over.id}.json`,
    job: null,
    fit: null,
    ...over,
  }
}

function job(over: Partial<ImageJob> = {}): ImageJob {
  return {
    id: 'job-1',
    model_id: 'sdxl',
    state: 'running',
    bytes_downloaded: 0,
    bytes_total: 0,
    started_at: 100,
    finished_at: null,
    error: null,
    error_code: null,
    sha256: null,
    path: null,
    files: [],
    ...over,
  }
}

describe('groupByTask', () => {
  it('groupByTask keeps catalog order within groups', () => {
    // Catalog order interleaves tasks; the groups come out in a fixed reading order
    // and each keeps the order its models arrived in.
    const groups = groupByTask([
      model({ id: 'wan-t2v', task: 'txt2video' }),
      model({ id: 'flux', task: 'txt2img' }),
      model({ id: 'upscale-x4', task: 'upscale' }),
      model({ id: 'sdxl', task: 'txt2img' }),
      model({ id: 'qwen-edit', task: 'img2img' }),
      model({ id: 'wan-i2v', task: 'img2video' }),
      model({ id: 'sd15', task: 'txt2img' }),
    ])
    expect(groups.map((g) => g.label)).toEqual([
      'Text → Image',
      'Image edit',
      'Text → Video',
      'Image → Video',
      'Upscale',
    ])
    expect(groups[0].models.map((m) => m.id)).toEqual(['flux', 'sdxl', 'sd15'])
    expect(groups.map((g) => g.task)).toEqual(['txt2img', 'img2img', 'txt2video', 'img2video', 'upscale'])
  })

  it('leaves out empty groups and keeps a task it does not know after the known ones', () => {
    const groups = groupByTask([model({ id: 'a', task: 'depth' }), model({ id: 'b', task: 'upscale' })])
    expect(groups.map((g) => g.label)).toEqual(['Upscale', 'depth'])
    expect(groupByTask([])).toEqual([])
  })
})

describe('stateAction', () => {
  it('stateAction returns install for none', () => {
    expect(stateAction(model({ id: 'sdxl', state: 'none' }), null, true)).toEqual({
      id: 'install',
      label: 'Install',
      disabled: false,
    })
  })

  it('stateAction labels partial without job with missing size', () => {
    // After an API restart the job is gone but the files are half there (or another model
    // holds the shared ones): the card says what is left to fetch, never "Resume".
    const action = stateAction(model({ id: 'flux', state: 'partial', missing_gb: 4.2 }), null, true)
    expect(action).toEqual({ id: 'install', label: 'Install · 4.2 GB to download', disabled: false })
    expect(action.label).not.toMatch(/resume/i)
    // A whole number reads without a trailing .0.
    expect(stateAction(model({ id: 'flux', state: 'partial', missing_gb: 12 }), null, true).label).toBe(
      'Install · 12 GB to download',
    )
    // Only the workflow is missing: nothing to download, so no size.
    expect(stateAction(model({ id: 'flux', state: 'partial', missing_gb: 0 }), null, true).label).toBe('Install')
    // A finished or failed job does not change it.
    expect(
      stateAction(model({ id: 'flux', state: 'partial', missing_gb: 4.2 }), job({ state: 'failed' }), true).label,
    ).toBe('Install · 4.2 GB to download')
  })

  it('stateAction returns cancel while running', () => {
    for (const state of ['queued', 'running'] as const) {
      expect(stateAction(model({ id: 'sdxl', state: 'partial' }), job({ state }), true)).toEqual({
        id: 'cancel',
        label: 'Cancel',
        disabled: false,
      })
    }
    // The job decides, not the on-disk state (the last file can land before the job closes).
    expect(stateAction(model({ id: 'sdxl', state: 'complete' }), job({ state: 'running' }), true).id).toBe('cancel')
  })

  it('stateAction returns remove for complete', () => {
    expect(stateAction(model({ id: 'sdxl', state: 'complete' }), null, true)).toEqual({
      id: 'remove',
      label: 'Remove',
      disabled: false,
    })
    expect(stateAction(model({ id: 'sdxl', state: 'complete' }), job({ state: 'completed' }), true).id).toBe('remove')
  })

  it('stateAction disables install when engine unavailable', () => {
    expect(stateAction(model({ id: 'sdxl', state: 'none' }), null, false)).toMatchObject({
      id: 'install',
      disabled: true,
    })
    expect(stateAction(model({ id: 'sdxl', state: 'partial', missing_gb: 1.5 }), null, false)).toMatchObject({
      id: 'install',
      label: 'Install · 1.5 GB to download',
      disabled: true,
    })
    // Taking files off disk and stopping a download do not need the engine.
    expect(stateAction(model({ id: 'sdxl', state: 'complete' }), null, false).disabled).toBe(false)
    expect(stateAction(model({ id: 'sdxl', state: 'partial' }), job({ state: 'running' }), false).disabled).toBe(false)
  })
})

describe('jobProgress', () => {
  it('jobProgress handles zero total', () => {
    expect(jobProgress(job({ bytes_total: 0, bytes_downloaded: 0 }))).toBe(0)
    expect(jobProgress(job({ bytes_total: 0, bytes_downloaded: 500 }))).toBe(0)
    expect(jobProgress(null)).toBe(0)
    expect(jobProgress(undefined)).toBe(0)
  })

  it('is the fraction downloaded, clamped to 0..1', () => {
    expect(jobProgress(job({ bytes_total: 1000, bytes_downloaded: 250 }))).toBe(0.25)
    expect(jobProgress(job({ bytes_total: 1000, bytes_downloaded: 1500 }))).toBe(1)
    // An install that had nothing to download still finishes.
    expect(jobProgress(job({ state: 'completed', bytes_total: 0 }))).toBe(1)
  })
})

describe('jobRate', () => {
  it('jobRate computes speed and eta, null on first poll', () => {
    const prev = job({ bytes_downloaded: 100_000_000, bytes_total: 1_000_000_000 })
    const cur = job({ bytes_downloaded: 150_000_000, bytes_total: 1_000_000_000 })
    // 50 MB in 5 s is 10 MB/s; 850 MB left is 85 s.
    expect(jobRate(prev, cur, 5)).toEqual({ mbPerS: 10, etaS: 85 })
    expect(jobRate(null, cur, 5)).toBeNull()
  })

  it('has no rate without elapsed time, progress, a total or the same job', () => {
    const prev = job({ bytes_downloaded: 100, bytes_total: 1000 })
    expect(jobRate(prev, job({ bytes_downloaded: 200, bytes_total: 1000 }), 0)).toBeNull()
    expect(jobRate(prev, job({ bytes_downloaded: 100, bytes_total: 1000 }), 2)).toBeNull()
    expect(jobRate(prev, job({ bytes_downloaded: 200, bytes_total: 0 }), 2)).toBeNull()
    expect(jobRate(prev, job({ id: 'job-2', bytes_downloaded: 200, bytes_total: 1000 }), 2)).toBeNull()
    expect(jobRate(prev, job({ bytes_downloaded: 1000, bytes_total: 1000 }), 2)).toBeNull()
  })
})

describe('renderableInstalled', () => {
  it('lists installed models the API renders, in catalog order', () => {
    const out = renderableInstalled([
      model({ id: 'a', state: 'complete' }),
      model({ id: 'b', state: 'partial' }),
      model({ id: 'c', state: 'complete', api_renderable: false }),
      model({ id: 'd', state: 'none' }),
      model({ id: 'e', state: 'complete' }),
    ])
    expect(out.map((m) => m.id)).toEqual(['a', 'e'])
  })
})

describe('parseSize', () => {
  it('reads WxH', () => {
    expect(parseSize('1024x1024')).toEqual({ width: 1024, height: 1024 })
    expect(parseSize('832x1216')).toEqual({ width: 832, height: 1216 })
  })

  it('rejects anything else', () => {
    for (const bad of ['', '1024', '1024x', 'x1024', '1024*1024', '1x1', '10240x10240', 'axb', '1024x1024x2']) {
      expect(parseSize(bad)).toBeNull()
    }
  })
})

describe('fitSpareGb', () => {
  const fit = (over: Partial<Fit> = {}): Fit => ({
    verdict: 'fits',
    reason: null,
    need: { gpu_gb: 31, ram_gb: 12, gpu_estimated: false, ram_estimated: true },
    render_free_gb: 50,
    ram_free_gb: 44,
    ...over,
  })

  it('is the smaller of the GPU and RAM headroom, to one decimal', () => {
    // GPU: 50 - 31 = 19; RAM: 44 - 12 = 32.
    expect(fitSpareGb(fit())).toBe(19)
    // RAM is the tighter one: 20.4 - 12 = 8.4.
    expect(fitSpareGb(fit({ ram_free_gb: 20.4 }))).toBe(8.4)
    // Float noise is rounded off: 50.3 - 31 = 19.299999999999997.
    expect(fitSpareGb(fit({ render_free_gb: 50.3 }))).toBe(19.3)
  })

  it('never goes below zero', () => {
    expect(fitSpareGb(fit({ render_free_gb: 20, verdict: 'wont_fit', reason: 'no' }))).toBe(0)
  })
})
