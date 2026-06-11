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
import './mcp.css'
import './dash/comfyui-pane.css'

import './dash/data.jsx'
import './dash/tweaks-panel.jsx'
// 2026-06-05: the standalone SidebarAgentBlock (+ its window hook bridge) is
// retired — its agent health folded into the consolidated Runtime widget in
// chrome.jsx, which imports useSidebarAgentRollup directly via ES modules.
import './dash/chrome.jsx'
import './dash/primitives.jsx'
import './dash/command-palette.jsx'
import './dash/flow-modals.jsx'
import './dash/extra-modals.jsx'
import './dash/dashboard.jsx'
import './dash/firstrun.jsx'
import './dash/slots.jsx'
import './dash/slot-modals.jsx'
import './dash/models.jsx'
import './dash/model-modals.jsx'
// issue #549 — Connections surface: providers + upstreams list + per-row test.
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

// v0.3 MCP additions — see `hal0 v3 mcp.html` for the original entry. We
// pull them into the main SPA so `#mcp` (and the equivalent `#agents/mcp`)
// renders the McpView inside the shared chrome rather than a separate page.
import './dash/mcp-data.jsx'
import './dash/mcp-modals.jsx'
import './dash/mcp.jsx'

// 3) main.jsx mounts <App /> into #root.
import './dash/main.jsx'

// Optional state-mgmt libraries installed for Phase B. Importing nothing
// keeps the bundle clean today, but the deps are present so Phase B can
// `import { useQuery } from '@tanstack/react-query'` without touching
// package.json again.
