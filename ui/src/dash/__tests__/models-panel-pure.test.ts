// The Models panel's pure helpers: filters, badges, the recommendation reason, the removal
// plan and the first-run picks. Nothing here judges a fit: the verdict is the server's.
import { describe, expect, it } from 'vitest'
import type { ComfyuiCatalogModel, Fit } from '@/api/hooks/useComfyui'
import {
  downloadGb,
  fixCommands,
  fmtEta,
  firstRunPicks,
  gbLabel,
  isFullPrecision,
  isNonCommercial,
  modelBadges,
  passesFilter,
  perUnit,
  recommendationReason,
  removalPlan,
  shellQuote,
  suggestAlternative,
} from '../image/models-panel-pure'

const fit = (verdict: Fit['verdict']): Fit => ({
  verdict,
  reason: verdict === 'fits' ? null : 'needs more than is free',
  need: { gpu_gb: 10, ram_gb: 10, gpu_estimated: false, ram_estimated: false },
  render_free_gb: 40,
  ram_free_gb: 40,
})

const model = (over: Partial<ComfyuiCatalogModel> = {}): ComfyuiCatalogModel => ({
  id: 'm',
  label: 'M',
  task: 'txt2img',
  state: 'none',
  api_renderable: true,
  recommended: false,
  license: 'Apache-2.0',
  est_seconds: 5,
  sizes: ['1024x1024'],
  notes: '',
  defaults: {},
  approx_gb: 6,
  missing_gb: 6,
  missing: [],
  workflow: 'm.json',
  job: null,
  fit: fit('fits'),
  ...over,
})

describe('full-precision variants', () => {
  it('are the ids ending -fp16 or -bf16', () => {
    expect(isFullPrecision('ltx2-t2v-bf16')).toBe(true)
    expect(isFullPrecision('wan22-t2v-fp16')).toBe(true)
    expect(isFullPrecision('ltx2-t2v')).toBe(false)
    expect(isFullPrecision('fp16-thing')).toBe(false)
  })
})

describe('isNonCommercial', () => {
  it('reads NC as a part of the license id, not any "nc" inside a word', () => {
    expect(isNonCommercial('SAI-NC-Research-Community')).toBe(true)
    expect(isNonCommercial('CC-BY-NC-SA-4.0')).toBe(true)
    // "Tencent" holds a lower-case nc; it is not a non-commercial license.
    expect(isNonCommercial('Tencent-Hunyuan-Community')).toBe(false)
    expect(isNonCommercial('Apache-2.0')).toBe(false)
    expect(isNonCommercial('')).toBe(false)
  })
})

describe('modelBadges', () => {
  it('lists Recommended, Default, Editor only, Large and Non-commercial in that order', () => {
    const m = model({
      id: 'x',
      recommended: true,
      api_renderable: false,
      approx_gb: 30,
      license: 'CC-BY-NC-SA-4.0',
    })
    expect(modelBadges(m, 'x')).toEqual([
      'Recommended',
      'Default',
      'Editor only',
      'Large',
      'Non-commercial',
    ])
  })

  it('gives a plain model none', () => {
    expect(modelBadges(model({ approx_gb: 29.9 }), null)).toEqual([])
  })
})

describe('passesFilter', () => {
  const wont = model({ fit: fit('wont_fit') })
  const unknown = model({ fit: null })
  const done = model({ state: 'complete' })

  it('shows everything under All', () => {
    expect([wont, unknown, done].every((m) => passesFilter(m, 'all', true))).toBe(true)
  })

  it('"fits" drops only a won\'t-fit verdict; an unknown fit stays', () => {
    expect(passesFilter(wont, 'fits', true)).toBe(false)
    expect(passesFilter(unknown, 'fits', true)).toBe(true)
    expect(passesFilter(model({ fit: fit('tight') }), 'fits', true)).toBe(true)
  })

  it('"fits" ignores a verdict the screen does not show (no engine)', () => {
    expect(passesFilter(wont, 'fits', false)).toBe(true)
  })

  it('"installed" keeps the complete models', () => {
    expect(passesFilter(done, 'installed', true)).toBe(true)
    expect(passesFilter(model({ state: 'partial' }), 'installed', true)).toBe(false)
  })
})

describe('perUnit', () => {
  it('counts a clip for video tasks and an image for the rest', () => {
    expect(perUnit('txt2video')).toBe('clip')
    expect(perUnit('img2video')).toBe('clip')
    expect(perUnit('txt2img')).toBe('image')
    expect(perUnit('upscale')).toBe('image')
  })
})

