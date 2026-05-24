// hal0 v0.3 — MCP servers view
// The visual identity: a switchboard for the home AI box. Each server row
// carries a live "call timeline" — 60s of tool calls scrolling right→left,
// recent ones glowing amber. Page feels like a monitor, not a list.

import { useAgentMcpClients } from '@/api/hooks/useAgentMcpClients'
import {
  useMcpServers,
  useMcpClients,
  useMcpCallStream,
  useMcpInstall,
  useMcpRestart,
  useMcpUninstall,
} from '@/api/hooks/useMcp'

const { useState: useStateM, useEffect: useEffectM, useRef: useRefM, useMemo: useMemoM, useCallback: useCallbackM } = React;

// ─── Live activity bus ───────────────────────────────────────────────
// Legacy local fake-stream — only used when the SSE hook returns no
// rows AND the server list is the HAL0_DATA mock (so a stale build
// without /api/mcp/stream still ticks the LiveTimeline visibly).
// Production (issue #206) drives the timeline from useMcpCallStream.
function useLiveCallStreamLocal(servers) {
  const [now, setNow] = useStateM(Date.now());
  const callsRef = useRefM({}); // serverId → [{ts, client, tool}]
  const CLIENTS = ["claude-code", "cursor", "claude-desktop"];
  const TOOLS = {
    "hal0-admin":      ["slot.list", "slot.restart", "model.search", "journal.tail", "lemond.status"],
    "hal0-memory":     ["recall", "write", "ns.list", "graph.query", "doc.add"],
    "filesystem":      ["read_file", "write_file", "list_directory", "grep", "stat"],
    "github":          ["repo.read", "search.code", "issue.list", "pr.diff", "actions.run"],
    "postgres":        ["query", "schema.tables", "explain", "describe"],
    "obsidian-vault":  [],
    "brave-search":    [],
    "timed-reminders": [],
  };

  useEffectM(() => {
    let raf;
    let alive = true;
    const tick = () => {
      if (!alive) return;
      const t = Date.now();
      const next = { ...callsRef.current };
      servers.forEach(s => {
        if (s.state !== "running") return;
        const rpm = s.activity?.rpm || 0;
        // probability per 500ms tick: rpm / 60 / 2
        const p = rpm / 120;
        if (Math.random() < p) {
          const tools = TOOLS[s.id] || ["call"];
          const clients = s.clients?.length ? s.clients : CLIENTS;
          const arr = next[s.id] ? [...next[s.id]] : [];
          arr.push({
            ts: t,
            client: clients[Math.floor(Math.random() * clients.length)],
            tool: tools[Math.floor(Math.random() * tools.length)] || "call",
          });
          next[s.id] = arr;
        }
      });
      // garbage-collect calls older than 60s
      Object.keys(next).forEach(id => {
        next[id] = next[id].filter(c => t - c.ts < 60000);
      });
      callsRef.current = next;
      setNow(t);
      raf = setTimeout(tick, 500);
    };
    raf = setTimeout(tick, 500);
    return () => { alive = false; clearTimeout(raf); };
  }, [servers]);

  return { calls: callsRef.current, now };
}

