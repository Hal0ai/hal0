// hal0 v0.3 — MCP page mount
// Composes the existing dashboard chrome (TopBar/Sidebar/Footer) with the new McpView.

import { useRuntimeRollup } from '@/api/hooks/useRuntime'
import { useSlots } from '@/api/hooks/useSlots'
import { useModels } from '@/api/hooks/useModels'

const { useState: useStateMnt, useEffect: useEffectMnt } = React;

const MCP_TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "showLiveTimelines": true,
  "compactRows": false
}/*EDITMODE-END*/;

// Sidebar customized for v0.3 — keeps the existing routes plus a new "MCP" item
// under the Agents grouping. We use a route value of "mcp" so the existing
// chrome highlights nothing for routes it doesn't know about.
function McpSidebar({ active }) {
  const slotsQuery  = useSlots();
  const modelsQuery = useModels();
  const L           = useRuntimeRollup();
  const slotCount   = slotsQuery.data?.length  ?? 0;
  const modelCount  = modelsQuery.data?.length ?? 0;
  const runtimeStatusClass = L.status === 'up' ? 'up' : L.status === 'down' ? 'down' : '';
  const items = [
    { id: "dashboard", label: "Dashboard", icon: Icons.dashboard, href: "hal0 v2 dashboard.html" },
    { id: "slots",     label: "Slots",     icon: Icons.slots, cnt: slotCount, href: "hal0 v2 dashboard.html#slots" },
    { id: "models",    label: "Models",    icon: Icons.models, cnt: modelCount, href: "hal0 v2 dashboard.html#models" },
    { id: "hardware",  label: "Hardware",  icon: Icons.hardware, href: "hal0 v2 dashboard.html#hardware" },
    { id: "backends",  label: "Backends",  icon: Icons.backends, href: "hal0 v2 dashboard.html#backends" },
    { id: "logs",      label: "Logs",      icon: Icons.logs, href: "hal0 v2 dashboard.html#logs" },
  ];
  const agentsItems = [
    { id: "agents",  label: "Agents",       icon: Icons.agent, href: "hal0 v2 dashboard.html#agent" },
    { id: "mcp",     label: "MCP Servers",  icon: <McpIcon />,  cnt: MCP_SERVERS.length, sub: "v0.3" },
    { id: "memory",  label: "Memory",       icon: <MemoryIcon />, href: "#memory" },
  ];
  return (
    <div className="sidebar">
      <div className="sb-section">Navigate</div>
      <div className="sb-list">
        {items.map(it => (
          <a key={it.id} className="sb-row" href={it.href || "#"}>
            {it.icon}
            <span className="lbl">{it.label}</span>
            {it.cnt !== undefined && <span className="cnt num">{it.cnt}</span>}
          </a>
        ))}
      </div>
      <div className="sb-section" style={{marginTop: 12}}>Agents · v0.3</div>
      <div className="sb-list">
        {agentsItems.map(it => (
          <a
            key={it.id}
            className={"sb-row" + (active === it.id ? " active" : "")}
            href={it.href || "#"}
          >
            {it.icon}
            <span className="lbl">{it.label}</span>
            {it.sub && <span className="sub mono">{it.sub}</span>}
            {it.cnt !== undefined && <span className="cnt num">{it.cnt}</span>}
          </a>
        ))}
      </div>
      <div className="sb-section" style={{marginTop: 12}}>System</div>
      <div className="sb-list">
        <a className="sb-row" href="hal0 v2 dashboard.html#settings">
          {Icons.settings}
          <span className="lbl">Settings</span>
        </a>
      </div>
      <div className="sb-spacer" />
      <div className="sb-status">
        <div className="row">
          <span className="k">runtime</span>
          <span className={"v " + runtimeStatusClass}><span className="dot" />{L.status}</span>
        </div>
        <div className="row">
          <span className="k">slots</span>
          <span className="v">{L.ready}/{L.total} ready</span>
        </div>
        <div className="ln" />
        <div className="row">
          <span className="k">mcp host</span>
          <span className="v" style={{color: "var(--accent)"}}>
            <span className="dot" style={{background: "var(--accent)", boxShadow: "0 0 6px var(--accent)"}} />
            up
          </span>
        </div>
        <div className="row">
          <span className="k">servers</span>
          <span className="v"><b>{MCP_SERVERS.filter(s => s.state === "running").length}</b>/{MCP_SERVERS.length}</span>
        </div>
      </div>
    </div>
  );
}

function McpIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="3.5" cy="3.5" r="1.5" />
      <circle cx="12.5" cy="3.5" r="1.5" />
      <circle cx="3.5" cy="12.5" r="1.5" />
      <circle cx="12.5" cy="12.5" r="1.5" />
      <circle cx="8" cy="8" r="2" />
      <path d="M5 5l1.5 1.5M9.5 6.5L11 5M5 11l1.5-1.5M9.5 9.5L11 11" />
    </svg>
  );
}
function MemoryIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="4" cy="4" r="1.5" />
      <circle cx="12" cy="4" r="1.5" />
      <circle cx="8" cy="11" r="1.5" />
      <path d="M5 5l2 5M11 5l-2 5M5 4h6" />
    </svg>
  );
}

