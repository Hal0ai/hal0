// hal0 — AgentView shell.
//
// AgentView is the `#agent` route. Per design §7 (Agent → Memory fold)
// it is now a THIN POINTER: memory's canonical home is the `#memory`
// route (Overview · Graph · Tools). The single Memory tab here renders
// MemoryTab, which is a pointer card plus the ADR-0014 graph-extraction
// gate (the one live agent-level control).
//
// The web-chat surface (HermesChatTab) was abandoned in favour of the
// `hermes chat` TUI, and the Personas / Skills / Plugins tabs were
// removed (they showed fixtures rather than live data).
//
// Hash routes supported (parsed by main.jsx parseRoute):
//   #agent          → memory pointer view (default)
//   #agent/memory   → memory pointer view
//
// Window-globals build shim: components register on `window` and read
// each other via the same. Don't add ES module imports across dash/*
// — main.tsx's load order is the contract.

const { useState: useStateAV, useEffect: useEffectAV } = React;

// Single-tab nav kept so the route + deep-link shape stay stable.
const AGENT_TABS = [
  { id: "memory",   label: "Memory" },
];

function _parseAgentTab() {
  const raw = (window.location.hash || "").replace(/^#/, "");
  const path = raw.split("?")[0];
  const parts = path.split("/");
  if (parts[0] !== "agent") return "memory";
  const sub = parts[1] || "memory";
  return AGENT_TABS.find(t => t.id === sub) ? sub : "memory";
}

function AgentView() {
  const [tab, setTab] = useStateAV(_parseAgentTab());

  useEffectAV(() => {
    const onHash = () => setTab(_parseAgentTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const goTab = (id) => {
    // Preserve top-level #agent route so the App router stays on this
    // view; sub-tabs ride in the second segment.
    window.location.hash = "#agent/" + id;
  };

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Tools</span>
        <h1>Agent</h1>
        <span className="vh-spacer" />
        <span className="hint mono">Chat in terminal: <code>hermes chat</code></span>
      </div>

      <div
        data-testid="agent-tab-nav"
        style={{display: "flex", gap: 0, borderBottom: "1px solid var(--line)", marginBottom: 18}}
      >
        {AGENT_TABS.map(t => (
          <button
            key={t.id}
            data-testid={"agent-tab-" + t.id}
            onClick={() => goTab(t.id)}
            style={{
              padding: "10px 16px",
              background: "transparent",
              border: "none",
              borderBottom: tab === t.id ? "2px solid var(--accent)" : "2px solid transparent",
              color: tab === t.id ? "var(--accent)" : "var(--fg-3)",
              fontFamily: "var(--jbm)",
              fontSize: 12.5,
              cursor: "pointer",
              fontWeight: 500,
            }}
          >{t.label}</button>
        ))}
      </div>

      {tab === "memory" && window.MemoryTab && <window.MemoryTab />}
    </div>
  );
}

Object.assign(window, { AgentView });
