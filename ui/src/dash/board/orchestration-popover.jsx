// orchestration-popover.jsx — window-global overlay (NO ES imports)
// Exports: window.OrchPopover
// Editable knobs (PUT /config): orchestrator_profile, default_assignee,
//   auto_decompose, auto_promote_children
// Read-only (GET /config): tick_interval, failure_limit, max_in_flight, claim_ttl

(function () {
  const { useState } = React;

  // Resolve BoardIcon at RENDER time (board-view.jsx registers it AFTER this
  // module loads; window.Icons is chrome's glyph-object, not a component).
  function Icon(props) {
    const BI = window.BoardIcon;
    return BI ? <BI {...props} /> : null;
  }

  function OrchPopover({ orch, set, onClose }) {
    // Live orchestration state. These window hooks are TanStack queries —
    // the value lives on `.data`, not the QueryResult object itself.
    const useOrch = window.__hal0UseBoardOrchestration;
    const orchData = (useOrch ? useOrch() : null)?.data || null;

    // Config hook (read-only values from config.yaml)
    const useConfig = window.__hal0UseBoardConfig;
    const cfg = (useConfig ? useConfig() : null)?.data || null;

    // Dropdown data hooks — unwrap `.data` to the arrays for .map().
    const useProfiles = window.__hal0UseBoardProfiles;
    const profiles = (useProfiles ? useProfiles() : null)?.data || [];

    const useAssignees = window.__hal0UseBoardAssignees;
    const assignees = (useAssignees ? useAssignees() : null)?.data || [];

    // Update mutation hook
    const useUpdateOrch = window.__hal0UseUpdateOrchestration;
    const updateOrch = useUpdateOrch ? useUpdateOrch() : null;

    // Local editable state seeded from live orchData (fallback to prop orch)
    const src = orchData || orch || {};
    const [mode, setMode] = useState(src.mode || "auto");
    const [profile, setProfile] = useState(src.orchestrator_profile || "");
    const [assignee, setAssignee] = useState(src.default_assignee || "");
    const [autoDecompose, setAutoDecompose] = useState(
      src.auto_decompose !== undefined ? src.auto_decompose : false
    );
    const [autoPromote, setAutoPromote] = useState(
      src.auto_promote_children !== undefined ? src.auto_promote_children : false
    );

    const toast = (msg) => {
      if (window.__hal0Toast) window.__hal0Toast(msg);
    };

    const handleSave = () => {
      const patch = {
        mode,
        orchestrator_profile: profile,
        default_assignee: assignee,
        auto_decompose: autoDecompose,
        auto_promote_children: autoPromote,
      };
      if (updateOrch) {
        updateOrch.mutate(patch);
        toast("Orchestration saved");
      } else if (set) {
        // fallback: caller-supplied set
        Object.entries(patch).forEach(([k, v]) => set(k, v));
        toast("Orchestration updated");
      }
      if (onClose) onClose();
    };

    // Read-only numeric values from config (config.yaml)
    const roTick = cfg ? cfg.tick_interval : (src.tickInterval ?? "—");
    const roFailure = cfg ? cfg.failure_limit : (src.failureLimit ?? "—");
    const roInflight = cfg ? cfg.max_in_flight : (src.maxInflight ?? "—");
    const roTtl = cfg ? cfg.claim_ttl : (src.claimTtl ?? "—");

    return (
      <div
        className="orch-pop"
        onClick={(e) => e.stopPropagation()}
        data-testid="board-orch-popover"
      >
        {/* header */}
        <div className="orch-pop-h">
          <span className="t">Orchestration</span>
          <span className="st">
            <span className="dot" />
            {mode === "auto" ? "dispatching" : "paused"}
          </span>
          {onClose && (
            <span className="x" onClick={onClose} style={{ marginLeft: "auto", cursor: "pointer" }}>
              <Icon name="close" size={14} />
            </span>
          )}
        </div>

        <div className="orch-pop-b">
          {/* mode seg — editable */}
          <div className="orch-mode">
            <div
              className={"orch-seg" + (mode === "auto" ? " on" : "")}
              onClick={() => setMode("auto")}
              data-testid="board-orch-mode-auto"
            >
              auto
            </div>
            <div
              className={"orch-seg" + (mode === "manual" ? " on" : "")}
              onClick={() => setMode("manual")}
              data-testid="board-orch-mode-manual"
            >
              manual
            </div>
          </div>

          {/* orchestrator_profile — editable dropdown */}
          <div className="orch-set">
            <div>
              <div className="ok">orchestrator profile</div>
              <div className="od">which agent profile drives the dispatcher</div>
            </div>
            <div className="ov">
              <select
                className="input"
                value={profile}
                onChange={(e) => setProfile(e.target.value)}
                data-testid="board-orch-profile"
              >
                <option value="">— none —</option>
                {profiles.map((p) => (
                  <option key={p.id || p} value={p.id || p}>
                    {p.name || p.id || p}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* default_assignee — editable dropdown */}
          <div className="orch-set">
            <div>
              <div className="ok">default assignee</div>
              <div className="od">agent assigned when no profile is specified</div>
            </div>
            <div className="ov">
              <select
                className="input"
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
                data-testid="board-orch-assignee"
              >
                <option value="">— none —</option>
                {assignees.map((a) => (
                  <option key={a.id || a} value={a.id || a}>
                    {a.name || a.id || a}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* auto_decompose — editable toggle */}
          <div className="orch-set">
            <div>
              <div className="ok">auto-decompose</div>
              <div className="od">orchestrator breaks complex tasks into subtasks</div>
            </div>
            <div className="ov">
              <div
                className={"tw-toggle" + (autoDecompose ? " on" : "")}
                onClick={() => setAutoDecompose((v) => !v)}
                data-testid="board-orch-autodecompose"
              >
                <span className="tw-switch" />
              </div>
            </div>
          </div>

          {/* auto_promote_children — editable toggle */}
          <div className="orch-set">
            <div>
              <div className="ok">auto-promote children</div>
              <div className="od">parent auto-advances when all children complete</div>
            </div>
            <div className="ov">
              <div
                className={"tw-toggle" + (autoPromote ? " on" : "")}
                onClick={() => setAutoPromote((v) => !v)}
                data-testid="board-orch-autopromote"
              >
                <span className="tw-switch" />
              </div>
            </div>
          </div>

          {/* read-only block from config.yaml */}
          <div className="orch-set" style={{ marginTop: 12, borderTop: "1px solid var(--border, #333)", paddingTop: 10 }}>
            <div style={{ width: "100%" }}>
              <div className="ok" style={{ marginBottom: 4 }}>
                config.yaml values <span style={{ color: "var(--fg-5, #888)", fontWeight: 400, fontSize: "0.78em" }}>read-only · config.yaml</span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "6px 16px" }}>
                <div className="orch-set" style={{ flexDirection: "column", gap: 0 }}>
                  <div className="od">tick interval</div>
                  <div className="ov num" data-testid="board-orch-ro-tick" style={{ color: "var(--fg-3, #aaa)" }}>
                    {roTick}{typeof roTick === "number" ? "s" : ""}
                  </div>
                </div>
                <div className="orch-set" style={{ flexDirection: "column", gap: 0 }}>
                  <div className="od">failure limit</div>
                  <div className="ov num" data-testid="board-orch-ro-failure" style={{ color: "var(--fg-3, #aaa)" }}>
                    {roFailure}
                  </div>
                </div>
                <div className="orch-set" style={{ flexDirection: "column", gap: 0 }}>
                  <div className="od">max in-flight</div>
                  <div className="ov num" data-testid="board-orch-ro-inflight" style={{ color: "var(--fg-3, #aaa)" }}>
                    {roInflight}
                  </div>
                </div>
                <div className="orch-set" style={{ flexDirection: "column", gap: 0 }}>
                  <div className="od">claim TTL</div>
                  <div className="ov num" data-testid="board-orch-ro-ttl" style={{ color: "var(--fg-3, #aaa)" }}>
                    {roTtl}{typeof roTtl === "number" ? "s" : ""}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* save */}
        <div className="orch-pop-f" style={{ padding: "10px 14px", display: "flex", justifyContent: "flex-end" }}>
          <button
            className="btn"
            onClick={handleSave}
            data-testid="board-action-orch-save"
          >
            Save
          </button>
        </div>
      </div>
    );
  }

  window.OrchPopover = OrchPopover;
})();
