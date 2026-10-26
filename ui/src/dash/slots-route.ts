// `#slots/<param>`: the Slots page's route param is `<tab>[/<sub>]`.
//
// `parseRoute` (main.jsx) keeps everything after `slots/` as one string, so `#slots/image/models`
// has the param `image/models`. The first segment names the tab ("image", "endpoints",
// "runner-images", "stacks", or a slot name that opens the edit drawer on Inference); the second
// is the tab's own sub-tab, which only Image Gen has. Everything that compares a param with a
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
