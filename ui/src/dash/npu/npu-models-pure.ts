// Pure row builders for the NPU Models sub-tab (Slots → NPU → Models).
// FLM rows come from the closed FastFlowLM catalogue; GGUF rows from the registry joined to the
// ggml-xdna fit map (GET /api/runners/xdna/fit, whose keys are the GGUF llm rows).
import type { FlmModel } from '@/api/hooks/useFlmCatalog'
import type { XdnaFit } from '@/api/hooks/useXdnaFit'

export type FlmUsage = 'flm chat' | 'flm stt' | 'flm embed'
export interface FlmFilter {
  installed: 'installed' | 'all'
  cap: 'all' | 'chat' | 'stt' | 'embed'
}
export interface FlmRow {
  model: string
  installed: boolean
  capabilities: string[]
  family: string | null
  usedBy: string | null
}

/** Installed first, then by family (no family last), then by name. */
export function flmCatalogRows(models: FlmModel[] | null | undefined, f: FlmFilter, usage: Record<string, string>): FlmRow[] {
  const rows: FlmRow[] = []
  for (const m of models ?? []) {
    const capabilities = m.capabilities ?? []
    if (f.installed === 'installed' && !m.installed) continue
    if (f.cap !== 'all' && !capabilities.includes(f.cap)) continue
    rows.push({ model: m.model, installed: !!m.installed, capabilities, family: m.family ?? null, usedBy: usage[m.model] ?? null })
  }
  return rows.sort(
    (a, b) =>
      Number(b.installed) - Number(a.installed) ||
      (a.family ?? '￿').localeCompare(b.family ?? '￿') ||
      a.model.localeCompare(b.model),
  )
}

type RegModel = { id: string; type?: string; size?: string | null; architecture?: string | null }
type SlotLike = { name: string; binary?: string; model?: unknown }
export interface GgufRow {
  id: string
  arch: string | null
  size: string | null
  fit: 'fused' | 'per-op' | null
  reason: string
  usedBy: string[]
}

/** GGUF llm rows with their NPU fit. The fit map's keys decide membership (the backend already
 *  selected GGUF llm rows); a registry row of another type is dropped, a missing one shows bare. */
export function ggufNpuRows(models: RegModel[] | null | undefined, fit: XdnaFit | null | undefined, slots: SlotLike[] | null | undefined): GgufRow[] {
  const reg = new Map((models ?? []).map((m) => [m.id, m]))
  const out: GgufRow[] = []
  for (const [id, row] of Object.entries(fit ?? {})) {
    const m = reg.get(id)
    if (m && m.type !== 'llm') continue
    out.push({
      id,
      arch: m?.architecture || null,
      size: m?.size || null,
      fit: row.xdna_path,
      reason: row.reason,
      usedBy: (slots ?? []).filter((s) => s.binary === 'xdna' && s.model === id).map((s) => s.name),
    })
  }
  return out
}
