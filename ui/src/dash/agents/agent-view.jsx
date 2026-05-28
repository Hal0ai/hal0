// hal0 v0.3 PR-8 — AgentView shell.
//
// AgentView is the `#agent` route. PR-8 split the original 974-LOC
// monolith (extras.jsx) into one file per tab; this shell is the tab
// nav + tab-content switch.
//
// Tab inventory (master plan §4 PR-8):
//   - HermesChatTab  (default, placeholder; PR-10 fills the composer)
//   - PersonasTab    (reads /api/agents/{id}/personas — PR-4 live)
//   - SkillsTab      (static skill catalog for v0.3)
//   - MemoryTab      (Cognee stats + "Peer memory" subsection folded
//                     in from the old Peers tab)
//   - PluginsTab     (wraps PluginTabHost from PR-7)
//
// Dropped vs v0.2.1:
//   - Inbox tab — approvals UX moved to the sidebar pip (PR-6) and
//     future inline approval cards in HermesChat (PR-10).
//   - Peers standalone tab — folded into MemoryTab as the "Peer memory"
//     subsection (the live MCP search Peers used is preserved).
//   - Overview tab — content moved to SidebarAgentBlock (PR-6); the
//     main pane now lands on HermesChatTab by default.
//
// Hash routes supported (parsed by main.jsx parseRoute):
//   #agent              → chat tab (default)
//   #agent/chat         → chat tab
//   #agent/personas     → personas tab
//   #agent/skills       → skills tab
//   #agent/memory       → memory tab
//   #agent/memory?subsection=peer → memory tab scrolled to Peer memory
//   #agent/plugins      → plugins tab
//   #peers (legacy)     → redirected to #agent/memory?subsection=peer
//
// Window-globals build shim: components register on `window` and read
// each other via the same. Don't add ES module imports across dash/*
// — main.tsx's load order is the contract.

const { useState: useStateAV, useEffect: useEffectAV } = React;

const AGENT_TABS = [
  { id: "chat",     label: "Chat" },
  { id: "personas", label: "Personas" },
  { id: "skills",   label: "Skills" },
  { id: "memory",   label: "Memory" },
  { id: "plugins",  label: "Plugins" },
];

function _parseAgentSubroute() {
  const raw = (window.location.hash || "").replace(/^#/, "");
  // Support legacy #peers → memory?subsection=peer.
  if (raw === "peers" || raw.startsWith("peers/") || raw.startsWith("peers?")) {
    window.location.hash = "#agent/memory?subsection=peer";
    return { tab: "memory", subsection: "peer" };
  }
  const [path, qs] = raw.split("?");
  const parts = path.split("/");
  if (parts[0] !== "agent") return { tab: "chat", subsection: null };
  const sub = parts[1] || "chat";
  const tab = AGENT_TABS.find(t => t.id === sub) ? sub : "chat";
  let subsection = null;
  if (qs) {
    for (const kv of qs.split("&")) {
      const [k, v = ""] = kv.split("=");
      if (k === "subsection") subsection = decodeURIComponent(v);
    }
  }
  return { tab, subsection };
}

function AgentView() {
  const initial = _parseAgentSubroute();
  const [tab, setTab] = useStateAV(initial.tab);
  const [subsection, setSubsection] = useStateAV(initial.subsection);
  const [editPersona, setEditPersona] = useStateAV(null);
  const [resetOpen, setResetOpen] = useStateAV(false);
  const noAgent = window.__hal0Banners && window.__hal0Banners.get && window.__hal0Banners.get()["no-agent"];

  useEffectAV(() => {
    const onHash = () => {
      const { tab: t, subsection: s } = _parseAgentSubroute();
      setTab(t);
      setSubsection(s);
    };
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
        <span className="hint mono">v0.3 · chat composer lands in PR-10</span>
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

      {tab === "chat"     && window.HermesChatTab && <window.HermesChatTab noAgent={noAgent} />}
      {tab === "personas" && window.PersonasTab   && <window.PersonasTab onEdit={(p) => setEditPersona(p)} />}
      {tab === "skills"   && window.SkillsTab     && <window.SkillsTab />}
      {tab === "memory"   && window.MemoryTab     && <window.MemoryTab subsection={subsection} onResetNs={() => setResetOpen(true)} />}
      {tab === "plugins"  && window.PluginsTab    && <window.PluginsTab agentId="hermes" />}

      <PersonaEditModal
        open={!!editPersona}
        persona={editPersona}
        onClose={() => setEditPersona(null)}
      />
      <ConfirmDialog
        open={resetOpen}
        onCancel={() => setResetOpen(false)}
        onConfirm={() => { setResetOpen(false); window.__hal0Toast && window.__hal0Toast("Cognee namespace 'shared' reset — 2,847 records deleted", "warn"); }}
        title="Reset memory namespace 'shared'?"
        message={<span>This permanently deletes <span className="mono" style={{color: "var(--fg)"}}>2,847</span> Cognee records across SQLite + LanceDB + Kuzu. Cannot be undone.</span>}
        confirmLabel="Reset namespace"
        destructive
        typeToConfirm="shared"
      />
    </div>
  );
}

Object.assign(window, { AgentView });
