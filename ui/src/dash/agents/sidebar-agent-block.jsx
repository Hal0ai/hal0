// hal0 dashboard — SidebarAgentBlock (v0.3 PR-6, master plan §4 PR-6).
//
// Compact agent status mounted in the left sidebar next to lemond's
// SidebarStatusBlock. Replaces the stats card that used to live in
// the Agents page Overview tab — the chat surface (PR-10) will take
// over the main pane.
//
// Render contract (master plan §4 PR-6):
//   - service status dot (green / amber / red)
//   - active persona name (click no-op for v0.3 — picker lands in PR-8)
//   - approvals pending count (red badge if > 0)
//   - skills count
//   - memory writes count
//   - MCP server status pip (rolls up hal0-memory + hal0-admin)
//   - [Open chat] → onGo("agent") (the dashboard's agent route)
//   - empty state when no agent installed: "Install Hermes" CTA → docs
//
// Data:
//   useSidebarAgentRollup() (ui/src/api/hooks/useAgents.ts) — TanStack
//   Query, 5s refetch + revalidate-on-focus. Missing endpoints render
//   as "—" with a one-shot console.warn ("hal0.sidebar.endpoint_missing")
//   so the operator sees the gap on the network tab without console spam.
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
    approvalsPending: 0,
    skillsCount: null,
    memoryWrites: null,
    mcpPip: { state: "unknown", servers: [] },
    loading: true,
  };
};

const STATUS_LABEL = {
  running: "running",
  broken: "broken",
  unknown: "—",
  not_installed: "not installed",
};

const MCP_PIP_LABEL = {
  green: "ok",
  yellow: "degraded",
  red: "down",
  unknown: "—",
};

const MCP_PIP_COLOR = {
  green: "var(--ok)",
  yellow: "var(--warn)",
  red: "var(--err)",
  unknown: "var(--fg-4)",
};

function _fmtCount(n) {
  if (n == null) return "—";
  if (typeof n !== "number") return String(n);
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

function SidebarAgentBlock({ onGo }) {
  const rollup = _useSidebarAgentRollup();
  const {
    installed,
    agentStatus,
    personaName,
    approvalsPending,
    skillsCount,
    memoryWrites,
    mcpPip,
    loading,
  } = rollup;

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

  const statusClass =
    agentStatus === "running"
      ? "up"
      : agentStatus === "broken"
        ? "down"
        : "";

  const approvalsBadgeClass = approvalsPending > 0 ? "v warn" : "v";

  return (
    <div
      className="sb-status sb-status-agent"
      data-testid="sidebar-agent-block"
    >
      <div className="row">
        <span className="k">agent</span>
        <span className={"v " + statusClass}>
          <span className="dot" />
          {STATUS_LABEL[agentStatus] ?? "—"}
        </span>
      </div>
      <div className="row">
        <span className="k">persona</span>
        <span
          className="v"
          title={personaName ?? "no persona active"}
          data-testid="sidebar-agent-persona"
        >
          {personaName ?? "—"}
        </span>
      </div>
      <div className="ln" />
      <div className="row">
        <span className="k">approvals</span>
        <span
          className={approvalsBadgeClass}
          data-testid="sidebar-agent-approvals"
        >
          {approvalsPending > 0 && <span className="badge num">{approvalsPending}</span>}
          {approvalsPending === 0 && <b>0</b>}
        </span>
      </div>
      <div className="row">
        <span className="k">skills</span>
        <span className="v" data-testid="sidebar-agent-skills">
          {skillsCount != null ? <b>{_fmtCount(skillsCount)}</b> : "—"}
        </span>
      </div>
      <div className="row">
        <span className="k">memory</span>
        <span className="v" data-testid="sidebar-agent-memory">
          {memoryWrites != null ? <b>{_fmtCount(memoryWrites)}</b> : "—"}
        </span>
      </div>
      <div className="row">
        <span className="k">mcp</span>
        <span
          className="v"
          style={{ color: MCP_PIP_COLOR[mcpPip.state] ?? "var(--fg-4)" }}
          title={
            mcpPip.servers.length
              ? mcpPip.servers.map((s) => `${s.name}: ${s.state}`).join(" · ")
              : "no MCP servers registered"
          }
          data-testid="sidebar-agent-mcp"
        >
          <span className="dot" />
          {MCP_PIP_LABEL[mcpPip.state] ?? "—"}
        </span>
      </div>
      <div className="ln" />
      <button
        className="nudge sb-status-cta"
        onClick={() => onGo && onGo("agent")}
        data-testid="sidebar-agent-open-chat"
      >
        Open chat →
      </button>
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
.sb-status-agent .row .v .badge {
  background: var(--err-soft, var(--warn-soft));
  color: var(--err, var(--warn));
  padding: 1px 7px;
  border-radius: 9999px;
  font-family: var(--jbm);
  font-size: 10.5px;
  font-weight: 500;
}
.sb-status-agent .row .v.warn .badge {
  background: var(--err, var(--warn));
  color: var(--bg);
}
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
