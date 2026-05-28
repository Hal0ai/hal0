// hal0 v0.3 PR-10 — ThinkingIndicator.
//
// Renders a collapsible "thinking" or "reasoning" panel under an
// assistant bubble. Aggregates the thinking.delta / reasoning.delta /
// reasoning.available events the use-hermes-session store collapses
// into rows of kind 'thinking' | 'reasoning'.
//
// Closed by default; click to expand. Pre-formatted, plain text — no
// markdown — to match upstream's untyped reasoning channel.

function HermesThinkingIndicator({ row }) {
  const React = window.React;
  const { useState } = React;
  if (!row) return null;
  const [open, setOpen] = useState(false);

  const flavor = row.kind === "reasoning" ? "reasoning" : "thinking";
  const labels = {
    thinking:  "thinking",
    reasoning: "reasoning",
  };
  const tone = flavor === "reasoning" ? "var(--dev-vulkan)" : "var(--fg-3)";

  return (
    <div
      data-testid={`hermes-${flavor}-indicator`}
      data-thinking-open={open ? "1" : "0"}
      style={{
        margin: "2px 0 6px 12px",
        fontFamily: "var(--jbm)",
        fontSize: 11,
      }}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: "inline-flex", alignItems: "center", gap: 6,
          background: "transparent", border: 0, padding: 0,
          cursor: "pointer", color: tone, fontFamily: "var(--jbm)",
          fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em",
        }}
      >
        <span style={{
          display: "inline-block", width: 8,
          transform: open ? "rotate(90deg)" : "rotate(0deg)",
          transition: "transform 120ms ease",
        }}>▸</span>
        {labels[flavor]}
      </button>
      {open && (
        <pre style={{
          marginTop: 4, padding: "6px 8px",
          background: "var(--bg-2)", border: "1px solid var(--line)",
          borderRadius: 4, fontSize: 11, color: "var(--fg-2)",
          whiteSpace: "pre-wrap", wordBreak: "break-word",
          maxHeight: 220, overflow: "auto",
        }}>
          {row.text}
        </pre>
      )}
    </div>
  );
}

Object.assign(window, { HermesThinkingIndicator });
