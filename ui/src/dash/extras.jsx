// hal0 dashboard — secondary views: Logs.
//
// Phase B1: Logs reads from real hooks. v0.3 PR-8 split the
// AgentView monolith out of this file into ui/src/dash/agents/* — see
// agent-view.jsx, hermes-chat-tab.jsx, personas-tab.jsx, skills-tab.jsx,
// memory-tab.jsx, plugins-tab.jsx. v0.4: BackendsView removed (the page
// duplicated Settings → Runtime + config.json).

import { useLogsHistorical, useLogsStream, useSlotLogsStream } from '@/api/hooks/useLogs'
import { useSlots } from '@/api/hooks/useSlots'

const { useState: useStateX } = React;

// Channels the Logs page can show. Replaces the old dead "merged/hal0"
// toggle (both positions resolved to the same event stream).
//   events → structured hal0 event stream (all subsystems, coarse lifecycle)
//   slot   → a single slot's RAW journald output (detailed model-load lines)
//   merged → hal0 events + the selected slot's raw logs, interleaved by ts
const LOG_CHANNELS = [["events", "events"], ["slot", "slot"], ["merged", "merged"]];

// ════════════════════════════════════════════════════════════════════
// LOGS
// ════════════════════════════════════════════════════════════════════
function LogsView() {
  const [channel, setChannel] = useStateX("events");
  const [level, setLevel] = useStateX(null);
  const [slotFilter, setSlotFilter] = useStateX(null);
  const [search, setSearch] = useStateX("");
  const [followTail, setFollowTail] = useStateX(true);
  const [paused, setPaused] = useStateX(false);
  const [pendingCount, setPendingCount] = useStateX(0);
  const scrollRef = React.useRef(null);

  // Slot list for the dropdown — real slots from /api/slots, not scraped
  // from log rows (which is why the dropdown used to go empty once real
  // journal entries — carrying no slot — arrived).
  const slotsQuery = useSlots();
  const slotNames = (slotsQuery.data || []).map(s => s.name);

  const needsSlot = channel === "slot" || channel === "merged";
  const wantEvents = channel === "events" || channel === "merged";
  const wantSlotLogs = needsSlot && !!slotFilter;

  // hal0 event stream (structured). ?slot= narrows to one slot's lifecycle
  // events server-side; level/search round-trip too so the wire stays small.
  const historical = useLogsHistorical({
    slot: slotFilter || null,
    level: level || null,
    q: search || null,
    enabled: wantEvents,
  });
  const live = useLogsStream({
    follow: !paused && wantEvents,
    slot: slotFilter || null,
    level: level || null,
    q: search || null,
  });
  // Raw per-slot journald tail — the ONLY source of detailed model-loading
  // lines. Blind-append ring (repeats are real); carries no severity so the
  // hook infers `level`.
  const slotLogs = useSlotLogsStream(wantSlotLogs ? slotFilter : null, {
    follow: !paused && wantSlotLogs,
  });

  const histEntries = historical.data?.entries ?? [];
  const eventRows = wantEvents ? [...histEntries, ...(live.ring || [])] : [];
  const rawRows = wantSlotLogs ? (slotLogs.ring || []) : [];
  // Dedup: the historical fetch and the SSE replay return the same events, so
  // a naive concat renders every row twice. Key on the content signature
  // (ts+source+msg) — raw slot lines get a client-arrival ts so genuine
  // repeats keep distinct keys and are preserved.
  const seen = new Set();
  const buf = [...eventRows, ...rawRows]
    .filter(e => {
      const k = `${e.ts}|${e.source}|${e.msg}`;
      if (seen.has(k)) return false;
      seen.add(k);
      return true;
    })
    .sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));

  const fil = e => {
    if (level && (e.level || "info") !== level) return false;
    if (search && !(e.msg || "").toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  };
  const lines = buf.filter(fil);

  // Surface real failure/degraded states instead of masking them (the page
  // used to fall back to fake demo lines, hiding an empty/broken backend).
  const banner = historical.isError
    ? { tone: "err", msg: "Failed to load journal history — check the API." }
    : slotLogs.degraded
    ? { tone: "warn", msg: `Slot logs unavailable — ${slotLogs.degraded}` }
    : channel === "slot" && !slotFilter
    ? { tone: "info", msg: "Select a slot to view its detailed logs." }
    : (wantEvents && live.disconnected) || (wantSlotLogs && slotLogs.disconnected)
    ? { tone: "warn", msg: "Log stream disconnected — reconnecting…" }
    : null;

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
      // Count live SSE lines not yet in view as "pending" — across whichever
      // channel(s) are active (events, raw slot, or both when merged).
      setPendingCount((live.ring?.length ?? 0) + (slotLogs.ring?.length ?? 0));
    } else {
      setPendingCount(0);
    }
  }, [followTail, live.ring, slotLogs.ring]);

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
            {LOG_CHANNELS.map(([k, l], i) => (
              <button key={k} onClick={() => setChannel(k)} style={{padding: "4px 11px", background: channel === k ? "var(--accent-soft)" : "transparent", color: channel === k ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: i < LOG_CHANNELS.length - 1 ? "1px solid var(--line)" : "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}>{l}</button>
            ))}
          </div>
          <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden", fontSize: 11, marginLeft: 8}}>
            {[["", "all"], ["info", "info"], ["warn", "warn"], ["error", "err"]].map(([k, l]) => (
              <button key={l} onClick={() => setLevel(k || null)} style={{padding: "4px 10px", background: (level || "") === k ? "var(--accent-soft)" : "transparent", color: (level || "") === k ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: l !== "err" ? "1px solid var(--line)" : "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}>{l}</button>
            ))}
          </div>
          <select
            className="input mono"
            value={slotFilter || ""}
            onChange={e => {
              const name = e.target.value || null;
              setSlotFilter(name);
              // Picking a slot means "show me this slot's logs" — but the rich
              // raw journald tail (detailed model-load lines, the same stream
              // the slot drawer shows) is only fetched on the slot/merged
              // channels. On the events-only channel that data stays hidden,
              // so promote to `merged` (events + raw slot logs, interleaved).
              if (name && channel === "events") setChannel("merged");
            }}
            title={needsSlot ? "Pick a slot to tail" : "Filter events to one slot"}
            style={{maxWidth: 150, height: 26, fontSize: 11, marginLeft: 8, padding: "0 8px", lineHeight: "24px", ...(needsSlot && !slotFilter ? {borderColor: "var(--accent)"} : {})}}
          >
            <option value="">{needsSlot ? "select slot…" : "all slots"}</option>
            {slotNames.map(s => <option key={s} value={s}>slot: {s}</option>)}
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

        {banner && (
          <div style={{
            padding: "8px 14px", fontSize: 11, fontFamily: "var(--jbm)",
            borderBottom: "1px solid var(--line)",
            color: banner.tone === "err" ? "var(--err)" : banner.tone === "warn" ? "var(--warn)" : "var(--fg-3)",
            background: banner.tone === "err" ? "rgba(233,86,86,0.08)" : banner.tone === "warn" ? "rgba(232,185,78,0.08)" : "var(--bg)",
          }}>
            {banner.msg}
          </div>
        )}

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
