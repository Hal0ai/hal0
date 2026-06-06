// hal0 dashboard — chrome (TopBar, Sidebar, Footer, Wordmark, ApprovalModal)
//
// Phase B1: Sidebar + Footer + TopBar now read lemond status from the
// `useLemondRollup` hook (live polling /v1/health every 2s). Fields
// fall back to the legacy HAL0_DATA.lemond shape when the hook hasn't
// yet returned (initial paint / mock build), so prototype layout is
// unchanged.

import { useLemondRollup } from '@/api/hooks/useLemonade'
import { useLogsStream } from '@/api/hooks/useLogs'
import { useSlots, useEndpoints } from '@/api/hooks/useSlots'
import { useModels } from '@/api/hooks/useModels'
import { useMemoryEnabled } from '@/api/hooks/useMemory'
import { useUpdateState } from '@/api/hooks/useUpdates'
import { useSidebarAgentRollup } from '@/api/hooks/useAgents'
import { useConfigUrls } from '@/api/hooks/useConfigUrls'
import { useHardware } from '@/api/hooks/useHardware'

const { useState: useStateC, useEffect: useEffectC } = React;

// ─── Wordmark — inline SVG, "hal" in currentColor + amber "0" ───
function Wordmark({ size = 16, mono = false }) {
  return (
    <span className="wm" style={{ display: "inline-flex", alignItems: "center", lineHeight: 1 }}>
      <svg
        viewBox="160 480 1340 500"
        role="img"
        aria-label="hal0"
        height={size}
        style={{ overflow: "visible" }}
      >
        <g transform="translate(150 194)" fill="currentColor">
          <g transform="translate(0 713.543886)">
            <path d="M 247.515625 -28.328125 C 247.515625 -20.523438 244.847656 -13.851562 239.515625 -8.3125 C 234.179688 -2.769531 227.613281 0 219.8125 0 L 202.578125 0 C 194.773438 0 188.203125 -2.769531 182.859375 -8.3125 C 177.523438 -13.851562 174.859375 -20.523438 174.859375 -28.328125 L 174.859375 -237.046875 C 174.859375 -239.515625 173.9375 -241.566406 172.09375 -243.203125 C 170.25 -244.847656 168.300781 -245.671875 166.25 -245.671875 L 106.515625 -245.671875 C 104.460938 -245.671875 102.515625 -244.847656 100.671875 -243.203125 C 98.828125 -241.566406 97.90625 -239.515625 97.90625 -237.046875 L 97.90625 -28.328125 C 97.90625 -20.523438 95.234375 -13.851562 89.890625 -8.3125 C 84.554688 -2.769531 77.988281 0 70.1875 0 L 52.953125 0 C 45.148438 0 38.476562 -2.769531 32.9375 -8.3125 C 27.394531 -13.851562 24.625 -20.523438 24.625 -28.328125 L 24.625 -387.90625 C 24.625 -396.113281 27.394531 -402.882812 32.9375 -408.21875 C 38.476562 -413.550781 45.148438 -416.21875 52.953125 -416.21875 L 70.1875 -416.21875 C 77.988281 -416.21875 84.554688 -413.550781 89.890625 -408.21875 C 95.234375 -402.882812 97.90625 -396.113281 97.90625 -387.90625 L 97.90625 -326.328125 C 97.90625 -324.273438 98.828125 -322.53125 100.671875 -321.09375 C 102.515625 -319.65625 104.460938 -318.9375 106.515625 -318.9375 L 179.171875 -318.9375 C 191.898438 -315.859375 203.394531 -315.859375 213.65625 -309.703125 C 223.914062 -303.546875 232.125 -295.234375 238.28125 -284.765625 C 244.4375 -274.296875 247.515625 -262.703125 247.515625 -249.984375 Z" />
          </g>
          <g transform="translate(272.763287 713.543886)">
            <path d="M 247.515625 -68.953125 C 247.515625 -49.660156 240.84375 -33.34375 227.5 -20 C 214.164062 -6.664062 198.054688 0 179.171875 0 L 93.59375 0 C 74.707031 0 58.488281 -6.664062 44.9375 -20 C 31.394531 -33.34375 24.625 -49.660156 24.625 -68.953125 L 24.625 -136.078125 C 24.625 -148.796875 27.804688 -160.179688 34.171875 -170.234375 C 40.535156 -180.296875 48.847656 -188.40625 59.109375 -194.5625 C 69.367188 -200.71875 80.863281 -203.796875 93.59375 -203.796875 L 166.25 -203.796875 C 168.300781 -203.796875 170.25 -204.617188 172.09375 -206.265625 C 173.9375 -207.910156 174.859375 -209.960938 174.859375 -212.421875 L 174.859375 -237.046875 C 174.859375 -239.515625 173.9375 -241.566406 172.09375 -243.203125 C 170.25 -244.847656 168.300781 -245.671875 166.25 -245.671875 L 93.59375 -245.671875 C 85.789062 -245.671875 79.117188 -248.335938 73.578125 -253.671875 C 68.035156 -259.015625 65.265625 -265.582031 65.265625 -273.375 L 65.265625 -290 C 65.265625 -298.207031 68.035156 -305.082031 73.578125 -310.625 C 79.117188 -316.164062 85.789062 -318.9375 93.59375 -318.9375 L 179.171875 -318.9375 C 191.898438 -318.9375 203.394531 -315.859375 213.65625 -309.703125 C 223.914062 -303.546875 232.125 -295.234375 238.28125 -284.765625 C 244.4375 -274.296875 247.515625 -262.703125 247.515625 -249.984375 Z M 174.859375 -80.65625 L 174.859375 -123.140625 C 174.859375 -125.191406 173.9375 -127.035156 172.09375 -128.671875 C 170.25 -130.316406 168.300781 -131.140625 166.25 -131.140625 L 106.515625 -131.140625 C 104.460938 -131.140625 102.515625 -130.316406 100.671875 -128.671875 C 98.828125 -127.035156 97.90625 -125.191406 97.90625 -123.140625 L 97.90625 -80.65625 C 97.90625 -78.601562 98.828125 -76.753906 100.671875 -75.109375 C 102.515625 -73.472656 104.460938 -72.65625 106.515625 -72.65625 L 166.25 -72.65625 C 168.300781 -72.65625 170.25 -73.472656 172.09375 -75.109375 C 173.9375 -76.753906 174.859375 -78.601562 174.859375 -80.65625 Z" />
          </g>
          <g transform="translate(545.526555 713.543886)">
            <path d="M 136.6875 -28.328125 C 136.6875 -20.523438 133.914062 -13.851562 128.375 -8.3125 C 122.832031 -2.769531 116.160156 0 108.359375 0 L 92.96875 0 C 74.09375 0 57.878906 -6.664062 44.328125 -20 C 30.785156 -33.34375 24.015625 -49.660156 24.015625 -68.953125 L 24.015625 -387.90625 C 24.015625 -396.113281 26.785156 -402.882812 32.328125 -408.21875 C 37.867188 -413.550781 44.539062 -416.21875 52.34375 -416.21875 L 69.578125 -416.21875 C 77.378906 -416.21875 84.050781 -413.550781 89.59375 -408.21875 C 95.132812 -402.882812 97.90625 -396.113281 97.90625 -387.90625 L 97.90625 -80.65625 C 97.90625 -75.320312 100.363281 -72.65625 105.28125 -72.65625 L 108.359375 -72.65625 C 116.160156 -72.65625 122.832031 -69.984375 128.375 -64.640625 C 133.914062 -59.304688 136.6875 -52.535156 136.6875 -44.328125 Z" />
          </g>
        </g>
        <g transform="translate(671 518)" fill={mono ? "currentColor" : "var(--accent)"} className="zero">
          <g transform="translate(183.175087 1.720401)">
            <path d="M 301.6875 188.453125 C 314.144531 188.523438 324.09375 191.941406 331.53125 198.703125 C 338.96875 205.460938 342.660156 214.578125 342.609375 226.046875 C 342.546875 237.503906 338.757812 246.578125 331.25 253.265625 C 323.738281 259.953125 313.753906 263.257812 301.296875 263.1875 L 226.5625 262.796875 C 214.101562 262.722656 204.15625 259.304688 196.71875 252.546875 C 189.28125 245.785156 185.59375 236.675781 185.65625 225.21875 C 185.707031 213.75 189.488281 204.671875 197 197.984375 C 204.507812 191.296875 214.492188 187.988281 226.953125 188.0625 Z M 152.546875 127.859375 C 121.648438 127.691406 97.0625 136.28125 78.78125 153.625 C 60.5 170.976562 51.28125 194.601562 51.125 224.5 C 50.957031 254.394531 59.921875 278.109375 78.015625 295.640625 C 96.109375 313.171875 120.601562 322.019531 151.5 322.1875 L 383.171875 323.421875 C 414.066406 323.585938 438.65625 315 456.9375 297.65625 C 475.21875 280.320312 484.441406 256.707031 484.609375 226.8125 C 484.765625 196.914062 475.796875 173.195312 457.703125 155.65625 C 439.609375 138.113281 415.113281 129.257812 384.21875 129.09375 Z M 152.90625 60.609375 L 384.578125 61.84375 C 408.992188 61.976562 431.144531 65.957031 451.03125 73.78125 C 470.925781 81.601562 487.804688 92.648438 501.671875 106.921875 C 515.535156 121.203125 526.148438 138.453125 533.515625 158.671875 C 540.890625 178.890625 544.507812 201.707031 544.375 227.125 C 544.238281 252.53125 540.378906 275.300781 532.796875 295.4375 C 525.210938 315.570312 514.410156 332.703125 500.390625 346.828125 C 486.367188 360.960938 469.375 371.835938 449.40625 379.453125 C 429.4375 387.078125 407.242188 390.820312 382.828125 390.6875 L 151.15625 389.453125 C 126.738281 389.328125 104.707031 385.347656 85.0625 377.515625 C 65.414062 369.691406 48.65625 358.640625 34.78125 344.359375 C 20.90625 330.078125 10.160156 312.703125 2.546875 292.234375 C -5.054688 271.765625 -8.789062 249.078125 -8.65625 224.171875 C -8.519531 198.753906 -4.664062 175.976562 2.90625 155.84375 C 10.488281 135.707031 21.289062 118.570312 35.3125 104.4375 C 49.34375 90.3125 66.34375 79.441406 86.3125 71.828125 C 106.289062 64.222656 128.488281 60.484375 152.90625 60.609375 Z" />
          </g>
        </g>
      </svg>
    </span>
  );
}

