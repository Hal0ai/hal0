// Pure parts of Models → FLM: a FastFlowLM catalogue row as the shared shell's state, the
// toolbar's filter and sort, and the per-role lines of the detail card.
import type { FlmCatalogRow, FlmPickerCap } from '@/dash/npu/npu-models-pure'
import { matchesStateFilter, type CatalogState } from './catalog-pure'

/** The pull this tab can see: the one `usePullJob` tracks. */
export interface FlmPull {
  inFlight: boolean
  modelId: string | null
  pct: number | null
}

/** Slots (lanes) that hold a row, each once, in catalogue order. */
export function flmUsers(row: Pick<FlmCatalogRow, 'inUseBy'>): string[] {
  const out: string[] = []
  for (const u of row.inUseBy) if (u.slot && !out.includes(u.slot)) out.push(u.slot)
  return out
}

/** "chat on npu, stt on npu": who uses a row, spelled for a sentence. */
export function flmUsedBy(row: Pick<FlmCatalogRow, 'inUseBy'>): string {
  return row.inUseBy.map((u) => `${u.role} on ${u.slot}`).join(', ')
}

export function flmState(row: FlmCatalogRow, pull: FlmPull): CatalogState {
  const pulling = pull.inFlight && pull.modelId === row.id
  return {
    installed: row.installed,
    inUseBy: flmUsers(row),
    download: pulling ? { pct: pull.pct } : null,
  }
}

export const FLM_ROLE_FILTERS: ReadonlyArray<{ id: FlmPickerCap; label: string }> = [
  { id: 'chat', label: 'Chat' },
  { id: 'stt', label: 'Speech to text' },
  { id: 'embed', label: 'Embeddings' },
]
/** The State chips FLM offers: its catalogue has no per-model update check. */
export const FLM_STATE_FILTERS = [
  { id: 'installed', label: 'Installed' },
  { id: 'inuse', label: 'In use' },
  { id: 'available', label: 'Not installed' },
] as const

export const FLM_SORT_FIELDS = [
  { id: 'name', label: 'name' },
  { id: 'size', label: 'size' },
  { id: 'family', label: 'family' },
]

export interface FlmFilter {
  q: string
  states: ReadonlyArray<string>
  roles: ReadonlyArray<string>
}

/** Text over name and family; State and Role are each OR within the group, AND across. */
export function filterFlmRows(rows: ReadonlyArray<FlmCatalogRow>, f: FlmFilter, pull: FlmPull): FlmCatalogRow[] {
  const needle = f.q.trim().toLowerCase()
  return rows.filter((r) => {
    if (needle && !`${r.id} ${r.family ?? ''}`.toLowerCase().includes(needle)) return false
    if (!matchesStateFilter(f.states, flmState(r, pull))) return false
    if (f.roles.length && !f.roles.some((role) => (r.caps as string[]).includes(role))) return false
    return true
  })
}

/** Sort a copy. A row with no size or family sorts last in either direction. */
export function sortFlmRows(rows: ReadonlyArray<FlmCatalogRow>, field: string, dir: 'asc' | 'desc'): FlmCatalogRow[] {
  const sign = dir === 'desc' ? -1 : 1
  const byName = (a: FlmCatalogRow, b: FlmCatalogRow) => a.id.localeCompare(b.id)
  return [...rows].sort((a, b) => {
    if (field === 'size') {
      if (a.sizeBytes == null || b.sizeBytes == null) return a.sizeBytes == null ? (b.sizeBytes == null ? byName(a, b) : 1) : -1
      return sign * (a.sizeBytes - b.sizeBytes) || byName(a, b)
    }
    if (field === 'family') {
      if (!a.family || !b.family) return !a.family ? (!b.family ? byName(a, b) : 1) : -1
      return sign * a.family.localeCompare(b.family) || byName(a, b)
    }
    return sign * byName(a, b)
  })
}

const ROLE_LABEL: Record<FlmPickerCap, string> = { chat: 'Chat', stt: 'Speech to text', embed: 'Embeddings' }
// The FLM roles (as `in_use_by` spells them) that each catalogue capability serves: the decision
// lane is a chat use, and the speech role is `asr`.
const ROLES_OF: Record<FlmPickerCap, ReadonlyArray<string>> = { chat: ['chat', 'decision'], stt: ['asr', 'stt'], embed: ['embed'] }

/** One line per capability: whether the model can serve it, and which roles use it for that now. */
export function flmRoleLines(row: FlmCatalogRow): Array<{ cap: FlmPickerCap; label: string; value: string; can: boolean }> {
  return (['chat', 'stt', 'embed'] as const).map((cap) => {
    const can = row.caps.includes(cap)
    const on = row.inUseBy.filter((u) => ROLES_OF[cap].includes(u.role)).map((u) => `${u.role} on ${u.slot}`)
    return { cap, label: ROLE_LABEL[cap], can, value: !can ? 'no' : on.length ? `yes · ${on.join(', ')}` : 'yes' }
  })
}
