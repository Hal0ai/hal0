// hal0 dashboard — window-globals bridge for useSidebarAgentRollup.
//
// SidebarAgentBlock is a .jsx prototype file that reads from window
// globals (matches the rest of dash/*.jsx). The hook lives in
// ui/src/api/hooks/useAgents.ts (TanStack Query, ES modules). This
// bridge republishes the hook as `window.__hal0UseSidebarAgentRollup`
// so the component finds it without violating the prototype's no-ES-import
// contract (preserved through v0.3 per main.tsx top comment).
//
// IMPORTED FROM main.tsx BEFORE chrome.jsx + sidebar-agent-block.jsx so
// the global is installed by the time the component evaluates.

import { useSidebarAgentRollup } from '@/api/hooks/useAgents'

;(window as unknown as { __hal0UseSidebarAgentRollup?: typeof useSidebarAgentRollup }).__hal0UseSidebarAgentRollup =
  useSidebarAgentRollup
