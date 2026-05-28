// hal0 v0.3 PR-10 — MessageBubble.
//
// Renders one transcript row of kind 'user' | 'assistant' | 'system' |
// 'error'. Assistant bubbles use hal0-native HermesMarkdown for code
// blocks + inline formatting and show a streaming caret while the
// message is still in flight.
//
// Styling: hal0 tokens only. User bubbles are right-aligned with the
// amber accent border; assistant bubbles are left-aligned with the
// default panel surface. Mobile (<768px) bubbles span ~95% width.

function HermesMessageBubble({ row }) {
  if (!row) return null;
  const kind = row.kind;

  if (kind === "user") {
    return (
      <div data-testid="hermes-msg-user" style={{
        display: "flex", justifyContent: "flex-end", padding: "4px 0",
      }}>
        <div className="mono" style={{
          maxWidth: "min(75ch, 90%)",
          background: "var(--accent-soft)",
          border: "1px solid var(--accent-line)",
          color: "var(--fg)",
          padding: "10px 14px",
          borderRadius: 10, borderBottomRightRadius: 2,
          fontSize: 13, lineHeight: 1.55,
          whiteSpace: "pre-wrap", wordBreak: "break-word",
        }}>
          {row.text}
        </div>
      </div>
    );
  }

  if (kind === "assistant") {
    const Md = window.HermesMarkdown;
    return (
      <div data-testid="hermes-msg-assistant" style={{
        display: "flex", justifyContent: "flex-start", padding: "4px 0",
      }}>
        <div style={{
          maxWidth: "min(75ch, 90%)",
          background: "var(--bg-2)",
          border: "1px solid var(--line)",
          color: "var(--fg)",
          padding: "10px 14px",
          borderRadius: 10, borderBottomLeftRadius: 2,
        }}>
          {Md
            ? <Md content={row.text || ""} streaming={!!row.streaming} />
            : <pre style={{margin: 0, fontFamily: "var(--jbm)", fontSize: 13, color: "var(--fg)"}}>{row.text}</pre>
          }
          {row.usage && (
            <div className="mono" style={{
              marginTop: 8, paddingTop: 6, borderTop: "1px solid var(--line-soft)",
              fontSize: 10, color: "var(--fg-4)",
              display: "flex", gap: 10,
            }}>
              {row.usage.input_tokens != null && <span>in {row.usage.input_tokens}</span>}
              {row.usage.output_tokens != null && <span>out {row.usage.output_tokens}</span>}
              {row.usage.cost_usd != null && <span>${row.usage.cost_usd.toFixed(4)}</span>}
            </div>
          )}
          {row.warning && (
            <div className="mono" style={{
              marginTop: 6, fontSize: 10, color: "var(--warn)",
            }}>{row.warning}</div>
          )}
        </div>
      </div>
    );
  }

  if (kind === "error") {
    return (
      <div data-testid="hermes-msg-error" className="mono" style={{
        margin: "6px 0", padding: "8px 12px",
        background: "var(--err-soft)", border: "1px solid var(--err-line)",
        color: "var(--err)", borderRadius: 6, fontSize: 12,
      }}>
        ⚠ {row.text}
      </div>
    );
  }

  if (kind === "status") {
    return (
      <div data-testid="hermes-msg-status" className="mono" style={{
        margin: "2px 0", textAlign: "center",
        fontSize: 10, color: "var(--fg-4)",
        fontStyle: row.ephemeral ? "italic" : "normal",
      }}>
        {row.text}
      </div>
    );
  }

  return null;
}

Object.assign(window, { HermesMessageBubble });