// ─── KPI strip — aggregate state across all servers ─────────────────
function McpKpiStrip({ servers, clients, calls, now }) {
  const running = servers.filter(s => s.state === "running").length;
  const failed  = servers.filter(s => s.state === "failed").length;
  const installing = servers.filter(s => s.state === "installing").length;
  const allCalls = useMemoM(() => {
    return Object.values(calls).flat().sort((a, b) => b.ts - a.ts);
  }, [calls, now]);
  const lastCall = allCalls[0];
  const lastAgo = lastCall ? Math.floor((now - lastCall.ts) / 1000) : null;
  const callsLast5m = allCalls.length; // 60s window we track — labeled as live

  const stats = [
    { l: "running",   v: running, total: servers.length, tone: "ok" },
    { l: "clients",   v: clients.length, sub: clients.map(c => c.name).join(" · "), tone: "amber" },
    { l: "calls / 60s", v: callsLast5m, sub: "live", tone: "amber" },
    { l: "failures", v: failed, tone: failed ? "err" : "dim" },
    { l: "installing", v: installing, tone: installing ? "warn" : "dim" },
    { l: "last activity", v: lastAgo === null ? "—" : (lastAgo < 1 ? "now" : `${lastAgo}s`), sub: lastCall ? `${lastCall.client} → ${lastCall.tool}` : "no recent calls", tone: "dim", wide: true },
  ];

  return (
    <div className="mcp-kpi">
      {stats.map((s, i) => (
        <div key={i} className={"mcp-kpi-cell" + (s.wide ? " wide" : "")}>
          <div className="mcp-kpi-l mono">{s.l}</div>
          <div className={"mcp-kpi-v mono num tone-" + s.tone}>
            {s.v}{s.total !== undefined && <span className="mcp-kpi-total">/{s.total}</span>}
          </div>
          {s.sub && <div className="mcp-kpi-sub mono">{s.sub}</div>}
        </div>
      ))}
    </div>
  );
}

