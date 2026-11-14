// Pure helpers for the NPU exclusion confirm (#3666): parse the backend's 409 `slot.npu_conflict`
// error and build the dialog copy. The details keys are fixed by the API contract.

export type NpuConflict = {
  slot: string
  holders: string[]
  holderEngine: 'flm' | 'xdna'
  unloads: string[]
  pinned: boolean
  pending: boolean
}

const strList = (v: unknown): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === 'string') : [])

/** The conflict a refused start carries, or null for any other error. */
export function npuConflictFromError(err: unknown): NpuConflict | null {
  const e = err as { code?: unknown; details?: Record<string, unknown> | null } | null
  if (!e || e.code !== 'slot.npu_conflict') return null
  const d = e.details ?? {}
  return {
    slot: typeof d.slot === 'string' ? d.slot : '',
    holders: strList(d.holders),
    holderEngine: d.holder_engine === 'xdna' ? 'xdna' : 'flm',
    unloads: strList(d.unloads),
    pinned: d.pinned === true,
    pending: d.pending === true,
  }
}

export function npuConflictCopy(c: NpuConflict): { title: string; body: string; confirmLabel: string } {
  const holder = c.holders.join(', ')
  const unloads = (c.unloads.length ? c.unloads : c.holders).join(', ')
  const lead =
    c.holderEngine === 'flm'
      ? `${c.slot} runs a per-op model, which needs the whole NPU.`
      : `${c.slot} needs the NPU, and ${holder} hold${c.holders.length === 1 ? 's' : ''} the NPU with per-op model${c.holders.length === 1 ? '' : 's'}.`
  const pinned = c.pinned ? ` ${holder} ${c.holders.length === 1 ? 'is' : 'are'} pinned.` : ''
  return {
    title: `Unload ${holder} to start ${c.slot}?`,
    body: `${lead} Starting it will unload ${unloads}.${pinned}`,
    confirmLabel: 'Unload and start',
  }
}
