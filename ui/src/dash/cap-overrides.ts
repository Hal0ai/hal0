// hal0 v3 dashboard — model drawer capability-overrides ledger (drawer
// overhaul PR-3, Task 8 / model drawer Option A).
//
// Replaces the four always-visible Auto/On/Off segmented rows (TypedCapSeg)
// with a ledger: Auto is invisible, only an overridden capability renders a
// chip, and "+ Override…" is the one control that lists what's left. Pure
// helpers here so the ledger's core logic (which caps show as chips, which
// feed the add-menu) is unit-testable without mounting the drawer.
//
// See docs/.devdocs/2026-09-01-slot-model-drawer-mockups.html panel 09 V1
// ("overrides ledger") — the binding visual vocabulary for this shape.

/** The four typed capability overrides the model drawer surfaces. Order here
 * is the order both the ledger's resting chips and the "+ Override…" menu
 * render in. */
export type CapId = 'thinking' | 'mtp' | 'jinja' | 'vision'

export interface CapDef {
  id: CapId
  /** Field label ("Thinking", "MTP", …). */
  label: string
  /** Decision-time copy shown in the "+ Override…" menu for this cap — what
   * choosing On or Off actually does. Copied from today's TypedCapSeg
   * FieldInfoIcon hint text (model-drawer.jsx, pre-ledger). */
  consequence: string
}

/** thinking | mtp | jinja | vision → null (auto/no opinion) | true (on) |
 * false (off) — the same tri-state `model.defaults.*` shape the drawer's
 * save body already writes (mtp/enable_thinking/jinja/vision). */
export type CapFlags = Record<CapId, boolean | null | undefined>

export interface OverriddenCap {
  id: CapId
  value: boolean
}

export const CAP_DEFS: CapDef[] = [
  {
    id: 'thinking',
    label: 'Thinking',
    consequence:
      'Auto defers to the profile/model default. On always shows reasoning steps before the answer; Off always hides them.',
  },
  {
    id: 'mtp',
    label: 'MTP',
    consequence:
      "auto — eligibility computed from the model's MTP tag × the slot's runtime. On/Off overrides that computation outright.",
  },
  {
    id: 'jinja',
    label: 'Jinja',
    consequence:
      "Auto defers to the model's own chat-template setting. On/Off forces jinja chat-template rendering either way.",
  },
  {
    id: 'vision',
    label: 'Vision',
    consequence:
      'Auto loads the mmproj vision projector whenever the model ships one. Off force-suppresses it — saves ~0.9 GB VRAM — even on a vision-capable model.',
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

/** Value-specific one-line summary for a single overridden chip (panel-07
 * style — see docs/.devdocs/2026-09-01-slot-model-drawer-mockups.html panel
 * 07's "Vision forced off — skips the mmproj projector, saves ~0.9 GB VRAM"
 * hint). Replaces the ledger's old resting hint, which permanently joined
 * every overridden cap's generic Auto/On/Off consequence text regardless of
 * which value was actually picked — a chip reading "off" got the same
 * blurb as one reading "on". Only the choice that changes host behavior in
 * a way worth calling out earns elaboration; forcing something on is
 * otherwise just the plain fact. */
export function overrideSummary(id: CapId, value: boolean): string {
  const def = CAP_DEFS.find((d) => d.id === id)
  const label = def ? def.label : id
  if (value) return `${label} forced on.`
  switch (id) {
    case 'thinking':
      return 'Thinking forced off — reasoning steps stay hidden.'
    case 'mtp':
      return 'MTP forced off — speculative decoding never runs, even if the runtime would otherwise enable it.'
    case 'jinja':
      return 'Jinja forced off — chat-template rendering never uses jinja.'
    case 'vision':
      return 'Vision forced off — skips the mmproj projector, saves ~0.9 GB VRAM.'
    default:
      return `${label} forced off.`
  }
}
