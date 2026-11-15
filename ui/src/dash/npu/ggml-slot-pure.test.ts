import { describe, expect, it } from 'vitest'
import {
  ggmlPerOpLine,
  ggmlPickerRows,
  ggmlVisibleRows,
  GGML_CTX_PRESETS,
  ggmlCreateBody,
  ggmlCtxError,
  ggmlEditBody,
  ggmlModelIds,
  ggmlNameError,
  ggmlProfileOptions,
  nextGgmlName,
  xdnaBlocker,
  type GgmlDraft,
} from './ggml-slot-pure'

const draft = (over: Partial<GgmlDraft> = {}): GgmlDraft => ({
  name: 'npu-gguf',
  model: 'qwen3-4b',
  profile: 'xdna',
  ctxSize: null,
  pmode: null,
  env: {},
  host_xrt: null,
  autoload: false,
  ...over,
})

describe('names', () => {
  it('picks the first free npu-gguf name against every slot', () => {
    expect(nextGgmlName([])).toBe('npu-gguf')
    expect(nextGgmlName(['npu-gguf', 'agent'])).toBe('npu-gguf-2')
    expect(nextGgmlName(['npu-gguf', 'npu-gguf-2'])).toBe('npu-gguf-3')
  })
  it('rejects a name any slot already uses, and bad characters', () => {
    expect(ggmlNameError('agent', ['agent', 'flm'])).toBe('name already in use')
    expect(ggmlNameError('Bad_Name', [])).toBe('lowercase + dashes only')
    expect(ggmlNameError('npu-gguf', ['agent'])).toBeNull()
    for (const n of ['npu', 'image', 'endpoints', 'stacks', 'runner-images']) expect(ggmlNameError(n, [])).toBe('that name is a Slots tab')
  })
})

describe('context', () => {
  it('has the four presets and bounds 128..16777216', () => {
    expect(GGML_CTX_PRESETS).toEqual([8192, 16384, 32768, 65536])
    expect(ggmlCtxError(64)).not.toBeNull()
    expect(ggmlCtxError(128)).toBeNull()
    expect(ggmlCtxError(16777216)).toBeNull()
    expect(ggmlCtxError(16777217)).not.toBeNull()
    expect(ggmlCtxError(1000.5)).not.toBeNull()
  })
})

describe('ggmlProfileOptions', () => {
  it('enables llama-server profiles and disables FLM and other runners, xdna first', () => {
    const opts = ggmlProfileOptions([
      { name: 'chat' },
      { name: 'flm', runner: 'flm', runtime_family: 'flm' },
      { name: 'halogen', runner: 'halogen', runtime_family: 'halogen' },
      { name: 'xdna', runner: 'xdna', runtime_family: 'llama-server' },
    ] as never)
    expect(opts.map((o) => o.name)).toEqual(['xdna', 'chat', 'flm', 'halogen'])
    const by = Object.fromEntries(opts.map((o) => [o.name, o]))
    expect(by.xdna.disabled).toBe(false)
    expect(by.chat.disabled).toBe(false)
    expect(by.flm.disabled).toBe(true)
    expect(by.flm.reason).toBeTruthy()
    expect(by.halogen.disabled).toBe(true)
  })
})

describe('ggmlCreateBody', () => {
  it('omits xdna when pmode, env and host_xrt are all empty', () => {
    const b = ggmlCreateBody(draft({ env: { GGML_XDNA_HEAD: '' } }))
    expect(b).toEqual({
      name: 'npu-gguf',
      type: 'llm',
      runtime: 'container',
      binary: 'xdna',
      device: 'npu',
      model: 'qwen3-4b',
      profile: 'xdna',
      autoload: false,
    })
    expect('xdna' in b).toBe(false)
  })
  it('sends the xdna table with empty env values dropped', () => {
    const b = ggmlCreateBody(draft({ pmode: 'turbo', env: { A: '1', B: '' }, host_xrt: '/opt/xrt' }))
    expect(b.xdna).toEqual({ pmode: 'turbo', env: { A: '1' }, host_xrt: '/opt/xrt' })
  })
  it('carries a per-slot context as tune.ctx_size', () => {
    expect(ggmlCreateBody(draft({ ctxSize: 65536 })).tune).toEqual({ ctx_size: 65536 })
    expect('tune' in ggmlCreateBody(draft())).toBe(false)
  })
})

