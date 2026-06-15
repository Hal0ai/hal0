// hal0 dashboard — window-globals bridge for the Agents Overview cards.
//
// agents-overview.jsx + agent-card.jsx are .jsx prototype files (no ES
// imports across dash/*; main.tsx import order is the contract). This
// bridge republishes the TanStack-Query hooks the live Hermes card needs
// under `window.__hal0Use*`, plus the bundled card art URLs (Vite-hashed,
// base-path-safe) under `window.__hal0AgentArt`, so the cards resolve them
// at runtime. Mirrors memory-tab-hook-bridge.ts.

import { useAgents, useAgentRestart } from '@/api/hooks/useAgents'
import { useSlots } from '@/api/hooks/useSlots'

// Bundled card art. Imported (not referenced from /public) so Vite fingerprints
// them and they survive any base-path mount.
import hermesArt from './assets/hermes.png'
import piArt from './assets/pi-amber.png'
import qwenArt from './assets/qwen-logo.svg'
import opencodeArt from './assets/opencode-logo.svg'

;(window as unknown as { __hal0UseAgents?: typeof useAgents }).__hal0UseAgents =
  useAgents
;(
  window as unknown as { __hal0UseAgentRestart?: typeof useAgentRestart }
).__hal0UseAgentRestart = useAgentRestart
;(window as unknown as { __hal0UseSlots?: typeof useSlots }).__hal0UseSlots =
  useSlots

;(
  window as unknown as { __hal0AgentArt?: Record<string, string> }
).__hal0AgentArt = {
  hermes: hermesArt,
  pi: piArt,
  qwen: qwenArt,
  opencode: opencodeArt,
}
