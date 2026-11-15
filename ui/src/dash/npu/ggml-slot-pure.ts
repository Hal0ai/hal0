// Pure helpers behind the GGML slot form (GgmlSlotForm.jsx): drafts, validation and the create
// and edit request bodies. A GGML slot is an xdna slot (ADR-0057): `binary: 'xdna'`, `device: 'npu'`.
import { SLOT_NAME_RE } from '../slots/slot-name'
import { isReservedSlotsTab } from '../slots-route'

export type GgmlDraft = {
  name: string
  model: string | null
  profile: string
  /** null = use the profile's `ctx_size`. */
  ctxSize: number | null
  pmode: string | null
  env: Record<string, string>
  host_xrt: string | null
  autoload: boolean
}

export type GgmlProfileRow = { name: string; runner?: string | null; runtime_family?: string | null; quarantined?: boolean }
export type GgmlProfileOption = { name: string; disabled: boolean; reason?: string }

export const GGML_CTX_PRESETS = [8192, 16384, 32768, 65536]
export const GGML_CTX_MIN = 128
export const GGML_CTX_MAX = 16777216
export const GGML_BASE_NAME = 'npu-gguf'

/** Profile bound (profiles.toml header): an integer in 128..16777216. */
export function ggmlCtxError(n: number): string | null {
  if (!Number.isInteger(n)) return 'whole number'
  if (n < GGML_CTX_MIN || n > GGML_CTX_MAX) return `${GGML_CTX_MIN}..${GGML_CTX_MAX.toLocaleString('en-US')}`
  return null
}

export function nextGgmlName(allSlotNames: string[]): string {
  const taken = new Set(allSlotNames)
  if (!taken.has(GGML_BASE_NAME)) return GGML_BASE_NAME
  for (let i = 2; ; i += 1) {
    const n = `${GGML_BASE_NAME}-${i}`
    if (!taken.has(n)) return n
  }
}

/** Why Add / New slot are unavailable until the ggml-xdna runner image is pulled. */
export const GGML_IMAGE_MISSING = 'The ggml-xdna runner image is not pulled yet.'

/** Checked against every slot name (GPU, remote alias, FLM shadow), not just NPU ones. */
export function ggmlNameError(name: string, allSlotNames: string[]): string | null {
  if (!SLOT_NAME_RE.test(name)) return 'lowercase + dashes only'
  if (isReservedSlotsTab(name)) return 'that name is a Slots tab'
  if (allSlotNames.includes(name)) return 'name already in use'
  return null
}

/** Profiles an xdna slot may use: runner unset or `xdna`, llama-server family. `xdna` first. */
export function ggmlProfileOptions(profiles: GgmlProfileRow[] | null | undefined): GgmlProfileOption[] {
  const out: GgmlProfileOption[] = (profiles ?? [])
    .filter((p) => p && !p.quarantined)
    .map((p) => {
      const runner = p.runner || ''
      const family = p.runtime_family || ''
      if (runner && runner !== 'xdna') return { name: p.name, disabled: true, reason: `runs on ${runner}, not ggml-xdna` }
      if (family && family !== 'llama-server') return { name: p.name, disabled: true, reason: `${family} profile, not llama-server` }
      return { name: p.name, disabled: false }
    })
  return out.sort((a, b) => Number(b.name === 'xdna') - Number(a.name === 'xdna'))
}

type XdnaTable = { pmode: string | null; env: Record<string, string>; host_xrt: string | null }

/** Never sent from the UI, whatever the saved env holds (hal0 pins it). */
export const GGML_ALWAYS_FIXED = 'GGML_XDNA_PGEMM_GLU_A'

function xdnaTable(d: GgmlDraft, fixed: readonly string[] = []): XdnaTable {
  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(d.env || {})) {
    if (k === GGML_ALWAYS_FIXED || fixed.includes(k)) continue
    if (v !== '' && v != null) env[k] = v
  }
  return { pmode: d.pmode || null, env, host_xrt: d.host_xrt || null }
}

