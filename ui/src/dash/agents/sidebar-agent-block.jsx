// hal0 dashboard — SidebarAgentBlock (v0.3 PR-6, master plan §4 PR-6).
//
// Compact agent status mounted in the left sidebar next to lemond's
// SidebarStatusBlock. Replaces the stats card that used to live in
// the Agents page Overview tab — the chat surface (PR-10) will take
// over the main pane.
//
// Render contract (W9 — honest minimal surface):
//   - service health dot (green when running, amber when unknown, red
//     when genuinely broken/down)
//   - agent name + status label
//   - active persona/profile name (from /api/agents/<id>/personas)
//   - [Memory →] → onGo("agent") (the dashboard's agent route, now the
//     Memory capability) plus an inline `hal0 chat` TUI hint. v0.4 dropped
//     the web chat surface, so the affordance no longer opens a dead chat.
//   - empty state when no agent installed: "Install Hermes" CTA → docs
//
// W9 simplification: the prior version rendered approvals / skills /
// memory-writes / MCP-pip rows. Several leaned on endpoints that often
// 404 and rendered as misleading "—" placeholders — and they sat next
// to a "broken" dot that was itself a false negative (driver.status()
// keyed off an env-file that may never exist even while the agent is
// up; fixed in J1). This widget is now a compact, honest health
// indicator only — it binds ONLY to fields the payload actually carries.
//
// Data:
//   useSidebarAgentRollup() (ui/src/api/hooks/useAgents.ts) — TanStack
//   Query, 5s refetch + revalidate-on-focus. Fields used: agentStatus
//   (from /api/agents `status`, truthful post-J1), agentId, personaName
//   (from /api/agents/<id>/personas). No invented fields.
//
// Mount point:
//   chrome.jsx Sidebar() — between `<div className="sb-spacer" />` and
//   `<SidebarStatusBlock />`. Visual variant via `.sb-status-agent`
//   class which sets a different accent color for the rollup dot so
//   the two blocks aren't visually identical.
//
// Window-globals pattern: matches the rest of dash/*.jsx — Object.assign
// at the bottom publishes the component so chrome.jsx finds it without
// a static import.

const _useSidebarAgentRollup = () => {
  // Hook lives on the global as a function returning the rollup. The
  // build-time shim in main.tsx imports the hook via ES modules and
  // republishes it on window — keeps the prototype's window-globals
  // contract intact.
  if (typeof window !== "undefined" && window.__hal0UseSidebarAgentRollup) {
    return window.__hal0UseSidebarAgentRollup();
  }
  return {
    installed: false,
    agentId: null,
    agentStatus: "not_installed",
    personaName: null,
    loading: true,
  };
};

// W9: dot tone is honest — green only when the agent is actually running
// (driver.status() now probes systemd + the agent port, post-J1), amber
// for an indeterminate "unknown", red for a genuine "broken".
const STATUS_LABEL = {
  running: "running",
  broken: "broken",
  unknown: "—",
  not_installed: "not installed",
};

// Terminal fallback shown in the Open-chat tooltip when there's no
// in-app deep link beyond the dashboard's own agent route.
const TUI_HINT = "Open the agent pane — or run `hal0 chat` in a terminal";

function SidebarAgentBlock({ onGo }) {
  const rollup = _useSidebarAgentRollup();
  const { installed, agentId, agentStatus, personaName, loading } = rollup;

  // Loading state: keep skeleton minimal — sidebar must NOT layout-shift
  // when the first 5s tick lands. Render the same row stack with em-dashes
  // so the dimensions match the populated state exactly.
  if (loading) {
    return (
      <div className="sb-status sb-status-agent">
        <div className="row">
          <span className="k">agent</span>
          <span className="v"><span className="dot" />…</span>
        </div>
      </div>
    );
  }

  // Empty state — no bundled agent installed. CTA links to docs which
  // installer instructions point at; PR-8 will wire the in-app install
  // flow but for v0.3 the docs path is the canonical onboarding.
  if (!installed) {
    return (
      <div className="sb-status sb-status-agent sb-status-empty">
        <div className="row">
          <span className="k">agent</span>
          <span className="v"><span className="dot" />not installed</span>
        </div>
        <div className="ln" />
        <div className="empty-cta mono">
          <a
            href="https://hal0.dev/docs/installer"
            target="_blank"
            rel="noopener noreferrer"
            className="btn sm"
            data-testid="sidebar-agent-install"
          >
            Install Hermes →
          </a>
        </div>
      </div>
    );
  }

  // Honest dot tone: green up only for a truly running agent, red down
  // for a genuine broken state. "unknown" gets the amber neutral class.
  const statusClass =
    agentStatus === "running"
      ? "up"
      : agentStatus === "broken"
        ? "down"
        : "warn";

  return (
    <div
      className="sb-status sb-status-agent"
      data-testid="sidebar-agent-block"
    >
      <div className="row">
        <span className="k">{agentId ?? "agent"}</span>
        <span
          className={"v " + statusClass}
          title={`agent ${agentId ?? ""} — ${STATUS_LABEL[agentStatus] ?? "unknown"}`}
        >
          <span className="dot" />
          {STATUS_LABEL[agentStatus] ?? "—"}
        </span>
      </div>
      {personaName && (
        <div className="row">
          <span className="k">profile</span>
          <span
            className="v"
            title={`active profile: ${personaName}`}
            data-testid="sidebar-agent-persona"
          >
            {personaName}
          </span>
        </div>
      )}
      <div className="ln" />
      <button
        className="nudge sb-status-cta"
        onClick={() => onGo && onGo("agent")}
        title={TUI_HINT}
        data-testid="sidebar-agent-open-memory"
      >
        Memory →
      </button>
      <div
        className="sb-status-tui mono"
        title="Run the agent chat in your terminal"
        data-testid="sidebar-agent-tui-hint"
      >
        Chat in terminal: <code>hal0 chat</code>
      </div>
    </div>
  );
}

// ─── Inject minimal scoped CSS for variants the dashboard.css base
//     doesn't carry (badge + sb-status-agent accent + button reset). The
//     base .sb-status rules in dashboard.css (lines 287–309) handle the
//     bulk of the layout; this delta keeps the new rules co-located with
//     the component while still respecting the design-token palette.
const _sidebarAgentBlockCss = `
.sb-status-agent .row .v.warn { color: var(--warn); }
.sb-status-empty .empty-cta {
  display: flex;
  justify-content: center;
  padding-top: 4px;
}
.sb-status-empty .empty-cta a { font-size: 11px; }
button.sb-status-cta {
  background: transparent;
  border: 0;
  padding: 0;
  text-align: left;
  width: 100%;
  font-family: inherit;
  font-size: inherit;
}
button.sb-status-cta:focus-visible {
  outline: 1px solid var(--accent);
  outline-offset: 2px;
}
.sb-status-tui {
  margin-top: 6px;
  font-size: 10.5px;
  color: var(--fg-4);
}
.sb-status-tui code {
  color: var(--accent);
  background: var(--accent-soft);
  padding: 0 4px;
  border-radius: 3px;
}
@media (max-width: 1024px) {
  .sb-status-agent .row { padding: 2px 0; }
  .sb-status-agent .ln { margin: 4px 0; }
}
`;

if (typeof document !== "undefined" && !document.getElementById("hal0-sidebar-agent-css")) {
  const s = document.createElement("style");
  s.id = "hal0-sidebar-agent-css";
  s.textContent = _sidebarAgentBlockCss;
  document.head.appendChild(s);
}

Object.assign(window, { SidebarAgentBlock });
