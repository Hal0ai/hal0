// hal0 — AgentView shell (#agent route), labelled "Agents".
//
// The Agents page is a tabbed shell. Clicking "Agents" lands on the Overview
// (the agent-card library); MCP is the other tab:
//   - Overview — the collectible agent cards (window.AgentsOverview). Hermes is
//                the live `serving` foil wired to real health + restart; the
//                rest of the library are roadmap cards behind a grey mask.
//                This is the default landing — always present, ungated.
//   - MCP    — the bundled FastMCP servers (window.McpServersPanel), moved in
//              from the dissolved Connections page, and under them the bridge
//              clients that reach hal0 over MCP (window.BridgeClientsCard).
//
// Memory is no longer a tab here: it is its own top-level area (#memory/<tab>,
// dash/memory-area/). main.jsx redirects the old #agent/memory address there.
//
// Hash routes (resolved here from window.location.hash):
//   #agent      → Overview tab (the cards landing; default)
//   #agent/mcp  → MCP tab      (also reached via #mcp)
//
// Window-globals build shim: components register on `window` and read each
// other via the same. Don't add ES module imports across dash/* — main.tsx's
// load order is the contract.

const AGENT_TABS = [
  { id: "overview", label: "Overview" },
  { id: "mcp",      label: "MCP" },
];

// Resolve the active tab from the current hash.
function _agentTab() {
  const raw = (window.location.hash || "").replace(/^#/, "");
  const parts = raw.split("?")[0].split("/");
  if (parts[0] === "mcp" || (parts[0] === "agent" && parts[1] === "mcp")) return "mcp";
  // bare #agent (or #agent/overview) → the cards Overview landing
  return "overview";
}

function AgentView() {
  const tab = _agentTab();

  // Overview is the bare #agent landing; MCP rides the #agent/mcp sub-path.
  const goTab = (id) => {
    window.location.hash = id === "overview" ? "#agent" : "#agent/" + id;
  };

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Tools</span>
        <h1>Agents</h1>
        <span className="vh-spacer" />
      </div>

      <div
        data-testid="agent-tab-nav"
        style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--line)", marginBottom: 18 }}
      >
        {AGENT_TABS.map((t) => (
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

      {tab === "overview" && window.AgentsOverview && <window.AgentsOverview />}
      {tab === "mcp" && (
        <div className="conn">
          {window.McpServersPanel ? <window.McpServersPanel /> : null}
          {window.BridgeClientsCard ? <window.BridgeClientsCard /> : null}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { AgentView });
