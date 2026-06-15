// hal0 operator board — AgentChat (window-global JSX)
// NO ES imports — React and all deps via window globals.
// Exports: window.AgentChat
const { useState, useEffect, useRef } = React;

// Resolve BoardIcon at RENDER time (board-view.jsx registers it AFTER this
// module loads; window.Icons is chrome's glyph-object, not a component).
function Icon(props) {
  const BI = window.BoardIcon;
  return BI ? <BI {...props} /> : null;
}

const AGENT_SUGGEST = window.AGENT_SUGGEST || [
  "what's blocked?",
  "triage everything",
  "assign all ready tasks",
  "free memory for img",
];

// ─── Agent chat slide-out (the orchestrator) ──────────────────────────
function AgentChat({ byId, onClose, onOpenTask }) {
  const chatHook = window.__hal0UseBoardChat ? window.__hal0UseBoardChat() : null;

  // live path
  const messages = chatHook ? chatHook.messages : (window.AGENT_SEED || []);
  const streaming = chatHook ? chatHook.streaming : false;

  // local draft (send delegates to hook or stub)
  const [draft, setDraft] = useState("");

  // stub state only used when hook absent
  const [stubMsgs, setStubMsgs] = useState(window.AGENT_SEED || []);
  const [stubTyping, setStubTyping] = useState(false);

  const threadRef = useRef(null);

  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [messages, stubMsgs, streaming, stubTyping]);

  const stubReply = (q) => {
    const lc = q.toLowerCase();
    if (lc.includes("block")) return { body: "One task is blocked: the img slot can't claim GTT to load sdxl-turbo. It needs ~6.5 GB of unified-memory headroom. Everything else is healthy.", refs: [] };
    if (lc.includes("triage")) return { body: "Triage tasks are being processed. I'll decompose anything over complexity threshold 3.", refs: [] };
    if (lc.includes("assign") || lc.includes("ready")) return { body: "Ready tasks will be dispatched. Confirm and I'll claim them.", refs: [] };
    if (lc.includes("memory") || lc.includes("free") || lc.includes("img")) return { body: "To free GTT for img I'd nuclear-evict the previous GPU tenant. Want me to queue the evict and retry the slot?", refs: [] };
    return { body: "Tracked. I'll keep the board in sync and surface anything that needs your call.", refs: [] };
  };

  const send = (text) => {
    const t = (text || draft).trim();
    if (!t) return;
    setDraft("");
    if (chatHook) {
      chatHook.send(t);
      return;
    }
    // stub fallback
    setStubMsgs(m => [...m, { role: "user", at: "just now", body: t, refs: [] }]);
    setStubTyping(true);
    setTimeout(() => {
      const r = stubReply(t);
      setStubTyping(false);
      setStubMsgs(m => [...m, { role: "assistant", at: "just now", body: r.body, refs: r.refs }]);
    }, 900);
  };

  const displayMsgs = chatHook ? messages : stubMsgs;
  const isTyping    = chatHook ? streaming : stubTyping;

  const roleLabel = (role) => role === "assistant" ? "agent" : "operator";
  const roleCls   = (role) => role === "assistant" ? "agent" : "operator";

  return (
    <React.Fragment>
      <div className="b-drawer-scrim" onClick={onClose} />
      <aside
        className="b-drawer chat"
        role="dialog"
        aria-label="agent orchestrator"
        data-testid="board-chat"
      >
        <div className="b-drawer-h">
          <span className="dh-title">
            <span className="kdot live" style={{ "--st": "var(--ok)" }} />
            agent · orchestrator
          </span>
          <span className="spacer" />
          <span className="dh-x" onClick={onClose}><Icon name="close" /></span>
        </div>

        <div className="chat-thread" ref={threadRef}>
          <div className="chat-intro">talks to the gateway dispatcher · acts on this board</div>

          {displayMsgs.map((m, i) => (
            <div
              className={"msg " + roleCls(m.role)}
              key={i}
              data-testid="board-chat-msg"
            >
              <div className="msg-meta">
                <span className="who">{roleLabel(m.role)}</span>
                <span>{m.at}</span>
              </div>
              <div className="msg-b">
                {m.body}
                {m.refs && m.refs.length > 0 && (
                  <div className="msg-refs">
                    {m.refs.map(id => byId[id] && (
                      <span
                        className="msg-ref"
                        key={id}
                        data-testid={`board-chat-ref-${id}`}
                        onClick={() => onOpenTask(id)}
                      >
                        <span className={window.liveDot ? window.liveDot(byId[id].status) : "kdot glow"} />
                        {id}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          ))}

          {isTyping && (
            <div className="msg agent" data-testid="board-chat-msg">
              <div className="msg-meta"><span className="who">agent</span></div>
              <div className="msg-b"><span className="typing"><i /><i /><i /></span></div>
            </div>
          )}
        </div>

        <div className="chat-suggest">
          {AGENT_SUGGEST.map((s, i) => (
            <button
              className="sugg"
              key={s}
              data-testid={`board-chat-suggest-${i}`}
              onClick={() => send(s)}
            >{s}</button>
          ))}
        </div>

        <div className="b-dr-composer">
          <textarea
            value={draft}
            placeholder="Ask the orchestrator…  (Enter to send)"
            data-testid="board-chat-input"
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          />
          <button
            className="btn"
            data-testid="board-chat-send"
            onClick={() => send()}
          >
            <Icon name="send" size={13} />Send
          </button>
        </div>
      </aside>
    </React.Fragment>
  );
}

Object.assign(window, { AgentChat });