// ─── Icons (consistent stroke style, 16x16 viewBox) ───
const Icon = ({ d, size = 16, fill = "none", stroke = "currentColor", sw = 1.5, children }) => (
  <svg width={size} height={size} viewBox="0 0 16 16" fill={fill} stroke={stroke} strokeWidth={sw} strokeLinecap="round" strokeLinejoin="round">
    {d ? <path d={d} /> : children}
  </svg>
);
const Icons = {
  dashboard: <Icon><rect x="2" y="2" width="5" height="5" rx="1"/><rect x="9" y="2" width="5" height="9" rx="1"/><rect x="2" y="9" width="5" height="5" rx="1"/><rect x="9" y="13" width="5" height="0.01"/></Icon>,
  slots:     <Icon><rect x="2" y="3" width="12" height="3" rx="0.5"/><rect x="2" y="7" width="12" height="3" rx="0.5"/><rect x="2" y="11" width="12" height="3" rx="0.5"/><circle cx="4" cy="4.5" r="0.6" fill="currentColor" stroke="none"/><circle cx="4" cy="8.5" r="0.6" fill="currentColor" stroke="none"/><circle cx="4" cy="12.5" r="0.6" fill="currentColor" stroke="none"/></Icon>,
  models:    <Icon><path d="M2 4l6-2 6 2-6 2-6-2z"/><path d="M2 8l6 2 6-2"/><path d="M2 12l6 2 6-2"/></Icon>,
  hardware:  <Icon><rect x="3" y="3" width="10" height="10" rx="1"/><rect x="5.5" y="5.5" width="5" height="5" rx="0.5"/><path d="M3 6h-1M3 10h-1M13 6h1M13 10h1M6 3v-1M10 3v-1M6 13v1M10 13v1"/></Icon>,
  backends:  <Icon><circle cx="4" cy="4" r="2"/><circle cx="12" cy="4" r="2"/><circle cx="4" cy="12" r="2"/><circle cx="12" cy="12" r="2"/><path d="M6 4h4M4 6v4M12 6v4M6 12h4"/></Icon>,
  logs:      <Icon><path d="M3 3h10M3 6h10M3 9h7M3 12h5"/></Icon>,
  agent:     <Icon><circle cx="8" cy="6" r="2.5"/><path d="M3 14c0-2.5 2.2-4.5 5-4.5s5 2 5 4.5"/><circle cx="13" cy="3" r="1.5"/></Icon>,
  settings:  <Icon><circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3 3l1.5 1.5M11.5 11.5L13 13M3 13l1.5-1.5M11.5 4.5L13 3"/></Icon>,
  bell:      <Icon d="M4 11h8c-1 0-1.5-0.5-1.5-2V6.5a2.5 2.5 0 0 0-5 0V9c0 1.5-0.5 2-1.5 2zM6.5 13a1.5 1.5 0 0 0 3 0"/>,
  search:    <Icon><circle cx="7" cy="7" r="4"/><path d="M10 10l3 3"/></Icon>,
  send:      <Icon d="M2 8l12-6-3 14-3-6-6-2z"/>,
  attach:    <Icon d="M9 3l-5 5a3 3 0 0 0 4 4l5-5a2 2 0 0 0-3-3l-5 5"/>,
  mic:       <Icon><rect x="6" y="2" width="4" height="7" rx="2"/><path d="M4 8a4 4 0 0 0 8 0M8 12v2M5 14h6"/></Icon>,
  chev:      <Icon d="M4 6l4 4 4-4"/>,
  chevR:     <Icon d="M6 4l4 4-4 4"/>,
  close:     <Icon d="M4 4l8 8M12 4l-8 8"/>,
  check:     <Icon d="M3 8l3 3 7-7"/>,
  warn:      <Icon><path d="M8 2l6 11H2L8 2z"/><path d="M8 7v3M8 12v0.01"/></Icon>,
  plus:      <Icon d="M8 3v10M3 8h10"/>,
  ext:       <Icon d="M6 3H3v10h10v-3M9 3h4v4M9 9l4-4"/>,
  download:  <Icon d="M8 2v8M4 7l4 4 4-4M3 14h10"/>,
  restart:   <Icon><path d="M14 8a6 6 0 1 1-2-4.5"/><path d="M14 1v3.5h-3.5"/></Icon>,
  unload:    <Icon d="M3 11h10M5 8l3 3 3-3M8 11V2"/>,
  start:     <Icon d="M5 3l8 5-8 5V3z"/>,
  edit:      <Icon d="M3 13l3-1 7-7-2-2-7 7-1 3z"/>,
  more:      <Icon><circle cx="3" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="8" cy="8" r="1" fill="currentColor" stroke="none"/><circle cx="13" cy="8" r="1" fill="currentColor" stroke="none"/></Icon>,
  cpu:       <Icon><rect x="4" y="4" width="8" height="8" rx="0.5"/><path d="M4 6h-1M4 10h-1M13 6h-1M13 10h-1M6 4v-1M10 4v-1M6 13v-1M10 13v-1"/><rect x="6.5" y="6.5" width="3" height="3"/></Icon>,
  flame:     <Icon d="M8 13c-3 0-4-2-4-4 0-2 2-3 2-5 1 2 5 2 5 6 0 2-1 3-3 3z"/>,
  chat:      <Icon><path d="M2.5 4.5a1.5 1.5 0 0 1 1.5-1.5h8a1.5 1.5 0 0 1 1.5 1.5v5a1.5 1.5 0 0 1-1.5 1.5H7l-3 2.5V11H4a1.5 1.5 0 0 1-1.5-1.5z"/></Icon>,
};