describe('recommendationReason', () => {
  it('cites the RAM figure with its source', () => {
    expect(recommendationReason({ id: 'a', pool_gb: 48, limited_by: 'ram' }, 'container')).toBe(
      'chosen from 48 GB of RAM (container limit)',
    )
    expect(recommendationReason({ id: 'a', pool_gb: 105.5, limited_by: 'ram' }, 'system')).toBe(
      'chosen from 105.5 GB of RAM (system)',
    )
  })

  it('drops the suffix when the RAM source is not known yet', () => {
    expect(recommendationReason({ id: 'a', pool_gb: 48, limited_by: 'ram' }, undefined)).toBe(
      'chosen from 48 GB of RAM',
    )
  })

  it('cites the GPU pool when the GPU limited the pick, whatever the RAM source', () => {
    expect(recommendationReason({ id: 'a', pool_gb: 96, limited_by: 'gpu' }, 'container')).toBe(
      'chosen from 96 GB of GPU memory',
    )
  })

  it('says nothing without a recommendation', () => {
    expect(recommendationReason(null, 'system')).toBeNull()
    expect(recommendationReason(undefined, 'system')).toBeNull()
  })
})

describe('firstRunPicks', () => {
  it('takes the recommended renderable model and the two smallest other renderable ones', () => {
    const models = [
      model({ id: 'big', approx_gb: 48 }),
      model({ id: 'rec', recommended: true, approx_gb: 7 }),
      model({ id: 'tiny', approx_gb: 4 }),
      model({ id: 'mid', approx_gb: 6.5 }),
      model({ id: 'video', api_renderable: false, approx_gb: 1 }),
    ]
    const picks = firstRunPicks(models)
    expect(picks?.recommended.id).toBe('rec')
    expect(picks?.others.map((m) => m.id)).toEqual(['tiny', 'mid'])
  })

  it('is null without a recommended renderable model', () => {
    expect(firstRunPicks([model({ id: 'a' })])).toBeNull()
    expect(firstRunPicks([model({ id: 'a', recommended: true, api_renderable: false })])).toBeNull()
  })

  it('leads with the recommended model when it fits (or has no verdict)', () => {
    for (const verdict of ['fits', 'tight'] as const) {
      const picks = firstRunPicks([
        model({ id: 'rec', recommended: true, fit: fit(verdict) }),
        model({ id: 'small', approx_gb: 4 }),
      ])
      expect(picks?.lead.id).toBe('rec')
    }
    expect(firstRunPicks([model({ id: 'rec', recommended: true, fit: null })])?.lead.id).toBe('rec')
  })

  // The picker and the fit check can disagree (#3076): a recommended model the server says won't
  // fit must not lead the block with an install button.
  it("leads with the largest renderable model that fits when the recommended one won't", () => {
    const models = [
      model({ id: 'rec', recommended: true, approx_gb: 48, fit: fit('wont_fit') }),
      model({ id: 'tiny', approx_gb: 4 }),
      model({ id: 'big', approx_gb: 12 }),
      model({ id: 'huge', approx_gb: 30, fit: fit('wont_fit') }),
      model({ id: 'video', api_renderable: false, approx_gb: 2 }),
    ]
    const picks = firstRunPicks(models)
    expect(picks?.recommended.id).toBe('rec')
    expect(picks?.lead.id).toBe('big')
    // The recommended model stays as a small card, first, then the smallest other one.
    expect(picks?.others.map((m) => m.id)).toEqual(['rec', 'tiny'])
  })

  it("keeps the recommended model in the lead when nothing else fits", () => {
    const picks = firstRunPicks([
      model({ id: 'rec', recommended: true, fit: fit('wont_fit') }),
      model({ id: 'other', approx_gb: 8, fit: fit('wont_fit') }),
      model({ id: 'unknown', approx_gb: 5, fit: null }),
    ])
    // An unknown verdict is not a verdict that it fits.
    expect(picks?.lead.id).toBe('rec')
    expect(picks?.others.map((m) => m.id)).toEqual(['unknown', 'other'])
  })
})

describe('downloadGb', () => {
  it('is the whole size for a model with nothing on disk', () => {
    expect(downloadGb(model({ state: 'none', approx_gb: 7.1, missing_gb: 7.1 }))).toBe(7.1)
  })

  it('is what is still missing for a partly installed model', () => {
    expect(downloadGb(model({ state: 'partial', approx_gb: 7.1, missing_gb: 2.4 }))).toBe(2.4)
  })

  it('falls back to the whole size when a partial model reports nothing missing', () => {
    expect(downloadGb(model({ state: 'partial', approx_gb: 7.1, missing_gb: 0 }))).toBe(7.1)
  })
})

