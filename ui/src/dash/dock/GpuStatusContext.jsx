// The one owner of the GPU state and its actions, shared by every surface that draws them: the
// dock (footer segment, GPU tab) and the top bar's mode toggle.
//
// Mounted once above both, so /status and /arbiter are polled once and the confirms exist once:
// the stop-while-rendering dialog and the Hold / Keep dialog (shut when the top-bar toggle draws
// the question as its own popover; see `confirmVia` in useStatusLineActions).
//
// `route` / `param` decide `/status`'s cadence (see useGpuStatus): fast on the Image Gen route or
// while a run of this browser is in flight, since the dock is mounted everywhere.
import { createContext, useContext } from 'react'
import { useImageRunStore } from '@/stores/useImageRunStore'
import { isImageGenRoute } from '../slots-route'
import { useStatusLineActions } from '../image/useStatusLineActions'
import { useGpuStatus } from './useGpuStatus'
import { ForceStopConfirm, PinConfirm } from './gpu-parts.jsx'

const GpuStatusContext = createContext(null)

/**
 * @param {{ route: string, param?: string | null, children: import('react').ReactNode }} props
 */
export function GpuStatusProvider({ route, param, children }) {
  const imageRunning = useImageRunStore((s) => s.run?.running === true)
  const gpu = useGpuStatus({ active: isImageGenRoute(route, param) || imageRunning })
  // A failed action's message goes when the line's state changes (the kind), not when its text
  // does: the headline counts seconds and steps.
  const actions = useStatusLineActions({ resetKey: gpu.view?.kind ?? null })
  return (
    <GpuStatusContext.Provider value={{ gpu, actions }}>
      {children}
      <ForceStopConfirm confirm={actions.confirm} />
      <PinConfirm confirm={actions.confirm} />
    </GpuStatusContext.Provider>
  )
}

/**
 * The shared GPU state and actions, or null outside a provider (a surface then draws nothing).
 * @returns {{
 *   gpu: ReturnType<typeof useGpuStatus>,
 *   actions: ReturnType<typeof useStatusLineActions>,
 * } | null}
 */
export function useGpuState() {
  return useContext(GpuStatusContext)
}
