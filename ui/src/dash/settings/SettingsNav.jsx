// Grouped settings nav (P3-ui split phase 1, spec (c) target tree: SERVER /
// MODELS / INFERENCE / ROUTING / OBSERVABILITY / DATA / DIAGNOSTICS /
// INTEGRATIONS). Replaces the old settings.jsx flat 12-item rail with a
// grouped one; every existing section keeps its original `id` so
// #settings/<id> deep links are unaffected — only the label/grouping
// changed for the four sections whose id was itself renamed in the old UI
// text ("Slots" → "Loaded Models", "Agents / Brain" unchanged).
//
// `disabled` entries render but refuse selection — used for the two pages
// gated on unbuilt backend lanes (Security → auth §1, Hardware Tuning →
// host-tuning §2/§21.1). Everything else here has a real page module; the
// stub pages (Library & Downloads, Health & Stats, Doctor) are visible and
// clickable — they're just not wired to real data yet (see each page's
// header comment).
export const NAV_GROUPS = [
  {
    title: "SERVER",
    items: [
      { id: "general", label: "General" },
      { id: "security", label: "Security", disabled: true, disabledReason: "coming with the auth lane (spec §1)" },
    ],
  },
  {
    title: "MODELS",
    items: [
      { id: "slots", label: "Loaded Models" },
      { id: "library", label: "Library & Downloads" },
      // ML-4 landed the runner-image registry + model-config taxonomy, so the
      // per-model launch-defaults page is now buildable (spec §7.1a/d).
      { id: "modeldefaults", label: "Model Defaults" },
    ],
  },
  {
    title: "INFERENCE",
    items: [
      // ML-4 landed the runner-image registry, unblocking the Backend & GPU
      // introspection surface (spec §7.1b, detected hw via /api/hardware).
      { id: "backend", label: "Backend & GPU" },
      { id: "hwtuning", label: "Hardware Tuning", disabled: true, disabledReason: "coming with the tuning lane (spec §2/§21.1)" },
      { id: "npu", label: "NPU" },
      { id: "voice", label: "Voice" },
      { id: "imagegen", label: "Image-gen" },
    ],
  },
  {
    title: "ROUTING",
    items: [
      { id: "agents", label: "Agents / Brain" },
    ],
  },
  {
    title: "OBSERVABILITY",
    items: [
      { id: "health", label: "Health & Stats" },
    ],
  },
  {
    title: "DATA",
    items: [
      { id: "storage", label: "Storage" },
      { id: "memory", label: "Memory" },
    ],
  },
  {
    title: "DIAGNOSTICS",
    items: [
      { id: "doctor", label: "Doctor" },
      { id: "updates", label: "Updates" },
      // D3 (post-R3 surface rework): runner/image evidence page. Grouped here
      // with Updates as the system & updates axis, far from the model editors.
      { id: "runtimes", label: "Runtimes" },
      { id: "advanced", label: "Advanced" },
      { id: "about", label: "About" },
    ],
  },
  {
    title: "INTEGRATIONS",
    items: [
      { id: "secrets", label: "Secrets" },
    ],
  },
];

// Flat id → item lookup + the ordered id list, derived once from NAV_GROUPS
// so the shell's VALID_IDS check and the nav's render loop can't drift.
export const VALID_IDS = NAV_GROUPS.flatMap(g => g.items.map(i => i.id));

export function SettingsNav({ section, onSelect }) {
  return (
    <div className="settings-nav">
      {NAV_GROUPS.map(group => (
        <div key={group.title} className="settings-nav-group">
          <div
            className="mono"
            style={{fontSize: 10, color: "var(--fg-4)", letterSpacing: "0.08em", padding: "10px 12px 4px"}}
          >
            {group.title}
          </div>
          {group.items.map(item => (
            <div
              key={item.id}
              className={"nav-item" + (section === item.id ? " active" : "") + (item.disabled ? " disabled" : "")}
              title={item.disabled ? item.disabledReason : undefined}
              onClick={() => { if (!item.disabled) onSelect(item.id); }}
              style={item.disabled ? {opacity: 0.5, cursor: "not-allowed"} : undefined}
            >
              {item.label}
              {item.disabled && <span style={{marginLeft: 6}}>{item.id === "hwtuning" ? "🔒" : "⛔"}</span>}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
