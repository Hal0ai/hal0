// hal0 v0.3 PR-10 — Transcript.
//
// Virtualized-ish list. ui/package.json doesn't carry react-window or
// react-virtuoso, so we ship a hand-rolled windowed renderer that's
// good enough for a conversation transcript:
//   - Auto-scroll to bottom when streaming + the user hasn't scrolled
//     away (sticky-bottom heuristic).
//   - Render every row (chat transcripts top out at a few hundred rows;
//     real perf risk is rAF reflows on streaming, NOT row count). If a
//     real perf problem shows up we drop in react-virtuoso later.
//
// Rows dispatch to MessageBubble / ToolCallCard / ApprovalCard /
// ThinkingIndicator via window-globals lookup.

function HermesTranscript({ rows, status }) {
  const React = window.React;
  const { useRef, useEffect, useState, useLayoutEffect } = React;
  const ref = useRef(null);
  const [stick, setStick] = useState(true);

  // Sticky-bottom: user scrolling up unsticks; scrolling back to within
  // 80px of the bottom re-sticks.
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const onScroll = () => {
      const dist = el.scrollHeight - el.scrollTop - el.clientHeight;
      setStick(dist < 80);
    };
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  // Auto-scroll on new content when sticky.
  useLayoutEffect(() => {
    if (!stick) return;
    const el = ref.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [rows, stick]);

  const Bubble = window.HermesMessageBubble;
  const ToolCard = window.HermesToolCallCard;
  const Approval = window.HermesApprovalCard;
  const Thinking = window.HermesThinkingIndicator;

  return (
    <div
      ref={ref}
      data-testid="hermes-transcript"
      data-row-count={rows.length}
      data-conn-state={status}
      style={{
        flex: 1, overflowY: "auto", overflowX: "hidden",
        padding: "16px 18px", display: "flex", flexDirection: "column",
        background: "var(--bg-1)",
      }}
    >
      {rows.length === 0 && status !== "open" && (
        <div data-testid="hermes-transcript-empty" className="mono" style={{
          margin: "auto", padding: "24px",
          color: "var(--fg-4)", fontSize: 12, textAlign: "center",
        }}>
          {status === "connecting" ? "Connecting to Hermes…" :
           status === "reconnecting" ? "Reconnecting…" :
           "Waiting for Hermes."}
        </div>
      )}
      {rows.length === 0 && status === "open" && (
        <div data-testid="hermes-transcript-empty" className="mono" style={{
          margin: "auto", padding: "24px",
          color: "var(--fg-4)", fontSize: 12, textAlign: "center", maxWidth: 320, lineHeight: 1.6,
        }}>
          Hermes is online. Send the first message to get a welcome
          with available tools + models.
        </div>
      )}
      {rows.map((row) => {
        switch (row.kind) {
          case "user":
          case "assistant":
          case "error":
          case "status":
            return Bubble ? <Bubble key={row.id} row={row} /> : null;
          case "tool":
            return ToolCard ? <ToolCard key={row.id} row={row} /> : null;
          case "approval":
            return Approval ? <Approval key={row.id} row={row} /> : null;
          case "thinking":
          case "reasoning":
            return Thinking ? <Thinking key={row.id} row={row} /> : null;
          default:
            return null;
        }
      })}
    </div>
  );
}

Object.assign(window, { HermesTranscript });
