// The one GPU banner that stays at the top of every page: ComfyUI's GPU gate has no credential
// for hal0's sign-in, so image generation is blocked until someone fixes it. Every other GPU
// state lives in the dock. It carries no dismiss and no button: nothing here clears it but the
// credential, which the status line used to say the same way.
import { useComfyui } from '@/api/hooks/useComfyui'
import { Banner } from '@/dash/primitives.jsx'
import { DETAIL_NO_CREDENTIAL, HEAD_NO_CREDENTIAL } from '@/dash/image/status-line-pure'

export function GpuGateBlockedBanner() {
  // Not the Image Gen pane's cadence: the banner only watches for the gate state.
  const status = useComfyui({ active: false }).data
  if (status?.gate?.state !== 'no_credential') return null
  return (
    <div data-testid="gpu-gate-blocked-banner">
      <Banner kind="err" compact heading={HEAD_NO_CREDENTIAL} body={DETAIL_NO_CREDENTIAL} />
    </div>
  )
}