describe('suggestAlternative', () => {
  const rec = model({ id: 'rec', recommended: true, approx_gb: 7 })
  const big = model({ id: 'big', approx_gb: 20 })
  const huge = model({ id: 'huge', approx_gb: 48, fit: fit('wont_fit') })

  it('is the recommended model when it is a different one and not a won\'t-fit', () => {
    expect(suggestAlternative(huge, [huge, rec, big])?.id).toBe('rec')
  })

  it('is the largest renderable model that fits when the recommended one does not', () => {
    const recWont = { ...rec, fit: fit('wont_fit') }
    expect(suggestAlternative(huge, [huge, recWont, big, model({ id: 'small', approx_gb: 4 })])?.id).toBe(
      'big',
    )
  })

  it('never suggests the model itself, and falls back when it is the recommended one', () => {
    const recWont = { ...rec, fit: fit('wont_fit') }
    expect(suggestAlternative(recWont, [recWont, big])?.id).toBe('big')
  })

  it('is null when nothing else fits or the verdicts are unknown', () => {
    expect(suggestAlternative(huge, [huge, { ...rec, fit: null }])).toBeNull()
    expect(suggestAlternative(huge, [huge])).toBeNull()
  })

  it('only suggests renderable models', () => {
    const editorOnly = model({ id: 'ed', api_renderable: false, recommended: true })
    expect(suggestAlternative(huge, [huge, editorOnly])).toBeNull()
  })
})

describe('removalPlan', () => {
  const lightning = model({ id: 'lightning', state: 'complete', workflow: 'lightning.json' })
  const full = model({ id: 'full', label: 'Full', state: 'complete' })
  const partial = model({ id: 'half', label: 'Half', state: 'partial' })
  const file = (subdir: string, relpath: string, models: string[], size = 100) => ({
    kind: subdir,
    subdir,
    relpath,
    size_bytes: size,
    models,
  })

  it('keeps a file another complete model loads and deletes the rest', () => {
    const files = [
      file('loras', 'lightning.safetensors', ['lightning']),
      file('diffusion_models', 'base.safetensors', ['lightning', 'full']),
      file('vae', 'unrelated.safetensors', ['full']),
    ]
    const plan = removalPlan(lightning, files, [lightning, full])
    expect(plan.deleted.map((f) => f.path)).toEqual(['loras/lightning.safetensors'])
    expect(plan.kept).toEqual([{ path: 'diffusion_models/base.safetensors', size: 100, usedBy: ['Full'] }])
    expect(plan.workflow).toBe('lightning.json')
  })

  it('does not hold a file back for a model that is only partly installed', () => {
    const files = [file('vae', 'v.safetensors', ['lightning', 'half'])]
    const plan = removalPlan(lightning, files, [lightning, partial])
    expect(plan.deleted.map((f) => f.path)).toEqual(['vae/v.safetensors'])
    expect(plan.kept).toEqual([])
  })
})

describe('shell helpers', () => {
  it('leaves a plain path bare and quotes one with spaces or quotes', () => {
    expect(shellQuote('/srv/models/comfyui/models')).toBe('/srv/models/comfyui/models')
    expect(shellQuote('/srv/my models')).toBe("'/srv/my models'")
    expect(shellQuote("/srv/it's")).toBe("'/srv/it'\\''s'")
  })

  it('builds the two fix commands', () => {
    expect(fixCommands('/srv/m')).toEqual([
      'sudo chgrp -R <hal0 group> /srv/m',
      'sudo chmod -R 2775 /srv/m',
    ])
  })
})

describe('fmtEta', () => {
  it('reads seconds, minutes and hours', () => {
    expect(fmtEta(4)).toBe('4 s')
    expect(fmtEta(61)).toBe('1 min 1 s')
    expect(fmtEta(600)).toBe('10 min')
    expect(fmtEta(3 * 3600 + 120)).toBe('3 h 2 min')
  })
})

describe('gbLabel', () => {
  it('drops a trailing .0 and keeps one decimal', () => {
    expect(gbLabel(6.9)).toBe('6.9')
    expect(gbLabel(12)).toBe('12')
    expect(gbLabel(2.5)).toBe('2.5')
    expect(gbLabel(47.94)).toBe('47.9')
  })
})
