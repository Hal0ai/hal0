// hal0 dashboard — secondary views: Logs.
//
// Phase B1: Logs reads from real hooks. v0.3 PR-8 split the
// AgentView monolith out of this file into ui/src/dash/agents/* — see
// agent-view.jsx, hermes-chat-tab.jsx, personas-tab.jsx, skills-tab.jsx,
// memory-tab.jsx, plugins-tab.jsx. v0.4: BackendsView removed (the page
// duplicated Settings → Lemonade-admin + config.json).

import { useLogsHistorical, useLogsStream } from '@/api/hooks/useLogs'

const { useState: useStateX } = React;

// ════════════════════════════════════════════════════════════════════
// LOGS
// ════════════════════════════════════════════════════════════════════
function LogsView() {
  const [source, setSource] = useStateX("merged");
  const [level, setLevel] = useStateX(null);
  const [slotFilter, setSlotFilter] = useStateX(null);
  const [search, setSearch] = useStateX("");
  const [followTail, setFollowTail] = useStateX(true);
  const [paused, setPaused] = useStateX(false);
  const [pendingCount, setPendingCount] = useStateX(0);
  const scrollRef = React.useRef(null);

  // Phase 3 of #322: historical fetch + SSE tail both hit /api/journal*.
  // Server-side filter params (source / level / search) round-trip into
  // the URL so the wire payload is already small; the client filter
  // pass below stays for slot-name filtering (still client-only — the
  // backend journal envelope doesn't carry slot yet) and for instant
  // search feedback while the user types.
  //
  // includeLemondWs flips on when source=lemond is selected so the page
  // can render raw lemond logs.entry frames alongside the projected
  // journal envelope, satisfying the design's "native fidelity" mode.
  const historical = useLogsHistorical({ source, level: level || null, q: search || null });
  const live = useLogsStream({
    follow: !paused,
    source,
    level: level || null,
    q: search || null,
    includeLemondWs: source === 'lemond',
  });

  // Demo lines preserve the design's grouped-error block (request-id
  // collapsing) so the screenshot suite has something to point at even
  // when the dev backend has no journal entries yet.
  const demoLines = [
    { ts: "14:01:58.330", source: "lemond", level: "ok",   slot: "primary", msg: "POST /v1/load model=qwen3.6-27b-mtp-q4_k_m backend=llamacpp:rocm" },
    { ts: "14:01:58.341", source: "lemond", level: "info", slot: "primary", msg: "ggml_init_cublas: found 1 ROCm device gfx1151" },
    { ts: "14:02:00.812", source: "lemond", level: "info", slot: "primary", msg: "llm_load_tensors: offloaded 49/49 layers to GPU" },
    { ts: "14:02:11.290", source: "hal0",   level: "ok",   slot: "primary", msg: "slot:primary state loading → ready · 13.1s" },
    { ts: "14:02:12.117", source: "hal0",   level: "info", slot: null,      msg: "omnirouter: filtered tool set = [generate_image, embed_text, transcribe_audio, route_to_chat]" },
    { ts: "14:02:15.443", source: "hal0",   level: "info", slot: "primary", msg: "session ftr-104 opened persona=primary" },
    { ts: "14:02:18.117", source: "lemond", level: "ok",   slot: "coder",   msg: "POST /v1/load model=qwen3-coder-30b backend=llamacpp:rocm (persona swap)" },
    { ts: "14:02:19.290", source: "hal0",   level: "ok",   slot: "coder",   msg: "tool_call read_file path=src/hal0/launchers/slot_manager.py" },
    { ts: "14:02:20.812", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · vae load · falling back to cpu", group: "req-7f3a" },
    { ts: "14:02:20.890", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · UNet partial offload (ngl 28/32)", group: "req-7f3a" },
    { ts: "14:02:20.911", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · sampler init: euler-a", group: "req-7f3a" },
    { ts: "14:02:20.934", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · scheduler: 20 steps cfg 7.0", group: "req-7f3a" },
    { ts: "14:02:22.443", source: "lemond", level: "ok",   slot: "coder",   msg: "/v1/chat/completions coder 38 tok/s TTFT 280ms" },
    { ts: "14:02:28.117", source: "lemond", level: "ok",   slot: "img",     msg: "POST /v1/load model=sd-turbo backend=sdcpp:rocm (tool dispatch)" },
    { ts: "14:02:32.290", source: "hal0",   level: "ok",   slot: "img",     msg: "tool_call generate_image · 4.1s · 2.4 MB" },
    { ts: "14:02:36.117", source: "hal0",   level: "info", slot: "img",     msg: "slot:img state serving → idle" },
  ];
  // historical now returns `{entries, next_since}`. Fall back to the
  // demoLines block when there's no journal data yet so the Logs page
  // still demos the design's grouped-warn collapser before any real
  // entries land — HAL0_DATA.journal is gone (#322 phase 3).
  const histEntries = historical.data?.entries ?? [];
  const sourceLines = histEntries.length > 0 ? histEntries : demoLines;
  const buf = [...sourceLines, ...(live.ring || [])]
    .sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));

  const fil = e => {
    if (source !== "merged" && e.source !== source) return false;
    if (level && e.level !== level) return false;
    if (slotFilter && e.slot !== slotFilter) return false;
    if (search && !e.msg.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  };
  const lines = buf.filter(fil);

  // Group adjacent same-group warns into a collapsible block
  const grouped = [];
  let curGroup = null;
  for (const ln of lines) {
    if (ln.group && curGroup && curGroup.id === ln.group) {
      curGroup.items.push(ln);
    } else if (ln.group) {
      curGroup = { id: ln.group, items: [ln], firstTs: ln.ts, source: ln.source, level: ln.level };
      grouped.push({ type: "group", group: curGroup });
    } else {
      curGroup = null;
      grouped.push({ type: "line", line: ln });
    }
  }

  const onScroll = (e) => {
    const el = e.target;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    if (atBottom !== followTail) setFollowTail(atBottom);
    if (atBottom) setPendingCount(0);
  };
  const jumpToLive = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      setFollowTail(true);
      setPendingCount(0);
    }
  };
  // simulate new lines arriving while user has scrolled up
  React.useEffect(() => {
    if (!followTail) {
      const t = setInterval(() => setPendingCount(c => c + 1), 1800);
      return () => clearInterval(t);
    } else {
      setPendingCount(0);
    }
  }, [followTail]);

  const allSlots = [...new Set(buf.map(e => e.slot).filter(Boolean))];

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Runtime</span>
        <h1>Logs</h1>
        <span className="vh-spacer" />
        <span className="hint mono">{lines.length} lines{paused ? " · paused" : ""}</span>
      </div>

      <div className="card" style={{overflow: "hidden", marginBottom: 12, position: "relative"}}>
        <div style={{padding: "10px 14px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 8, background: "var(--bg)", flexWrap: "wrap"}}>
          <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden", fontSize: 11}}>
            {[["merged", "merged"], ["hal0", "hal0"], ["lemond", "lemond"]].map(([k, l]) => (
              <button key={k} onClick={() => setSource(k)} style={{padding: "4px 11px", background: source === k ? "var(--accent-soft)" : "transparent", color: source === k ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: k !== "lemond" ? "1px solid var(--line)" : "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}>{l}</button>
            ))}
          </div>
          <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden", fontSize: 11, marginLeft: 8}}>
            {[["", "all"], ["ok", "ok"], ["info", "info"], ["warn", "warn"], ["error", "err"]].map(([k, l]) => (
              <button key={l} onClick={() => setLevel(k || null)} style={{padding: "4px 10px", background: (level || "") === k ? "var(--accent-soft)" : "transparent", color: (level || "") === k ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: l !== "err" ? "1px solid var(--line)" : "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}>{l}</button>
            ))}
          </div>
          <select
            className="input mono"
            value={slotFilter || ""}
            onChange={e => setSlotFilter(e.target.value || null)}
            style={{maxWidth: 140, height: 26, fontSize: 11, marginLeft: 8}}
          >
            <option value="">all slots</option>
            {allSlots.map(s => <option key={s} value={s}>slot: {s}</option>)}
          </select>
          <input
            className="input mono"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="search…"
            style={{flex: 1, minWidth: 120, maxWidth: 280, marginLeft: 8, height: 26, fontSize: 11}}
          />
          <span className="mono" style={{marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: followTail ? "var(--ok)" : "var(--fg-4)"}}>
            <span className={"dot " + (followTail ? "ready" : "idle")} />
            <span>{followTail ? "follow tail" : "paused tail"}</span>
          </span>
          <button className="btn ghost sm" onClick={() => setPaused(p => !p)}>{paused ? "Resume" : "Pause"}</button>
          <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Exporting .log — stubbed", "info")}>{Icons.download}</button>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          style={{background: "#070707", maxHeight: "calc(100vh - 280px)", overflowY: "auto", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.6, position: "relative"}}
        >
          {grouped.map((g, i) => g.type === "line"
            ? <LogLine key={i} e={g.line} search={search} />
            : <LogGroup key={i} group={g.group} search={search} />
          )}
          {paused && (
            <div style={{padding: "12px 16px", textAlign: "center", color: "var(--warn)", fontSize: 11, background: "rgba(232,185,78,0.08)", borderTop: "1px solid var(--warn-line)"}}>
              ⏸ stream paused · resume to drain buffer
            </div>
          )}
        </div>

        {!followTail && (
          <button
            onClick={jumpToLive}
            style={{
              position: "absolute",
              right: 20,
              bottom: 20,
              background: "var(--accent)",
              color: "#0a0a0a",
              border: "1px solid var(--accent)",
              borderRadius: 999,
              padding: "8px 14px",
              fontFamily: "var(--jbm)",
              fontSize: 11.5,
              fontWeight: 600,
              cursor: "pointer",
              boxShadow: "0 8px 24px -4px rgba(0,0,0,0.5)",
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            ↓ Jump to live
            {pendingCount > 0 && <span style={{background: "#0a0a0a", color: "var(--accent)", padding: "1px 6px", borderRadius: 999, fontSize: 10}}>+{pendingCount}</span>}
          </button>
        )}
      </div>
    </div>
  );
}

function LogLine({ e, search }) {
  const msg = search && e.msg.toLowerCase().includes(search.toLowerCase())
    ? highlightSearch(e.msg, search)
    : e.msg;
  return (
    <div style={{padding: "2px 16px", display: "grid", gridTemplateColumns: "100px 78px 60px 80px 1fr", gap: 12, borderLeft: e.level === "warn" ? "2px solid var(--warn)" : e.level === "error" ? "2px solid var(--err)" : "2px solid transparent"}}>
      <span style={{color: "var(--fg-5)"}}>{e.ts}</span>
      <span style={{color: e.source === "lemond" ? "var(--dev-vulkan)" : "var(--accent)"}}>{e.source}</span>
      <span style={{color: e.level === "ok" ? "var(--ok)" : e.level === "warn" ? "var(--warn)" : e.level === "error" ? "var(--err)" : "var(--fg-3)"}}>{e.level}</span>
      <span style={{color: e.slot ? "var(--fg-2)" : "var(--fg-5)"}}>{e.slot || "—"}</span>
      <span style={{color: "var(--fg-2)"}}>{msg}</span>
    </div>
  );
}

function LogGroup({ group, search }) {
  const [open, setOpen] = useStateX(false);
  const head = group.items[0];
  const rest = group.items.length - 1;
  return (
    <>
      <div
        style={{padding: "2px 16px", display: "grid", gridTemplateColumns: "100px 78px 60px 80px 1fr", gap: 12, borderLeft: "2px solid var(--warn)", cursor: "pointer", background: open ? "rgba(232,185,78,0.05)" : "transparent"}}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{color: "var(--fg-5)"}}>{head.ts}</span>
        <span style={{color: "var(--dev-vulkan)"}}>{head.source}</span>
        <span style={{color: "var(--warn)"}}>{head.level}</span>
        <span style={{color: "var(--fg-2)"}}>{head.slot || "—"}</span>
        <span style={{color: "var(--fg-2)", display: "flex", alignItems: "center", gap: 8}}>
          {open ? "▾" : "▸"} <b style={{color: "var(--fg)", fontWeight: 500}}>{head.msg}</b>
          <span style={{color: "var(--fg-4)", fontSize: 10, marginLeft: 4}}>+ {rest} more · request {group.id}</span>
        </span>
      </div>
      {open && group.items.slice(1).map((ln, i) => (
        <div key={i} style={{padding: "2px 16px 2px 32px", display: "grid", gridTemplateColumns: "84px 78px 60px 80px 1fr", gap: 12, color: "var(--fg-3)", borderLeft: "2px solid rgba(232,185,78,0.4)", background: "rgba(232,185,78,0.03)"}}>
          <span style={{color: "var(--fg-5)"}}>{ln.ts}</span>
          <span style={{color: "var(--dev-vulkan)"}}>{ln.source}</span>
          <span style={{color: "var(--warn)"}}>{ln.level}</span>
          <span>{ln.slot || "—"}</span>
          <span>{ln.msg}</span>
        </div>
      ))}
    </>
  );
}

function highlightSearch(text, q) {
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <span style={{background: "var(--accent-soft)", color: "var(--accent)", padding: "0 2px", borderRadius: 2}}>{text.slice(i, i + q.length)}</span>
      {text.slice(i + q.length)}
    </>
  );
}

Object.assign(window, { LogsView });
