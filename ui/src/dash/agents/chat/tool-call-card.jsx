// hal0 v0.3 PR-10 — ToolCallCard.
//
// Renders an in-transcript card for a tool.start/tool.complete pair (with
// any tool.progress in between). Shape-only port of upstream
// `~/src/hermes-agent/web/src/components/ToolCall.tsx` — same chevron
// header, same auto-expand-on-error, same elapsed timer — but built
// against hal0 dashboard.css tokens (no Tailwind v4, no
// @nous-research/ui).

function _fmtElapsed(ms) {
  if (!Number.isFinite(ms) || ms < 0) return null;
  if (ms < 1000) return `${ms}ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`;
  const m = Math.floor(s / 60);
  return `${m}m${Math.floor(s % 60)}s`;
}

const TOOL_TICK_MS = 500;

function HermesToolCallCard({ row }) {
  const React = window.React;
  const { useState, useEffect } = React;
  if (!row) return null;

  // userOverride is null → follow default (error open, others closed).
  const [userOverride, setUserOverride] = useState(null);
  const open = userOverride ?? (row.status === "error");

  // Tick now while running so elapsed updates live.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (row.status !== "running") return undefined;
    const id = window.setInterval(() => setNow(Date.now()), TOOL_TICK_MS);
    return () => window.clearInterval(id);
  }, [row.status]);

  const hasTimestamps = row.startedAt > 0;
  const elapsedMs = hasTimestamps
    ? (row.completedAt ?? now) - row.startedAt
    : null;
  const elapsedLabel = elapsedMs != null ? _fmtElapsed(elapsedMs) : null;

  const hasBody = !!(row.context || row.preview || row.summary || row.error || row.inline_diff);

  const bulletColor =
    row.status === "running" ? "var(--accent)" :
    row.status === "error"   ? "var(--err)"    :
                               "var(--ok)";
  const borderColor =
    row.status === "running" ? "var(--accent-line)" :
    row.status === "error"   ? "var(--err-line)"    :
                               "var(--line)";
  const bgColor =
    row.status === "running" ? "var(--accent-soft)" :
    row.status === "error"   ? "var(--err-soft)"    :
                               "var(--bg-2)";

  return (
    <div
      data-testid="hermes-tool-card"
      data-tool-status={row.status}
      data-tool-name={row.name}
      style={{
        margin: "6px 0",
        border: `1px solid ${borderColor}`,
        background: bgColor,
        borderRadius: 6,
        overflow: "hidden",
        fontFamily: "var(--jbm)",
      }}
    >
      <button
        type="button"
        disabled={!hasBody}
        onClick={() => hasBody && setUserOverride(!open)}
        aria-expanded={open}
        data-testid="hermes-tool-card-header"
        style={{
          display: "flex", alignItems: "center", gap: 8,
          width: "100%", padding: "6px 10px",
          background: "transparent", border: 0,
          textAlign: "left", cursor: hasBody ? "pointer" : "default",
          color: "var(--fg)",
          fontFamily: "var(--jbm)",
          fontSize: 11.5,
        }}
      >
        {hasBody && (
          <span style={{
            color: "var(--fg-3)", width: 10, display: "inline-block",
            transition: "transform 120ms ease",
            transform: open ? "rotate(90deg)" : "rotate(0deg)",
          }}>▸</span>
        )}
        <span style={{color: bulletColor, fontSize: 12}}>●</span>
        <span style={{fontWeight: 500}}>{row.name}</span>
        {row.context && !open && (
          <span style={{color: "var(--fg-4)", marginLeft: 4, fontWeight: 400}}>
            {row.context.length > 60 ? `${row.context.slice(0, 60)}…` : row.context}
          </span>
        )}
        <span style={{flex: 1}} />
        {elapsedLabel && (
          <span className="mono" style={{
            color: row.status === "error" ? "var(--err)" : "var(--fg-4)",
            fontSize: 10,
          }}>{elapsedLabel}</span>
        )}
        {row.status === "running" && (
          <span data-testid="hermes-tool-spinner" style={{
            color: "var(--accent)", fontSize: 10, marginLeft: 6,
          }}>…</span>
        )}
      </button>

      {open && hasBody && (
        <div data-testid="hermes-tool-card-body" style={{
          padding: "6px 10px 10px", borderTop: "1px solid var(--line-soft)",
          fontSize: 11.5, color: "var(--fg-2)",
        }}>
          {row.context && (
            <div style={{marginBottom: 6}}>
              <div className="mono" style={{
                fontSize: 9, color: "var(--fg-4)",
                textTransform: "uppercase", letterSpacing: "0.08em",
                marginBottom: 2,
              }}>args</div>
              <pre style={{margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word"}}>
                {row.context}
              </pre>
            </div>
          )}
          {row.preview && row.status === "running" && (
            <div style={{marginBottom: 6}}>
              <div className="mono" style={{
                fontSize: 9, color: "var(--fg-4)",
                textTransform: "uppercase", letterSpacing: "0.08em",
                marginBottom: 2,
              }}>preview</div>
              <pre style={{margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word", color: "var(--fg-2)"}}>
                {row.preview}
              </pre>
            </div>
          )}
          {row.summary && (
            <div style={{marginBottom: 6}}>
              <div className="mono" style={{
                fontSize: 9, color: "var(--fg-4)",
                textTransform: "uppercase", letterSpacing: "0.08em",
                marginBottom: 2,
              }}>result</div>
              <pre style={{margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word"}}>
                {row.summary}
              </pre>
            </div>
          )}
          {row.inline_diff && (
            <div style={{marginBottom: 6}}>
              <div className="mono" style={{
                fontSize: 9, color: "var(--fg-4)",
                textTransform: "uppercase", letterSpacing: "0.08em",
                marginBottom: 2,
              }}>diff</div>
              <pre style={{
                margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word",
                background: "var(--bg-3)", padding: 6, borderRadius: 4,
                fontSize: 11, color: "var(--fg)",
              }}>{row.inline_diff}</pre>
            </div>
          )}
          {row.error && (
            <div className="mono" style={{
              padding: "6px 8px",
              background: "var(--err-soft)", border: "1px solid var(--err-line)",
              color: "var(--err)", borderRadius: 4, fontSize: 11,
            }}>
              {row.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

Object.assign(window, { HermesToolCallCard });
