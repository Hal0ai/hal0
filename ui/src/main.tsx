// hal0 v3 dashboard — entry point (Phase A).
//
// The design prototype (src/dash/*.jsx) was originally compiled in-browser
// by @babel/standalone and used a script-concatenation pattern where each
// file publishes its exports onto `window` via `Object.assign(window, {...})`
// and reads sibling exports back from `window`.
//
// For the Vite build we keep the prototype unchanged. We just:
//   1. Install React + ReactDOM as globals BEFORE any dash module loads.
//   2. Import every dash module as a side effect, in the same order as the
//      original `hal0 v2 dashboard.html` `<script>` tags. Each module's
//      top-level `Object.assign(window, …)` runs and installs its components.
//   3. Import `dash/main.jsx` last — it reads everything from globals and
//      calls `ReactDOM.createRoot(...).render(...)` itself.
//
// Phase B (API wiring) will replace HAL0_DATA-driven views with real hooks
// and start migrating files to plain ES module imports. The window-globals
// shim stays available during the transition.

// 1) Install React + ReactDOM globals BEFORE any dash module runs. The
//    install must happen in a separately-imported module — ES module
//    evaluation depth-first means imports run fully before the importer's
//    own statements, so an inline `globalThis.React = React` further down
//    in this file would execute AFTER the dash imports.
import './globals-install'

// Self-hosted fonts (package.json shipped these but nothing imported them,
// so the dashboard silently depended on the fonts.googleapis.com <link> in
// index.html — a per-load external fetch on what is a local-first LAN
// appliance, and offline it stalls then falls back). The CSS font stacks
// already list "Geist Variable" / "JetBrains Mono" (dashboard.css --geist /
// --jbm), so bundling the files makes them deterministic.
import '@fontsource-variable/geist'
import '@fontsource/jetbrains-mono/400.css'
import '@fontsource/jetbrains-mono/500.css'
import '@fontsource/jetbrains-mono/600.css'

// 2) Side-effect imports — order matches the original script tags in
//    `hal0 v2 dashboard.html`. Each module installs its components on
//    `window` via `Object.assign(window, …)`.
import './dashboard.css'
import './dash/comfyui-pane.css'
import './dash/engine-panes.css'
import './dash/npu.css'
import './dash/memory-overhaul.css'
import './dash/activity-log.css'
import './dash/overhaul.css'
import './dash/redesign.css'

import './dash/data.jsx'
import './dash/tweaks-panel.jsx'
import './dash/chrome.jsx'
import './dash/primitives.jsx'
import './dash/cards-shell.jsx'
// Board card modules — each registers a window global (SlotList,
// ThroughputCard2, UtilizationCard, QuickChatCard, ServicesCard, PowerCard,
// SlotTrackCard, …). MUST load after cards-shell (they use DCard/StatusDot);
// the redesign view offers slottrack/power/quickchat as swap-in widgets.
import './dash/slot-list.jsx'
import './dash/metric-cards.jsx'
import './dash/quickchat-card.jsx'
import './dash/services-card.jsx'
import './dash/optin-cards.jsx'
import './dash/command-palette.jsx'
import './dash/flow-modals.jsx'
import './dash/extra-modals.jsx'
import './dash/dashboard.jsx'
// Dashboard redesign (design_handoff_dashboard_redesign): fixed-band layout
// with swap-in-place customization (DashboardRedesignView). Replaces the
// free-form DashGrid / DashboardOverhaulView board. Must load after
// cards-shell and the card modules above (slottrack / power / quickchat
// window globals are offered as swap-in widgets).
import './dash/dashboard-redesign.jsx'
import './dash/slots.jsx'
import './dash/slot-modals.jsx'
import './dash/models.jsx'
import './dash/model-modals.jsx'
// D1 (post-R3 surface rework): the model editor drawer — "the model is the
// launchable thing". Publishes window.ModelDrawer, which models.jsx renders in
// place of RecipeEditorModal. Loads after model-modals.jsx (reuses its hooks).
import './dash/model-drawer.jsx'
// Connections surface: local OpenAI endpoints + folded-in MCP servers
// (connections-overhaul). The old standalone MCP page is removed; #mcp aliases
// to this view.
import './dash/connections.css'
import './dash/connections.jsx'
// issue #658 — Profiles: container-slot template catalog + iGPU intent labels.
import './dash/profiles.jsx'
// Stacks: named, portable slot+profile+model bundles (Focus layout) — registers
// window.StacksView, rendered as the third Slots tab.
import './dash/stacks.css'
import './dash/stacks.jsx'
// Services page (#services): companion-service management — status +
// lifecycle + mDNS. Registers window.ServicesView. Must load after
// services-card.jsx (reuses its ComfyJobQueue window global).
import './dash/services.css'
import './dash/services.jsx'
// Settings (#settings): P3-ui split (phase 1) replaced the window-globals
// settings.jsx monolith with settings/SettingsShell.jsx, a real ES module —
// main.jsx imports it directly instead of reading window.SettingsView, so
// there's no side-effect import to list here anymore.

