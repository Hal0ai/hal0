// `#slots/<param>`: the Slots page's route param is `<tab>[/<sub>]`.
//
// `parseRoute` (main.jsx) keeps everything after `slots/` as one string, so `#slots/image/models`
// has the param `image/models`. The first segment names the tab ("image", "endpoints",
// "runner-images", "stacks", or a slot name that opens the edit drawer on Inference); the second
// is the tab's own sub-tab, which Image Gen and NPU (`#slots/npu/<lanes|models|engines>`) have. Everything that compares a param with a
// tab name compares the first segment.

/** The tab segment of a Slots route param, or null for a bare `#slots`. */
export function slotsTab(param: string | null | undefined): string | null {
  if (!param) return null
  return param.split('/')[0] || null
}

/** The sub-tab segment of a Slots route param, or null when there is none. */
export function slotsSub(param: string | null | undefined): string | null {
  if (!param) return null
  return param.split('/')[1] || null
}

/** Whether a route is the Image Gen pane (`#slots/image[/<sub>]`). The pane's own GPU status line
 *  says what the global image-mode banner would, so the banner is not shown there. */
export function isImageGenRoute(route: string | null | undefined, param: string | null | undefined): boolean {
  return route === 'slots' && slotsTab(param) === 'image'
}

/** Slots-page tabs that have their own pane. A route param naming one is that tab, never a slot:
 *  a slot called `npu` is not reachable as `#slots/npu` (reserved tab names win over slot names). */
export const SLOTS_RESERVED_TABS = ['image', 'npu', 'endpoints', 'runner-images', 'stacks'] as const

/** Whether the tab segment of a Slots route param is one of the reserved tab names. */
export function isReservedSlotsTab(param: string | null | undefined): boolean {
  const t = slotsTab(param)
  return t != null && (SLOTS_RESERVED_TABS as readonly string[]).includes(t)
}

/** The hash that opens a slot's edit drawer. A slot named like a reserved tab goes through
 *  `#slots/edit/<name>`, because `#slots/<name>` would open the tab instead. */
export function slotEditHash(name: string): string {
  return isReservedSlotsTab(name) ? `#slots/edit/${name}` : `#slots/${name}`
}

/** The slot a Slots route param asks to edit: `edit/<name>` → name, a bare non-reserved param →
 *  itself, anything else (a reserved tab, with or without a sub-tab) → null. */
export function slotEditTarget(param: string | null | undefined): string | null {
  if (!param) return null
  if (param.startsWith('edit/')) return param.slice('edit/'.length) || null
  if (isReservedSlotsTab(param)) return null
  return param
}

// ── Return target for the slot edit drawer ───────────────────────────────────────────────────────
//
// Closing the Slots edit drawer normally lands on `#slots`. When it was opened from the NPU tab
// (the FLM lane's Edit, an FLM "used by" link) closing it should return there. The opener records
// the target here (module-level, mirrored to sessionStorage; both guarded) together with the slot
// it was for, and the close handler takes it once.
const DRAWER_RETURN_KEY = 'hal0:slot-drawer-return'
let drawerReturn: { target: string; slot: string } | null = null

/** Remember where closing `slot`'s edit drawer should go. */
export function setSlotDrawerReturn(target: string, slot: string): void {
  drawerReturn = { target, slot }
  try {
    window.sessionStorage.setItem(DRAWER_RETURN_KEY, JSON.stringify(drawerReturn))
  } catch {
    /* private mode / sandboxed frame: the module-level copy still works */
  }
}

/** The recorded return target for `slot`, cleared on read; null when none or for another slot. */
export function takeSlotDrawerReturn(slot?: string | null): string | null {
  let rec = drawerReturn
  if (!rec) {
    try {
      const raw = window.sessionStorage.getItem(DRAWER_RETURN_KEY)
      rec = raw ? (JSON.parse(raw) as { target: string; slot: string }) : null
    } catch {
      rec = null
    }
  }
  drawerReturn = null
  try {
    window.sessionStorage.removeItem(DRAWER_RETURN_KEY)
  } catch {
    /* ignore */
  }
  if (!rec || (slot != null && rec.slot !== slot)) return null
  return rec.target
}

/** Drop the recorded return target when the route leaves the drawer without `onClose` (the
 *  drawer's own "NPU tab" link, browser Back, a sidebar click). Called by the Slots route effect
 *  with the drawer slot of the previous and the current route; only a drawer -> no-drawer
 *  transition drops it, so a re-run of the effect (slots poll) between the opener recording the
 *  target and the hash change landing cannot eat it. */
export function dropSlotDrawerReturnOnLeave(prevTarget: string | null, nextTarget: string | null): void {
  if (prevTarget != null && nextTarget == null) takeSlotDrawerReturn()
}

/** Open a slot's edit drawer from the NPU tab; closing it returns to the tab. */
export function openSlotEditFromNpu(name: string): void {
  const here = typeof window !== 'undefined' ? window.location.hash || '' : ''
  setSlotDrawerReturn(here.startsWith('#slots/npu') ? here : '#slots/npu', name)
  window.location.hash = slotEditHash(name)
}
