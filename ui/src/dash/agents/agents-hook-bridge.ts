// hal0 dashboard — window-globals bridge for the Agents Overview cards.
//
// agents-overview.jsx + agent-card.jsx are .jsx prototype files (no ES
// imports across dash/*; main.tsx import order is the contract). This
// bridge republishes the TanStack-Query hooks the live Hermes card needs
// under `window.__hal0Use*`, plus the bundled card art URLs (Vite-hashed,
// base-path-safe) under `window.__hal0AgentArt`, so the cards resolve them
// at runtime. Mirrors memory-tab-hook-bridge.ts.
//
// The second block of names (overview, repair, gateway restart, uninstall,
// approvals mode, log tail, personas, converge, job poll, config URLs,
// capabilities, approvals count, Open WebUI re-mirror) serves the state-aware
// cards.

import {
  useAgentApprovalsCount,
  useAgentGatewayRestart,
  useAgentOverview,
  useAgentPersonas,
  useAgentRepair,
  useAgentRestart,
  useAgents,
  useAgentUninstall,
  useCommandApprovalsMode,
  useHermesReconcileUpstreams,
  usePersonaActivate,
  useUnitLogTail,
} from '@/api/hooks/useAgents'
import { useCapabilities } from '@/api/hooks/useCapabilities'
import { useComponentConverge } from '@/api/hooks/useComponents'
import { useConfigUrls } from '@/api/hooks/useConfigUrls'
import { useSlots } from '@/api/hooks/useSlots'
import { useUpdateJob } from '@/api/hooks/useUpdates'
import { deriveCardModel } from './agent-phase'

// Bundled card art. Imported (not referenced from /public) so Vite fingerprints
// them and they survive any base-path mount.
import hermesArt from './assets/hermes.webp'
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

Object.assign(window, {
  __hal0UseAgentOverview: useAgentOverview,
  __hal0UseAgentRepair: useAgentRepair,
  __hal0UseAgentGatewayRestart: useAgentGatewayRestart,
  __hal0UseAgentUninstall: useAgentUninstall,
  __hal0UseCommandApprovalsMode: useCommandApprovalsMode,
  __hal0UseUnitLogTail: useUnitLogTail,
  __hal0UseAgentPersonas: useAgentPersonas,
  __hal0UsePersonaActivate: usePersonaActivate,
  __hal0UseComponentConverge: useComponentConverge,
  __hal0UseUpdateJob: useUpdateJob,
  __hal0UseConfigUrls: useConfigUrls,
  __hal0UseCapabilities: useCapabilities,
  __hal0UseAgentApprovalsCount: useAgentApprovalsCount,
  __hal0UseHermesReconcileUpstreams: useHermesReconcileUpstreams,
  // Pure (no hook): the cards call it each render with the hooks' data.
  __hal0DeriveCardModel: deriveCardModel,
})

;(
  window as unknown as { __hal0AgentArt?: Record<string, string> }
).__hal0AgentArt = {
  hermes: hermesArt,
  pi: piArt,
  qwen: qwenArt,
  opencode: opencodeArt,
}
