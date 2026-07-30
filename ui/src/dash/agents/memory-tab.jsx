// hal0 — MemoryTab (design §7: Agent → Memory fold).
//
// The #agent route's Memory tab is now a THIN POINTER to the canonical
// Memory home (#memory → Overview · Graph · Tools). It renders:
//   - A pointer card directing the operator to the Memory section.
//   - The ADR-0023 graph-extraction gate (MemoryGraphPanel) — the one
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

// ── ADR-0023 Graph extraction panel ──────────────────────────────────
// Extraction is routed to a single local enabled-llm slot (extraction_slot).
function MemoryGraphPanel() {
  const useStatus = window.__hal0UseMemoryGraphStatus;
  const useUpdate = window.__hal0UseUpdateMemoryGraph;
  const status = useStatus ? useStatus() : { isLoading: false, isError: false, data: null };
  const update = useUpdate ? useUpdate() : { mutate: () => {}, isPending: false };
  const data = status.data;
  const enabled = !!(data && data.enabled);
  // ADR-0023: prefer extraction_slot; fall back to the deprecated `route`
  // mirror only if the new field is absent.
  const extractionSlot = (data && (data.extraction_slot || data.route)) || "utility";
  const slotResolves = !!(data && data.slot_resolves);
  const availableSlots = (data && data.available_slots) || [];

  const [showSlotPicker, setShowSlotPicker] = useStateMT(false);
  const [draftSlot, setDraftSlot] = useStateMT(extractionSlot);

  const openPanel = () => {
    setDraftSlot(extractionSlot);
    setShowSlotPicker(true);
  };

  const submit = () => {
    const payload = { enabled: true, extraction_slot: draftSlot };
    update.mutate(payload, {
      onSuccess: (resp) => {
        setShowSlotPicker(false);
        window.__hal0Toast && window.__hal0Toast("Graph extraction enabled", "ok");
        // The gate persisted, but the hindsight-api restart may have failed —
        // surface the propagation error as a warning if present.
        const propErr = resp && resp.propagation && resp.propagation.error;
        if (propErr) {
          window.__hal0Toast &&
            window.__hal0Toast(`Slot saved, but hindsight-api restart failed: ${propErr}`, "warn");
        }
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
      <div className="mo-card-h">
        <span className="mo-eyebrow">Graph extraction</span>
        <span className="grow" />
        {enabled ? <span className="chip ok">ON · {extractionSlot}</span> : <span className="chip">OFF</span>}
      </div>

      {!enabled && !showSlotPicker && (
        <>
          <p style={{fontSize: 13, color: "var(--fg-2)", margin: "0 0 14px", lineHeight: 1.55}}>
            Graph extraction is marked off. On the Hindsight engine this is a hal0-side label: the daemon builds its graph natively and keeps extracting regardless, so the graph counters below stay live. Turning it on is what points extraction at your chosen slot.
          </p>
          <button className="btn primary sm" onClick={openPanel}>
            Enable graph extraction
          </button>
        </>
      )}

      {enabled && !showSlotPicker && (
        <>
          <div style={{display: "flex", alignItems: "center", gap: 8, marginBottom: 12}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-3)"}}>extraction slot</span>
            <span className="mono" style={{fontSize: 12, color: "var(--fg)", fontWeight: 500}}>{extractionSlot}</span>
            <span
              className="mono"
              data-testid="graph-slot-resolves"
              style={{fontSize: 10.5, padding: "1px 6px", borderRadius: "var(--rad)", border: "1px solid var(--line)", color: slotResolves ? "var(--ok, #6a6)" : "var(--err, #c66)"}}
            >
              {slotResolves ? "resolves" : "no matching enabled llm slot"}
            </span>
          </div>
          <div style={{display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 0, border: "1px solid var(--line)", borderRadius: "var(--rad)", overflow: "hidden", marginBottom: 12}}>
            {[
              { l: "Built", v: String(builds),  sub: "lifetime" },
              { l: "Errors", v: String(errors),  sub: errors ? "see logs" : "—" },
              { l: "Live",  v: String(inFlight),sub: "pending" },
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
            <button className="btn ghost sm" onClick={openPanel}>{Icons.edit} Change slot</button>
            <button className="btn danger sm" onClick={disable}>Disable</button>
          </div>
        </>
      )}

      {showSlotPicker && (
        <div style={{display: "flex", flexDirection: "column", gap: 14}}>
          <div>
            <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 6}}>Extraction slot</div>
            {availableSlots.length > 0 ? (
              <select
                data-testid="graph-slot-select"
                value={draftSlot}
                onChange={e => setDraftSlot(e.target.value)}
                style={{width: "100%", padding: "6px 8px", background: "var(--bg-2)", border: "1px solid var(--line)", color: "var(--fg)", fontFamily: "var(--jbm)", fontSize: 12}}
              >
                {availableSlots.map(s => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            ) : (
              <input
                type="text"
                data-testid="graph-slot-input"
                value={draftSlot}
                onChange={e => setDraftSlot(e.target.value)}
                placeholder="utility"
                style={{width: "100%", padding: "6px 8px", background: "var(--bg-2)", border: "1px solid var(--line)", color: "var(--fg)", fontFamily: "var(--jbm)", fontSize: 12}}
              />
            )}
          </div>

          {/* ADR-0023 §3 — extraction routes to a local enabled-llm slot. */}
          <div style={{padding: 12, border: "1px solid var(--line)", borderRadius: "var(--rad)", background: "var(--bg-2)"}}>
            <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 4}}>Routing</div>
            <p style={{fontSize: 12, color: "var(--fg-2)", margin: 0, lineHeight: 1.5}}>
              Graph extraction sends ingested memory text to the&nbsp;
              <span className="mono" style={{color: "var(--fg)"}}>{draftSlot}</span> slot
              {" "}for entity + relation extraction. Everything stays on-box — pick a
              cheap local slot (e.g. <span className="mono" style={{color: "var(--fg)"}}>utility</span>)
              to keep extraction light. Quality may vary on small models.
            </p>
          </div>

          {/* ADR-0023 §4 quality caveat copy. */}
          <div style={{padding: 12, border: "1px dashed var(--line)", borderRadius: "var(--rad)"}}>
            <p style={{fontSize: 12, color: "var(--fg-3)", margin: 0, lineHeight: 1.5}}>
              Graph quality varies by model. We don't currently measure it for you — your results may vary.
            </p>
          </div>

          <div style={{display: "flex", gap: 8, justifyContent: "flex-end"}}>
            <button className="btn ghost sm" onClick={() => setShowSlotPicker(false)}>Cancel</button>
            <button className="btn primary sm" onClick={submit} disabled={update.isPending}>
              {update.isPending ? "Saving…" : enabled ? "Save" : "Enable graph extraction"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

Object.assign(window, { MemoryTab, MemoryGraphPanel });
