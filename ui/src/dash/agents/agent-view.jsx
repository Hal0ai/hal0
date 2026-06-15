// hal0 — AgentView shell (#agent route), labelled "Agents".
//
// v0.5 nav: the Agents page is a tabbed shell. Clicking "Agents" lands on the
// Overview (the agent-card library); Memory + MCP are additional tabs:
//   - Overview — the collectible agent cards (window.AgentsOverview). Hermes is
//                the live `serving` foil wired to real health + restart; the
//                rest of the library are roadmap cards behind a grey mask.
//                This is the default landing — always present, ungated.
//   - Memory — the full Hindsight memory page (window.MemoryView), moved in
//              from the standalone #memory route. Gated on the memory
//              subsystem (HAL0_MEMORY_ENABLED) via the window bridge.
//   - MCP    — the bundled FastMCP servers (window.McpServersPanel), moved in
//              from the dissolved Connections page.
//
// Hash routes (resolved here from window.location.hash):
//   #agent                  → Overview tab (the cards landing; default)
//   #agent/memory[/section] → Memory tab  (also reached via #memory[/section])
//   #agent/mcp              → MCP tab      (also reached via #mcp)
//
// MemoryView keeps its own Overview·Graph·Tools sub-tabs, which navigate via
// #memory/<section>; main.jsx rewrites those to #agent/memory/<section> so
// they resolve back here with the section preserved.
//
// Window-globals build shim: components register on `window` and read each
// other via the same. Don't add ES module imports across dash/* — main.tsx's
// load order is the contract.

const AGENT_TAB_DEFS = [
  { id: "overview", label: "Overview", needsMemory: false },
  { id: "memory",   label: "Memory",   needsMemory: true },
  { id: "mcp",      label: "MCP",       needsMemory: false },
];

function _agentTabs(memoryEnabled) {
  return AGENT_TAB_DEFS.filter((t) => !t.needsMemory || memoryEnabled);
}

// Resolve the active tab + (for Memory) its sub-section from the current hash.
function _agentRoute(memoryEnabled) {
  const raw = (window.location.hash || "").replace(/^#/, "");
  const path = raw.split("?")[0];
  const parts = path.split("/");
  const head = parts[0];

  if (head === "mcp" || (head === "agent" && parts[1] === "mcp")) {
    return { tab: "mcp", section: null };
  }
  if (head === "memory" || (head === "agent" && parts[1] === "memory")) {
    if (!memoryEnabled) return { tab: "overview", section: null };
    const section = head === "memory" ? parts[1] || null : parts[2] || null;
    return { tab: "memory", section };
  }
  // bare #agent (or #agent/overview) → the cards Overview landing
  return { tab: "overview", section: null };
}

function AgentView() {
  // Memory gate — read through the window bridge like main.jsx does, so this
  // strict no-ES-imports module stays within the dash/*.jsx contract.
  const useMemEnabled = (typeof window !== "undefined" && window.__hal0UseMemoryEnabled) || null;
  const memoryEnabled = useMemEnabled ? useMemEnabled() : false;

  const tabs = _agentTabs(memoryEnabled);
  const { tab, section } = _agentRoute(memoryEnabled);

  // Memory tab is canonically reached via #memory (so MemoryView's internal
  // #memory/<section> nav round-trips); Overview is the bare #agent landing;
  // MCP rides the #agent/mcp sub-path.
  const goTab = (id) => {
    window.location.hash =
      id === "memory" ? "#memory" : id === "overview" ? "#agent" : "#agent/" + id;
  };

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Tools</span>
        <h1>Agents</h1>
        <span className="vh-spacer" />
        <span className="hint mono">Chat in terminal: <code>hermes chat</code></span>
      </div>

      <div
        data-testid="agent-tab-nav"
        style={{ display: "flex", gap: 0, borderBottom: "1px solid var(--line)", marginBottom: 18 }}
      >
        {tabs.map((t) => (
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
      {tab === "memory" && window.MemoryView && <window.MemoryView param={section} />}
      {tab === "mcp" && (
        <div className="conn">
          {window.McpServersPanel ? <window.McpServersPanel /> : null}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { AgentView });
