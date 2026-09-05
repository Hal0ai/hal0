// hal0 dashboard — runs an actionable `command` NextStep through its typed
// hook (D6 remediation chips). Pairs with next-step-actions.ts, the pure
// target -> action mapping this hook executes.
//
// Two hooks cover every mapped action today: useServiceRepair (the same
// one-click repair RestartApiPanel.jsx uses) and useSlotRestart (the same
// mutation the slot card's Restart button uses) — both invalidate their own
// queries on success, so the panel's next poll picks up the change without
// this hook doing it again.

import { useServiceRepair } from '@/api/hooks/useServicesHealth'
import { useSlotRestart } from '@/api/hooks/useSlots'
import { actionForNextStep } from './next-step-actions'

export function useNextStepRunner() {
  const repair = useServiceRepair()
  const slotRestart = useSlotRestart()

  function run(step) {
    const action = actionForNextStep(step)
    if (!action) return
    if (action.kind === 'serviceRestart') {
      toast(`Restarting ${action.unit}…`, 'warn')
      repair.mutate(action.unit, {
        onSuccess: () => toast(`${action.unit} restarted`, 'ok'),
        onError: (e) => toast(e?.message || `Failed to restart ${action.unit}`, 'err'),
      })
      return
    }
    toast(`Restarting slot ${action.slot}…`, 'warn')
    slotRestart.mutate(action.slot, {
      onSuccess: () => toast(`Slot ${action.slot} restarted`, 'ok'),
      onError: (e) => toast(e?.message || `Failed to restart slot ${action.slot}`, 'err'),
    })
  }

  return {
    run,
    actionFor: actionForNextStep,
    pending: repair.isPending || slotRestart.isPending,
  }
}