// ─── TopBar ───
function TopBar({ route, hostUptime = "14d 02:11", onBell, onCmdK, approvals = 0 }) {
  // Issue #333: hostname from live /api/hardware (useHardware hook) instead of
  // the legacy HAL0_DATA seed. Fall back to a neutral "hal0" placeholder
  // while the first response is in flight so the layout stays stable.
  const hw = useHardware();
  const hostName = hw.data?.name || "hal0";
  const labels = {
    dashboard: ["Overview", "Dashboard"],
    firstrun:  ["Setup",   "FirstRun"],
    slots:     ["Lifecycle", "Slots"],
    models:    ["Catalog", "Models"],
    logs:      ["Runtime", "Logs"],
    agent:     ["Tools",  "Agent"],
    settings:  ["Configure", "Settings"],
  };
  const [eyebrow, title] = labels[route] || ["", ""];
  return (
    <div className="topbar">
      <div className="tb-brand">
        <Wordmark size={18} />
        <span className="ver mono">v0.2.1</span>
      </div>
      {route !== "firstrun" && (
        <div className="tb-eyebrow mono">
          <span>{eyebrow}</span>
          <span className="sep">/</span>
          <span className="now">{title}</span>
        </div>
      )}
      <div className="tb-spacer" />
      <button className="tb-cmdk" onClick={onCmdK}>
        {Icons.search}<span>Command palette</span>
        <kbd>⌘K</kbd>
      </button>
      <div className="tb-host">
        <span className="host-dot" />
        <b>{hostName}</b>
        <span className="ut">· up {hostUptime}</span>
      </div>
      <button className="tb-bell" onClick={onBell} aria-label="Agent approvals">
        {Icons.bell}
        {approvals > 0 && <span className="badge num">{approvals}</span>}
      </button>
    </div>
  );
}

