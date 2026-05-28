// hal0 dashboard — window-globals bridge for memory-graph hooks.
//
// MemoryTab is a .jsx prototype file (no ES imports across dash/*).
// This bridge republishes the TanStack-Query memory hooks under
// `window.__hal0UseMemoryGraphStatus` + `window.__hal0UseUpdateMemoryGraph`
// so memory-tab.jsx finds them the same way SidebarAgentBlock does.

import { useMemoryGraphStatus, useUpdateMemoryGraph } from '@/api/hooks/useMemory'

;(window as unknown as {
  __hal0UseMemoryGraphStatus?: typeof useMemoryGraphStatus
  __hal0UseUpdateMemoryGraph?: typeof useUpdateMemoryGraph
}).__hal0UseMemoryGraphStatus = useMemoryGraphStatus
;(window as unknown as {
  __hal0UseMemoryGraphStatus?: typeof useMemoryGraphStatus
  __hal0UseUpdateMemoryGraph?: typeof useUpdateMemoryGraph
}).__hal0UseUpdateMemoryGraph = useUpdateMemoryGraph
