// Pure helpers behind the GGML slot form (GgmlSlotForm.jsx): drafts, validation and the create
// and edit request bodies. A GGML slot is an xdna slot (ADR-0057): `binary: 'xdna'`, `device: 'npu'`.
import { SLOT_NAME_RE } from '../slots/slot-name'

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

/** Checked against every slot name (GPU, remote alias, FLM shadow), not just NPU ones. */
export function ggmlNameError(name: string, allSlotNames: string[]): string | null {
  if (!SLOT_NAME_RE.test(name)) return 'lowercase + dashes only'
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

function xdnaTable(d: GgmlDraft): XdnaTable {
  const env: Record<string, string> = {}
  for (const [k, v] of Object.entries(d.env || {})) if (v !== '' && v != null) env[k] = v
  return { pmode: d.pmode || null, env, host_xrt: d.host_xrt || null }
}

function sameXdna(a: XdnaTable, b: XdnaTable): boolean {
  if (a.pmode !== b.pmode || a.host_xrt !== b.host_xrt) return false
  const ak = Object.keys(a.env)
  return ak.length === Object.keys(b.env).length && ak.every((k) => a.env[k] === b.env[k])
}

const xdnaEmpty = (t: XdnaTable) => !t.pmode && !t.host_xrt && Object.keys(t.env).length === 0

export function ggmlCreateBody(d: GgmlDraft): Record<string, unknown> {
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
  const x = xdnaTable(d)
  if (!xdnaEmpty(x)) body.xdna = x
  if (d.ctxSize != null) body.tune = { ctx_size: d.ctxSize }
  return body
}

export function ggmlEditBody(d: GgmlDraft, saved: GgmlDraft): Record<string, unknown> {
  const body: Record<string, unknown> = {}
  if (d.model !== saved.model && d.model) body.model = { default: d.model }
  if (d.profile !== saved.profile) body.profile = d.profile
  if (d.autoload !== saved.autoload) body.autoload = d.autoload
  if (d.ctxSize !== saved.ctxSize) body.tune = { ctx_size: d.ctxSize }
  const x = xdnaTable(d)
  const s = xdnaTable(saved)
  if (!sameXdna(x, s)) body.xdna = xdnaEmpty(x) ? null : x
  return body
}