// ─── Sidebar ───
function Sidebar({ route, onGo }) {
  const slotsQuery  = useSlots();
  const modelsQuery = useModels();
  const slotCount   = slotsQuery.data?.length  ?? 0;
  const modelCount  = modelsQuery.data?.length ?? 0;
  // 0.4 gate: the Agent route is reduced to the Memory tab, so when the
  // memory subsystem is disabled (HAL0_MEMORY_ENABLED!=1) there is nothing
  // to show — drop the nav item entirely. Driven by /api/status so the UI
  // can never disagree with the backend. main.jsx applies the matching
  // route guard for deep links.
  const memoryEnabled = useMemoryEnabled();
  const items = [
    { id: "dashboard", label: "Dashboard", icon: Icons.dashboard },
    { id: "slots",     label: "Slots",     icon: Icons.slots, cnt: slotCount },
    { id: "models",    label: "Models",    icon: Icons.models, cnt: modelCount },
    { id: "logs",      label: "Logs",      icon: Icons.logs },
    ...(memoryEnabled ? [{ id: "agent", label: "Agent", icon: Icons.agent }] : []),
    // Issue #206 — MCP page wired to /api/mcp/*. Lives under "Agents"
    // conceptually but kept as a sibling in the sidebar so the URL is
    // discoverable. Icon reuses the agent glyph (no dedicated MCP icon
    // in the design system yet).
    { id: "mcp",       label: "MCP",       icon: Icons.agent },
    { id: "settings",  label: "Settings",  icon: Icons.settings },
  ];
  return (
    <div className="sidebar">
      <div className="sb-section">Navigate</div>
      <div className="sb-list">
        {items.map(it => (
          <div
            key={it.id}
            className={"sb-row" + (route === it.id ? " active" : "")}
            onClick={() => onGo(it.id)}
          >
            {it.icon}
            <span className="lbl">{it.label}</span>
            {it.cnt !== undefined && <span className="cnt num">{it.cnt}</span>}
          </div>
        ))}
      </div>
      <div className="sb-spacer" />
      {/*
        Runtime widget (2026-06-05): the former three stacked status blocks
        (SidebarAgentBlock / SidebarEndpointBlock / SidebarStatusBlock) are
        consolidated into ONE card so hermes, hal0, lemond and openwebui read
        as a single runtime rollup. hermes + openwebui rows deep-link to their
        own dashboards.
      */}
      <SidebarRuntimeWidget onGo={onGo} />
    </div>
  );
}

