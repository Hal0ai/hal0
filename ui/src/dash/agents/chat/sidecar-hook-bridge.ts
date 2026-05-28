// hal0 dashboard — window-globals bridge for HermesSidecar's TanStack
// hooks.
//
// The sidecar is a .jsx prototype file (no ES imports across the
// dash/* boundary). This bridge republishes the hooks it consumes onto
// window before the sidecar evaluates.
//
// IMPORTED FROM main.tsx BEFORE ui/src/dash/agents/chat/hermes-sidecar.jsx.

import { useMcpStatusPip } from '@/api/hooks/useAgents'

;(window as unknown as {
  __hal0UseMcpStatusPip?: typeof useMcpStatusPip
}).__hal0UseMcpStatusPip = useMcpStatusPip
