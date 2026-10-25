// Copy for the live "GPU: image mode" banner (<GpuImageModeBanner> in primitives.jsx), the
// one-line form of the Image Gen status line, from the `arbiter` block of /api/comfyui/arbiter.
// Pure so it can be unit-tested without the dashboard's window globals.
//
// The banner has the arbiter and nothing else: no render progress, no queue, no memory budget.
// So it names the state the arbiter can vouch for, with the same headlines as the status line
// (status-line-pure.ts), and leaves the figures to the line. It says "Image mode is on" rather
// than counting down a grace: a render in ComfyUI's own queue (the editor's) is invisible to the
// arbiter and keeps pushing the countdown forward.

import {
  HEAD_HANDOFF,
  HEAD_PINNED,
  HEAD_RELOADING,
  HEAD_RENDERING,
  HEAD_SWITCHING,
  SWITCH_PHASES,
  waitLine,
} from './image/status-line-pure'

export type ArbiterBlock =
  | {
      mode?: string | null
      pinned?: boolean | null
      switching?: boolean | null
      phase?: string | null
      unload_set?: string[] | null
      llm_waiters?: { count: number; oldest_s: number | null } | null
    }
  | null
  | undefined

export type ImageModeBannerCopy = { heading: string; body: string }

const HEAD_ON = 'Image mode is on'

// Where the banner's link goes: the Engine tab, which has the controls.
export const IMAGE_MODE_BANNER_HREF = '#slots/image/engine'

// null while the GPU is not in image mode (or the arbiter did not answer).
// A pin holds image mode until it is unpinned or switched back by hand, so the "reload after the
// last render" promise does not apply then.
export function imageModeBanner(arbiter: ArbiterBlock): ImageModeBannerCopy | null {
  if (arbiter?.mode !== 'img') return null
  const phase = arbiter.phase ?? null
  // Nothing was paused (or none is known): do not say LLM slots stay stopped.
  const paused = (arbiter.unload_set?.length ?? 0) > 0
  const wait = waitLine(arbiter.llm_waiters)
  const line = (heading: string, body: string): ImageModeBannerCopy => ({
    heading,
    body: wait ? `${body} ${wait}` : body,
  })

  if ((phase && SWITCH_PHASES.has(phase)) || (arbiter.switching && phase == null)) {
    return line(HEAD_SWITCHING, paused ? 'LLM slots are being paused for image generation.' : 'Image generation is taking the GPU.')
  }
  if (phase === 'reloading') {
    return line(HEAD_RELOADING, 'Chat comes back as each slot loads.')
  }
  // Work in progress is what the headline says, pinned or not (a pin only changes how long the
  // slots stay stopped).
  if (phase === 'handoff') {
    return line(HEAD_HANDOFF, 'A ComfyUI editor prompt is taking the GPU.')
  }
  if (phase === 'rendering') {
    const until = arbiter.pinned ? 'until you unpin image mode' : 'until the last image is done'
    return line(HEAD_RENDERING, paused ? `LLM slots stay stopped ${until}.` : 'Image generation holds the GPU.')
  }
  if (arbiter.pinned) {
    return line(
      HEAD_PINNED,
      paused
        ? 'Image mode is pinned: LLM slots stay stopped and GPU chat is refused until it is unpinned or switched back to inference.'
        : 'Image mode is pinned: GPU chat is refused until it is unpinned or switched back to inference.',
    )
  }
  return line(
    HEAD_ON,
    paused
      ? 'LLM slots are stopped while image generation holds the GPU — they reload about 10 s after the last image.'
      : 'Image generation holds the GPU.',
  )
}
