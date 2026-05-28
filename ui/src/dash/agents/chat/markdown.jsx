// hal0 v0.3 PR-10 — hal0-native markdown renderer for chat bubbles.
//
// Shape-only port of upstream `~/src/hermes-agent/web/src/components/Markdown.tsx`
// (NOT vendored): same block parser (code fence / heading / hr / list /
// paragraph) and same inline rules (bold, italic, code, links). Targeted
// at typical LLM output, NOT a CommonMark conformance suite.
//
// Styling uses hal0 dashboard.css tokens (--fg, --bg-2, --accent, --line).
// No Tailwind v4. Code blocks get a copy button and a language chip when
// the fence carries a lang tag.
//
// Streaming caret (`streaming` prop): inline pulse on the last block so
// the cursor hugs the final character instead of jumping to a new line.

function _parseBlocks(text) {
  const lines = String(text || "").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^```(\w*)/);
    if (fence) {
      const lang = fence[1] || "";
      const buf = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        buf.push(lines[i]); i++;
      }
      i++; // skip closing ```
      blocks.push({ type: "code", lang, content: buf.join("\n") });
      continue;
    }
    const heading = line.match(/^(#{1,4})\s+(.+)/);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, content: heading[2] });
      i++; continue;
    }
    if (/^[-*_]{3,}\s*$/.test(line)) {
      blocks.push({ type: "hr" }); i++; continue;
    }
    // List
    const li = line.match(/^\s*([-*]|\d+[.)])\s+(.+)/);
    if (li) {
      const ordered = /\d/.test(li[1]);
      const items = [];
      while (i < lines.length) {
        const m = lines[i].match(/^\s*([-*]|\d+[.)])\s+(.+)/);
        if (!m) break;
        items.push(m[2]);
        i++;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }
    // Paragraph — accumulate until blank line.
    if (line.trim() === "") { i++; continue; }
    const para = [line];
    i++;
    while (i < lines.length && lines[i].trim() !== "" && !lines[i].match(/^```/) && !lines[i].match(/^#{1,4}\s/)) {
      para.push(lines[i]); i++;
    }
    blocks.push({ type: "paragraph", content: para.join("\n") });
  }
  return blocks;
}

// ── Inline parser ────────────────────────────────────────────────
// Returns a flat array of React nodes from a string, handling **bold**,
// *italic*, `code`, [link](url).
function _renderInline(text) {
  if (text == null) return null;
  const out = [];
  let cursor = 0;
  const RE = /(\*\*([^*]+)\*\*)|(`([^`]+)`)|(\*([^*]+)\*)|(\[([^\]]+)\]\(([^)]+)\))/g;
  let m;
  let key = 0;
  while ((m = RE.exec(text)) !== null) {
    if (m.index > cursor) out.push(text.slice(cursor, m.index));
    if (m[2] !== undefined) {
      out.push(<strong key={key++} style={{color: "var(--fg)"}}>{m[2]}</strong>);
    } else if (m[4] !== undefined) {
      out.push(
        <code key={key++} style={{
          background: "var(--bg-2)", padding: "0.5px 5px",
          borderRadius: 3, fontFamily: "var(--jbm)", fontSize: "0.92em",
          border: "1px solid var(--line)", color: "var(--accent)",
        }}>{m[4]}</code>,
      );
    } else if (m[6] !== undefined) {
      out.push(<em key={key++} style={{color: "var(--fg-2)"}}>{m[6]}</em>);
    } else if (m[8] !== undefined) {
      out.push(
        <a key={key++} href={m[9]} target="_blank" rel="noopener noreferrer"
           style={{color: "var(--accent)", textDecoration: "underline"}}>
          {m[8]}
        </a>,
      );
    }
    cursor = RE.lastIndex;
  }
  if (cursor < text.length) out.push(text.slice(cursor));
  return out;
}

function _StreamingCaret() {
  return (
    <span
      aria-hidden
      style={{
        display: "inline-block", width: "0.5em", height: "1em",
        marginLeft: 2, verticalAlign: "-0.15em",
        background: "var(--fg-3)", animation: "hermesCaret 1s steps(2) infinite",
      }}
    />
  );
}

