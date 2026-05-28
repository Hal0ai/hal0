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

import './dash/data.jsx'
import './dash/tweaks-panel.jsx'
// v0.3 PR-6: bridge the SidebarAgentBlock TanStack Query hook onto
// window BEFORE chrome.jsx + sidebar-agent-block.jsx evaluate. The
// component (.jsx, prototype window-globals style) reads
// `window.__hal0UseSidebarAgentRollup` instead of importing the hook
// directly — preserves the no-ES-imports contract for the dash/*.jsx
// pile (see main.tsx top comment).
import './dash/agents/sidebar-agent-hook-bridge'
import './dash/agents/sidebar-agent-block.jsx'
import './dash/chrome.jsx'
import './dash/primitives.jsx'
import './dash/command-palette.jsx'
import './dash/flow-modals.jsx'
import './dash/extra-modals.jsx'
import './dash/dashboard.jsx'
import './dash/chat.jsx'
import './dash/firstrun.jsx'
import './dash/slots.jsx'
import './dash/slot-modals.jsx'
import './dash/models.jsx'
import './dash/model-modals.jsx'
import './dash/settings.jsx'
// v0.3 PR-7: install the plugin SDK shim BEFORE extras.jsx mounts the
// PluginTabHost. The shim publishes window.__HAL0_PLUGINS__ +
// window.__HAL0_PLUGIN_SDK__ (plus the __HERMES_* aliases) so plugin
// bundles loaded via PluginTabHost find the registry the moment their
// IIFE evaluates.
import './dash/agents/plugin-sdk-shim.js'
import './dash/agents/plugin-host.jsx'

import './dash/extras.jsx'

// v0.3 PR-8: AgentView is now split into per-tab files under
// ui/src/dash/agents/. Bridges install the TanStack-Query hooks onto
// `window.__hal0Use*` BEFORE the .jsx tab modules evaluate, so each
// tab can read them without violating the no-ES-imports contract.
// Load order matters: AgentView (the shell) reads window.{HermesChatTab,
// PersonasTab, SkillsTab, MemoryTab, PluginsTab} at render time, so the
// tabs must register on window before AgentView mounts. AgentView itself
// must register AFTER extras.jsx so its definition wins over any stale
// symbol the old monolith would have left behind (defence-in-depth —
// the old monolith is already removed).
import './dash/agents/personas-tab-hook-bridge'
import './dash/agents/memory-tab-hook-bridge'
// v0.3 PR-10: HermesChat surface — composer + transcript + sidecar over
// the WS proxy from PR-9 (master plan §4 PR-10). The session store
// + connection manager publishes on window via use-hermes-session.js;
// markdown/bubble/tool/approval/thinking/transcript/composer/sidecar
// each register on window via Object.assign at file bottom. Load order
// matters: session store FIRST so the sidecar can read it, then the
// leaf components, then transcript (which composes them), then composer
// + sidecar, then hermes-chat-tab.jsx (which composes the whole grid).
// Sidecar's TanStack-Query bridge publishes useMcpStatusPip onto window
// BEFORE the sidecar evaluates.
import './dash/agents/chat/sidecar-hook-bridge'
import './dash/agents/chat/use-hermes-session.js'
import './dash/agents/chat/markdown.jsx'
import './dash/agents/chat/message-bubble.jsx'
import './dash/agents/chat/tool-call-card.jsx'
import './dash/agents/chat/approval-card.jsx'
import './dash/agents/chat/thinking-indicator.jsx'
import './dash/agents/chat/transcript.jsx'
import './dash/agents/chat/composer.jsx'
import './dash/agents/chat/hermes-sidecar.jsx'
import './dash/agents/hermes-chat-tab.jsx'
import './dash/agents/personas-tab.jsx'
import './dash/agents/skills-tab.jsx'
import './dash/agents/memory-tab.jsx'
import './dash/agents/plugins-tab.jsx'
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
