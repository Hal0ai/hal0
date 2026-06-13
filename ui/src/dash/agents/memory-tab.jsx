// hal0 — MemoryTab (design §7: Agent → Memory fold).
//
// The #agent route's Memory tab is now a THIN POINTER to the canonical
// Memory home (#memory → Overview · Graph · Tools). It renders:
//   - A pointer card directing the operator to the Memory section.
//   - The ADR-0014 graph-extraction gate (MemoryGraphPanel) — the one
//     live agent-level control kept here.
//
// Everything else that used to live here (peer memory cards, the
// Namespaces side card, hardcoded Cognee stats + Recent records
// fixtures) was removed when memory moved to its own #memory route.
//
// The `subsection` prop is accepted for route-shape compatibility but
// is no longer used (the old #peer-memory scroll target is gone).

const { useState: useStateMT } = React;

function MemoryTab({ subsection } = {}) { // eslint-disable-line no-unused-vars
  // Live engine summary for the pointer card's mini-stat. Optional —
  // omitted gracefully when the hook isn't present.
  const useMemoryEngine = window.__hal0UseMemoryEngine;
  const engineQuery = useMemoryEngine ? useMemoryEngine() : { data: null };
  const engine = engineQuery.data;

  return (
    <div data-testid="memory-tab">
      <div className="ag-pointer card">
        <div className="ag-ptr-ic">{Icons.memory}</div>
        <div className="ag-ptr-body">
          <div className="ag-ptr-h mono">Memory</div>
          <p>
            Agent memory now lives in the dedicated <b className="mono">Memory</b> section —
            bank Overview, the knowledge-graph explorer, and Tools (recall, reflect,
            documents and directives) all live in one home there.
          </p>
          <div className="ag-ptr-actions">
            <button
              className="btn primary sm"
              data-testid="memory-open-view"
              onClick={() => { window.location.hash = "#memory"; }}
            >
              {Icons.memory} Open in Memory
            </button>
            <button
              className="btn ghost sm"
              data-testid="memory-open-graph"
              onClick={() => { window.location.hash = "#memory/graph"; }}
            >
              Open graph
            </button>
          </div>
        </div>
        {engine && (
          <div className="ag-ptr-stat mono" data-testid="memory-ptr-stat">
            <div>
              <span className="k">engine</span>
              <span className="v">{engine.reachable ? "reachable" : "offline"}</span>
            </div>
            {engine.banks_total != null && (
              <div>
                <span className="k">banks</span>
                <span className="v num">{engine.banks_total}</span>
              </div>
            )}
            {engine.version && (
              <div>
                <span className="k">version</span>
                <span className="v">{engine.version}</span>
              </div>
            )}
          </div>
        )}
      </div>

      <div className="sec" style={{marginTop: 18}}>
        <h2>Graph extraction</h2>
        <div className="rule" />
      </div>
      <MemoryGraphPanel />
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

Object.assign(window, { MemoryTab, MemoryGraphPanel });
