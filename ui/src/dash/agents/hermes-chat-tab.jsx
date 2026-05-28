// hal0 v0.3 PR-10 — HermesChatTab.
//
// REPLACES the PR-8 placeholder. Default tab inside AgentView (#agent).
// Composes the chat surface as a CSS-grid:
//
//   ┌───────────────────────────┬──────────────┐
//   │  Transcript               │  HermesSidecar│
//   │  (virtualized list)       │  (persona,    │
//   │                           │   model,      │
//   │                           │   mcp, ops)   │
//   ├───────────────────────────┤              │
//   │  Composer (Enter submits) │              │
//   └───────────────────────────┴──────────────┘
//
// Mobile (<768px): sidecar collapses into a bottom sheet (toggleable
// via a small tab handle). Composer stays sticky at the viewport
// bottom (the natural flow inside the grid handles this).
//
// State: window.useHermesSession (hook) + window.__hal0HermesSession
// (connection api) installed by use-hermes-session.js.
//
// noAgent: render the install CTA matching PR-8's placeholder shape so
// existing agent-view-v3 specs that gate on hermes-chat-placeholder
// continue to pass. We keep the placeholder marker on the noAgent
// branch ONLY.

function HermesChatTab({ noAgent, agentId } = {}) {
  const React = window.React;
  const { useEffect, useState } = React;
  const agent = agentId || "hermes";
  const sess = window.useHermesSession;
  const session = window.__hal0HermesSession;

  // Read only the slices the shell cares about so streaming-delta
  // store updates don't re-render the shell.
  const shellSlice = sess
    ? sess((s) => ({
        transcript: s.transcript,
        connectionState: s.connectionState,
      }))
    : { transcript: [], connectionState: "idle" };

  // Mobile-sheet toggle
  const [sheetOpen, setSheetOpen] = useState(false);

  useEffect(() => {
    if (noAgent || !session || !session.connect) return undefined;
    session.connect(agent);
    return () => {
      // We don't auto-disconnect on unmount because PR-8's tab nav uses
      // conditional render and a user clicking back into chat would
      // re-open the WS. Holding the socket open across tab switches
      // matches the upstream UX too (App.tsx:716 persistent ChatPage).
    };
  }, [agent, noAgent, session]);

  if (noAgent) {
    return (
      <div
        data-testid="hermes-chat-placeholder"
        className="card"
        style={{
          padding: 48, textAlign: "center", borderStyle: "dashed",
          display: "flex", flexDirection: "column", alignItems: "center", gap: 14,
        }}
      >
        <div className="mono" style={{fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.1em"}}>
          Hermes · chat
        </div>
        <div className="mono" style={{fontSize: 16, color: "var(--fg)", letterSpacing: "-0.01em"}}>
          No bundled agent installed.
        </div>
        <p className="mono" style={{fontSize: 12, color: "var(--fg-3)", maxWidth: 460, lineHeight: 1.55, margin: 0}}>
          Run <span className="mono" style={{color: "var(--fg)"}}>hal0 agent install hermes</span> to bring the chat surface online.
        </p>
      </div>
    );
  }

  const Transcript = window.HermesTranscript;
  const Composer   = window.HermesComposer;
  const Sidecar    = window.HermesSidecar;

  return (
    <div
      data-testid="hermes-chat-surface"
      data-conn-state={shellSlice.connectionState}
      className="hermes-chat-grid"
    >
      <div className="hermes-chat-pane">
        {Transcript && <Transcript rows={shellSlice.transcript} status={shellSlice.connectionState} />}
        {Composer && (
          <Composer
            connectionState={shellSlice.connectionState}
            disabled={false}
          />
        )}
      </div>
      <div
        className={"hermes-chat-sidecar" + (sheetOpen ? " open" : "")}
        data-testid="hermes-chat-sidecar-wrap"
      >
        {Sidecar && <Sidecar agentId={agent} />}
      </div>
      {/* Mobile sheet toggle */}
      <button
        type="button"
        data-testid="hermes-chat-sheet-toggle"
        onClick={() => setSheetOpen(!sheetOpen)}
        className="hermes-chat-sheet-toggle"
        aria-label="Toggle agent sidecar"
      >
        {sheetOpen ? "Close" : "Agent"}
      </button>
    </div>
  );
}

// Scoped CSS for the grid + mobile bottom sheet. Co-located with the
// component so the layout lives next to the markup.
const _hermesChatTabCss = `
.hermes-chat-grid {
  position: relative;
  display: grid;
  grid-template-columns: minmax(0, 1fr) 280px;
  gap: 0;
  height: calc(100vh - var(--topbar-h, 52px) - var(--footer-h, 52px) - 120px);
  min-height: 480px;
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
  background: var(--bg-1);
}
.hermes-chat-pane {
  display: flex;
  flex-direction: column;
  min-width: 0;
  background: var(--bg-1);
}
.hermes-chat-sidecar {
  display: flex;
  flex-direction: column;
  overflow-y: auto;
  background: var(--bg);
}
.hermes-chat-sheet-toggle {
  display: none;
  position: absolute;
  bottom: 76px;
  right: 12px;
  z-index: 20;
  background: var(--accent-soft);
  color: var(--accent);
  border: 1px solid var(--accent-line);
  border-radius: 9999px;
  padding: 6px 14px;
  font-family: var(--jbm);
  font-size: 11px;
  cursor: pointer;
  box-shadow: 0 4px 14px rgba(0,0,0,0.35);
}
@media (max-width: 768px) {
  .hermes-chat-grid {
    grid-template-columns: minmax(0, 1fr);
  }
  .hermes-chat-sidecar {
    position: absolute;
    bottom: 0; left: 0; right: 0;
    max-height: 70vh;
    border-top: 1px solid var(--line);
    background: var(--bg);
    transform: translateY(100%);
    transition: transform 180ms ease;
    z-index: 15;
  }
  .hermes-chat-sidecar.open {
    transform: translateY(0);
  }
  .hermes-chat-sheet-toggle {
    display: inline-block;
  }
}
`;

if (typeof document !== "undefined" && !document.getElementById("hal0-hermes-chat-tab-css")) {
  const s = document.createElement("style");
  s.id = "hal0-hermes-chat-tab-css";
  s.textContent = _hermesChatTabCss;
  document.head.appendChild(s);
}

Object.assign(window, { HermesChatTab });
