// Actions the command palette runs that talk to the API. Kept out of the palette component so
// they can be tested without mounting it.
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { queryClient } from '@/lib/queryClient'

type Toast = (message: string, kind?: string) => void

/**
 * Cancel a model download and say what happened (#4105).
 *
 * Through the shared client, which throws on a non-2xx answer: a bare `fetch` only rejects on a
 * network error, so a refused or failed cancel used to be reported as "Download cancelled".
 */
export async function cancelModelPull(modelId: string, toast?: Toast): Promise<boolean> {
  try {
    await apiPost(ENDPOINTS.modelPullCancel(modelId))
  } catch (err) {
    const why = err instanceof Error && err.message ? err.message : 'see logs'
    toast?.(`Cancel failed: ${why}`, 'err')
    return false
  }
  toast?.('Download cancelled', 'warn')
  // The registry row may flip back to its pre-pull state, as the Models page's cancel refreshes.
  void queryClient.invalidateQueries({ queryKey: ['models'] })
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('hal0:pull-ended', { detail: { modelId } }))
  }
  return true
}
