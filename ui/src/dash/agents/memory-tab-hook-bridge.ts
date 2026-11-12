// hal0 dashboard — window-globals bridge for the memory gate.
//
// main.jsx and command-palette.jsx are window-globals prototype files (no ES
// imports across dash/*). This bridge republishes the memory-enabled hook and
// its pending flag under `window.__hal0Use*` so they find them at runtime.
// Imported in main.tsx before either evaluates.
//
// It once also published the graph, list and stats hooks for the Agents ▸
// Memory tab (memory-tab.jsx); that tab is gone and nothing reads them.

import { useMemoryEnabled, useMemoryEnabledPending } from '@/api/hooks/useMemory'

// main.jsx drops the #memory route, and the palette its Memory entry, when
// the memory subsystem is disabled.
;(window as unknown as {
  __hal0UseMemoryEnabled?: typeof useMemoryEnabled
}).__hal0UseMemoryEnabled = useMemoryEnabled
// Companion pending flag — lets main.jsx tell "loading" from "settled
// disabled", so the #memory → #dashboard redirect fires only after the status
// query resolves (not during the transient loading window).
;(window as unknown as {
  __hal0UseMemoryEnabledPending?: typeof useMemoryEnabledPending
}).__hal0UseMemoryEnabledPending = useMemoryEnabledPending
