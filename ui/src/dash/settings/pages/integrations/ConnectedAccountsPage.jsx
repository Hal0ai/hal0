// INTEGRATIONS ▸ Connected Accounts — OAuth for Hermes skills (/api/oauth).
//
// #2252 shipped ConnectedAccountsPanel inside the legacy ConnectionsView, but
// main.jsx redirects #connections to #slots/endpoints (the v0.5 nav dissolved
// that page), so nothing in the dashboard led to it (#2267). It is mounted
// here instead. The panel itself stays in connections.jsx next to the other
// engine-block panes and is read off `window`, the same way Slots ▸ Endpoints
// mounts LocalEndpointsPanel and Agent ▸ MCP mounts McpServersPanel.
export function ConnectedAccountsPage() {
  const Panel = typeof window !== "undefined" ? window.ConnectedAccountsPanel : null;
  return (
    <div className="conn">
      {Panel ? <Panel /> : null}
    </div>
  );
}