// ─── Connected clients ribbon ────────────────────────────────────────
function ClientsRibbon({ clients, calls, now, onTeach }) {
  return (
    <div className="mcp-clients">
      <div className="mcp-clients-h">
        <span className="mono">Connected clients<span className="ct">· {clients.length}</span></span>
        <span style={{flex: 1}} />
        <button className="mcp-link mono" onClick={onTeach}>How do I point a new client at this host?  →</button>
      </div>
      <div className="mcp-clients-row">
        {clients.map(c => {
          const recentCalls = Object.values(calls).flat().filter(call => call.client === c.id && now - call.ts < 5000).length;
          const live = recentCalls > 0;
          return (
            <div key={c.id} className={"mcp-client" + (live ? " live" : "")}>
              <div className="mcp-client-h">
                <span className={"mcp-client-dot" + (live ? " pulsing" : "")} />
                <span className="mcp-client-name mono">{c.name}</span>
                <span className="mcp-client-role mono">{c.role}</span>
              </div>
              <div className="mcp-client-meta mono">
                <span className="k">host</span><span className="v">{c.host}</span>
                <span className="dvd">·</span>
                <span className="k">since</span><span className="v">{c.since}</span>
              </div>
              <div className="mcp-client-servers">
                {c.servers.map(sid => (
                  <span key={sid} className="mcp-client-server mono">{sid}</span>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Live call timeline (the bold piece) ─────────────────────────────
// 60-second window. Each call is a vertical tick on the timeline,
// position by age (right = now, left = 60s ago). Recent calls glow amber.
function LiveTimeline({ serverId, calls, now, state }) {
  const events = (calls[serverId] || []).slice(-200);
  const isRunning = state === "running";
  const WINDOW = 60000;

  return (
    <div className={"mcp-tl" + (isRunning ? " on" : " off")}>
      <div className="mcp-tl-track">
        {/* baseline gridlines: every 15s */}
        {[0, 15, 30, 45].map(s => (
          <div key={s} className="mcp-tl-grid" style={{ right: `${(s / 60) * 100}%` }} />
        ))}
        {/* call ticks */}
        {events.map((e, i) => {
          const age = now - e.ts;
          if (age > WINDOW) return null;
          const right = (age / WINDOW) * 100;
          const opacity = 1 - (age / WINDOW) * 0.75;
          const glow = age < 4000;
          return (
            <div
              key={e.ts + "-" + i}
              className={"mcp-tl-tick" + (glow ? " glow" : "")}
              style={{ right: `${right}%`, opacity }}
              title={`${e.tool} via ${e.client}`}
            />
          );
        })}
        {/* now-marker */}
        <div className="mcp-tl-now" />
      </div>
      <div className="mcp-tl-axis mono">
        <span>−60s</span>
        <span>−45</span>
        <span>−30</span>
        <span>−15</span>
        <span className="now">now</span>
      </div>
    </div>
  );
}

// ─── Copy-to-clipboard ──────────────────────────────────────────────
function CopyField({ value, monoClass = "mono" }) {
  const [copied, setCopied] = useStateM(false);
  const onCopy = (e) => {
    e.stopPropagation();
    if (navigator.clipboard && value) {
      navigator.clipboard.writeText(value).catch(() => {});
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1400);
  };
  return (
    <div className="mcp-copy">
      <span className={monoClass + " mcp-copy-val"} title={value}>{value || "—"}</span>
      <button className="mcp-copy-btn mono" onClick={onCopy} title="Copy">
        {copied ? "copied" : (
          <svg width="13" height="13" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
            <rect x="5" y="5" width="9" height="9" rx="1.5" />
            <path d="M3 11V3a1 1 0 0 1 1-1h8" />
          </svg>
        )}
      </button>
    </div>
  );
}

// ─── Server row card ────────────────────────────────────────────────
function McpServerRow({ server, calls, now, clients, onMenuOpen, menuOpen, onCloseMenu, onConfig, onLogs, onConfirmUninstall, onToggle }) {
  const isBundled = server.bundled;
  const state = server.state;
  const callsLast60 = (calls[server.id] || []).length;

  // visible client chips (only the clients connected to this server)
  const connectedClients = clients.filter(c => c.servers.includes(server.id));

  const stateChip = (() => {
    switch (state) {
      case "running":    return <span className="mcp-state ok"><span className="dot ready" /> running<span className="dim mono">· {server.since}</span></span>;
      case "stopped":    return <span className="mcp-state dim"><span className="dot empty" /> stopped<span className="dim mono">· {server.since}</span></span>;
      case "failed":     return <span className="mcp-state err"><span className="dot error" /> failed<span className="dim mono">· {server.lastError?.code}</span></span>;
      case "installing": return <span className="mcp-state warn"><span className="dot loading" /> installing<span className="dim mono">· {server.progressLabel}</span></span>;
      default: return null;
    }
  })();

  return (
    <div className={"mcp-row state-" + state + (isBundled ? " bundled" : "")}>
      {/* Header band */}
      <div className="mcp-row-h">
        <div className="mcp-row-id">
          <span className="mcp-row-name mono">{server.name}</span>
          {isBundled && <span className="mcp-row-bundled mono">bundled</span>}
          <span className="mcp-row-ver mono">v{server.version}</span>
          <span className="mcp-row-provider mono">· {server.provider}</span>
        </div>
        <div className="mcp-row-state-cell">{stateChip}</div>
        <div className="mcp-row-actions">
          {state === "running" && (
            <>
              <button className="btn ghost sm" onClick={() => onLogs(server)} title="View logs">{Icons.logs}</button>
              <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Restarting ${server.name}…`, "info")} title="Restart">{Icons.restart}</button>
              <button className="btn ghost sm" onClick={() => onConfig(server)} title="Edit config">{Icons.edit}</button>
            </>
          )}
          {state === "stopped" && (
            <>
              <button className="btn sm" onClick={() => onToggle(server, true)}>Start</button>
              <button className="btn ghost sm" onClick={() => onConfig(server)} title="Edit config">{Icons.edit}</button>
            </>
          )}
          {state === "failed" && (
            <>
              <button className="btn sm" onClick={() => onConfig(server)}>Fix config</button>
              <button className="btn ghost sm" onClick={() => onLogs(server)}>{Icons.logs}</button>
            </>
          )}
          {state === "installing" && (
            <button className="btn ghost sm" disabled style={{opacity: 0.6}}>installing…</button>
          )}
          <div className="mcp-row-more" style={{position: "relative"}}>
            <button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); onMenuOpen(server.id); }}>{Icons.more}</button>
            {menuOpen && (
              <Menu
                anchor="right"
                onClose={onCloseMenu}
                style={{ top: "calc(100% + 4px)", right: 0 }}
                items={[
                  { label: state === "running" ? "Disable server" : "Enable server", icon: <PwrIcon />, onClick: () => onToggle(server, state !== "running") },
                  { label: "Open in browser", icon: Icons.ext, onClick: () => window.__hal0Toast && window.__hal0Toast(`Opening ${server.url}…`, "info") },
                  { label: "Restart", icon: Icons.restart, onClick: () => window.__hal0Toast && window.__hal0Toast(`Restarting ${server.name}…`, "info") },
                  { label: "Edit config", icon: Icons.edit, onClick: () => onConfig(server) },
                  { label: "View logs", icon: Icons.logs, onClick: () => onLogs(server) },
                  { divider: true },
                  ...(isBundled
                    ? [{ label: "Uninstall (bundled)", icon: <TrashIcon />, danger: true, onClick: () => window.__hal0Toast && window.__hal0Toast("Bundled servers cannot be uninstalled", "warn") }]
                    : [{ label: "Uninstall…", icon: <TrashIcon />, danger: true, onClick: () => onConfirmUninstall(server) }]
                  ),
                ]}
              />
            )}
          </div>
        </div>
      </div>

      {/* Body */}
      <div className="mcp-row-body">
        <div className="mcp-row-desc">{server.description}</div>

        {state === "installing" ? (
          <div className="mcp-installing">
            <div className="mcp-installing-bar">
              <div className="mcp-installing-bar-fill" style={{ width: `${server.progress}%` }} />
            </div>
            <div className="mcp-installing-meta mono">
              <span>{server.progress}% · {server.progressLabel}</span>
              <span style={{flex: 1}} />
              <button className="mcp-link mono">Cancel install →</button>
            </div>
          </div>
        ) : state === "failed" ? (
          <div className="mcp-failed">
            <div className="mcp-failed-h mono"><span className="mcp-failed-code">{server.lastError.code}</span>last attempt {server.lastError.ts} · attempt #{server.lastError.attempts}</div>
            <div className="mcp-failed-body mono">{server.lastError.msg}</div>
          </div>
        ) : (
          <div className="mcp-row-grid">
            {/* Connect URL */}
            <div className="mcp-cell">
              <div className="mcp-cell-l mono">connect url</div>
              <CopyField value={server.url || "(unavailable)"} />
              <div className="mcp-cell-sub mono">{server.transport} · pid {server.pid || "—"}</div>
            </div>

            {/* Capabilities */}
            <div className="mcp-cell">
              <div className="mcp-cell-l mono">exposes</div>
              <div className="mcp-cell-caps mono">
                {server.tools !== null && server.tools !== undefined ? (
                  <>
                    <span><b className="num">{server.tools}</b> tools</span>
                    {server.resources > 0 && <><span className="dim">·</span><span><b className="num">{server.resources}</b> resources</span></>}
                    {server.prompts > 0 && <><span className="dim">·</span><span><b className="num">{server.prompts}</b> prompts</span></>}
                  </>
                ) : <span className="dim">—</span>}
              </div>
              <div className="mcp-cell-sub mono">{server.transport}</div>
            </div>

            {/* Connected clients */}
            <div className="mcp-cell">
              <div className="mcp-cell-l mono">connected<span className="ct"> · {connectedClients.length}</span></div>
              <div className="mcp-clients-chips">
                {connectedClients.length === 0 ? (
                  <span className="dim mono">no clients</span>
                ) : connectedClients.map(c => {
                  const recent = (calls[server.id] || []).filter(e => e.client === c.id && now - e.ts < 5000).length > 0;
                  return (
                    <span key={c.id} className={"mcp-client-chip mono" + (recent ? " active" : "")}>
                      <span className={"mcp-client-chip-dot" + (recent ? " pulsing" : "")} />
                      {c.name}
                    </span>
                  );
                })}
              </div>
              <div className="mcp-cell-sub mono">{callsLast60} calls in last 60s</div>
            </div>
          </div>
        )}

        {/* Live timeline — the bold piece */}
        {(state === "running" || state === "stopped") && (
          <LiveTimeline serverId={server.id} calls={calls} now={now} state={state} />
        )}
      </div>
    </div>
  );
}

function PwrIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 4a5 5 0 1 0 6 0" /><line x1="8" y1="2" x2="8" y2="8" />
    </svg>
  );
}
function TrashIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 4h10M6.5 4V3a1 1 0 0 1 1-1h1a1 1 0 0 1 1 1v1M5 4l0.5 9a1 1 0 0 0 1 1h3a1 1 0 0 0 1-1L11 4" />
    </svg>
  );
}
function PlusIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round">
      <path d="M8 3v10M3 8h10" />
    </svg>
  );
}

// ─── Main view ──────────────────────────────────────────────────────
function McpView() {
  // ADR-0013 §8 — top-level mode toggle: servers (existing) | clients
  // (new per-agent view). Defaults to "servers" so existing nav stays
  // unchanged.
  const [mode, setMode] = useStateM("servers");
  // Live data via /api/mcp/* (issue #206). When the backend returns no
  // rows (404 → mock fallback in the hook, or genuinely empty), fall
  // through to the HAL0_DATA mock so the prototype demo + Playwright
  // specs keep rendering against the rich fixture set.
  const serversQ = useMcpServers();
  const clientsQ = useMcpClients();
  const liveServers = serversQ.data || [];
  const liveClients = clientsQ.data || [];
  const servers = liveServers.length > 0 ? liveServers : MCP_SERVERS;
  const clients = liveClients.length > 0 ? liveClients.map(c => ({
    ...c,
    // The prototype card reads `servers` (legacy alias) + a `since`
    // string; normalise so the existing render path keeps working.
    servers: c.servers || c.connected_to || [],
    since: typeof c.since === 'number' ? new Date(c.since * 1000).toLocaleTimeString() : (c.since || '—'),
  })) : MCP_CLIENTS;
  const [filter, setFilter] = useStateM("all");
  const [menuId, setMenuId] = useStateM(null);
  const [installOpen, setInstallOpen] = useStateM(false);
  const [configFor, setConfigFor] = useStateM(null);
  const [logsFor, setLogsFor] = useStateM(null);
  const [confirmUninstall, setConfirmUninstall] = useStateM(null);
  const [teachOpen, setTeachOpen] = useStateM(false);
  // SSE-backed call stream. Falls back to the local randomised stream
  // when no live events have arrived AND we're rendering the HAL0_DATA
  // mock list, so the demo timeline still ticks visibly.
  const sseStream = useMcpCallStream();
  const localStream = useLiveCallStreamLocal(liveServers.length === 0 ? servers : []);
  const hasLiveCalls = Object.values(sseStream.calls).some(arr => arr && arr.length > 0);
  const calls = hasLiveCalls ? sseStream.calls : (liveServers.length === 0 ? localStream.calls : sseStream.calls);
  const now = hasLiveCalls ? sseStream.now : (liveServers.length === 0 ? localStream.now : sseStream.now);
  const restartMut = useMcpRestart();
  const uninstallMut = useMcpUninstall();

  // close menus on outside click
  useEffectM(() => {
    const off = () => setMenuId(null);
    document.addEventListener("click", off);
    return () => document.removeEventListener("click", off);
  }, []);

  const filtered = useMemoM(() => {
    if (filter === "all") return servers;
    if (filter === "bundled")  return servers.filter(s => s.bundled);
    if (filter === "issues")   return servers.filter(s => s.state === "failed" || s.state === "installing");
    return servers.filter(s => s.state === filter);
  }, [filter, servers]);

  const filters = [
    { id: "all",      label: "All",        count: servers.length },
    { id: "running",  label: "Running",    count: servers.filter(s => s.state === "running").length },
    { id: "bundled",  label: "Bundled",    count: servers.filter(s => s.bundled).length },
    { id: "stopped",  label: "Stopped",    count: servers.filter(s => s.state === "stopped").length },
    { id: "issues",   label: "Issues",     count: servers.filter(s => s.state === "failed" || s.state === "installing").length },
  ];

  const toggleServer = (s, next) => {
    // Backend route stubs 501 for stop/start (ADR-0013 follow-up); the
    // mutation hook catches the 501 and shows the toast for us, so we
    // just fire the restart mutation and let the polling refresh the
    // server list when the action actually does something.
    if (next) {
      restartMut.mutate(s.id);
    } else {
      uninstallMut.mutate(s.id);
    }
    window.__hal0Toast && window.__hal0Toast(`${s.name} ${next ? "starting…" : "stopping…"}`, "info");
  };

  const noClients = clients.length === 0;

  return (
    <div className="view mcp-view">
      <div className="vh">
        <span className="vh-eye mono">Agents · v0.3</span>
        <h1>{mode === "servers" ? "MCP Servers" : "MCP Clients"}</h1>
        <span className="vh-spacer" />
        {mode === "servers"
          ? <span className="hint mono">hal0 hosts an arbitrary number of MCP servers · clients connect over <span style={{color: "var(--fg-2)"}}>{MCP_HOST_BASE}/mcp/*</span></span>
          : <span className="hint mono">per-agent allow-lists · ADR-0013 · read-only in v0.3 alpha</span>
        }
        {mode === "servers" && <button className="btn ghost" onClick={() => setTeachOpen(true)}>Connect a client</button>}
        {mode === "servers" && <button className="btn" onClick={() => setInstallOpen(true)}><PlusIcon /> Install</button>}
      </div>

      {/* ADR-0013 §8 mode switch — Servers (what we host) | Clients
          (what our bundled agents are allowed to reach out to). */}
      <div className="mcp-filterbar" style={{marginTop: 0, marginBottom: 14}}>
        <div className="mcp-tabs">
          <button
            className={"mcp-tab" + (mode === "servers" ? " on" : "")}
            onClick={() => setMode("servers")}
          >
            <span>Servers</span>
            <span className="mcp-tab-ct num">{servers.length}</span>
          </button>
          <button
            className={"mcp-tab" + (mode === "clients" ? " on" : "")}
            onClick={() => setMode("clients")}
          >
            <span>Clients</span>
            <span className="mcp-tab-ct num">per-agent</span>
          </button>
        </div>
      </div>

      {mode === "clients" ? <McpClientsView /> : null}
      {mode !== "servers" ? null : (
      <>
      {/* KPI strip */}
      <McpKpiStrip servers={servers} clients={clients} calls={calls} now={now} />

      {/* Connected clients ribbon, OR empty state if zero */}
      {noClients
        ? <NoClientsState onTeach={() => setTeachOpen(true)} />
        : <ClientsRibbon clients={clients} calls={calls} now={now} onTeach={() => setTeachOpen(true)} />
      }

      {/* Filter bar */}
      <div className="mcp-filterbar">
        <div className="mcp-tabs">
          {filters.map(f => (
            <button
              key={f.id}
              className={"mcp-tab" + (filter === f.id ? " on" : "")}
              onClick={() => setFilter(f.id)}
            >
              <span>{f.label}</span>
              <span className="mcp-tab-ct num">{f.count}</span>
            </button>
          ))}
        </div>
        <span style={{flex: 1}} />
        <div className="mcp-legend mono">
          <span className="lg"><span className="mcp-tl-tick demo glow" /> last 4s</span>
          <span className="lg"><span className="mcp-tl-tick demo" /> last 60s</span>
        </div>
      </div>

      {/* Server list */}
      <div className="mcp-list">
        {filtered.map(s => (
          <McpServerRow
            key={s.id}
            server={s}
            calls={calls}
            now={now}
            clients={clients}
            menuOpen={menuId === s.id}
            onMenuOpen={(id) => setMenuId(menuId === id ? null : id)}
            onCloseMenu={() => setMenuId(null)}
            onConfig={(srv) => setConfigFor(srv)}
            onLogs={(srv) => setLogsFor(srv)}
            onConfirmUninstall={(srv) => setConfirmUninstall(srv)}
            onToggle={toggleServer}
          />
        ))}
        {filtered.length === 0 && (
          <div className="mcp-empty mono">No servers match this filter.</div>
        )}
      </div>

      {/* Install drawer (catalog) — wired to /api/mcp/install which
          501s pending ADR-0013. The mutation hook shows a toast on
          501 so the install button doesn't look broken. */}
      <InstallDrawerWired
        open={installOpen}
        onClose={() => setInstallOpen(false)}
      />

      {/* Edit config modal */}
      <EditConfigModal
        open={!!configFor}
        server={configFor}
        onClose={() => setConfigFor(null)}
      />

      {/* Logs drawer */}
      <LogsDrawer
        open={!!logsFor}
        server={logsFor}
        onClose={() => setLogsFor(null)}
      />

      {/* Uninstall confirm — fires the mutation, which 501s pending
          ADR-0013. The hook surfaces the toast. */}
      <ConfirmDialog
        open={!!confirmUninstall}
        title={confirmUninstall ? `Uninstall ${confirmUninstall.name}?` : ""}
        message={
          <span>
            Removes the server binary, env, and supervisor entry. Connected clients will lose access immediately.
            {confirmUninstall && <><br /><br /><span className="mono" style={{color: "var(--fg-4)"}}>{(confirmUninstall.clients?.length || confirmUninstall.connected?.length || 0)} clients are currently connected.</span></>}
          </span>
        }
        onCancel={() => setConfirmUninstall(null)}
        onConfirm={() => {
          if (!confirmUninstall) return;
          uninstallMut.mutate(confirmUninstall.id);
          setConfirmUninstall(null);
        }}
        confirmLabel="Uninstall"
        destructive
        typeToConfirm={confirmUninstall?.name}
      />

      {/* How-to-connect modal */}
      <ConnectClientModal open={teachOpen} onClose={() => setTeachOpen(false)} />
      </>
      )}
    </div>
  );
}

// ─── ADR-0013 §8 per-agent Clients view (read-only alpha) ──────────────
//
// One card per installed agent (hermes, pi-coder, …). Each card lists
// the [mcp.servers.*] entries from the agent's TOML, the three-color
// chip per server, the auth.kind + token status (no token rendering),
// and the per-tool classification chips.
function McpClientsView() {
  const list = useAgentMcpClients();
  if (list.isLoading) {
    return <div className="mcp-empty mono">Loading agent allow-lists…</div>;
  }
  if (list.isError) {
    return (
      <div className="mcp-empty mono" style={{color: "var(--err, #c66)"}}>
        Could not load agent allow-lists: {String(list.error?.message || "unknown")}
      </div>
    );
  }
  const agents = list.data?.agents || [];
  if (agents.length === 0) {
    return (
      <div className="mcp-empty mono">
        No agents installed. Install Hermes via <span style={{color: "var(--fg-2)"}}>hal0 agent install hermes</span> to see this view populated.
      </div>
    );
  }
  return (
    <div style={{display: "flex", flexDirection: "column", gap: 14}}>
      {agents.map(a => <AgentMcpCard key={a.name} agent={a} />)}
    </div>
  );
}

function AgentMcpCard({ agent }) {
  return (
    <div className="card" style={{padding: 18}}>
      <div style={{display: "flex", alignItems: "baseline", gap: 14, marginBottom: 12}}>
        <span className="mono" style={{fontSize: 16, fontWeight: 500, letterSpacing: "-0.02em"}}>{agent.display || agent.name}</span>
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>{agent.name}</span>
        <span style={{marginLeft: "auto", fontSize: 11, color: "var(--fg-3)", fontFamily: "var(--jbm)"}}>
          workspace: <span className="mono" style={{color: "var(--fg-2)"}}>{agent.workspace}</span>
        </span>
      </div>
      <div style={{display: "grid", gap: 10}}>
        {agent.servers.map(s => <AgentMcpServerRow key={s.name} server={s} />)}
      </div>
    </div>
  );
}

function AgentMcpServerRow({ server }) {
  const healthColor = {
    green: "var(--ok, #6c6)",
    yellow: "var(--warn, #cb6)",
    red: "var(--err, #c66)",
    unknown: "var(--fg-4)",
  }[server.health] || "var(--fg-4)";
  return (
    <div style={{padding: "10px 12px", border: "1px solid var(--line-soft)", borderRadius: "var(--rad)", background: "var(--bg-2)"}}>
      <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 6}}>
        <span style={{display: "inline-block", width: 8, height: 8, borderRadius: "50%", background: healthColor, flexShrink: 0}} title={`health: ${server.health}`} />
        <span className="mono" style={{fontSize: 13, fontWeight: 500, color: "var(--fg)"}}>{server.name}</span>
        {server.builtin && <span className="chip">builtin</span>}
        {!server.enabled && <span className="chip" style={{color: "var(--fg-4)"}}>disabled</span>}
        {server.url && <span className="mono" style={{fontSize: 11, color: "var(--fg-4)", marginLeft: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{server.url}</span>}
        <span style={{marginLeft: "auto", display: "flex", gap: 6, alignItems: "center"}}>
          <AuthChip auth={server.auth} />
        </span>
      </div>
      <div style={{display: "flex", flexWrap: "wrap", gap: 4}}>
        {server.tools.allow.map(t => (
          <span key={"a-" + t} className="chip ok" title="allow — autonomous call">{t}</span>
        ))}
        {server.tools.gated.map(t => (
          <span key={"g-" + t} className="chip amber" title="gated — approval queue">{t}</span>
        ))}
        {server.tools.blocked.map(t => (
          <span key={"b-" + t} className="chip err" title="blocked — hard reject">{t}</span>
        ))}
        {server.tools.allow.length + server.tools.gated.length + server.tools.blocked.length === 0 && (
          <span className="mono" style={{fontSize: 11, color: "var(--fg-5)"}}>
            no tools listed — default-deny means nothing callable
          </span>
        )}
      </div>
    </div>
  );
}

function AuthChip({ auth }) {
  if (auth.kind === "none") {
    return <span className="chip" style={{fontSize: 10}}>no-auth</span>;
  }
  const tone = auth.tokenStatus === "present" ? "ok" : auth.tokenStatus === "missing" ? "err" : "";
  return (
    <span
      className={"chip " + tone}
      title={`token via env: ${auth.env || "(unset)"} — status: ${auth.tokenStatus}`}
      style={{fontSize: 10}}
    >
      bearer · {auth.tokenStatus}
    </span>
  );
}

// ─── Empty-clients teaching state ───────────────────────────────────
function NoClientsState({ onTeach }) {
  return (
    <div className="mcp-clients no-clients">
      <div className="mcp-clients-h">
        <span className="mono">Connected clients<span className="ct">· 0</span></span>
      </div>
      <div className="mcp-empty-clients">
        <div className="mcp-empty-illo mono">
          <span style={{color: "var(--fg-4)"}}>client</span>
          <span style={{color: "var(--fg-5)"}}>– – – –</span>
          <span style={{color: "var(--accent)"}}>hal0</span>
        </div>
        <div className="mcp-empty-body">
          <div className="mcp-empty-title mono">No MCP clients have connected to this host yet.</div>
          <div className="mcp-empty-sub">Point Claude Code, Claude Desktop, or Cursor at one of the running servers below. The connect URL is on each row.</div>
          <button className="btn" onClick={onTeach}>Show me how →</button>
        </div>
      </div>
    </div>
  );
}

// Thin wrapper that injects the install mutation so the existing
// InstallDrawer prop interface (onInstall(item)) stays unchanged.
// On 501 the mutation hook displays the ADR-0013 toast; on success it
// invalidates the server query so the new row appears in the list.
function InstallDrawerWired({ open, onClose }) {
  const installMut = useMcpInstall();
  return (
    <InstallDrawer
      open={open}
      onClose={onClose}
      onInstall={(item) => {
        installMut.mutate({ name: item.name, spec: item.id || item.name });
        onClose();
      }}
    />
  );
}

window.McpView = McpView;
