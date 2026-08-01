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

// Grounded in the hal0-brain profile's real responsibilities: slot lifecycle,
// model download/setup, benchmarking, hardware.
const AGENT_SUGGEST = window.AGENT_SUGGEST || [
  "Help me create a new slot",
  "Download and set up a model",
  "Benchmark the model on a slot",
  "How's the hardware doing?",
];

// ─── Minimal markdown → React (no deps, no innerHTML) ─────────────────
// The brain slot replies in markdown; render the common subset (fences,
// lists, headings, bold/italic/inline-code, links) instead of raw text.
// Anything unrecognised falls through as plain text — never worse than before.

function mdInline(text, keyBase) {
  const out = [];
  // Tokenise inline code first so `**x**` inside backticks stays literal.
  const parts = String(text).split(/(`[^`]+`)/g);
  parts.forEach((part, pi) => {
    if (!part) return;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      out.push(<code className="md-code" key={`${keyBase}-c${pi}`}>{part.slice(1, -1)}</code>);
      return;
    }
    // bold / italic / links on the remaining plain runs
    const rx = /(\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^)]+\))/g;
    part.split(rx).forEach((seg, si) => {
      if (!seg) return;
      const key = `${keyBase}-${pi}-${si}`;
      if (seg.startsWith("**") && seg.endsWith("**")) {
        out.push(<strong key={key}>{seg.slice(2, -2)}</strong>);
      } else if (seg.startsWith("*") && seg.endsWith("*") && seg.length > 2) {
        out.push(<em key={key}>{seg.slice(1, -1)}</em>);
      } else if (seg.startsWith("[")) {
        const m = seg.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
        // Model output is attacker-influenceable (tool results, task titles fed
        // back into context) — only linkify http(s); anything else stays text.
        if (m && /^https?:\/\//i.test(m[2].trim())) {
          out.push(<a href={m[2].trim()} target="_blank" rel="noreferrer" key={key}>{m[1]}</a>);
        } else {
          out.push(seg);
        }
      } else {
        out.push(seg);
      }
    });
  });
  return out;
}

function Markdown({ text }) {
  const src = String(text || "");
  const blocks = [];
  // Split out fenced code blocks first; everything between is prose.
  const segments = src.split(/```(\w*)\n?([\s\S]*?)(?:```|$)/g);
  // split() with 2 capture groups yields [prose, lang, code, prose, ...]
  for (let i = 0; i < segments.length; i += 3) {
    const prose = segments[i];
    if (prose && prose.trim()) {
      let list = null;
      const flushList = (key) => {
        if (!list) return;
        blocks.push(list.ordered
          ? <ol className="md-list" key={key}>{list.items}</ol>
          : <ul className="md-list" key={key}>{list.items}</ul>);
        list = null;
      };
      prose.split("\n").forEach((line, li) => {
        const key = `b${i}-l${li}`;
        const bullet = line.match(/^\s*[-*]\s+(.*)$/);
        const ordered = line.match(/^\s*\d+[.)]\s+(.*)$/);
        const heading = line.match(/^\s*#{1,4}\s+(.*)$/);
        if (bullet || ordered) {
          const item = <li key={key}>{mdInline((bullet || ordered)[1], key)}</li>;
          if (list && list.ordered === !!ordered) list.items.push(item);
          else { flushList(key + "-fl"); list = { ordered: !!ordered, items: [item] }; }
        } else {
          flushList(key + "-fl");
          if (heading) blocks.push(<div className="md-h" key={key}>{mdInline(heading[1], key)}</div>);
          else if (line.trim()) blocks.push(<p className="md-p" key={key}>{mdInline(line, key)}</p>);
        }
      });
      flushList(`b${i}-end`);
    }
    const code = segments[i + 2];
    if (code != null && code.trim()) {
      blocks.push(
        <pre className="md-pre" key={`b${i}-pre`} data-lang={segments[i + 1] || undefined}>
          <code>{code.replace(/\n$/, "")}</code>
        </pre>
      );
    }
  }
  return <React.Fragment>{blocks}</React.Fragment>;
}

// ─── Folded model reasoning ───────────────────────────────────────────
function Thinking({ text }) {
  if (!text) return null;
  return (
    <details className="msg-think" data-testid="board-chat-thinking">
      <summary>thinking</summary>
      <pre>{text}</pre>
    </details>
  );
}

// ─── Tool-call card (call + matched result) ───────────────────────────
// Gated calls (status=pending) park on the ApprovalQueue: the card shows the
// gate inline with Approve/Deny (same endpoints as the top-bar bell) so the
// operator never has to leave the thread to unblock the steward.
function ToolCard({ msg, onResolve }) {
  const tc = msg.tool_call || {};
  const args = tc.arguments && Object.keys(tc.arguments).length > 0
    ? JSON.stringify(tc.arguments)
    : "";
  const status = msg.status || "done";
  const statusLabel = status === "pending" ? "awaiting approval" : status;
  const dotColor =
    status === "error" || status === "denied" ? "var(--err)"
    : status === "running" ? "var(--info)"
    : status === "pending" ? "var(--warn, #E8B94E)"
    : "var(--ok)";
  let resultText = "";
  if (msg.result !== undefined) {
    try { resultText = JSON.stringify(msg.result, null, 2); }
    catch { resultText = String(msg.result); }
    if (resultText && resultText.length > 1200) resultText = resultText.slice(0, 1200) + " …";
  }
  const canResolve = status === "pending" && msg.approval_id && onResolve;
  return (
    <div className={"tool-card " + status} data-testid="board-chat-tool">
      <div className="tool-card-h">
        <span className={"kdot " + (status === "running" || status === "pending" ? "live" : "")}
          style={{ "--st": dotColor }} />
        <span className="tool-name">{tc.name || msg.body || "tool"}</span>
        {args && <span className="tool-args" title={args}>{args}</span>}
        <span className="tool-status">{statusLabel}</span>
      </div>
      {canResolve && (
        <div className="tool-approval" data-testid="board-chat-approval">
          <span className="tool-approval-note">gated call — runs only with your approval</span>
          <button className="btn" data-testid="board-chat-approve"
            onClick={() => onResolve(msg.approval_id, "approve")}>Approve</button>
          <button className="btn ghost" data-testid="board-chat-deny"
            onClick={() => onResolve(msg.approval_id, "deny")}>Deny</button>
        </div>
      )}
      {status === "approved" && (
        <div className="tool-approval-note">approved — executing, the turn continues automatically</div>
      )}
      {status === "denied" && (
        <div className="tool-approval-note">denied — the call was dropped</div>
      )}
      {resultText && (
        <details className="tool-result">
          <summary>result</summary>
          <pre>{resultText}</pre>
        </details>
      )}
    </div>
  );
}

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

  // Steward status dot. The header used to hard-code a green pulsing `live`
  // dot regardless of backend state, so during a brain-lane outage (#1418) or
  // while the slot warms, the drawer still signalled a healthy agent — the
  // only degraded state it ever surfaced was a missing hook bridge.
  //
  // There is no cheap dedicated liveness probe for the steward, so this is
  // derived from the evidence the drawer already holds: the outcome of the
  // last turn. It defaults to UNKNOWN (neutral, no pulse) rather than green —
  // "we have not talked to it yet" must not render as "healthy", which is the
  // same rule the agent-liveness work landed for `unit_active: null` (#1459).
  const lastAssistant = [...displayMsgs].reverse().find(m => m.role === "assistant");
  const stewardState = !chatHook          ? "unknown"
    : isTyping                            ? "busy"
    : lastAssistant?.error                ? "down"
    : lastAssistant                       ? "ok"
    : "unknown";
  const STEWARD_DOT = {
    ok:      { cls: "kdot live", st: "var(--ok)",   title: "last turn completed" },
    busy:    { cls: "kdot live", st: "var(--warn)", title: "responding…" },
    down:    { cls: "kdot",      st: "var(--bad)",  title: "last turn failed" },
    unknown: { cls: "kdot",      st: "var(--fg-4)", title: "no turn taken yet — status unknown" },
  }[stewardState];

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

  // `tool` frames are the steward's audited platform/board mutations — label
  // them as tool activity, not as operator messages.
  const roleLabel = (role) => role === "assistant" ? "hal0-brain" : role === "tool" ? "tool" : "operator";
  const roleCls   = (role) => role === "assistant" ? "agent" : role === "tool" ? "tool" : "operator";

  return (
    <React.Fragment>
      <div className="b-drawer-scrim" onClick={onClose} />
      <aside
        className="b-drawer chat"
        role="dialog"
        aria-label="hal0-brain platform steward"
        data-testid="board-chat"
      >
        <div className="b-drawer-h">
          <span className="dh-title">
            <span
              className={STEWARD_DOT.cls}
              style={{ "--st": STEWARD_DOT.st }}
              title={STEWARD_DOT.title}
              data-testid="board-chat-steward-dot"
              data-state={stewardState}
            />
            hal0-brain · platform steward
          </span>
          <span className="spacer" />
          {chatHook && (
            <label
              className={"chat-auto-approve" + (chatHook.autoApprove ? " on" : "")}
              title="Auto-approve gated tool calls for this session only — includes destructive tools (deletes, config writes). Resets when the page reloads."
              data-testid="board-chat-auto-approve"
            >
              <input
                type="checkbox"
                checked={chatHook.autoApprove || false}
                onChange={e => chatHook.setAutoApprove(e.target.checked)}
              />
              auto-approve
            </label>
          )}
          {chatHook && displayMsgs.length > 0 && (
            <button
              className="chat-new-session"
              title="Start a new session (clears this thread — the model only sees what's in it)"
              data-testid="board-chat-new-session"
              onClick={() => chatHook.reset()}
            >new session</button>
          )}
          <span className="dh-x" onClick={onClose}><Icon name="close" /></span>
        </div>

        <div className="chat-thread" ref={threadRef}>
          <div className="chat-intro">
            {chatHook
              ? "stewards this hal0 instance · slots · models · benchmarks · board"
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
              {m.role === "tool" && m.tool_call ? (
                <ToolCard msg={m} onResolve={chatHook ? chatHook.resolveApproval : undefined} />
              ) : (
                <div className="msg-b">
                  {m.role === "assistant" && <Thinking text={m.thinking} />}
                  {m.role === "assistant" ? <Markdown text={m.body} /> : m.body}
                  {m.error && m.retryText && chatHook && (
                    <button
                      className="btn ghost msg-retry"
                      data-testid="board-chat-retry"
                      title="Resend this message"
                      onClick={() => chatHook.send(m.retryText)}
                    >Retry</button>
                  )}
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
              )}
            </div>
          ))}

          {isTyping && (
            <div className="msg agent" data-testid="board-chat-msg">
              <div className="msg-meta"><span className="who">hal0-brain</span></div>
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
            placeholder={chatHook ? "Ask hal0-brain…  (Enter to send)" : "chat unavailable"}
            data-testid="board-chat-input"
            onChange={e => setDraft(e.target.value)}
            onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
          />
          {isTyping && (
            <button
              className="btn ghost"
              data-testid="board-chat-stop"
              title="Stop the current turn (keeps the thread)"
              onClick={() => chatHook && chatHook.stop()}
            >Stop</button>
          )}
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
