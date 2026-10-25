// Copy for the live "GPU: image mode" banner (<GpuImageModeBanner> in
// primitives.jsx), from the /api/comfyui/status arbiter block. Pure so it can
// be unit-tested without the dashboard's window globals.

export type ArbiterBlock = { mode?: string | null; pinned?: boolean | null } | null | undefined

export type ImageModeBannerCopy = { heading: string; body: string }

// null while the GPU is not in image mode (or the arbiter did not answer).
// A pin holds image mode until it is unpinned or switched back by hand, so the
// "reload after the last render" promise does not apply then.
export function imageModeBanner(arbiter: ArbiterBlock): ImageModeBannerCopy | null {
  if (arbiter?.mode !== 'img') return null
  if (arbiter.pinned) {
    return {
      heading: 'GPU: image mode (pinned)',
      body: 'Image mode is pinned: LLM slots stay stopped and GPU chat is refused until it is unpinned or switched back to inference.',
    }
  }
  return {
    heading: 'GPU: image mode',
    body: 'LLM slots are stopped while image generation holds the GPU — they reload shortly after the last render finishes.',
  }
}
