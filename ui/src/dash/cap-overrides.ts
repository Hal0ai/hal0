// hal0 v3 dashboard — model drawer file-fact overrides ledger.
//
// ADR-0029: the model carries FILE FACTS only. Two of them are tri-state
// (Auto / On / Off) and live on the model as top-level fields (`model.mtp`, `model.jinja`): whether the file has MTP
// heads (`mtp`) and whether its chat template works with --jinja (`jinja`).
// Thinking and vision are tunes now and live on the profile.
//
// Auto is invisible: only an overridden fact renders a chip, and
// "+ Override…" is the one control that lists what's left. Pure helpers here
// so the ledger's core logic is unit-testable without mounting the drawer.

/** The typed file-fact overrides the model drawer surfaces. Order here
 * is the order both the ledger's resting chips and the "+ Override…" menu
 * render in. */
export type CapId = 'mtp' | 'jinja'

export interface CapDef {
  id: CapId
  /** Field label ("MTP", "Jinja"). */
  label: string
  /** Decision-time copy shown in the "+ Override…" menu for this cap — what
   * choosing On or Off actually does. Copied from today's TypedCapSeg
   * FieldInfoIcon hint text (model-drawer.jsx, pre-ledger). */
  consequence: string
}

/** mtp | jinja → null (auto/no opinion) | true (on) | false (off) — the same
 * tri-state `model.mtp` / `model.jinja` shape the drawer's save body writes. */
export type CapFlags = Record<CapId, boolean | null | undefined>

export interface OverriddenCap {
  id: CapId
  value: boolean
}

export const CAP_DEFS: CapDef[] = [
  {
    id: 'mtp',
    label: 'MTP heads',
    consequence:
      "Auto reads the model's 'mtp' tag. On says this file has MTP heads, so a profile with MTP auto may draft with them. Off says it has none, whatever the tag says.",
  },
  {
    id: 'jinja',
    label: 'Jinja',
    consequence:
      "Auto launches with --jinja. Off records that this file's chat template does not work with --jinja, so hal0 launches it without.",
  },
]

/** Only the overridden (non-null) caps, in CAP_DEFS order — the ledger's
 * resting-state chips. Auto (null/undefined) is invisible by design. */
export function overriddenCaps(flags: CapFlags): OverriddenCap[] {
  const out: OverriddenCap[] = []
  for (const def of CAP_DEFS) {
    const v = flags[def.id]
    if (v === null || v === undefined) continue
    out.push({ id: def.id, value: !!v })
  }
  return out
}

/** The caps NOT yet overridden — what "+ Override…" offers, in CAP_DEFS
 * order. */
export function remainingCaps(flags: CapFlags): CapDef[] {
  return CAP_DEFS.filter((def) => {
    const v = flags[def.id]
    return v === null || v === undefined
  })
}

/** Value-specific one-line summary for a single overridden chip. Only the
 * choice that changes host behavior in a way worth calling out earns
 * elaboration; forcing something on is otherwise just the plain fact. */
export function overrideSummary(id: CapId, value: boolean): string {
  const def = CAP_DEFS.find((d) => d.id === id)
  const label = def ? def.label : id
  if (value) return `${label} forced on.`
  switch (id) {
    case 'mtp':
      return 'MTP heads marked absent — profiles never draft with this file, even if it is tagged mtp.'
    case 'jinja':
      return 'Jinja marked unsupported — this file launches without --jinja.'
    default:
      return `${label} forced off.`
  }
}
