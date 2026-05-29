// hal0 dashboard — window-globals bridge for persona-budget hooks.
//
// PersonaBudgetPanel is a .jsx prototype file (no ES imports across
// dash/*). This bridge republishes the TanStack Query hooks as
// `window.__hal0UsePersonaBudget` + `window.__hal0PutPersonaBudget`
// so the panel finds them the same way PersonasTab finds
// useAgentPersonas (see personas-tab-hook-bridge.ts).
//
// IMPORTED FROM main.tsx BEFORE persona-budget-panel.jsx evaluates.

import { usePersonaBudget, usePutPersonaBudget } from '@/api/hooks/useBudget'

;(
  window as unknown as {
    __hal0UsePersonaBudget?: typeof usePersonaBudget
    __hal0PutPersonaBudget?: typeof usePutPersonaBudget
  }
).__hal0UsePersonaBudget = usePersonaBudget
;(
  window as unknown as {
    __hal0UsePersonaBudget?: typeof usePersonaBudget
    __hal0PutPersonaBudget?: typeof usePutPersonaBudget
  }
).__hal0PutPersonaBudget = usePutPersonaBudget