describe('fixed knobs', () => {
  const saved = draft({ env: { GGML_XDNA_PGEMM_GLU_A: '1', GGML_XDNA_HEAD: '1' } })
  it('never sends GLU_A on an unrelated change, even with no catalogue', () => {
    expect(ggmlEditBody({ ...saved, pmode: 'turbo' }, saved)).toEqual({ xdna: { pmode: 'turbo', env: { GGML_XDNA_HEAD: '1' }, host_xrt: null } })
  })
  it('strips catalogue-fixed names and is not dirty on its own', () => {
    const s2 = draft({ env: { OTHER_PINNED: '1' } })
    expect(ggmlEditBody({ ...s2, pmode: 'turbo' }, s2, ['OTHER_PINNED'])).toEqual({ xdna: { pmode: 'turbo', env: {}, host_xrt: null } })
    expect(ggmlEditBody(saved, draft({ env: { GGML_XDNA_HEAD: '1' } }))).toEqual({})
  })
  it('strips it on create too', () => {
    expect(ggmlCreateBody(draft({ env: { GGML_XDNA_PGEMM_GLU_A: '1' } })).xdna).toBeUndefined()
  })
})

describe('ggmlEditBody', () => {
  it('sends only the changed key; a pmode change sends the whole xdna table', () => {
    expect(ggmlEditBody(draft({ pmode: 'turbo' }), draft())).toEqual({
      xdna: { pmode: 'turbo', env: {}, host_xrt: null },
    })
  })
  it('sends nothing when nothing changed', () => {
    expect(ggmlEditBody(draft(), draft())).toEqual({})
  })
  it('maps model, profile, autoload and context', () => {
    const b = ggmlEditBody(
      draft({ model: 'b', profile: 'chat', autoload: true, ctxSize: 32768 }),
      draft({ model: 'a' }),
    )
    expect(b).toEqual({ model: { default: 'b' }, profile: 'chat', autoload: true, tune: { ctx_size: 32768 } })
  })
  it('clears the context override with tune.ctx_size null', () => {
    expect(ggmlEditBody(draft({ ctxSize: null }), draft({ ctxSize: 16384 }))).toEqual({ tune: { ctx_size: null } })
  })
  it('clears the xdna table with null', () => {
    expect(ggmlEditBody(draft(), draft({ pmode: 'turbo', env: { A: '1' } }))).toEqual({ xdna: null })
  })
})

describe('ggmlModelIds', () => {
  const fit = { 'blob-sha-a': { xdna_path: 'fused', reason: '' }, 'tiny.gguf': { xdna_path: 'per-op', reason: '' }, 'embed-x': { xdna_path: null, reason: '' } }
  it('lists exactly the fit map keys, and ignores registry llm rows the fit map lacks', () => {
    const models = [{ id: 'blob-sha-a', type: 'llm' }, { id: 'tiny.gguf', type: 'llm' }, { id: 'embed-x', type: 'embedding' }, { id: 'flm-only', type: 'llm' }]
    expect(ggmlModelIds(models, fit)).toEqual(['blob-sha-a', 'tiny.gguf'])
  })
  it('is empty without a fit map', () => {
    expect(ggmlModelIds([{ id: 'a', type: 'llm' }], null)).toEqual([])
  })
})

