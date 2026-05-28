// hal0 v0.3 PR-8 — PersonasTab.
//
// Reads /api/agents/hermes/personas via the useAgentPersonas hook
// (bridged onto window in personas-tab-hook-bridge.ts). v0.3 is
// read-only: the list renders one card per persona, the active
// persona gets the accent marker, and clicking a card opens the
// existing PersonaEditModal in detail mode.
//
// The "+ custom" card opens PersonaEditModal in create mode (modal
// already supports a null `persona` prop). Modal lives in flow-modals.jsx.

const { useState: useStatePT } = React;

function PersonasTab({ onEdit } = {}) {
  const usePersonas = window.__hal0UseAgentPersonas;
  const personasQuery = usePersonas ? usePersonas("hermes") : { data: null, isLoading: false, isError: false };
  const data = personasQuery.data;
  const live = data && Array.isArray(data.personas) ? data.personas : [];
  const activeId = data && data.active;

  // Map live API rows onto the prototype card shape. Fall back to a
  // static demo trio when the endpoint hasn't seeded yet so the page
  // doesn't render empty during onboarding screenshots.
  const FALLBACK = [
    { name: "hermes",       slot: "primary", model: "qwen3.6-27b-mtp",  tone: "operator",     desc: "Default — terse, technical, runs skills aggressively. Wired to the dashboard chat surface.", active: true },
    { name: "hermes-coder", slot: "coder",   model: "qwen3-coder-30b", tone: "code-focused", desc: "Swaps in when the persona dropdown picks coder. Optimised for refactors and review." },
    { name: "hermes-npu",   slot: "agent",   model: "gemma3:1b",       tone: "low-latency",  desc: "NPU coresident · for short follow-ups while keeping voice+embed warm." },
  ];
  const cards = live.length > 0
    ? live.map(p => ({
        name: p.display_name || p.id,
        id: p.id,
        slot: p.slot || null,
        model: p.model || "",
        tone: p.tone || "",
        desc: p.description || "",
        active: !!p.active || (activeId && p.id === activeId),
      }))
    : FALLBACK;
  const addCard = { name: "+ custom", slot: null, model: "", tone: "", desc: "Add a persona — pick a chat slot, set a system prompt, and pick a skill set.", isAdd: true };
  const rows = [...cards, addCard];

  return (
    <div data-testid="personas-tab" style={{display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 14}}>
      {rows.map((p, i) => (
        <div
          key={i}
          className="card"
          data-testid={p.isAdd ? "persona-card-add" : ("persona-card-" + (p.id || p.name))}
          style={{padding: 18, position: "relative", borderColor: p.active ? "var(--accent-line)" : "var(--line)", borderStyle: p.isAdd ? "dashed" : "solid"}}
        >
          {p.active && <div style={{position: "absolute", top: 0, left: 0, right: 0, height: 2, background: "var(--accent)"}} />}
          <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 10}}>
            <div style={{width: 36, height: 36, borderRadius: 6, background: p.isAdd ? "var(--bg-2)" : "var(--accent-soft)", border: "1px solid " + (p.isAdd ? "var(--line)" : "var(--accent-line)"), display: "inline-flex", alignItems: "center", justifyContent: "center", color: p.isAdd ? "var(--fg-4)" : "var(--accent)"}}>
              {p.isAdd ? Icons.plus : Icons.agent}
            </div>
            <div>
              <div className="mono" style={{fontSize: 14, fontWeight: 500, letterSpacing: "-0.01em"}}>{p.name}</div>
              {p.slot && <div className="mono" style={{fontSize: 11, color: "var(--fg-3)", marginTop: 2}}>routes to slot <b style={{color: "var(--accent)"}}>{p.slot}</b>{p.model ? " · " + p.model : ""}</div>}
            </div>
            {p.active && <span style={{marginLeft: "auto"}} className="chip amber">active</span>}
          </div>
          {p.desc && <p style={{fontSize: 12.5, color: "var(--fg-2)", margin: "0 0 12px", lineHeight: 1.55}}>{p.desc}</p>}
          {!p.isAdd && (
            <div style={{display: "flex", gap: 6, alignItems: "center"}}>
              {p.tone && <span className="chip">{p.tone}</span>}
              <span style={{marginLeft: "auto", display: "flex", gap: 6}}>
                <button className="btn ghost sm" onClick={() => onEdit && onEdit(p)}>{Icons.edit} Edit</button>
                {!p.active && <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Persona ${p.name} activated`, "ok")}>Activate</button>}
              </span>
            </div>
          )}
          {p.isAdd && (
            <div style={{display: "flex", justifyContent: "flex-end"}}>
              <button className="btn ghost sm" onClick={() => onEdit && onEdit(p)}>{Icons.plus} Create persona</button>
            </div>
          )}
        </div>
      ))}
      {personasQuery.isError && (
        <div className="mono" style={{gridColumn: "1 / -1", fontSize: 11, color: "var(--err)"}}>
          /api/agents/hermes/personas unreachable — showing fallback list.
        </div>
      )}
    </div>
  );
}

Object.assign(window, { PersonasTab });
