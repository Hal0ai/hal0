// The GPU state the status dock draws: what the Image Gen pane's status line shows, computed once.
//
// Lifted from ImageStatusLine's body (same hooks, same server-clock skew, same one-second tick,
// same render-time carry-on); the line stays until the pane drops it. The words and the choice of
// buttons are status-line-pure's; what a button calls is useStatusLineActions's. `active` is
// whether /status polls at its active cadence: on the Image Gen route, or while a run of this
// browser is in flight (the dock is mounted everywhere, so it does not poll fast otherwise).
import { useArbiter, useComfyui, useComfyuiModels } from '@/api/hooks/useComfyui'
import { useMemoryBudget } from '@/api/hooks/useStatsHardware'
import { useSlots } from '@/api/hooks/useSlots'
import { useImageRunStore } from '@/stores/useImageRunStore'
import { actionFacts, statusLine } from '@/dash/image/status-line-pure'
import { reloadSteps, switchSteps } from '@/dash/image/status-line-parts-pure'
import { useDismissedRefusalAt } from '@/dash/image/useStatusLineActions'
import { useEditorAccess } from '@/dash/image/useEditorAccess'
import { useTick } from '@/dash/image/ImageStatusLine.jsx'

/**
 * @param {{ active: boolean }} opts
 * @returns {{
 *   view: ReturnType<typeof statusLine>, env: any, status: any, memory: any, slots: any,
 *   facts: ReturnType<typeof actionFacts>, steps: any[], progress: any, elapsedS: number,
 *   editorUrl: string | null | undefined, run: { running: boolean, cancel: () => void } | null,
 *   ctx: any,
 * }} `view` is null when there is nothing to say (no GPU, no engine on this host).
 */
export function useGpuStatus({ active }) {
  const run = useImageRunStore((s) => s.run)
  const ownRun = run?.running === true
  const arbiterQ = useArbiter()
  const statusQ = useComfyui({ active, busy: ownRun })
  const memory = useMemoryBudget().data
  const engineAvailable = useComfyuiModels().data?.engine?.available
  const slots = useSlots().data
  // The editor listens on the box's loopback: off the box there is no link to open (editor-reach.ts).
  const editorUrl = useEditorAccess().url
  const dismissedAt = useDismissedRefusalAt()

  const env = arbiterQ.data
  const status = statusQ.data
  const facts = actionFacts(env, status)
  useTick(env?.arbiter?.phase === 'grace' || facts.renderRunning)

  const nowS = Date.now() / 1000
  // `restore_at` is on the server's clock and the browser's can be off: the countdown is taken on
  // the server's (`skewMs` is read off each /arbiter answer's Date header, 0 when it has none).
  // The render time below stays on the browser's clock, both ends of it being the browser's.
  const skewS = (env?.skewMs ?? 0) / 1000
  const view = statusLine(env, status, memory, {
    slots,
    dismissedAt,
    now: nowS - skewS,
    engineAvailable,
    ownRun,
  })

  const progress = status?.progress ?? null
  // `elapsed_s` is as of the last poll; carry it on from there.
  const elapsedS = progress
    ? progress.elapsed_s + Math.max(0, statusQ.dataUpdatedAt ? nowS - statusQ.dataUpdatedAt / 1000 : 0)
    : 0
  const kind = view?.kind
  const steps =
    kind === 'switching'
      ? switchSteps(env?.arbiter?.phase)
      : kind === 'reloading'
        ? reloadSteps(env?.arbiter?.unload_set, slots)
        : []

  const ctx = { ...facts, editorUrl, onCancel: run?.cancel }
  return { view: view ?? null, env, status, memory, slots, facts, steps, progress, elapsedS, editorUrl, run, ctx }
}
