// hal0 v0.3 PR-8 — HermesChatTab (placeholder).
//
// The chat composer + transcript lands in PR-10. PR-8 ships a minimal
// placeholder so AgentView has a default tab and the nav doesn't dead-end.
//
// The link points at the activity log (Logs view filtered for hermes)
// so operators have somewhere to go in the interim.

function HermesChatTab({ noAgent } = {}) {
  if (noAgent) {
    return (
      <div className="card" style={{padding: 40, textAlign: "center", borderStyle: "dashed"}}>
        <div className="mono" style={{fontSize: 14, color: "var(--fg-3)", marginBottom: 6}}>
          No bundled agent installed.
        </div>
        <div className="mono" style={{fontSize: 11, color: "var(--fg-5)"}}>
          Run <span className="mono" style={{color: "var(--fg)"}}>hal0 agent install hermes</span> to bring the chat surface online.
        </div>
      </div>
    );
  }
  return (
    <div
      data-testid="hermes-chat-placeholder"
      className="card"
      style={{
        padding: 48,
        textAlign: "center",
        borderStyle: "dashed",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 14,
      }}
    >
      <div className="mono" style={{fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.1em"}}>
        Hermes · chat
      </div>
      <div className="mono" style={{fontSize: 16, color: "var(--fg)", letterSpacing: "-0.01em"}}>
        Chat surface lands in PR-10.
      </div>
      <p className="mono" style={{fontSize: 12, color: "var(--fg-3)", maxWidth: 460, lineHeight: 1.55, margin: 0}}>
        The composer, transcript, persona dropdown, and inline approval
        cards arrive with the next PR. Hermes is still running — you can
        see its tool calls land in the activity log below.
      </p>
      <a
        href="#logs"
        className="btn ghost sm"
        style={{display: "inline-flex", alignItems: "center", gap: 6}}
      >
        {window.Icons && window.Icons.logs} View activity log
      </a>
    </div>
  );
}

Object.assign(window, { HermesChatTab });
