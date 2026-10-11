// Grouped settings nav — settings-panel-cleanup consolidation. The old
// 8-group / 20-page tree collapsed to 4 groups / 15 pages:
//   - Overview (new, default landing) absorbs Health & Stats + General
//   - Loaded Models absorbs Library & Downloads
//   - Hardware & Runtimes merges Backend & GPU + Runtimes (both evidence UIs)
//   - Updates absorbs About
//   - Hardware Tuning (blocked stub, spec D4) removed until its lane lands
export const NAV_GROUPS = [
  {
    title: "GENERAL",
    items: [
      { id: "overview", label: "Overview" },
      { id: "security", label: "Security" },
      { id: "doctor", label: "Doctor" },
    ],
  },
  {
    title: "MODELS & INFERENCE",
    items: [
      { id: "slots", label: "Loaded Models" },
      { id: "modeldefaults", label: "Model Defaults" },
      { id: "capabilities", label: "AI Capabilities" },
    ],
  },
  {
    title: "SYSTEM",
    items: [
      { id: "hardware", label: "Hardware & Runtimes" },
      { id: "storage", label: "Storage" },
      { id: "memory", label: "Memory" },
      { id: "updates", label: "Updates" },
      { id: "advanced", label: "Advanced" },
    ],
  },
  {
    title: "INTEGRATIONS",
    items: [
      { id: "secrets", label: "Secrets" },
      { id: "accounts", label: "Connected Accounts" },
      { id: "agents", label: "Agent Chat" },
      { id: "realtime", label: "Realtime" },
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
              className={"nav-item" + (section === item.id ? " active" : "")}
              onClick={() => onSelect(item.id)}
            >
              {item.label}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
