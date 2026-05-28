// hal0 v0.3 PR-10 — Composer.
//
// Textarea + send button. Master plan §6 #2 (user decision):
//   Enter submits, Shift+Enter inserts a newline. ChatGPT / Claude.ai
//   pattern.
//
// Auto-grows up to a cap (~12 lines) to keep the bottom of the page
// from disappearing on long drafts. Mobile (<768px) sticks to the
// viewport bottom — handled in hermes-chat-tab.jsx's grid.
//
// Connects to the WS submit channel via window.__hal0HermesSession.
// Composer is intentionally dumb — it doesn't read transcript state
// or own its own loading flag; the streaming bubble is sufficient
// feedback.

function HermesComposer({ connectionState, disabled }) {
  const React = window.React;
  const { useState, useRef, useEffect } = React;
  const [draft, setDraft] = useState("");
  const taRef = useRef(null);
  const session = window.__hal0HermesSession || {};

  // Auto-grow textarea. Reset to scrollHeight on every change; cap at
  // 12 lines (~ 12 * line-height(1.55) * 13px font = ~ 240px).
  useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const next = Math.min(ta.scrollHeight, 240);
    ta.style.height = `${next}px`;
  }, [draft]);

  const send = () => {
    const text = draft.trim();
    if (!text || disabled) return;
    const ok = session.submitPrompt && session.submitPrompt(text);
    if (ok || ok == null) setDraft("");
  };

  const onKeyDown = (e) => {
    // Enter submits, Shift+Enter newline (master plan §6 #2).
    if (e.key === "Enter" && !e.shiftKey && !e.metaKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      send();
    }
  };

  const isReady = connectionState === "open";
  const label =
    connectionState === "connecting"   ? "Connecting…" :
    connectionState === "reconnecting" ? "Reconnecting…" :
    connectionState === "closed"       ? "Disconnected" :
                                         "Message Hermes (Enter to send, Shift+Enter for newline)";

  return (
    <div
      data-testid="hermes-composer"
      data-conn-state={connectionState}
      style={{
        display: "flex", alignItems: "flex-end", gap: 8,
        padding: "10px 14px",
        background: "var(--bg)",
        borderTop: "1px solid var(--line)",
      }}
    >
      <div style={{flex: 1, position: "relative"}}>
        <textarea
          ref={taRef}
          data-testid="hermes-composer-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder={label}
          rows={1}
          disabled={disabled}
          style={{
            width: "100%", boxSizing: "border-box",
            padding: "10px 12px",
            background: "var(--bg-2)",
            border: "1px solid var(--line)",
            borderRadius: 8,
            color: "var(--fg)",
            fontFamily: "var(--jbm)",
            fontSize: 13, lineHeight: 1.55,
            resize: "none",
            minHeight: 40, maxHeight: 240, overflowY: "auto",
            outline: "none",
          }}
          onFocus={(e) => { e.target.style.borderColor = "var(--accent-line)"; }}
          onBlur={(e)  => { e.target.style.borderColor = "var(--line)"; }}
        />
      </div>
      <button
        type="button"
        data-testid="hermes-composer-send"
        onClick={send}
        disabled={!draft.trim() || !isReady || disabled}
        className="btn"
        style={{height: 40, padding: "0 16px", fontSize: 12.5}}
        title="Send (Enter)"
      >
        Send
      </button>
    </div>
  );
}

Object.assign(window, { HermesComposer });
