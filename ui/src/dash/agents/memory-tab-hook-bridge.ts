// hal0 dashboard — window-globals bridge for memory-graph hooks.
//
// MemoryTab is a .jsx prototype file (no ES imports across dash/*).
// This bridge republishes the TanStack-Query memory hooks under
// `window.__hal0Use*` so memory-tab.jsx finds them at runtime.

import {
  useAgentMemoryStats,
  useMemoryEnabled,
  useMemoryEnabledPending,
  useMemoryGraphStatus,
  useMemoryList,
  useUpdateMemoryGraph,
} from '@/api/hooks/useMemory'
import { useFeatures } from '@/api/hooks/useFeatures'

;(window as unknown as {
  __hal0UseMemoryGraphStatus?: typeof useMemoryGraphStatus
  __hal0UseUpdateMemoryGraph?: typeof useUpdateMemoryGraph
  __hal0UseMemoryList?: typeof useMemoryList
  __hal0UseAgentMemoryStats?: typeof useAgentMemoryStats
  __hal0UseMemoryEnabled?: typeof useMemoryEnabled
}).__hal0UseMemoryGraphStatus = useMemoryGraphStatus
;(window as unknown as {
  __hal0UseUpdateMemoryGraph?: typeof useUpdateMemoryGraph
}).__hal0UseUpdateMemoryGraph = useUpdateMemoryGraph
;(window as unknown as {
  __hal0UseMemoryList?: typeof useMemoryList
}).__hal0UseMemoryList = useMemoryList
;(window as unknown as {
  __hal0UseAgentMemoryStats?: typeof useAgentMemoryStats
}).__hal0UseAgentMemoryStats = useAgentMemoryStats
// 0.4 gate: main.jsx (strict no-ES-imports prototype file) reads this to
// drop the Agent route when the memory subsystem is disabled.
;(window as unknown as {
  __hal0UseMemoryEnabled?: typeof useMemoryEnabled
}).__hal0UseMemoryEnabled = useMemoryEnabled
// Companion pending-flag — allows main.jsx to distinguish "loading"
// from "settled disabled" so the #agent→#dashboard redirect only fires
// after the status query resolves (not during the transient loading window).
;(window as unknown as {
  __hal0UseMemoryEnabledPending?: typeof useMemoryEnabledPending
}).__hal0UseMemoryEnabledPending = useMemoryEnabledPending
// /api/features — memory_engine is the live engine name for the tab label.
;(window as unknown as {
  __hal0UseFeatures?: typeof useFeatures
}).__hal0UseFeatures = useFeatures
