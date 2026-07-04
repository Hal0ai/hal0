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

// Grounded in real tools: board reads/mutations + slot/model/hardware reads.
const AGENT_SUGGEST = window.AGENT_SUGGEST || [
  "what's blocked?",
  "which slots are serving?",
  "triage everything",
  "how's the hardware doing?",
];

// ─── Agent chat slide-out (the orchestrator) ──────────────────────────
function AgentChat({ chat, byId, onClose, onOpenTask }) {
  // Chat state is owned by BoardView (which stays mounted) and passed in, so
  // the thread persists across closing/reopening the drawer. Fall back to a
  // local hook call only if no chat was provided (standalone/demo use).
  const chatHook = chat !== undefined
    ? chat
    : (window.__hal0UseBoardChat ? window.__hal0UseBoardChat() : null);

  // NO STUB DATA (CONTRACTS.md hard rule): when the hook bridge isn't loaded
  // the composer is disabled and the thread shows an unavailable notice —
  // canned fake replies here used to mask a broken bridge as a working agent.
  const displayMsgs = chatHook ? chatHook.messages : [];
  const isTyping    = chatHook ? chatHook.streaming : false;

  const [draft, setDraft] = useState("");
  const threadRef = useRef(null);

  useEffect(() => {
    if (threadRef.current) threadRef.current.scrollTop = threadRef.current.scrollHeight;
  }, [displayMsgs, isTyping]);

  const send = (text) => {
    const t = (text || draft).trim();
    if (!t || !chatHook) return;
    setDraft("");
    chatHook.send(t);
  };

  // `tool` frames are the orchestrator's audited board mutations — label them
  // as tool activity, not as operator messages.
  const roleLabel = (role) => role === "assistant" ? "agent" : role === "tool" ? "tool" : "operator";
  const roleCls   = (role) => role === "assistant" ? "agent" : role === "tool" ? "tool" : "operator";

  return (
    <React.Fragment>
      <div className="b-drawer-scrim" onClick={onClose} />
      <aside
        className="b-drawer chat"
        role="dialog"
        aria-label="platform assistant"
        data-testid="board-chat"
      >
        <div className="b-drawer-h">
          <span className="dh-title">
            <span className="kdot live" style={{ "--st": "var(--ok)" }} />
            agent · platform assistant
          </span>
          <span className="spacer" />
          <span className="dh-x" onClick={onClose}><Icon name="close" /></span>
        </div>

        <div className="chat-thread" ref={threadRef}>
          <div className="chat-intro">
            {chatHook
              ? "administers this hal0 instance · board · slots · models · settings"
              : "chat backend unavailable — hook bridge not loaded"}
          </div>

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
            disabled={!chatHook}
            placeholder={chatHook ? "Ask the orchestrator…  (Enter to send)" : "chat unavailable"}
            data-testid="board-chat-input"
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          />
          <button
            className="btn"
            data-testid="board-chat-send"
            disabled={!chatHook}
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
