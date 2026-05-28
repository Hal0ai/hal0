// hal0 dashboard — window-globals bridge for useAgentPersonas.
//
// PersonasTab is a .jsx prototype file (no ES imports across dash/*).
// This bridge republishes the hook as `window.__hal0UseAgentPersonas`
// so the tab component finds it the same way SidebarAgentBlock does.
//
// IMPORTED FROM main.tsx BEFORE extras.jsx + dash/agents/*.jsx evaluate.

import { useAgentPersonas } from '@/api/hooks/useAgents'

;(window as unknown as { __hal0UseAgentPersonas?: typeof useAgentPersonas }).__hal0UseAgentPersonas =
  useAgentPersonas