// Minimal TopBar wrapper that matches the existing chrome but defaults route
// to a v0.3-friendly label.
function McpTopBar({ onCmdK }) {
  return (
    <div className="topbar">
      <div className="tb-brand">
        <Wordmark size={18} />
        <span className="ver mono">v0.3.0-rc1</span>
      </div>
      <div className="tb-eyebrow mono">
        <span>Agents</span>
        <span className="sep">/</span>
        <span className="now">MCP Servers</span>
      </div>
      <div className="tb-spacer" />
      <button className="tb-cmdk" onClick={onCmdK}>
        {Icons.search}<span>Command palette</span>
        <kbd>⌘K</kbd>
      </button>
      <div className="tb-host">
        <span className="host-dot" />
        <b>{HAL0_DATA.host.name}</b>
        <span className="ut">· up {HAL0_DATA.host.uptime}</span>
      </div>
      <button className="tb-bell" aria-label="Agent approvals">
        {Icons.bell}
        {HAL0_DATA.approvals.length > 0 && <span className="badge num">{HAL0_DATA.approvals.length}</span>}
      </button>
    </div>
  );
}

// Toast & footer-journal we inherit from chrome.jsx; for this page we keep
// the footer's slim journal strip but drop the expandable pane to reduce noise.
function McpFooter() {
  const L = useRuntimeRollup();
  return (
    <div className="footer">
      <div className="foot-chips">
        <div className="foot-chip up">
          <span className="dot" />
          <span className="k">mcp-host:</span>
          <span className="v">up</span>
        </div>
        <div className="foot-chip">
          <span className="k">servers</span>
          <span className="v num">{MCP_SERVERS.filter(s => s.state === "running").length}/{MCP_SERVERS.length}</span>
        </div>
        <div className="foot-chip accent">
          <span className="k">clients</span>
          <span className="v num">{MCP_CLIENTS.length}</span>
        </div>
        <div className="foot-chip">
          <span className="k">runtime:</span>
          <span className="v">{L.status === 'up' ? `${L.ready}/${L.total} ready` : L.status}</span>
        </div>
      </div>
      <div className="foot-journal mono">
        <span className="ent ok"><span className="ts">14:02:48.117</span> <span className="sl">[mcp]</span> <span className="ar">·</span> tool call slot.list → claude-code · 9 results</span>
        <span className="sep">  </span>
        <span className="ent info"><span className="ts">14:02:41.218</span> <span className="sl">[mcp]</span> <span className="ar">·</span> client cursor reconnected to hal0-memory</span>
        <span className="sep">  </span>
        <span className="ent warn"><span className="ts">14:02:33.882</span> <span className="sl">[mcp]</span> <span className="ar">·</span> brave-search exited code 78 — config error</span>
      </div>
    </div>
  );
}

// ─── Root ─────────────────────────────────────────────────────────
function McpApp() {
  const [tweaks, setTweak] = useTweaks(MCP_TWEAK_DEFAULTS);
  const [toast, setToast] = useStateMnt(null);

  useEffectMnt(() => {
    window.__hal0Toast = (msg, kind = "info") => {
      const id = Date.now() + Math.random();
      setToast({ msg, kind, id });
      setTimeout(() => setToast(t => (t && t.id === id ? null : t)), 3500);
    };
    return () => { delete window.__hal0Toast; };
  }, []);

  return (
    <>
      <div className="app">
        <McpTopBar />
        <McpSidebar active="mcp" />
        <div className="main">
          <McpView />
        </div>
        <McpFooter />
      </div>

      {toast && (
        <div className={"hal0-toast " + (toast.kind || "info")} role="status" aria-live="polite">
          <span className="toast-dot" />
          <span className="toast-msg mono">{toast.msg}</span>
          <button className="toast-close" onClick={() => setToast(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      <TweaksPanel title="hal0 mcp servers — tweaks">
        <TweakSection title="Display">
          <TweakToggle
            label="Compact rows"
            value={tweaks.compactRows}
            onChange={v => setTweak("compactRows", v)}
          />
          <TweakToggle
            label="Show live timelines"
            value={tweaks.showLiveTimelines}
            onChange={v => setTweak("showLiveTimelines", v)}
          />
        </TweakSection>
        <TweakSection title="Demo navigation">
          <TweakButton onClick={() => window.location.href = "hal0 v2 dashboard.html"}>
            Open v0.2 dashboard
          </TweakButton>
        </TweakSection>
      </TweaksPanel>
    </>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(
  <BannerProvider>
    <McpApp />
  </BannerProvider>
);