// ─── Runtime widget (consolidated status card) ───
//
// Single sidebar card that rolls up the four runtime surfaces that used to
// live in three separate blocks:
//   - hermes    — bundled agent health (useSidebarAgentRollup → /api/agents).
//                 Row key deep-links to the standalone Hermes dashboard.
//   - hal0      — the composite ``hal0`` /v1 upstream surfaced as a synthetic
//                 entry on /api/slots (useEndpoints filters `_synthetic`);
//                 NOT a lifecycle slot, so it's read-only here. Model count is
//                 the chat figure (advertised_models) plus a non-chat modality
//                 breakdown counted from the real slots by group.
//   - lemond    — the inference runtime (useLemondRollup → /v1/health). npu
//                 ``coresident`` row only renders when lemond reports it.
//   - openwebui — the external chat UI unit (useInstallState.openwebui_running).
//                 Row key deep-links to the OpenWebUI app.
//
// hermes + openwebui link targets are NOT hardcoded — useConfigUrls() reads
// GET /api/config/urls, where the backend derives the reachable host from the
// request (so links work on localhost / LAN IP / hal0.local / a custom
// reverse-proxy domain) and honours the HAL0_{OPENWEBUI,HERMES}_PUBLIC_URL
// env overrides. Each `*_enabled` flag gates whether we render a link at all.
function SidebarRuntimeWidget({ onGo }) {
  const L         = useLemondRollup();
  const agent     = useSidebarAgentRollup();
  const endpoints = useEndpoints().data || [];
  const slots     = useSlots().data     || [];
  const urls      = useConfigUrls();

  // ── lemond ──
  const lemondClass = L.status === 'up' ? 'up' : L.status === 'down' ? 'down' : '';

  // ── hermes ── honest dot tone: green=running, red=broken, amber=unknown.
  // "off" when no agent is installed (no false-broken red on a fresh box).
  const agentClass =
    agent.agentStatus === 'running' ? 'up'
      : agent.agentStatus === 'broken' ? 'down'
        : 'warn';
  const agentLabel =
    !agent.installed ? 'off'
      : agent.agentStatus === 'running' ? 'running'
        : agent.agentStatus === 'broken' ? 'broken'
          : '—';
  // hermes web dashboard link — only when the backend advertises a public
  // URL (loopback-only otherwise, so no host:port fallback exists).
  const hermesUrl      = urls.data?.hermes || "";
  const hermesLinkable = urls.data?.hermes_enabled === true && !!hermesUrl;

  // ── hal0 endpoint ── the synthetic composite upstream (first/only one).
  const ep        = endpoints[0];
  const epServing = !!ep && ep.status === 'serving';
  const epClass   = !ep ? 'warn' : epServing ? 'up' : 'down';
  const epLabel   = !ep ? '—' : epServing ? 'serving' : 'offline';
  const chatCount = ep?.advertised_models ?? 0;
  const embedCount = slots.filter(s => s.group === "embed").length;
  const voiceCount = slots.filter(s => s.group === "voice").length;
  const imgCount   = slots.filter(s => s.group === "img").length;
  const extraParts = [];
  if (embedCount > 0) extraParts.push(`${embedCount} embed`);
  if (voiceCount > 0) extraParts.push(`${voiceCount} voice`);
  if (imgCount   > 0) extraParts.push(`${imgCount} img`);
  const modelsTitle = [`${chatCount} chat`, ...extraParts].join(" · ");

  // ── openwebui ── "—" until /api/config/urls resolves, then running/off.
  // `openwebui_enabled` already folds in "unit up AND reachably linkable",
  // so it drives both the dot and whether the key is a link.
  const owuiUrl      = urls.data?.openwebui || "";
  const owuiKnown    = urls.isSuccess;
  const owuiEnabled  = urls.data?.openwebui_enabled === true;
  const owuiClass    = !owuiKnown ? 'warn' : owuiEnabled ? 'up' : 'down';
  const owuiLabel    = !owuiKnown ? '—' : owuiEnabled ? 'running' : 'off';
  const owuiLinkable = owuiEnabled && !!owuiUrl;

  return (
    <div className="sb-status sb-runtime" data-testid="sidebar-runtime-widget">
      <div className="sb-runtime-h">Runtime</div>

      {/* hermes — deep-links to the Hermes dashboard when one is published */}
      <div className="row" data-testid="runtime-row-hermes">
        {hermesLinkable ? (
          <a
            className="k rt-link"
            href={hermesUrl}
            target="_blank"
            rel="noopener noreferrer"
            title="Open the Hermes dashboard"
          >hermes ↗</a>
        ) : (
          <span className="k">hermes</span>
        )}
        <span className={"v " + agentClass} title={`agent ${agent.agentId ?? ""} — ${agentLabel}`}>
          <span className="dot" />{agentLabel}
        </span>
      </div>

      {/* hal0 — composite /v1 endpoint (read-only) */}
      <div className="row" data-testid="runtime-row-hal0" title={ep?._synthetic_reason || ""}>
        <span className="k">hal0</span>
        <span className={"v " + epClass}><span className="dot" />{epLabel}</span>
      </div>
      <div className="row rt-sub" title={modelsTitle}>
        <span className="k">models</span>
        <span className="v">
          <b>{chatCount}</b>
          {extraParts.length > 0 && (
            <span className="rt-extra"> + {extraParts.join(" · ")}</span>
          )}
        </span>
      </div>

      {/* lemond — inference runtime */}
      <div
        className="row"
        data-testid="runtime-row-lemond"
        title={`${L.loaded} model${L.loaded === 1 ? "" : "s"} resident in Lemonade / ${L.budget} max_loaded_models cap`}
      >
        <span className="k">lemond</span>
        <span className={"v " + lemondClass}>
          <span className="dot" />
          {L.status}{L.status === 'up' && L.version !== '—' ? ` · ${L.version}` : ''}
        </span>
      </div>
      {L.coresident && (
        <div className="row">
          <span className="k">npu</span>
          <span className="v" style={{ color: "var(--dev-npu)" }}><span className="dot" />coresident</span>
        </div>
      )}

      {/* openwebui — deep-links to the external chat UI when reachable */}
      <div className="row" data-testid="runtime-row-openwebui">
        {owuiLinkable ? (
          <a
            className="k rt-link"
            href={owuiUrl}
            target="_blank"
            rel="noopener noreferrer"
            title="Open the OpenWebUI chat"
          >openwebui ↗</a>
        ) : (
          <span className="k">openwebui</span>
        )}
        <span className={"v " + owuiClass}><span className="dot" />{owuiLabel}</span>
      </div>

      <div className="ln" />
      <div className="nudge" onClick={() => onGo && onGo("logs")}>View runtime logs →</div>
    </div>
  );
}

