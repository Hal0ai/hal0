// hal0 v0.3 — MCP modals & drawers
// InstallDrawer (catalog), EditConfigModal, LogsDrawer, ConnectClientModal

import { useMcpCatalog, useMcpServerLogs, useMcpConfigPatch, useMcpResolve } from '@/api/hooks/useMcp';

const { useState: useStateMM, useMemo: useMemoMM, useEffect: useEffectMM } = React;

// ─── Install drawer — catalog + URL escape hatch ────────────────────
function InstallDrawer({ open, onClose, onInstall }) {
  const [q, setQ] = useStateMM("");
  const [cat, setCat] = useStateMM("all");
  const [tab, setTab] = useStateMM("catalog"); // catalog | url

  // Live catalog via /api/mcp/catalog (issue #206). Falls through to
  // the HAL0_DATA MCP_CATALOG mock when the hook returns no items so
  // the prototype demo + tests render against the rich fixture set.
  const catalogQ = useMcpCatalog();
  const liveItems = catalogQ.data?.items || [];
  const items = liveItems.length > 0 ? liveItems : MCP_CATALOG;

  const filtered = useMemoMM(() => {
    return items.filter(it => {
      if (cat !== "all" && it.category !== cat) return false;
      if (q && !(`${it.name} ${it.description} ${it.author}`.toLowerCase().includes(q.toLowerCase()))) return false;
      return true;
    });
  }, [q, cat, items]);

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={720}
      eyebrow="MCP · install"
      title="Install an MCP server"
      foot={
        <>
          <span style={{color: "var(--fg-4)"}}>{items.length} servers in the catalog · curated by hal0 · community-contributed</span>
          <button className="btn ghost sm" onClick={onClose}>Done</button>
        </>
      }
    >
      <div className="mcp-install-tabs">
        <button className={"mcp-install-tab" + (tab === "catalog" ? " on" : "")} onClick={() => setTab("catalog")}>
          Catalog
        </button>
        <button className={"mcp-install-tab" + (tab === "url" ? " on" : "")} onClick={() => setTab("url")}>
          From URL / manifest
        </button>
      </div>

      {tab === "catalog" ? (
        <>
          <div className="mcp-install-search">
            <input
              className="input mono"
              placeholder="Search servers, authors, descriptions…"
              value={q}
              onChange={e => setQ(e.target.value)}
              autoFocus
            />
          </div>
          <div className="mcp-install-cats">
            {MCP_CATEGORIES.map(c => (
              <button
                key={c.id}
                className={"mcp-install-cat" + (cat === c.id ? " on" : "")}
                onClick={() => setCat(c.id)}
              >{c.label}</button>
            ))}
          </div>
          <div className="mcp-install-list">
            {filtered.map(item => (
              <div key={item.id} className="mcp-install-item">
                <div className="mcp-install-item-h">
                  <span className="mcp-install-name mono">{item.name}</span>
                  {item.verified && <span className="mcp-install-verified mono" title="Officially maintained">✓ verified</span>}
                  <span className="mcp-install-author mono">by {item.author}</span>
                  <span style={{flex: 1}} />
                  <span className="mcp-install-stars mono">{item.stars.toLocaleString()} ★</span>
                </div>
                <div className="mcp-install-desc">{item.description}</div>
                <div className="mcp-install-foot">
                  <span className="mcp-install-cat-pill mono">{item.category}</span>
                  <span className="mcp-install-tools mono">{item.tools} tools</span>
                  <span style={{flex: 1}} />
                  <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Opening ${item.name} README`, "info")}>README</button>
                  <button className="btn sm" onClick={() => onInstall(item)}>Install</button>
                </div>
              </div>
            ))}
            {filtered.length === 0 && (
              <div className="mcp-empty mono" style={{padding: 32}}>
                No catalog entries match. Try a different search, or paste a manifest URL.
              </div>
            )}
          </div>
        </>
      ) : (
        <InstallFromUrl onInstall={onInstall} />
      )}
    </Drawer>
  );
}

function InstallFromUrl({ onInstall }) {
  const [url, setUrl] = useStateMM("");
  const [pasted, setPasted] = useStateMM(false);
  // Debounce the resolve query — typing a URL char-by-char would
  // otherwise hammer /api/mcp/resolve. 350 ms feels live but spares the
  // backend the unfinished-paste storm.
  const [debouncedUrl, setDebouncedUrl] = useStateMM("");
  useEffectMM(() => {
    const id = setTimeout(() => setDebouncedUrl(url.trim()), 350);
    return () => clearTimeout(id);
  }, [url]);
  const resolveQ = useMcpResolve(pasted ? debouncedUrl : "");
  const resolved = resolveQ.data;

  const examples = [
    { label: "OCI image",   v: "oci://ghcr.io/example/mcp-something:latest" },
    { label: "npx package", v: "npm:@some-org/mcp-things" },
    { label: "uvx package", v: "uvx:mcp-things" },
    { label: "git repo",    v: "git+https://github.com/example/mcp-things" },
    { label: "manifest",    v: "https://example.com/mcp.json" },
  ];

  const transportLine = resolved
    ? `${resolved.transport} · via ${resolved.source_kind}`
    : "—";
  const toolsLine = resolved
    ? `${resolved.tools} tool${resolved.tools === 1 ? "" : "s"}, ` +
      `${resolved.resources} resource${resolved.resources === 1 ? "" : "s"}, ` +
      `${resolved.prompts} prompt${resolved.prompts === 1 ? "" : "s"}.`
    : "";
  const envLine = resolved
    ? (resolved.env_required && resolved.env_required.length > 0
        ? `Requires env: ${resolved.env_required.join(", ")}.`
        : "No env vars required.")
    : "";

  return (
    <div className="mcp-install-url">
      <div className="mcp-install-url-h mono">URL · manifest · package spec</div>
      <input
        className="input mono"
        placeholder="oci://, git+https://, npm:, uvx:, or a manifest URL"
        value={url}
        onChange={e => { setUrl(e.target.value); setPasted(true); }}
      />
      <div className="mcp-install-url-examples mono">
        <span className="dim">Examples:</span>
        {examples.map((ex, i) => (
          <button key={i} className="mcp-install-url-ex" onClick={() => { setUrl(ex.v); setPasted(true); }}>
            <span className="dim">{ex.label}</span>
            <span>{ex.v}</span>
          </button>
        ))}
      </div>

      {pasted && url && (
        <div className="mcp-install-url-preview" data-testid="mcp-install-url-preview">
          <div className="mono" style={{fontSize: 11, color: "var(--fg-4)", marginBottom: 6, textTransform: "uppercase", letterSpacing: "0.08em"}}>resolved manifest</div>
          <div className="mcp-install-url-card">
            {resolveQ.isLoading || resolveQ.isFetching ? (
              <div className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>Resolving manifest…</div>
            ) : resolveQ.isError ? (
              <div className="mono" style={{fontSize: 12, color: "var(--err, #c66)"}}>
                Could not resolve: {String(resolveQ.error?.message || "unknown error")}
              </div>
            ) : resolved ? (
              <>
                <div style={{display: "flex", alignItems: "baseline", gap: 8, marginBottom: 4}}>
                  <span className="mono" data-testid="mcp-install-resolved-name" style={{fontSize: 14, color: "var(--fg)", fontWeight: 500}}>{resolved.name}</span>
                  <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>{transportLine}</span>
                </div>
                {resolved.description && (
                  <div style={{fontSize: 12, color: "var(--fg-2)", marginBottom: 6}} data-testid="mcp-install-resolved-desc">{resolved.description}</div>
                )}
                <div style={{fontSize: 12, color: "var(--fg-3)", marginBottom: 10}} data-testid="mcp-install-resolved-tools">{toolsLine} {envLine}</div>
                <div className="mono" style={{fontSize: 10.5, color: "var(--fg-4)", padding: 8, background: "var(--bg)", borderRadius: "var(--rad-sm)", border: "1px solid var(--line)"}}>
                  hal0 will add <span style={{color: "var(--fg-2)"}}>{resolved.id}</span> to its installed servers. The supervisor follow-up will spawn it; until then it lists as stopped.
                </div>
              </>
            ) : (
              <div className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>Paste a URL to see the resolved manifest.</div>
            )}
          </div>
          <div style={{display: "flex", gap: 8, marginTop: 12, justifyContent: "flex-end"}}>
            <button className="btn ghost sm" onClick={() => { setUrl(""); setPasted(false); }}>Cancel</button>
            <button
              className="btn sm"
              disabled={!resolved || resolveQ.isLoading || resolveQ.isFetching}
              onClick={() => resolved && onInstall({ manifest: resolved })}
            >Install</button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Edit config modal ──────────────────────────────────────────────
function EditConfigModal({ open, server, onClose }) {
  const [env, setEnv] = useStateMM({});
  // Config-write hook — backend currently 501s pending ADR-0013; the
  // mutation hook surfaces the toast for us so the Save button looks
  // alive instead of silently failing.
  const configMut = useMcpConfigPatch();
  useEffectMM(() => { if (server) setEnv({ ...(server.env || {}) }); }, [server]);
  if (!server) return null;

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={`MCP · ${server.name}`}
      title="Edit server config"
      width={620}
      foot={
        <>
          <span style={{color: "var(--fg-4)"}}>Changes apply on next server restart.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" onClick={() => {
              configMut.mutate({ id: server.id, body: { env } });
              onClose();
            }}>Save</button>
          </span>
        </>
      }
    >
      <div className="mcp-cfg-grid">
        <div className="mcp-cfg-row">
          <div className="mcp-cfg-l mono">connect url</div>
          <div className="mono mcp-cfg-v">{server.url || "—"}</div>
        </div>
        <div className="mcp-cfg-row">
          <div className="mcp-cfg-l mono">transport</div>
          <div className="mono mcp-cfg-v">{server.transport}</div>
        </div>
        <div className="mcp-cfg-row">
          <div className="mcp-cfg-l mono">version</div>
          <div className="mono mcp-cfg-v">v{server.version}</div>
        </div>

        <div className="mcp-cfg-sec mono">Environment</div>
        {Object.keys(env).length === 0 ? (
          <div className="mcp-cfg-empty mono">No env vars declared by this server.</div>
        ) : (
          <div className="mcp-cfg-env">
            {Object.entries(env).map(([k, v]) => (
              <div key={k} className="mcp-cfg-env-row">
                <span className="mcp-cfg-env-k mono">{k}</span>
                <input
                  className="input mono"
                  value={v}
                  onChange={e => setEnv(prev => ({ ...prev, [k]: e.target.value }))}
                  placeholder={`set ${k}…`}
                  style={v === "" ? { borderColor: "var(--err-line)" } : {}}
                />
              </div>
            ))}
          </div>
        )}

        <div className="mcp-cfg-sec mono">Auto-start</div>
        <label className="mcp-cfg-toggle mono">
          <input type="checkbox" defaultChecked style={{accentColor: "var(--accent)"}} />
          <span>Restart this server when hal0 restarts</span>
        </label>

        <div className="mcp-cfg-sec mono">Allowed clients</div>
        <div className="mcp-cfg-allow mono">
          <span className="mcp-cfg-allow-pill on">any local client</span>
          <span className="mcp-cfg-allow-pill">claude-code only</span>
          <span className="mcp-cfg-allow-pill">require token</span>
        </div>
      </div>
    </Modal>
  );
}

// ─── Logs drawer (per-server tail) ──────────────────────────────────
function LogsDrawer({ open, server, onClose }) {
  if (!server) return null;
  // Live audit rows via /api/mcp/{id}/logs (issue #206). Polls every
  // 3 s while open. Empty result falls through to the prototype sample
  // lines so the drawer still looks alive against a brand-new install.
  const logsQ = useMcpServerLogs(open ? server.id : null);
  const liveEvents = logsQ.data?.events || [];
  const fmtTs = (ts) => {
    if (typeof ts === 'number') return new Date(ts * 1000).toLocaleTimeString();
    if (typeof ts === 'string') return ts;
    return '—';
  };
  const lvlOf = (e) => {
    if (e.outcome === 'failed' || e.outcome === 'denied') return 'warn';
    if (e.outcome === 'executed' || e.outcome === 'approved') return 'ok';
    return 'info';
  };
  const liveLines = liveEvents.map((e, i) => ({
    ts: fmtTs(e.timestamp),
    lvl: lvlOf(e),
    src: server.name,
    msg: `tool call: ${e.tool || 'call'} · ${e.client_id || 'anonymous'}${e.gated ? ' (gated)' : ''}`,
  }));
  const sampleLines = liveLines.length > 0 ? liveLines : [
    { ts: "14:02:11.117", lvl: "ok",   src: "supervisor", msg: `${server.name} pid ${server.pid || "—"} up · 14d 02:11` },
    { ts: "14:02:30.290", lvl: "info", src: server.name,  msg: "tool call: slot.list" },
    { ts: "14:02:30.310", lvl: "info", src: server.name,  msg: "→ 9 results (claude-code)" },
    { ts: "14:02:34.117", lvl: "info", src: server.name,  msg: "tool call: slot.status" },
    { ts: "14:02:34.121", lvl: "info", src: server.name,  msg: "→ {status: 'up', ...} (cursor)" },
    { ts: "14:02:39.443", lvl: "ok",   src: server.name,  msg: "tool call: model.search query='reranker'" },
    { ts: "14:02:39.502", lvl: "info", src: server.name,  msg: "→ 3 results (claude-code)" },
    { ts: "14:02:41.218", lvl: "warn", src: server.name,  msg: "client cursor closed transport stream" },
    { ts: "14:02:41.218", lvl: "info", src: server.name,  msg: "client cursor reconnected · session resumed" },
    { ts: "14:02:48.117", lvl: "ok",   src: server.name,  msg: "tool call: journal.tail lines=200" },
  ];

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={680}
      eyebrow={`MCP · ${server.name} · live tail`}
      title="Server logs"
      foot={
        <>
          <span style={{color: "var(--ok)", display: "inline-flex", alignItems: "center", gap: 5}}>
            <span className="dot ready" style={{width: 6, height: 6}} />
            following tail
          </span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm">Open full logs →</button>
            <button className="btn ghost sm" onClick={onClose}>Close</button>
          </span>
        </>
      }
    >
      <div className="mcp-logs">
        {sampleLines.map((l, i) => (
          <div key={i} className={"mcp-logs-line " + l.lvl}>
            <span className="ts">{l.ts}</span>
            <span className="sl">[{l.src}]</span>
            <span className="lvl">{l.lvl}</span>
            <span className="msg">{l.msg}</span>
          </div>
        ))}
      </div>
    </Drawer>
  );
}

// ─── How-to-connect modal ───────────────────────────────────────────
function ConnectClientModal({ open, onClose }) {
  const [client, setClient] = useStateMM("claude-code");
  const url = `${MCP_HOST_BASE}/mcp/hal0-admin`;

  const snippets = {
    "claude-code": {
      label: "Claude Code",
      cmd: `claude mcp add hal0-admin --url "${url}"`,
      explainer: "Run this in any shell — your Claude Code installation persists the server to its global MCP config.",
    },
    "claude-desktop": {
      label: "Claude Desktop",
      cmd: `// In claude_desktop_config.json:
{
  "mcpServers": {
    "hal0-admin": {
      "url": "${url}"
    }
  }
}`,
      explainer: "Add the entry to the mcpServers object of your Claude Desktop config and restart the app.",
    },
    "cursor": {
      label: "Cursor",
      cmd: `// In ~/.cursor/mcp.json:
{
  "mcpServers": {
    "hal0-admin": {
      "url": "${url}"
    }
  }
}`,
      explainer: "Settings → MCP → Add server. Cursor will pick this up at next reload.",
    },
  };
  const cur = snippets[client];

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="MCP · onboarding"
      title="Point a client at hal0"
      width={620}
      foot={
        <>
          <span style={{color: "var(--fg-4)"}}>All servers exposed by this host live under <span className="mono" style={{color: "var(--fg-2)"}}>{MCP_HOST_BASE}/mcp/&lt;name&gt;</span></span>
          <button className="btn ghost sm" onClick={onClose}>Close</button>
        </>
      }
    >
      <div style={{fontSize: 13, color: "var(--fg-2)", lineHeight: 1.6, marginBottom: 16}}>
        hal0 is an MCP host — your local Claude or Cursor connects to it the same way it would to any other MCP server, by URL.
      </div>

      <div className="mcp-onboard-tabs">
        {Object.entries(snippets).map(([k, s]) => (
          <button
            key={k}
            className={"mcp-onboard-tab" + (client === k ? " on" : "")}
            onClick={() => setClient(k)}
          >{s.label}</button>
        ))}
      </div>

      <div className="mcp-onboard-explain">{cur.explainer}</div>

      <pre className="mcp-onboard-code mono">{cur.cmd}</pre>

      <div style={{display: "flex", gap: 8, marginTop: 12, alignItems: "center"}}>
        <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
          Once connected, the client appears in the Connected clients ribbon and the server rows below.
        </span>
        <span style={{flex: 1}} />
        <button className="btn ghost sm" onClick={() => {
          navigator.clipboard && navigator.clipboard.writeText(cur.cmd);
          window.__hal0Toast && window.__hal0Toast("Snippet copied", "info");
        }}>Copy snippet</button>
      </div>
    </Modal>
  );
}

Object.assign(window, { InstallDrawer, EditConfigModal, LogsDrawer, ConnectClientModal });