function sameXdna(a: XdnaTable, b: XdnaTable): boolean {
  if (a.pmode !== b.pmode || a.host_xrt !== b.host_xrt) return false
  const ak = Object.keys(a.env)
  return ak.length === Object.keys(b.env).length && ak.every((k) => a.env[k] === b.env[k])
}

const xdnaEmpty = (t: XdnaTable) => !t.pmode && !t.host_xrt && Object.keys(t.env).length === 0

export function ggmlCreateBody(d: GgmlDraft, fixed: readonly string[] = []): Record<string, unknown> {
  const body: Record<string, unknown> = {
    name: d.name,
    type: 'llm',
    runtime: 'container',
    binary: 'xdna',
    device: 'npu',
    model: d.model,
    profile: d.profile,
    autoload: d.autoload,
  }
  const x = xdnaTable(d, fixed)
  if (!xdnaEmpty(x)) body.xdna = x
  if (d.ctxSize != null) body.tune = { ctx_size: d.ctxSize }
  return body
}

export function ggmlEditBody(d: GgmlDraft, saved: GgmlDraft, fixed: readonly string[] = []): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  if (d.model !== saved.model && d.model) body.model = { default: d.model }
  if (d.profile !== saved.profile) body.profile = d.profile
  if (d.autoload !== saved.autoload) body.autoload = d.autoload
  if (d.ctxSize !== saved.ctxSize) body.tune = { ctx_size: d.ctxSize }
  const x = xdnaTable(d, fixed)
  const s = xdnaTable(saved, fixed)
  if (!sameXdna(x, s)) body.xdna = xdnaEmpty(x) ? null : x
  return body
}

export const GGML_REQUANT_CMD = 'llama-quantize --tensor-type ssm_out=q5_K <in> <out> Q4_K_M'

/** The requantize command applies only when ssm_out's type is the sole thing keeping a qwen35
 *  1024-wide model off the fused path (model_fit.py reason `…ssm_out.weight is X, not Q5_K`). */
export function ggmlRequantHint(row: { xdna_path: string | null; reason?: string } | null | undefined): boolean {
  return !!row && row.xdna_path === 'per-op' && /ssm_out\.weight is .+, not Q5_K/.test(row.reason || '')
}

/** One chip naming what keeps a model off the fused path, parsed from the three model_fit.py
 *  per-op reasons (architecture, embedding_length, ssm_out type). `fix` marks the one blocker a
 *  requantize clears. Null for a fused row, a null-path row, or a reason this does not recognise
 *  (the caller keeps the full reason as a tooltip either way). */
export interface XdnaBlocker {
  label: string
  title: string
  fix: boolean
}

export function xdnaBlocker(row: { xdna_path: string | null; reason?: string } | null | undefined): XdnaBlocker | null {
  if (!row || row.xdna_path !== 'per-op') return null
  const r = row.reason || ''
  let m = /^architecture '([^']*)' is not 'qwen35'/.exec(r)
  if (m) return { label: `${m[1] || '?'} arch`, title: `The fused kernel is Qwen3.5-only (${r})`, fix: false }
  m = /^embedding_length (\S+) is not (\d+)/.exec(r)
  if (m) return { label: `${m[1]} wide`, title: `The fused kernel needs a ${m[2]}-wide model (${r})`, fix: false }
  m = /ssm_out\.weight is (.+), not Q5_K/.exec(r)
  if (m && m[1] === 'missing') return { label: 'no ssm_out', title: `The fused kernel needs an ssm_out tensor in Q5_K (${r})`, fix: false }
  if (m) return { label: `ssm_out ${m[1].toLowerCase()}`, title: `The fused kernel needs ssm_out in Q5_K; requantize to fix (${r})`, fix: true }
  return null
}