function _CodeBlock({ lang, content }) {
  const onCopy = () => {
    try {
      navigator.clipboard.writeText(content);
      if (window.__hal0Toast) window.__hal0Toast("Copied", "ok");
    } catch (_e) {
      if (window.__hal0Toast) window.__hal0Toast("Copy unavailable", "warn");
    }
  };
  return (
    <div style={{
      background: "var(--bg-2)", border: "1px solid var(--line)",
      borderRadius: 6, margin: "8px 0", overflow: "hidden",
      fontFamily: "var(--jbm)",
    }}>
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "4px 10px",
        borderBottom: "1px solid var(--line)",
        background: "var(--bg-3)",
      }}>
        <span className="mono" style={{
          fontSize: 10, color: "var(--fg-3)", textTransform: "uppercase",
          letterSpacing: "0.08em",
        }}>{lang || "code"}</span>
        <button
          type="button"
          onClick={onCopy}
          className="btn ghost sm"
          style={{height: 22, padding: "2px 8px", fontSize: 10}}
        >Copy</button>
      </div>
      <pre style={{
        margin: 0, padding: "10px 12px", fontSize: 12.5,
        color: "var(--fg)", whiteSpace: "pre-wrap", wordBreak: "break-word",
        overflowX: "auto",
      }}>{content}</pre>
    </div>
  );
}

function _Block({ block, caret }) {
  switch (block.type) {
    case "code":
      return (
        <div>
          <_CodeBlock lang={block.lang} content={block.content} />
          {caret}
        </div>
      );
    case "heading": {
      const Tag = `h${Math.min(4, Math.max(1, block.level))}`;
      const sizes = { 1: 18, 2: 16, 3: 14, 4: 13 };
      return (
        <Tag style={{
          margin: "10px 0 4px", fontWeight: 600,
          fontSize: sizes[block.level] || 13, color: "var(--fg)",
          letterSpacing: "-0.01em",
        }}>
          {_renderInline(block.content)}
          {caret}
        </Tag>
      );
    }
    case "hr":
      return <hr style={{border: 0, borderTop: "1px solid var(--line)", margin: "10px 0"}} />;
    case "list": {
      const ListTag = block.ordered ? "ol" : "ul";
      return (
        <ListTag style={{margin: "4px 0", paddingLeft: 22, color: "var(--fg)"}}>
          {block.items.map((it, i) => (
            <li key={i} style={{margin: "2px 0", lineHeight: 1.55}}>
              {_renderInline(it)}
              {caret && i === block.items.length - 1 ? caret : null}
            </li>
          ))}
        </ListTag>
      );
    }
    case "paragraph":
    default:
      return (
        <p style={{margin: "4px 0", lineHeight: 1.55, color: "var(--fg)"}}>
          {_renderInline(block.content)}
          {caret}
        </p>
      );
  }
}

function Markdown({ content, streaming }) {
  const React = window.React;
  const blocks = React.useMemo(() => _parseBlocks(content || ""), [content]);
  const caret = streaming ? <_StreamingCaret /> : null;
  return (
    <div className="mono" style={{fontFamily: "var(--geist), system-ui", fontSize: 13, color: "var(--fg)"}}>
      {blocks.map((b, i) => (
        <_Block
          key={i}
          block={b}
          caret={caret && i === blocks.length - 1 ? caret : null}
        />
      ))}
      {blocks.length === 0 && caret}
    </div>
  );
}

// Inject keyframes once for the streaming caret.
if (typeof document !== "undefined" && !document.getElementById("hal0-hermes-md-css")) {
  const s = document.createElement("style");
  s.id = "hal0-hermes-md-css";
  s.textContent = `@keyframes hermesCaret { 0%, 50% { opacity: 1; } 50.01%, 100% { opacity: 0; } }`;
  document.head.appendChild(s);
}

Object.assign(window, { HermesMarkdown: Markdown });