// ─── Footer ───
// Phase 3 of #322: the journal pane reads `useLogsStream` against
// /api/journal/stream (no more HAL0_DATA.journal fallback) and the
// update chip reads `useUpdateState` directly (no more `updateAvailable`
// prop threaded from main.jsx — the chip is self-driven).
//
// The `updateAvailable` prop is kept as an OPTIONAL suppression gate
// so existing callers (BannerStack dismiss state) can still hide the
// chip without re-architecting. When omitted the chip defaults to
// "show if there is a real update" — never the prototype's hardcoded
// "v0.2.2 available" string.
function Footer({ updateAvailable, expanded = false, onToggle }) {
  // Lemond rollup gives live status / loaded / throughput / queued.
  // useLogsStream subscribes to /api/journal/stream when the pane is
  // expanded (saves opening an SSE we don't render).
  const L = useLemondRollup();
  const [paneSrc, setPaneSrc] = useStateC("merged");
  const [paneQ, setPaneQ] = useStateC("");
  // Source filter rides the SSE URL so the backend pre-filters; search
  // is client-only against `entry.msg` so the user sees instant feedback
  // while typing (no reconnect per keystroke).
  const live = useLogsStream({ follow: expanded, source: paneSrc });

  // The ring is the SOLE source of truth — no HAL0_DATA fallback. When
  // empty (cold load before SSE replays or a genuinely quiet system)
  // the body renders an empty-state placeholder below.
  const ringSorted = [...(live.ring || [])].sort((a, b) =>
    (a.ts || '').localeCompare(b.ts || ''),
  );

  // Source filter is also applied client-side so entries that arrived
  // on the previous (broader) SSE stream don't linger after the user
  // narrows the chip. Server-side filtering on the new SSE prevents
  // FUTURE entries from arriving; this catches the residual ring.
  // Search filter is purely client-only (no SSE reconnect per keystroke).
  const filtered = ringSorted.filter((e) => {
    if (paneSrc !== 'merged' && e.source !== paneSrc) return false;
    return !(paneQ && (!e.msg || !e.msg.toLowerCase().includes(paneQ.toLowerCase())));
  });

  // Update chip — self-driven from useUpdateState. The prop can still
  // suppress (banner-dismiss memory) but never resurrect.
  const updates = useUpdateState();
  const updateState = updates.data;
  const hal0Channel = updateState && updateState.hal0;
  const hasUpdate = !!(
    hal0Channel &&
    hal0Channel.available &&
    hal0Channel.available !== hal0Channel.current
  );
  // Backwards-compat: when caller passes `updateAvailable={false}` the
  // chip stays hidden even when a real update exists (used to honour
  // the banner-stack dismiss). Default (undefined) is "no suppression".
  const showUpdateChip = hasUpdate && updateAvailable !== false;

  return (
    <div className={"footer" + (expanded ? " expanded" : "")}>
      {expanded && (
        <div className="foot-pane">
          <div className="foot-pane-h mono">
            <span>Live journal</span>
            <span className="ct">· {filtered.length} / {ringSorted.length}</span>
            <div className="foot-pane-filter mono">
              {[["merged", "merged"], ["hal0", "hal0"], ["lemond", "lemond"]].map(([k, l]) => (
                <button
                  key={k}
                  onClick={() => setPaneSrc(k)}
                  className={"foot-pane-chip" + (paneSrc === k ? " on" : "")}
                >{l}</button>
              ))}
            </div>
            <input
              className="foot-pane-search mono"
              value={paneQ}
              onChange={e => setPaneQ(e.target.value)}
              placeholder="search…"
            />
            <span style={{display: "inline-flex", gap: 10, marginLeft: "auto"}}>
              <span style={{color: live.disconnected ? "var(--warn)" : "var(--ok)", display: "inline-flex", alignItems: "center", gap: 5}}>
                <span className={"dot" + (live.disconnected ? "" : " ready")} />
                {live.disconnected ? "reconnecting…" : "follow tail"}
              </span>
              <a href="#logs" className="foot-pane-link">Open full logs →</a>
            </span>
          </div>
          <div className="foot-pane-body">
            {ringSorted.length === 0 ? (
              <div className="foot-pane-empty" style={{padding: 24, textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 11.5}}>
                No events yet
              </div>
            ) : filtered.length === 0 ? (
              <div style={{padding: 24, textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 11.5}}>
                No journal entries match. <span style={{color: "var(--accent)", cursor: "pointer"}} onClick={() => { setPaneSrc("merged"); setPaneQ(""); }}>Clear filters</span>
              </div>
            ) : filtered.map((e, i) => (
              <div key={e.id ?? i} className={"foot-line " + e.level}>
                <span className="ts">{e.ts}</span>
                <span className={"sl " + e.source}>[{e.source}]</span>
                <span className="lvl">{e.level}</span>
                <span className="msg">{paneQ ? highlightFoot(e.msg, paneQ) : e.msg}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <div className="foot-chips">
        <div className={"foot-chip " + (L.status === 'up' ? 'up' : '')}>
          <span className="dot" />
          <span className="k">lemond:</span>
          <span className="v">{L.status}</span>
        </div>
        {/*
          Throughput chip (#326): Lemonade does not yet surface
          throughput_mbps on /v1/health for every backend. The rollup
          coerces missing → null. Hide the chip entirely instead of
          rendering "—" or "0.0 MB/s" — a value of 0 from a live system
          actually serving traffic is just as meaningless as null.
        */}
        {L.throughput != null && L.throughput > 0 && (
          <div className="foot-chip">
            <span className="k">throughput</span>
            <span className="v num">{`${L.throughput} MB/s`}</span>
          </div>
        )}
        {/* "models loaded N/budget" chip removed (2026-06-05) — the figure now
            lives in the sidebar Runtime widget's lemond row tooltip only. */}
        {L.coresident && (
          <div className="foot-chip" style={{ color: "var(--dev-npu)" }}>
            <span className="dot" />
            <span className="k">npu</span>
            <span className="v">coresident</span>
          </div>
        )}
        {L.queued != null && (
          <div className="foot-chip">
            <span className="k">queued</span>
            <span className="v num">{L.queued}</span>
          </div>
        )}
        {showUpdateChip && (
          <div className="foot-chip accent">
            <span className="k">●</span>
            <span className="v">{`hal0 ${hal0Channel.available} available`}</span>
          </div>
        )}
        <button className="foot-toggle" onClick={onToggle} aria-expanded={expanded} aria-label="Toggle journal pane">
          <span style={{transform: expanded ? "rotate(180deg)" : "rotate(0)", transition: "transform 0.18s ease", display: "inline-block"}}>⌃</span>
          <span>journal</span>
        </button>
      </div>
      {/*
        Always-visible footer journal ribbon: a thin tail of the most
        recent entries. Pulls from the same SSE ring; renders nothing
        before the first frame arrives so we never hardcode mock copy.
      */}
      {ringSorted.length > 0 && (
        <div className="foot-journal mono">
          {ringSorted.slice(-6).map((e, i, arr) => (
            <span key={e.id ?? i} className={"ent " + e.level}>
              <span className="ts">{e.ts}</span>
              <span className="sl">[{e.source}]</span>
              <span className="ar">·</span>
              <span>{e.msg}</span>
              {i < arr.length - 1 && <span className="sep">  </span>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function highlightFoot(text, q) {
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <span style={{background: "var(--accent-soft)", color: "var(--accent)", padding: "0 2px", borderRadius: 2}}>{text.slice(i, i + q.length)}</span>
      {text.slice(i + q.length)}
    </>
  );
}

// ─── Approval inbox modal ───
function ApprovalModal({ open, onClose, items }) {
  if (!open) return null;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-shell approval-modal" onClick={e => e.stopPropagation()}>
        <div className="modal-h">
          <div className="modal-h-eye mono">Agent · Approvals</div>
          <h2 className="mono">Pending gated tool calls</h2>
          <button className="modal-close" onClick={onClose} aria-label="Close">{Icons.close}</button>
        </div>
        <div className="modal-body">
          <div className="approval-banner mono">
            <span>{Icons.warn}</span>
            <span>3 requests waiting. Calls block until you approve or deny — agents pause cleanly.</span>
          </div>
          {items.map((a, i) => (
            <div key={i} className="approval-card">
              <div className="approval-h">
                <span className="ts mono">{a.ts}</span>
                <span className="ag mono">{a.agent}</span>
                <span className="sep mono">requests</span>
                <span className="tool mono"><b>{a.tool}</b></span>
              </div>
              <div className="approval-body mono">
                <div className="arg-row">
                  <span className="k">argument</span>
                  <span className="v">{a.arg}</span>
                </div>
                <div className="arg-row">
                  <span className="k">capability</span>
                  <span className="v">{a.tool.startsWith("model_") ? "registry-write" : a.tool.startsWith("fs_") ? "fs-write" : "shell-exec"}</span>
                </div>
                <div className="arg-row">
                  <span className="k">policy</span>
                  <span className="v">gated · requires operator approval</span>
                </div>
              </div>
              <div className="approval-actions">
                <button className="btn danger sm">Deny</button>
                <button className="btn ghost sm">Deny + remember</button>
                <button className="btn sm">Approve</button>
              </div>
            </div>
          ))}
        </div>
        <div className="modal-foot mono">
          <span>Configure auto-approve rules in the agent view.</span>
          <button className="btn ghost sm" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}

// ─── Generic modal shell styles (added inline to keep modal CSS together) ───
const modalStyles = `
.modal-backdrop {
  position: fixed; inset: 0;
  background: rgba(0, 0, 0, 0.65);
  backdrop-filter: blur(2px);
  z-index: 100;
  display: flex; align-items: flex-start; justify-content: center;
  padding: 80px 24px 24px;
  overflow-y: auto;
}
.modal-shell {
  background: var(--bg-1);
  border: 1px solid var(--line-strong);
  border-radius: var(--rad-lg);
  box-shadow: 0 24px 80px -16px rgba(0, 0, 0, 0.8);
  max-width: 680px;
  width: 100%;
  overflow: hidden;
}
.modal-h {
  padding: 20px 22px 16px;
  border-bottom: 1px solid var(--line-soft);
  position: relative;
}
.modal-h-eye { font-size: 10px; color: var(--accent); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 4px; }
.modal-h h2 { font-size: 18px; font-weight: 500; margin: 0; letter-spacing: -0.02em; }
.modal-close { position: absolute; top: 16px; right: 16px; width: 28px; height: 28px; display: inline-flex; align-items: center; justify-content: center; background: transparent; border: 1px solid var(--line); border-radius: var(--rad-sm); cursor: pointer; color: var(--fg-3); }
.modal-close:hover { color: var(--fg); border-color: var(--line-strong); }
.modal-body { padding: 18px 22px; max-height: 70vh; overflow-y: auto; }
.modal-foot { padding: 14px 22px; border-top: 1px solid var(--line-soft); background: var(--bg); display: flex; align-items: center; justify-content: space-between; font-size: 11px; color: var(--fg-4); }

.approval-banner { display: flex; gap: 10px; align-items: center; padding: 10px 12px; background: var(--warn-soft); border: 1px solid var(--warn-line); color: var(--warn); border-radius: var(--rad); font-size: 12px; margin-bottom: 16px; }
.approval-card { border: 1px solid var(--line); border-radius: var(--rad); margin-bottom: 12px; overflow: hidden; }
.approval-h { display: flex; align-items: center; gap: 8px; padding: 10px 14px; border-bottom: 1px solid var(--line-soft); font-size: 11.5px; background: var(--bg); }
.approval-h .ts { color: var(--fg-4); }
.approval-h .ag { color: var(--accent); font-weight: 500; }
.approval-h .sep { color: var(--fg-4); }
.approval-h .tool b { color: var(--fg); font-weight: 500; }
.approval-body { padding: 12px 14px; display: flex; flex-direction: column; gap: 5px; font-size: 11.5px; }
.approval-body .arg-row { display: grid; grid-template-columns: 100px 1fr; gap: 12px; }
.approval-body .arg-row .k { color: var(--fg-4); }
.approval-body .arg-row .v { color: var(--fg-2); }
.approval-actions { padding: 10px 14px; border-top: 1px solid var(--line-soft); background: var(--bg); display: flex; gap: 6px; justify-content: flex-end; }
`;

// inject modal CSS once
if (typeof document !== "undefined" && !document.getElementById("hal0-modal-css")) {
  const s = document.createElement("style");
  s.id = "hal0-modal-css";
  s.textContent = modalStyles;
  document.head.appendChild(s);
}

// ─── Bottom tab bar (mobile <720px) ───
function BottomTabs({ route, onGo }) {
  const tabs = [
    { id: "dashboard", label: "Home",   icon: Icons.dashboard },
    { id: "agent",     label: "Agent",  icon: Icons.agent },
    { id: "slots",     label: "Slots",  icon: Icons.slots },
    { id: "models",    label: "Models", icon: Icons.models },
    { id: "settings",  label: "More",   icon: Icons.settings },
  ];
  return (
    <nav className="bottom-tabs" aria-label="Primary">
      {tabs.map(t => (
        <button
          key={t.id}
          className={"bottom-tab" + (route === t.id ? " active" : "")}
          onClick={() => onGo(t.id)}
        >
          {t.icon}
          <span>{t.label}</span>
        </button>
      ))}
    </nav>
  );
}

Object.assign(window, { Wordmark, Icons, Icon, TopBar, Sidebar, Footer, ApprovalModal, BottomTabs });
