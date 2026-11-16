// Pure builders for the NPU tab's model lists.
// FLM picker options come from the closed FastFlowLM catalogue (one dropdown per modality on the
// FLM lane); GGUF rows (Slots → NPU → Models) from the registry joined to the ggml-xdna fit map
// (GET /api/runners/xdna/fit, whose keys are the GGUF llm rows).
import type { FlmModel, FlmModelUse } from '@/api/hooks/useFlmCatalog'
import type { XdnaFit } from '@/api/hooks/useXdnaFit'

export interface FlmPickerOption {
  id: string
  installed: boolean
  sizeBytes: number | null
  family: string | null
  /** A chat model that also takes audio input (the catalogue lists it as chat + stt). */
  audio: boolean
}
export interface FlmPickerGroups {
  installed: FlmPickerOption[]
  download: FlmPickerOption[]
}
/** The modality a picker serves, spelled as the catalogue spells its capabilities. */
export type FlmPickerCap = 'chat' | 'stt' | 'embed'

/** Does a catalogue row serve `cap`? A row with no `capabilities` field at all (an older
 *  payload) is treated as chat-only, as the lane has always done; an empty list serves nothing. */
export function flmServes(m: Pick<FlmModel, 'capabilities'>, cap: FlmPickerCap): boolean {
  if (!m.capabilities) return cap === 'chat'
  return m.capabilities.includes(cap)
}

/** Options for one modality's model dropdown, split into installed and download-on-pick.
 *  Each group is sorted by family (none last), then name. `current` (the saved or staged model)
 *  is always offered, as an installed row when the catalogue does not list it. */
export function flmPickerOptions(models: FlmModel[] | null | undefined, cap: FlmPickerCap, current?: string | null): FlmPickerGroups {
  const opts: FlmPickerOption[] = []
  for (const m of models ?? []) {
    if (!m.model || !flmServes(m, cap)) continue
    opts.push({
      id: m.model,
      installed: m.installed !== false,
      sizeBytes: typeof m.size_bytes === 'number' && m.size_bytes > 0 ? m.size_bytes : null,
      family: m.family || null,
      audio: cap === 'chat' && (m.capabilities ?? []).includes('stt'),
    })
  }
  if (current && !opts.some((o) => o.id === current)) {
    opts.push({ id: current, installed: true, sizeBytes: null, family: null, audio: false })
  }
  const cmp = (a: FlmPickerOption, b: FlmPickerOption) =>
    (a.family ?? '￿').localeCompare(b.family ?? '￿') || a.id.localeCompare(b.id)
  return {
    installed: opts.filter((o) => o.installed).sort(cmp),
    download: opts.filter((o) => !o.installed).sort(cmp),
  }
}

export interface FlmCatalogRow extends FlmPickerOption {
  /** The roles the model serves, spelled as the catalogue spells them. */
  caps: FlmPickerCap[]
  /** FLM roles configured or serving it (the catalogue's `in_use_by`); empty when free. */
  inUseBy: FlmModelUse[]
}

/** One row per catalogue model across every modality (Models → FLM). Built from
 *  `flmPickerOptions`, so size, family, installed and audio read exactly as the lane dropdown
 *  reads them. Installed first, then by family and name. */
export function flmCatalogRows(models: FlmModel[] | null | undefined): FlmCatalogRow[] {
  const byId = new Map<string, FlmCatalogRow>()
  const uses = new Map<string, FlmModelUse[]>()
  for (const m of models ?? []) {
    if (m.model && Array.isArray(m.in_use_by)) uses.set(m.model, m.in_use_by)
  }
  for (const cap of ['chat', 'stt', 'embed'] as const) {
    const g = flmPickerOptions(models, cap)
    for (const o of [...g.installed, ...g.download]) {
      const hit = byId.get(o.id)
      if (hit) {
        hit.caps.push(cap)
        hit.audio = hit.audio || o.audio
      } else byId.set(o.id, { ...o, caps: [cap], inUseBy: uses.get(o.id) ?? [] })
    }
  }
  return [...byId.values()].sort(
    (a, b) =>
      Number(b.installed) - Number(a.installed) ||
      (a.family ?? '￿').localeCompare(b.family ?? '￿') ||
      a.id.localeCompare(b.id),
  )
}

/** `6.4 GB`, `640 MB`; null when the catalogue gave no size. */
export function formatFlmSize(bytes: number | null | undefined): string | null {
  if (typeof bytes !== 'number' || !(bytes > 0)) return null
  const gb = bytes / 1e9
  if (gb >= 0.95) return `${gb >= 10 ? Math.round(gb) : gb.toFixed(1)} GB`
  return `${Math.round(bytes / 1e6)} MB`
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
