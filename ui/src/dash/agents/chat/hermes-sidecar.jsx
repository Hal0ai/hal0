// hal0 v0.3 PR-10 — HermesSidecar.
//
// Right rail of the chat surface (collapses to a bottom sheet < 768px in
// hermes-chat-tab.jsx's grid).
//
// Composition (master plan §4 PR-10):
//   - PersonaSwitcher: lists active personas, click → activate
//   - ModelBadge:      pulled from session.info via the store
//   - MCPStatusRow:    rolls up hal0-memory + hal0-admin from useMcpStatusPip
//   - AgentControls:   [Reload] + [Restart] (Restart confirms when streaming)
//
// Data flow:
//   - personas list  → window.__hal0UseAgentPersonas (PR-6 bridge, PR-4 API)
//   - mcp pip        → window.__hal0UseMcpStatusPip  (PR-6 bridge)
//   - model + state  → window.useHermesSession (this PR's store)
//
// Hot-swap persona: master plan §6 #3 + PR-4 — POST activate, hermes
// applies on next turn. We just call window.__hal0HermesSession.activatePersona
// and let TanStack revalidate.

function _PersonaSwitcher({ agentId }) {
  const React = window.React;
  const { useState } = React;
  const usePersonas = window.__hal0UseAgentPersonas;
  const q = usePersonas ? usePersonas(agentId) : { data: null, isLoading: false };
  const [open, setOpen] = useState(false);
  const data = (q && q.data) || { personas: [], active: null };
  const personas = Array.isArray(data.personas) ? data.personas : [];
  const activeId = data.active;
  const activeName =
    personas.find((p) => p.id === activeId)?.display_name || activeId || "—";

  const onPick = (pid) => {
    setOpen(false);
    if (pid === activeId) return;
    const session = window.__hal0HermesSession;
    if (session && session.activatePersona) session.activatePersona(agentId, pid);
  };

  if (personas.length === 0) {
    return (
      <div data-testid="hermes-sidecar-persona" className="mono" style={{fontSize: 11, color: "var(--fg-3)"}}>
        <div style={{color: "var(--fg-4)", fontSize: 9, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 2}}>persona</div>
        <div>—</div>
      </div>
    );
  }

  return (
    <div data-testid="hermes-sidecar-persona" className="mono" style={{position: "relative", fontSize: 11.5}}>
      <div style={{color: "var(--fg-4)", fontSize: 9, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 2}}>persona</div>
      <button
        type="button"
        data-testid="hermes-sidecar-persona-button"
        onClick={() => setOpen(!open)}
        className="btn ghost sm"
        style={{
          width: "100%", justifyContent: "space-between",
          display: "flex", alignItems: "center", height: 28,
          padding: "4px 10px",
        }}
      >
        <span>{activeName}</span>
        <span style={{color: "var(--fg-3)", fontSize: 10}}>{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <div data-testid="hermes-sidecar-persona-menu" style={{
          position: "absolute", top: "100%", left: 0, right: 0,
          marginTop: 4, padding: 4,
          background: "var(--bg-1)", border: "1px solid var(--line)",
          borderRadius: 6, zIndex: 30,
          boxShadow: "0 6px 24px rgba(0,0,0,0.32)",
        }}>
          {personas.map((p) => (
            <button
              key={p.id}
              type="button"
              data-testid={`hermes-sidecar-persona-option-${p.id}`}
              onClick={() => onPick(p.id)}
              style={{
                width: "100%", display: "block",
                background: p.id === activeId ? "var(--accent-soft)" : "transparent",
                border: 0, padding: "5px 8px",
                color: p.id === activeId ? "var(--accent)" : "var(--fg)",
                fontFamily: "var(--jbm)", fontSize: 11, textAlign: "left",
                borderRadius: 4, cursor: "pointer",
              }}
            >
              <div style={{fontWeight: 500}}>{p.display_name || p.id}</div>
              {p.description && (
                <div style={{color: "var(--fg-4)", fontSize: 10, marginTop: 2}}>{p.description}</div>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function _ModelBadge() {
  const sess = window.useHermesSession;
  // Select only the slice we care about so updates to transcript don't
  // re-render the badge.
  const { model, provider } = sess ? sess((s) => ({ model: s.model, provider: s.provider })) : { model: null, provider: null };
  return (
    <div data-testid="hermes-sidecar-model" className="mono" style={{fontSize: 11.5}}>
      <div style={{color: "var(--fg-4)", fontSize: 9, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 2}}>model</div>
      <div style={{color: "var(--fg)", fontWeight: 500}}>
        {model || "—"}
      </div>
      {provider && (
        <div style={{color: "var(--fg-4)", fontSize: 10, marginTop: 2}}>{provider}</div>
      )}
    </div>
  );
}

function _MCPStatusRow() {
  const usePip = window.__hal0UseMcpStatusPip;
  const q = usePip ? usePip() : { data: { state: "unknown", servers: [] } };
  const pip = (q && q.data) || { state: "unknown", servers: [] };
  const color =
    pip.state === "green" ? "var(--ok)" :
    pip.state === "yellow" ? "var(--warn)" :
    pip.state === "red" ? "var(--err)" : "var(--fg-4)";
  const label =
    pip.state === "green" ? "ok" :
    pip.state === "yellow" ? "degraded" :
    pip.state === "red" ? "down" : "—";
  return (
    <div data-testid="hermes-sidecar-mcp" className="mono" style={{fontSize: 11.5}}>
      <div style={{color: "var(--fg-4)", fontSize: 9, textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 2}}>mcp servers</div>
      <div style={{display: "flex", alignItems: "center", gap: 6, color}}>
        <span style={{
          display: "inline-block", width: 6, height: 6,
          borderRadius: "50%", background: "currentColor",
          boxShadow: "0 0 6px currentColor",
        }} />
        {label}
      </div>
      {pip.servers.length > 0 && (
        <div style={{marginTop: 4, fontSize: 10, color: "var(--fg-3)"}}>
          {pip.servers.map((s, i) => (
            <div key={i} style={{display: "flex", justifyContent: "space-between"}}>
              <span>{s.name}</span>
              <span style={{color: s.state === "running" ? "var(--ok)" : "var(--err)"}}>{s.state}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function _AgentControls({ agentId }) {
  const React = window.React;
  const { useState } = React;
  const [confirmOpen, setConfirmOpen] = useState(false);
  const sess = window.useHermesSession;
  const streaming = sess ? sess((s) => !!s.activeAssistantId) : false;

  const onRestart = () => {
    if (streaming) { setConfirmOpen(true); return; }
    const s = window.__hal0HermesSession;
    if (s && s.restartAgent) s.restartAgent(agentId);
  };
  const onConfirm = () => {
    setConfirmOpen(false);
    const s = window.__hal0HermesSession;
    if (s && s.restartAgent) s.restartAgent(agentId);
  };

  return (
    <div data-testid="hermes-sidecar-controls" style={{
      display: "flex", flexDirection: "column", gap: 6,
    }}>
      <button
        type="button"
        data-testid="hermes-sidecar-restart"
        onClick={onRestart}
        className="btn ghost sm"
        style={{justifyContent: "center"}}
        title={streaming ? "Restart while message in flight — confirm required" : "Restart agent"}
      >
        Restart agent
      </button>
      {confirmOpen && (
        <div data-testid="hermes-sidecar-restart-confirm" className="mono" style={{
          padding: 8,
          background: "var(--warn-soft)",
          border: "1px solid var(--warn-line)",
          borderRadius: 4,
          fontSize: 10, color: "var(--warn)",
        }}>
          A message is streaming. Restart will drop it.
          <div style={{marginTop: 6, display: "flex", gap: 4, justifyContent: "flex-end"}}>
            <button
              type="button"
              className="btn ghost sm"
              onClick={() => setConfirmOpen(false)}
            >Cancel</button>
            <button
              type="button"
              className="btn sm"
              data-testid="hermes-sidecar-restart-confirm-yes"
              onClick={onConfirm}
            >Restart</button>
          </div>
        </div>
      )}
    </div>
  );
}

function HermesSidecar({ agentId }) {
  return (
    <aside data-testid="hermes-sidecar" style={{
      display: "flex", flexDirection: "column", gap: 14,
      padding: "16px 14px",
      background: "var(--bg)",
      borderLeft: "1px solid var(--line)",
      minWidth: 240,
    }}>
      <div className="mono" style={{
        fontSize: 9, color: "var(--fg-4)",
        textTransform: "uppercase", letterSpacing: "0.1em",
      }}>
        hermes · sidecar
      </div>
      <_PersonaSwitcher agentId={agentId} />
      <_ModelBadge />
      <_MCPStatusRow />
      <div style={{flex: 1}} />
      <_AgentControls agentId={agentId} />
    </aside>
  );
}

Object.assign(window, { HermesSidecar });
