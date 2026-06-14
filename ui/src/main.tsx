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

// 2) Side-effect imports — order matches the original script tags in
//    `hal0 v2 dashboard.html`. Each module installs its components on
//    `window` via `Object.assign(window, …)`.
import './dashboard.css'
import './dash/comfyui-pane.css'
import './dash/engine-panes.css'
import './dash/memory-overhaul.css'
import './dash/activity-log.css'
import './dash/overhaul.css'

import './dash/data.jsx'
import './dash/tweaks-panel.jsx'
// 2026-06-05: the standalone SidebarAgentBlock (+ its window hook bridge) is
// retired — its agent health folded into the consolidated Runtime widget in
// chrome.jsx, which imports useSidebarAgentRollup directly via ES modules.
import './dash/chrome.jsx'
import './dash/primitives.jsx'
import './dash/cards-shell.jsx'
// Dashboard-overhaul card modules — each registers a window global the grid
// (dash-grid.jsx) wires by name (SlotList, ThroughputCard2, UtilizationCard,
// QuickChatCard, ServicesCard). MUST load after cards-shell (they use DCard/
// StatusDot) and before dash-grid so the globals exist when the grid renders.
import './dash/slot-list.jsx'
import './dash/metric-cards.jsx'
import './dash/quickchat-card.jsx'
import './dash/services-card.jsx'
import './dash/optin-cards.jsx'
import './dash/command-palette.jsx'
import './dash/flow-modals.jsx'
import './dash/extra-modals.jsx'
import './dash/dashboard.jsx'
// W3: masonry grid + edit mode + layout persistence (DashGrid, DashboardOverhaulView)
// Bridge must come BEFORE dash-grid.jsx so window.__hal0Use* are set when the
// .jsx module evaluates. dash-grid.jsx must come after cards-shell (W1).
import './dash/dash-grid-hook-bridge'
import './dash/dash-grid.jsx'
import './dash/install-state-bridge'
import './dash/firstrun.jsx'
import './dash/slots.jsx'
import './dash/slot-modals.jsx'
import './dash/models.jsx'
import './dash/model-modals.jsx'
// Connections surface: local OpenAI endpoints + folded-in MCP servers
// (connections-overhaul). The old standalone MCP page is removed; #mcp aliases
// to this view.
import './dash/connections.css'
import './dash/connections.jsx'
// issue #658 — Profiles: container-slot template catalog + iGPU intent labels.
import './dash/profiles.jsx'
import './dash/settings.jsx'

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

// 3) main.jsx mounts <App /> into #root.
import './dash/main.jsx'

// Optional state-mgmt libraries installed for Phase B. Importing nothing
// keeps the bundle clean today, but the deps are present so Phase B can
// `import { useQuery } from '@tanstack/react-query'` without touching
// package.json again.