describe('xdnaBlocker', () => {
  const perOp = (reason: string) => xdnaBlocker({ xdna_path: 'per-op', reason })
  it('names the one check that failed, as a short chip label', () => {
    expect(perOp("architecture 'gemma4' is not 'qwen35'")).toMatchObject({ label: 'gemma4 arch', fix: false })
    expect(perOp('embedding_length 5120 is not 1024')).toMatchObject({ label: '5120 wide', fix: false })
    expect(perOp('blk.0.ssm_out.weight is Q8_0, not Q5_K')).toMatchObject({ label: 'ssm_out q8_0', fix: true })
    expect(perOp('blk.0.ssm_out.weight is missing, not Q5_K')).toMatchObject({ label: 'no ssm_out', fix: false })
  })
  it('keeps the full reason in the tooltip', () => {
    expect(perOp('embedding_length 5120 is not 1024')!.title).toContain('embedding_length 5120 is not 1024')
  })
  it('is null for fused, null-path, and unrecognised reasons', () => {
    expect(xdnaBlocker({ xdna_path: 'fused', reason: 'x' })).toBeNull()
    expect(xdnaBlocker({ xdna_path: null, reason: 'not a GGUF file' })).toBeNull()
    expect(perOp('something new')).toBeNull()
  })
})

describe('ggml card model picker', () => {
  const fit = {
    a: { xdna_path: 'fused', reason: '' },
    b: { xdna_path: 'per-op', reason: "architecture 'lfm2' is not 'qwen35'", npu_ok: true },
    big: { xdna_path: 'per-op', reason: '', npu_ok: false, npu_note: '74B too large' },
    rr: { xdna_path: null, reason: 'not gguf' },
    emb: { xdna_path: 'fused', reason: '' },
  }
  const models = [
    { id: 'a', type: 'llm', quant: 'Q4_K_M', size: '2.1 GB', installed: true },
    { id: 'big', type: 'llm', installed: false },
    { id: 'emb', type: 'embedding' },
  ]

  it('lists non-null paths, drops non-llm registry rows, and carries chips data', () => {
    const rows = ggmlPickerRows(models, fit)
    expect(rows.map((r) => r.id)).toEqual(['a', 'b', 'big'])
    expect(rows[0]).toMatchObject({ path: 'fused', quant: 'Q4_K_M', size: '2.1 GB', installed: true, npuOk: true })
    expect(rows[2]).toMatchObject({ installed: false, npuOk: false, npuNote: '74B too large' })
  })

  it('a missing npu_ok is ok, and a row absent from the registry counts as installed', () => {
    const rows = ggmlPickerRows([], { a: { xdna_path: 'fused', reason: '' } })
    expect(rows[0]).toMatchObject({ npuOk: true, installed: true, npuNote: null })
  })

  it('hides npu_ok:false rows unless showAll, but keeps the current model', () => {
    const rows = ggmlPickerRows(models, fit)
    expect(ggmlVisibleRows(rows, false).rows.map((r) => r.id)).toEqual(['a', 'b'])
    expect(ggmlVisibleRows(rows, false).hidden).toBe(1)
    expect(ggmlVisibleRows(rows, true).rows.map((r) => r.id)).toEqual(['a', 'b', 'big'])
    expect(ggmlVisibleRows(rows, false, 'big')).toMatchObject({ hidden: 0 })
    expect(ggmlVisibleRows(rows, false, 'big').rows.map((r) => r.id)).toEqual(['a', 'b', 'big'])
  })

  it('ggmlPerOpLine turns each model_fit reason into one sentence', () => {
    expect(ggmlPerOpLine({ xdna_path: 'per-op', reason: "architecture 'lfm2' is not 'qwen35'" })).toBe('Per-op path: architecture lfm2, not qwen35. Correct, slower decode.')
    expect(ggmlPerOpLine({ xdna_path: 'per-op', reason: 'embedding_length 2048 is not 1024' })).toContain('embedding width 2048, not 1024')
    expect(ggmlPerOpLine({ xdna_path: 'per-op', reason: 'blk.0.ssm_out.weight is Q4_K, not Q5_K' })).toContain('ssm_out is Q4_K, not Q5_K')
    expect(ggmlPerOpLine({ xdna_path: 'per-op', reason: 'odd' })).toBe('Per-op path: odd')
    expect(ggmlPerOpLine({ xdna_path: 'fused', reason: '' })).toBeNull()
    expect(ggmlPerOpLine(null)).toBeNull()
  })
})