import './dash/extras.jsx'

// AgentView is the `#agent` route shell. v0.4 reduced it to the Memory
// capability only — the web-chat (HermesChatTab) surface plus the
// Personas / Skills / Plugins tabs were removed (web chat is abandoned in
// favour of the `hermes chat` TUI; the other tabs showed fixtures rather
// than live data). The MemoryTab bridge installs its TanStack-Query
// hooks onto `window.__hal0Use*` BEFORE memory-tab.jsx evaluates, and
// memory-tab.jsx registers on window BEFORE agent-view.jsx mounts.
import './dash/agents/memory-tab-hook-bridge'
import './dash/agents/memory-tab.jsx'
// Agents Overview (the bare #agent landing tab): a card library where Hermes
// is wired to live health + restart. The hook-bridge installs window.__hal0Use*
// + window.__hal0AgentArt BEFORE the cards evaluate; agent-card.jsx registers
// LiveAgentCard/LockedAgentCard, agents-overview.jsx registers AgentsOverview —
// all BEFORE agent-view.jsx reads them.
import './dash/agents/agent-cards.css'
import './dash/agents/agents-hook-bridge'
import './dash/agents/agent-card.jsx'
import './dash/agents/agents-overview.jsx'
import './dash/agents/agent-view.jsx'

// Hindsight Memory view (#memory) — bridge installs the TanStack-Query
// hooks on window.__hal0Use* BEFORE memory.jsx evaluates.
import './dash/memory-hook-bridge'
import './dash/memory-graph-engine.jsx'
import './dash/memory-graph-structured.jsx'
import './dash/memory-graph-ego.jsx'
import './dash/memory-graph.jsx'
import './dash/memory-tools.jsx'
import './dash/memory.jsx'

// Operator Board (#board) — a hal0-skinned kanban wired to the Hermes kanban
// backend via the audited /api/board/* proxy + a live WS + an agent-chat SSE
// orchestrator. Same window-globals contract as the rest of dash/*:
//   1. board.css  — scoped under `.board` (board drawer classes renamed `.b-drawer*`).
//   2. board-hook-bridge — publishes the useBoard hooks on window.__hal0UseBoard*
//      BEFORE any board .jsx evaluates (mirrors memory-tab-hook-bridge).
//   3. leaf components (kcard/lane/drawers/overlays) register their window globals.
//   4. board-view last — its render reads the leaf globals + the bridged hooks.
import './dash/board/board.css'
import './dash/board/board-hook-bridge'
import './dash/board/kcard.jsx'
import './dash/board/lane.jsx'
import './dash/board/task-drawer.jsx'
import './dash/board/agent-chat.jsx'
import './dash/board/orchestration-popover.jsx'
import './dash/board/new-board-modal.jsx'
import './dash/board/new-task-modal.jsx'
import './dash/board/board-view.jsx'

// Benchmarks page — registers window.BenchmarksView for the #benchmarks route.
import Benchmarks from './dash/Benchmarks'
;(window as any).BenchmarksView = Benchmarks

// 3) main.jsx mounts <App /> into #root.
import './dash/main.jsx'

// Optional state-mgmt libraries installed for Phase B. Importing nothing
// keeps the bundle clean today, but the deps are present so Phase B can
// `import { useQuery } from '@tanstack/react-query'` without touching
// package.json again.
