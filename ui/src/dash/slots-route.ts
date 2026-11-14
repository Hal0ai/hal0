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
