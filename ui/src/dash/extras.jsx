// hal0 dashboard — secondary views: Logs.
//
// Phase B1: Logs reads from real hooks. v0.3 PR-8 split the
// AgentView monolith out of this file into ui/src/dash/agents/* — see
// agent-view.jsx, hermes-chat-tab.jsx, personas-tab.jsx, skills-tab.jsx,
// memory-tab.jsx, plugins-tab.jsx. v0.4: BackendsView removed (the page
// duplicated Settings → Runtime + config.json).

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
  const historical = useLogsHistorical({ source, level: level || null, q: search || null });
  const live = useLogsStream({
    follow: !paused,
    source,
    level: level || null,
    q: search || null,
  });

  // Demo lines preserve the design's grouped-error block (request-id
  // collapsing) so the screenshot suite has something to point at even
  // when the dev backend has no journal entries yet.
  const demoLines = [
    { ts: "14:01:58.330", source: "hal0", level: "ok",   slot: "primary", msg: "slot:primary container start · profile=rocm" },
    { ts: "14:01:58.341", source: "hal0", level: "info", slot: "primary", msg: "ggml_init_cublas: found 1 ROCm device gfx1151" },
    { ts: "14:02:00.812", source: "hal0", level: "info", slot: "primary", msg: "llm_load_tensors: offloaded 49/49 layers to GPU" },
    { ts: "14:02:11.290", source: "hal0", level: "ok",   slot: "primary", msg: "slot:primary state loading → ready · 13.1s" },
    { ts: "14:02:12.117", source: "hal0", level: "info", slot: null,      msg: "omnirouter: filtered tool set = [generate_image, embed_text, transcribe_audio, route_to_chat]" },
    { ts: "14:02:15.443", source: "hal0", level: "info", slot: "primary", msg: "session ftr-104 opened persona=primary" },
    { ts: "14:02:18.117", source: "hal0", level: "ok",   slot: "utility", msg: "slot:utility container restart · model=qwopus3.5-9b-coder-mtp (persona swap)" },
    { ts: "14:02:19.290", source: "hal0", level: "ok",   slot: "utility", msg: "tool_call read_file path=src/hal0/launchers/slot_manager.py" },
    { ts: "14:02:20.812", source: "hal0", level: "warn", slot: "img",     msg: "sd-turbo · vae load · falling back to cpu", group: "req-7f3a" },
    { ts: "14:02:20.890", source: "hal0", level: "warn", slot: "img",     msg: "sd-turbo · UNet partial offload (ngl 28/32)", group: "req-7f3a" },
    { ts: "14:02:20.911", source: "hal0", level: "warn", slot: "img",     msg: "sd-turbo · sampler init: euler-a", group: "req-7f3a" },
    { ts: "14:02:20.934", source: "hal0", level: "warn", slot: "img",     msg: "sd-turbo · scheduler: 20 steps cfg 7.0", group: "req-7f3a" },
    { ts: "14:02:22.443", source: "hal0", level: "ok",   slot: "utility", msg: "/v1/chat/completions utility 38 tok/s TTFT 280ms" },
    { ts: "14:02:28.117", source: "hal0", level: "ok",   slot: "img",     msg: "slot:img container start · model=sd-turbo (tool dispatch)" },
    { ts: "14:02:32.290", source: "hal0", level: "ok",   slot: "img",     msg: "tool_call generate_image · 4.1s · 2.4 MB" },
    { ts: "14:02:36.117", source: "hal0", level: "info", slot: "img",     msg: "slot:img state serving → idle" },
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
  // Keep pending count in sync with actual buffered live lines when
  // the user has scrolled up. Count real unread lines rather than
  // simulating arrivals with a fake interval.
  React.useEffect(() => {
    if (!followTail) {
      // Count live SSE lines not yet in view as "pending".
      setPendingCount(live.ring?.length ?? 0);
    } else {
      setPendingCount(0);
    }
  }, [followTail, live.ring]);

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
            {[["merged", "merged"], ["hal0", "hal0"]].map(([k, l]) => (
              <button key={k} onClick={() => setSource(k)} style={{padding: "4px 11px", background: source === k ? "var(--accent-soft)" : "transparent", color: source === k ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: k !== "hal0" ? "1px solid var(--line)" : "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}>{l}</button>
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
          <button className="btn ghost sm" title="Export current journal buffer as .log" onClick={async () => {
            try {
              // Fetch the current journal buffer (up to 5000 lines) and
              // trigger a client-side blob download — no server-side export
              // endpoint needed.
              const resp = await fetch('/api/journal?limit=5000', { headers: { Accept: 'application/json' } });
              if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
              const data = await resp.json();
              const entries = data?.entries ?? [];
              const text = entries.length > 0
                ? entries.map(e => `${e.ts || ''} [${e.level || 'info'}] ${e.source ? '[' + e.source + '] ' : ''}${e.msg || ''}`.trim()).join('\n')
                : lines.map(e => `${e.ts || ''} [${e.level || 'info'}] ${e.msg || ''}`.trim()).join('\n');
              const blob = new Blob([text], { type: 'text/plain' });
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `hal0-journal-${new Date().toISOString().slice(0,19).replace(/[T:]/g,'-')}.log`;
              a.click();
              URL.revokeObjectURL(url);
              window.__hal0Toast && window.__hal0Toast('Journal exported', 'ok');
            } catch (err) {
              window.__hal0Toast && window.__hal0Toast(`Export failed — ${err?.message || 'see console'}`, 'err');
            }
          }}>{Icons.download}</button>
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
    <div className={"log-row log-" + (e.level || "info")}>
      <span className="log-ts">{e.ts}</span>
      <span className="log-source">{e.source}</span>
      <span className="log-level">{e.level}</span>
      <span className={"log-slot" + (e.slot ? "" : " empty")}>{e.slot || "—"}</span>
      <span className="log-msg">{msg}</span>
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
        className={"log-row log-warn log-group-row" + (open ? " open" : "")}
        onClick={() => setOpen(o => !o)}
      >
        <span className="log-ts">{head.ts}</span>
        <span className="log-source">{head.source}</span>
        <span className="log-level">{head.level}</span>
        <span className="log-slot">{head.slot || "—"}</span>
        <span className="log-msg log-group-msg">
          {open ? "▾" : "▸"} <b>{head.msg}</b>
          <span className="log-group-meta">+ {rest} more · request {group.id}</span>
        </span>
      </div>
      {open && group.items.slice(1).map((ln, i) => (
        <div key={i} className="log-row log-warn log-group-child">
          <span className="log-ts">{ln.ts}</span>
          <span className="log-source">{ln.source}</span>
          <span className="log-level">{ln.level}</span>
          <span className="log-slot">{ln.slot || "—"}</span>
          <span className="log-msg">{ln.msg}</span>
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
