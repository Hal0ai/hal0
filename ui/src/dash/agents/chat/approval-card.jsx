// hal0 v0.3 PR-10 — ApprovalCard.
//
// Renders an inline card for an approval / clarify / sudo / secret
// request emitted by hermes. The four event flavours share an
// approval-like shape (block on a user response) so we collapse them
// into a single React component that dispatches to the right respond
// RPC via window.__hal0HermesSession.
//
// Master plan §6 #4: inline card is the PRIMARY surface. The sidebar
// pip + toast are notification-only — the actual user action happens
// here.
//
// Once resolved (decision sent), the card collapses to a thin
// confirmation strip so the transcript stays scannable.

function HermesApprovalCard({ row }) {
  const React = window.React;
  const { useState, useEffect } = React;
  if (!row) return null;
  const flavor = row.kind2 || "approval";
  const session = window.__hal0HermesSession || {};
  const [pendingDecision, setPendingDecision] = useState(null);
  const [draft, setDraft] = useState(""); // for clarify/sudo/secret text

  const payload = row.payload || {};
  const requestId = row.requestId;
  const title =
    flavor === "clarify" ? "Hermes needs clarification" :
    flavor === "sudo"    ? "Hermes needs sudo"           :
    flavor === "secret"  ? "Hermes needs a secret"       :
                           "Approval required";
  const tone =
    flavor === "sudo" || flavor === "secret"
      ? "var(--err)"
      : "var(--warn)";

  useEffect(() => {
    // On mount, scroll the card into view if a "Jump to it" toast fires
    // for this requestId. The use-hermes-session WS layer dispatches
    // `hal0:hermes:approval` CustomEvents with the requestId.
    const onJump = (ev) => {
      if (!ev.detail || ev.detail.requestId !== requestId) return;
      const el = document.querySelector(`[data-approval-rid="${requestId}"]`);
      if (el && typeof el.scrollIntoView === "function") {
        el.scrollIntoView({behavior: "smooth", block: "center"});
      }
    };
    window.addEventListener("hal0:hermes:approval", onJump);
    return () => window.removeEventListener("hal0:hermes:approval", onJump);
  }, [requestId]);

  if (row.resolved) {
    return (
      <div data-testid="hermes-approval-card" data-resolved="1" className="mono" style={{
        margin: "4px 0", padding: "6px 10px", fontSize: 11,
        color: "var(--fg-4)",
        background: "var(--bg-2)", border: "1px solid var(--line)",
        borderRadius: 6,
      }}>
        ✓ {title} — {pendingDecision || "responded"}
      </div>
    );
  }

  const onApprove = () => {
    setPendingDecision("approved");
    if (flavor === "approval") session.respondApproval(requestId, "approve");
    else if (flavor === "clarify") session.respondClarify(requestId, draft);
    else if (flavor === "sudo") session.respondSudo(requestId, draft);
    else if (flavor === "secret") session.respondSecret(requestId, draft);
  };
  const onReject = () => {
    setPendingDecision("rejected");
    if (flavor === "approval") session.respondApproval(requestId, "reject");
    else session.respondClarify && session.respondClarify(requestId, "");
  };

  const needsInput = flavor === "clarify" || flavor === "sudo" || flavor === "secret";

  return (
    <div
      data-testid="hermes-approval-card"
      data-approval-rid={requestId}
      data-approval-kind={flavor}
      style={{
        margin: "6px 0", padding: "10px 12px",
        background: flavor === "approval" ? "var(--warn-soft)" : "var(--err-soft)",
        border: `1px solid ${flavor === "approval" ? "var(--warn-line)" : "var(--err-line)"}`,
        borderRadius: 8,
        fontFamily: "var(--jbm)",
      }}
    >
      <div style={{
        display: "flex", alignItems: "center", gap: 8,
        fontSize: 11.5, color: tone, marginBottom: 8, fontWeight: 500,
      }}>
        <span style={{fontSize: 14}}>⚠</span>
        <span>{title}</span>
      </div>

      {payload.tool && (
        <div className="mono" style={{
          fontSize: 11, color: "var(--fg-2)", marginBottom: 6,
        }}>
          <span style={{color: "var(--fg-4)"}}>tool </span>
          <b style={{color: "var(--fg)"}}>{payload.tool}</b>
        </div>
      )}
      {payload.question && (
        <div style={{
          fontSize: 12, color: "var(--fg)", marginBottom: 8, lineHeight: 1.5,
        }}>
          {payload.question}
        </div>
      )}
      {payload.prompt && (
        <div style={{
          fontSize: 12, color: "var(--fg)", marginBottom: 8, lineHeight: 1.5,
        }}>
          {payload.prompt}
        </div>
      )}
      {payload.args && (
        <pre style={{
          margin: "0 0 8px", padding: "6px 8px",
          background: "var(--bg-2)", border: "1px solid var(--line)",
          borderRadius: 4, fontSize: 11, color: "var(--fg-2)",
          whiteSpace: "pre-wrap", wordBreak: "break-word",
          maxHeight: 200, overflow: "auto",
        }}>
          {typeof payload.args === "string" ? payload.args : JSON.stringify(payload.args, null, 2)}
        </pre>
      )}

      {needsInput && (
        <input
          type={flavor === "sudo" || flavor === "secret" ? "password" : "text"}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={
            flavor === "clarify" ? "Your answer…" :
            flavor === "sudo"    ? "sudo password" :
                                   "secret value"
          }
          data-testid="hermes-approval-input"
          className="mono"
          style={{
            width: "100%", padding: "6px 8px", fontSize: 12,
            background: "var(--bg-1)", border: "1px solid var(--line)",
            color: "var(--fg)", borderRadius: 4, marginBottom: 8,
            fontFamily: "var(--jbm)",
          }}
        />
      )}

      {/* Choices come through for clarify.request as `choices: string[]` */}
      {Array.isArray(payload.choices) && payload.choices.length > 0 && (
        <div style={{display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 8}}>
          {payload.choices.map((c, i) => (
            <button
              key={i}
              type="button"
              className="btn ghost sm"
              data-testid={`hermes-approval-choice-${i}`}
              onClick={() => {
                setPendingDecision(c);
                session.respondClarify && session.respondClarify(requestId, c);
              }}
            >{c}</button>
          ))}
        </div>
      )}

      <div style={{display: "flex", gap: 6, justifyContent: "flex-end"}}>
        <button
          type="button"
          className="btn ghost sm"
          data-testid="hermes-approval-reject"
          onClick={onReject}
        >
          {flavor === "approval" ? "Reject" : "Cancel"}
        </button>
        <button
          type="button"
          className="btn sm"
          data-testid="hermes-approval-approve"
          onClick={onApprove}
          disabled={needsInput && !draft.trim()}
        >
          {flavor === "approval" ? "Approve" :
           flavor === "clarify"  ? "Send"    :
                                   "Submit"}
        </button>
      </div>
    </div>
  );
}

Object.assign(window, { HermesApprovalCard });
