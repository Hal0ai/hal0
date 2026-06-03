// hal0 — AgentView shell.
//
// AgentView is the `#agent` route. v0.4 reduced it to the Memory
// capability only. The web-chat surface (HermesChatTab) was abandoned in
// favour of the `hal0 chat` TUI, and the Personas / Skills / Plugins tabs
// were removed (they showed fixtures rather than live data). The single-
// tab nav is kept so the route + deep-link shape stay stable.
//
// Tab inventory:
//   - MemoryTab      (Cognee stats + "Peer memory" subsection folded
//                     in from the old Peers tab)
//
// Hash routes supported (parsed by main.jsx parseRoute):
//   #agent              → memory tab (default)
//   #agent/memory       → memory tab
//   #agent/memory?subsection=peer → memory tab scrolled to Peer memory
//   #peers (legacy)     → redirected to #agent/memory?subsection=peer
//
// Window-globals build shim: components register on `window` and read
// each other via the same. Don't add ES module imports across dash/*
// — main.tsx's load order is the contract.

const { useState: useStateAV, useEffect: useEffectAV } = React;

// v0.4: the Agent view is reduced to the Memory capability only. Web
// chat (HermesChatTab) plus the Personas / Skills / Plugins tabs were
// removed — web chat is abandoned in favour of the `hal0 chat` TUI, and
// the other tabs surfaced fixtures rather than live data. The tab nav is
// kept (single tab) so the route + deep-link shape stay stable.
const AGENT_TABS = [
  { id: "memory",   label: "Memory" },
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
  if (parts[0] !== "agent") return { tab: "memory", subsection: null };
  const sub = parts[1] || "memory";
  const tab = AGENT_TABS.find(t => t.id === sub) ? sub : "memory";
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
        <span className="hint mono">Chat in terminal: <code>hal0 chat</code></span>
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

      {tab === "memory"   && window.MemoryTab     && <window.MemoryTab subsection={subsection} onResetNs={() => setResetOpen(true)} />}

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
