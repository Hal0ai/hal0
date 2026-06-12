// hal0 v0.3 PR-8 — MemoryTab.
//
// Composes:
//   - GraphExtractionPanel (ADR-0014 — graph build status + route picker)
//   - Memory engine stats card (live: /api/agents/hermes/memory/stats)
//   - Recent records card (live: GET /api/memory/list)
//   - Namespaces side card (derived from live stats)
//   - "Peer memory" subsection (folded in from the old Peers tab)
//
// The Peer memory subsection consumes the live MCP search at
// /api/memory/search (dataset=agents, tag=agent-identity) — the only
// fully-live surface from the original Peers tab. v0.3 keeps it
// read-only per ADR-0011 §2.
//
// `subsection` prop scrolls the page to #peer-memory on mount when set
// (parses #agent/memory?subsection=peer in agent-view.jsx).

const { useState: useStateMT, useEffect: useEffectMT } = React;

function MemoryTab({ subsection } = {}) {
  // Live hooks injected via memory-tab-hook-bridge.ts
  const useMemoryList = window.__hal0UseMemoryList;
  const useAgentMemoryStats = window.__hal0UseAgentMemoryStats;
  // /api/features supplies the live engine name (memory_engine).
  // Fall back to "memory engine" if unavailable.
  const useFeaturesHook = window.__hal0UseFeatures;

  const statsQuery = useAgentMemoryStats ? useAgentMemoryStats("hermes") : { isLoading: false, isError: false, data: null };
  const listQuery = useMemoryList ? useMemoryList({ dataset: "shared", limit: 10 }) : { isLoading: false, isError: false, data: null };
  const featuresQuery = useFeaturesHook ? useFeaturesHook() : { data: null };
  const engineLabel = featuresQuery.data?.memory_engine || "memory engine";

  const stats = statsQuery.data;
  const records = listQuery.data?.items ?? [];

  useEffectMT(() => {
    if (subsection === "peer") {
      const el = document.getElementById("peer-memory");
      if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }, [subsection]);

  // Namespace list derived from live stats
  const namespaces = [];
  if (stats) {
    namespaces.push({ name: "shared", desc: "default · all agents", recs: null, active: true });
    if (stats.available) {
      namespaces.push({ name: stats.namespace, desc: "agent · private", recs: stats.writes, active: false });
    }
  }

  return (
    <div data-testid="memory-tab" style={{display: "grid", gridTemplateColumns: "1fr 320px", gap: 16}}>
      <div>
        <MemoryGraphPanel />

        {/* ── Memory engine stats card ── */}
        <div className="card" style={{padding: 18, marginBottom: 14}}>
          {statsQuery.isLoading && (
            <div className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>Loading memory stats…</div>
          )}
          {statsQuery.isError && (
            <div className="mono" style={{fontSize: 12, color: "var(--err, #c66)"}}>Memory stats unavailable</div>
          )}
          {!statsQuery.isLoading && !statsQuery.isError && (
            <>
              <div style={{display: "flex", alignItems: "center", gap: 12, marginBottom: 14}}>
                <span data-testid="memory-engine-label" className="mono" style={{fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.1em"}}>{engineLabel} · shared</span>
                <span className="mono num" style={{fontSize: 24, color: "var(--fg)", letterSpacing: "-0.02em"}}>{stats?.writes ?? 0}</span>
                <span className="mono" style={{fontSize: 12, color: "var(--fg-3)"}}>records</span>
                <span style={{marginLeft: "auto"}} className={`chip ${stats?.available ? "ok" : ""}`}>
                  {stats?.available ? "healthy" : "offline"}
                </span>
              </div>
              {stats?.last_write && (
                <div className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
                  last write: {stats.last_write}
                </div>
              )}
              {!stats?.available && (
                <div className="mono" style={{fontSize: 11, color: "var(--fg-4)", marginTop: 6}}>
                  {engineLabel} not configured or unavailable
                </div>
              )}
            </>
          )}
        </div>

        {/* ── Recent records ── */}
        <div className="sec"><h2>Recent records</h2><div className="rule" /></div>
        <div className="card" style={{overflow: "hidden", marginBottom: 24}}>
          {listQuery.isLoading && (
            <div style={{padding: "16px 18px", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>Loading records…</div>
          )}
          {listQuery.isError && (
            <div style={{padding: "16px 18px", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--err, #c66)"}}>Could not load records</div>
          )}
          {!listQuery.isLoading && !listQuery.isError && records.length === 0 && (
            <div data-testid="memory-records-empty" style={{padding: "24px 18px", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)", textAlign: "center"}}>
              no records yet
            </div>
          )}
          {records.map((r, i) => (
            <div key={r.id || i} style={{padding: "12px 18px", borderBottom: "1px solid var(--line-soft)", fontFamily: "var(--jbm)", fontSize: 12}}>
              <div style={{display: "flex", gap: 10, marginBottom: 4}}>
                <span style={{color: "var(--fg-5)"}}>{r.timestamp ? r.timestamp.slice(11, 19) : "—"}</span>
                <span style={{color: "var(--accent)"}}>{r.source || r.dataset || "—"}</span>
                {r.tags && r.tags[0] && <span className="chip">{r.tags[0]}</span>}
              </div>
              <div style={{color: "var(--fg-2)", paddingLeft: 0}}>{r.text}</div>
            </div>
          ))}
        </div>

        {/* ── Peer memory (folded in from the old Peers tab) ─────────── */}
        <div id="peer-memory" className="sec" data-testid="peer-memory-section">
          <h2>Peer memory</h2>
          <div className="rule" />
        </div>
        <p className="mono" style={{fontSize: 11.5, color: "var(--fg-4)", margin: "4px 0 12px", lineHeight: 1.55}}>
          Agent identity cards published by other hal0 instances (ADR-0011). Cards are immutable; this view is read-only.
        </p>
        <PeerMemoryList />
      </div>

      <div>
        <div className="side-card">
          <div className="side-card-h"><span>Namespaces</span></div>
          <div className="side-card-b">
            {statsQuery.isLoading && (
              <div className="mono" style={{fontSize: 11, color: "var(--fg-4)", padding: "10px 0"}}>Loading…</div>
            )}
            {!statsQuery.isLoading && namespaces.length === 0 && (
              <div data-testid="namespaces-empty" className="mono" style={{fontSize: 11, color: "var(--fg-4)", padding: "10px 0"}}>no namespaces available</div>
            )}
            {namespaces.map(n => (
              <div key={n.name} style={{padding: "10px 0", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", gap: 10, fontFamily: "var(--jbm)", fontSize: 12}}>
                <span className={"dot " + (n.active ? "ready" : "idle")} />
                <div>
                  <div style={{color: "var(--fg)", fontWeight: 500}}>{n.name}</div>
                  <div style={{color: "var(--fg-4)", fontSize: 10}}>{n.desc}</div>
                </div>
                {n.recs != null && (
                  <span style={{marginLeft: "auto", color: "var(--fg-3)"}} className="num">{n.recs}</span>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── ADR-0014 Graph extraction panel ──────────────────────────────────
// Copy MUST match ADR-0014 §3 + §4 verbatim — changes need an ADR amend.
function MemoryGraphPanel() {
  const useStatus = window.__hal0UseMemoryGraphStatus;
  const useUpdate = window.__hal0UseUpdateMemoryGraph;
  const status = useStatus ? useStatus() : { isLoading: false, isError: false, data: null };
  const update = useUpdate ? useUpdate() : { mutate: () => {}, isPending: false };
  const data = status.data;
  const enabled = !!(data && data.enabled);
  const route = (data && data.route) || "upstream";

  const [showRoutePicker, setShowRoutePicker] = useStateMT(false);
  const [draftRoute, setDraftRoute] = useStateMT(route);
  const [draftProvider, setDraftProvider] = useStateMT(
    (data && data.upstream && data.upstream.provider) || "openrouter",
  );
  const [draftModel, setDraftModel] = useStateMT(
    (data && data.upstream && data.upstream.model) || "anthropic/claude-3.5-sonnet",
  );

  const openPanel = () => {
    setDraftRoute(route);
    setDraftProvider((data && data.upstream && data.upstream.provider) || "openrouter");
    setDraftModel((data && data.upstream && data.upstream.model) || "anthropic/claude-3.5-sonnet");
    setShowRoutePicker(true);
  };

  const submit = () => {
    const payload = { enabled: true, route: draftRoute };
    if (draftRoute === "upstream") {
      payload.upstream = { provider: draftProvider, model: draftModel };
    }
    update.mutate(payload, {
      onSuccess: () => {
        setShowRoutePicker(false);
        window.__hal0Toast && window.__hal0Toast("Graph extraction enabled", "ok");
      },
      onError: (err) => {
        window.__hal0Toast && window.__hal0Toast(`Enable failed: ${err.message}`, "err");
      },
    });
  };

  const disable = () => {
    update.mutate({ enabled: false }, {
      onSuccess: () => {
        window.__hal0Toast && window.__hal0Toast("Graph extraction disabled", "warn");
      },
    });
  };

  if (status.isLoading) {
    return (
      <div className="card" style={{padding: 18, marginBottom: 14}}>
        <div className="mono" style={{fontSize: 12, color: "var(--fg-4)"}}>Loading graph extraction status…</div>
      </div>
    );
  }
  if (status.isError) {
    return (
      <div className="card" style={{padding: 18, marginBottom: 14, borderColor: "var(--err-line, var(--line))"}}>
        <div className="mono" style={{fontSize: 12, color: "var(--err, #c66)"}}>Memory engine unavailable: {String(status.error?.message || "unknown")}</div>
      </div>
    );
  }

  const errors = (data && data.errors) || 0;
  const builds = (data && data.builds_ok) || 0;
  const inFlight = (data && data.in_flight) || 0;
  const lastBuilt = data && data.last_built_at;

  return (
    <div className="card" style={{padding: 18, marginBottom: 14}}>
      <div style={{display: "flex", alignItems: "center", gap: 12, marginBottom: 10}}>
        <span className="mono" style={{fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.1em"}}>Graph extraction · ADR-0014</span>
        <span style={{marginLeft: "auto"}}>
          {enabled ? <span className="chip ok">ON · {route}</span> : <span className="chip">OFF</span>}
        </span>
      </div>

      {!enabled && !showRoutePicker && (
        <>
          <p style={{fontSize: 13, color: "var(--fg-2)", margin: "0 0 14px", lineHeight: 1.55}}>
            Graph extraction is off. Memory still stores + searches vectors; the entity/relation graph powering <span className="mono" style={{color: "var(--fg)"}}>memory_search(mode="graph")</span> isn't built.
          </p>
          <button className="btn primary sm" onClick={openPanel}>
            Enable graph extraction
          </button>
        </>
      )}

      {enabled && !showRoutePicker && (
        <>
          <div style={{display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 0, border: "1px solid var(--line)", borderRadius: "var(--rad)", overflow: "hidden", marginBottom: 12}}>
            {[
              { l: "Builds OK", v: String(builds),  sub: "lifetime" },
              { l: "Errors",    v: String(errors),  sub: errors ? "see logs" : "—" },
              { l: "In flight", v: String(inFlight),sub: "pending" },
            ].map((s, i) => (
              <div key={i} style={{padding: 14, borderRight: i < 2 ? "1px solid var(--line)" : "none"}}>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em"}}>{s.l}</div>
                <div className="mono num" style={{fontSize: 22, color: errors > 0 && i === 1 ? "var(--err, #c66)" : "var(--fg)", marginTop: 4}}>{s.v}</div>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>{s.sub}</div>
              </div>
            ))}
          </div>
          {lastBuilt && (
            <div className="mono" style={{fontSize: 11, color: "var(--fg-4)", marginBottom: 10}}>
              Last build: {lastBuilt}
            </div>
          )}
          {data && data.last_error && (
            <div className="mono" style={{fontSize: 11, color: "var(--err, #c66)", marginBottom: 10}}>
              Last error: {data.last_error}
            </div>
          )}
          <div style={{display: "flex", gap: 8}}>
            <button className="btn ghost sm" onClick={openPanel}>{Icons.edit} Change route</button>
            <button className="btn danger sm" onClick={disable}>Disable</button>
          </div>
        </>
      )}

      {showRoutePicker && (
        <div style={{display: "flex", flexDirection: "column", gap: 14}}>
          <div>
            <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6}}>Route</div>
            {[
              { id: "upstream", label: "upstream", desc: "Send memory text to a hosted provider per build. Best quality." },
              { id: "primary",  label: "primary",  desc: "Use the live primary slot. Stays on-box. Quality depends on the model." },
              { id: "agent",    label: "agent",    desc: "Use the agent slot (NPU). Stays on-box. Latency low; context constrained." },
            ].map(o => (
              <label key={o.id} style={{display: "flex", alignItems: "flex-start", gap: 8, padding: "6px 0", cursor: "pointer"}}>
                <input
                  type="radio"
                  name="graph-route"
                  value={o.id}
                  checked={draftRoute === o.id}
                  onChange={() => setDraftRoute(o.id)}
                />
                <span style={{flex: 1}}>
                  <span className="mono" style={{fontSize: 12, color: "var(--fg)", fontWeight: 500}}>{o.label}</span>
                  <span style={{display: "block", fontSize: 11.5, color: "var(--fg-3)", lineHeight: 1.4}}>{o.desc}</span>
                </span>
              </label>
            ))}
          </div>

          {draftRoute === "upstream" && (
            <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8}}>
              <label>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginBottom: 4}}>Provider</div>
                <select
                  value={draftProvider}
                  onChange={e => setDraftProvider(e.target.value)}
                  style={{width: "100%", padding: "6px 8px", background: "var(--bg-2)", border: "1px solid var(--line)", color: "var(--fg)", fontFamily: "var(--jbm)", fontSize: 12}}
                >
                  <option value="openrouter">openrouter</option>
                  <option value="anthropic">anthropic</option>
                  <option value="openai">openai</option>
                  <option value="custom">custom</option>
                </select>
              </label>
              <label>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", marginBottom: 4}}>Model</div>
                <input
                  type="text"
                  value={draftModel}
                  onChange={e => setDraftModel(e.target.value)}
                  placeholder="anthropic/claude-3.5-sonnet"
                  style={{width: "100%", padding: "6px 8px", background: "var(--bg-2)", border: "1px solid var(--line)", color: "var(--fg)", fontFamily: "var(--jbm)", fontSize: 12}}
                />
              </label>
            </div>
          )}

          {/* ADR-0014 §3 verbatim privacy disclosure copy. */}
          <div style={{padding: 12, border: "1px solid var(--line)", borderRadius: "var(--rad)", background: "var(--bg-2)"}}>
            <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4}}>Privacy</div>
            <p style={{fontSize: 12, color: "var(--fg-2)", margin: 0, lineHeight: 1.5}}>
              Graph extraction sends ingested memory text to&nbsp;
              <span className="mono" style={{color: "var(--fg)"}}>{draftRoute === "upstream" ? `${draftProvider} (${draftModel})` : draftRoute === "primary" ? "your primary slot" : "your agent slot"}</span>
              {draftRoute === "upstream" ? " for entity + relation extraction. Your raw memory store stays local. Switch to a local slot to keep everything on-box (quality may vary on small models)." : ". This stays on-box. Quality may vary on small models."}
            </p>
          </div>

          {/* ADR-0014 §4 verbatim quality caveat copy. */}
          <div style={{padding: 12, border: "1px dashed var(--line)", borderRadius: "var(--rad)"}}>
            <p style={{fontSize: 12, color: "var(--fg-3)", margin: 0, lineHeight: 1.5}}>
              Graph quality varies by model. We don't currently measure it for you — your results may vary.
            </p>
          </div>

          <div style={{display: "flex", gap: 8, justifyContent: "flex-end"}}>
            <button className="btn ghost sm" onClick={() => setShowRoutePicker(false)}>Cancel</button>
            <button className="btn primary sm" onClick={submit} disabled={update.isPending}>
              {update.isPending ? "Saving…" : enabled ? "Save route" : "Enable graph extraction"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── PeerMemoryList (folded in from old AgentPeers / #247) ───────────
// Reads identity cards from the `agents` Cognee dataset via the
// hal0-memory MCP. One card per peer with a TCP-ping reachability dot
// (not stored; pinged on render). Cards immutable per ADR-0011 §2.
function PeerMemoryList() {
  const [cards, setCards] = useStateMT([]);
  const [loading, setLoading] = useStateMT(true);
  const [err, setErr] = useStateMT(null);

  useEffectMT(() => {
    let cancelled = false;
    (async () => {
      try {
        // #302: REST shim at /api/memory/search instead of /mcp/memory.
        // The streamable-HTTP MCP transport at /mcp/memory/mcp requires
        // the initialize handshake — not doable from a fetch() oneshot.
        const resp = await fetch("/api/memory/search", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-hal0-Agent": "hal0-dashboard" },
          body: JSON.stringify({
            query: "agent identity",
            tags: ["agent-identity"],
            dataset: "agents",
            limit: 50,
          }),
        });
        const data = await resp.json();
        if (cancelled) return;
        const items = (data && data.items) || [];
        setCards(items);
        setLoading(false);
      } catch (e) {
        if (!cancelled) {
          setErr(String(e));
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="card" style={{padding: 20, color: "var(--fg-3)"}}>Loading peers…</div>;
  }
  if (err) {
    return <div className="card" style={{padding: 20, color: "var(--err)"}}>memory MCP unreachable: {err}</div>;
  }
  if (!cards.length) {
    return (
      <div className="card" style={{padding: 40, textAlign: "center", borderStyle: "dashed"}}>
        <div className="mono" style={{fontSize: 14, color: "var(--fg-3)", marginBottom: 6}}>No agent identity cards published yet.</div>
        <div className="mono" style={{fontSize: 11, color: "var(--fg-5)"}}>Cards appear here when a bundled agent finishes <code>hal0 agent bootstrap</code>.</div>
      </div>
    );
  }
  return (
    <div style={{display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12}}>
      {cards.map((c, i) => <PeerCard key={i} card={c} />)}
    </div>
  );
}

function PeerCard({ card }) {
  const md = (card && card.metadata) || {};
  const endpoint = md.endpoint || {};
  const hs = md.hal0_state || {};
  const roles = md.roles || [];
  const [reach, setReach] = useStateMT("checking");
  const [expanded, setExpanded] = useStateMT(false);

  useEffectMT(() => {
    let cancelled = false;
    const url = endpoint.url;
    if (!url) { setReach("none"); return; }
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 5000);
    (async () => {
      try {
        await fetch(url, { method: "HEAD", signal: ctrl.signal, mode: "no-cors" });
        if (!cancelled) setReach("ok");
      } catch (e) {
        if (cancelled) return;
        setReach(e.name === "AbortError" ? "timeout" : "error");
      } finally {
        clearTimeout(tid);
      }
    })();
    return () => { cancelled = true; ctrl.abort(); };
  }, [endpoint.url]);

  const dotColor = reach === "ok" ? "var(--ok)" : reach === "timeout" ? "var(--warn)" : reach === "error" ? "var(--err)" : "var(--fg-5)";

  return (
    <div className="card" style={{padding: 16, display: "flex", flexDirection: "column", gap: 8}}>
      <div style={{display: "flex", alignItems: "center", gap: 10}}>
        <span style={{width: 8, height: 8, borderRadius: "50%", background: dotColor}} aria-label={`endpoint ${reach}`} />
        <div className="mono" style={{fontSize: 14, fontWeight: 500}}>{md.display_name || md.agent_id || "(unnamed)"}</div>
      </div>
      <div className="mono" style={{fontSize: 11, color: "var(--fg-3)"}}>{md.agent_id || "—"}</div>
      {roles.length > 0 && (
        <div style={{display: "flex", flexWrap: "wrap", gap: 4}}>
          {roles.map((r, i) => <span key={i} className="chip">{r}</span>)}
        </div>
      )}
      <div className="mono" style={{fontSize: 10.5, color: "var(--fg-4)"}}>
        endpoint: {endpoint.url || "(none)"}<br />
        registered: {hs.registered_at || "—"}
      </div>
      <button
        onClick={() => setExpanded(e => !e)}
        className="mono"
        style={{
          marginTop: 4,
          padding: "4px 8px",
          fontSize: 10,
          background: "transparent",
          border: "1px solid var(--line)",
          borderRadius: 4,
          color: "var(--fg-3)",
          cursor: "pointer",
          alignSelf: "flex-start",
        }}
      >{expanded ? "hide" : "show"} metadata</button>
      {expanded && (
        <pre className="mono" style={{fontSize: 10, color: "var(--fg-3)", overflow: "auto", maxHeight: 220, margin: 0, padding: 8, background: "var(--bg-2)", borderRadius: 4}}>
          {JSON.stringify(md, null, 2)}
        </pre>
      )}
    </div>
  );
}

Object.assign(window, { MemoryTab, PeerMemoryList, PeerCard });