/** Ids the GGML slot form offers. The backend fit map (GET /api/runners/xdna/fit) is the authority
 *  for "GGUF chat model" (its keys are the rows `_is_gguf_llm` selects), the same rule the NPU Models
 *  table uses; the registry only vetoes a row it knows to be a non-llm type. Filtering the registry
 *  client-side instead listed llm rows `_is_gguf_llm` excludes (non-GGUF or non-FLM backends, empty-backend
 *  rows whose path is not .gguf), and gave them no fit chip (#3647). */
export function ggmlModelIds(
  models: Array<{ id: string; type?: string }> | null | undefined,
  fit: Record<string, unknown> | null | undefined,
): string[] {
  const reg = new Map((models ?? []).map((m) => [m.id, m]))
  return Object.keys(fit ?? {}).filter((id) => {
    const m = reg.get(id)
    return !m || m.type === 'llm'
  })
}

/** One row of a GGML slot card's model dropdown. */
export interface GgmlPickerRow {
  id: string
  path: 'fused' | 'per-op'
  reason: string
  quant: string | null
  size: string | null
  /** false = the registry lists it but the file is not on disk yet. */
  installed: boolean
  /** false = the backend marks it impractical on the NPU (`npu_ok`); a missing field is ok. */
  npuOk: boolean
  npuNote: string | null
}

type FitLike = { xdna_path: string | null; reason?: string; npu_ok?: boolean; npu_note?: string | null }
type RegLike = { id: string; type?: string; quant?: string | null; size?: string | null; installed?: boolean }

/** Models an xdna slot can run: fit rows with a non-null `xdna_path` (the backend's authority for
 *  "GGUF chat model that runs on the NPU"), minus registry rows known to be a non-llm type. */
export function ggmlPickerRows(models: RegLike[] | null | undefined, fit: Record<string, FitLike> | null | undefined): GgmlPickerRow[] {
  const reg = new Map((models ?? []).map((m) => [m.id, m]))
  const rows: GgmlPickerRow[] = []
  for (const [id, f] of Object.entries(fit ?? {})) {
    if (!f || (f.xdna_path !== 'fused' && f.xdna_path !== 'per-op')) continue
    const m = reg.get(id)
    if (m && m.type && m.type !== 'llm') continue
    rows.push({
      id,
      path: f.xdna_path,
      reason: f.reason || '',
      quant: m?.quant || null,
      size: m?.size || null,
      installed: m ? m.installed !== false : true,
      npuOk: f.npu_ok !== false,
      npuNote: f.npu_note || null,
    })
  }
  return rows
}

/** Rows the dropdown lists: `npu_ok === false` rows are hidden unless `showAll`. The slot's current
 *  model always stays listed so the closed trigger can name it. */
export function ggmlVisibleRows(rows: GgmlPickerRow[], showAll: boolean, current?: string | null): { rows: GgmlPickerRow[]; hidden: number } {
  const hidden = rows.filter((r) => !r.npuOk && r.id !== current).length
  return { rows: showAll ? rows : rows.filter((r) => r.npuOk || r.id === current), hidden }
}

/** One plain sentence for the card's per-op line, from the model_fit.py reason. Null for a fused or
 *  unknown row. An unrecognised reason is passed through verbatim. */
export function ggmlPerOpLine(row: { xdna_path: string | null; reason?: string } | null | undefined): string | null {
  if (!row || row.xdna_path !== 'per-op') return null
  const r = row.reason || ''
  const tail = 'Correct, slower decode.'
  let m = /^architecture '([^']*)' is not '([^']*)'/.exec(r)
  if (m) return `Per-op path: architecture ${m[1] || '?'}, not ${m[2]}. ${tail}`
  m = /^embedding_length (\S+) is not (\d+)/.exec(r)
  if (m) return `Per-op path: embedding width ${m[1]}, not ${m[2]}. ${tail}`
  m = /ssm_out\.weight is (.+), not Q5_K/.exec(r)
  if (m) return `Per-op path: ssm_out is ${m[1]}, not Q5_K. ${tail}`
  return r ? `Per-op path: ${r}` : `Per-op path. ${tail}`
}
