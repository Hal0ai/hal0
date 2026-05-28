// hal0 v0.3 PR-8 — PluginsTab.
//
// Thin wrapper around PluginTabHost (PR-7). Lives so AgentView's tab
// switch can render <window.PluginsTab agentId="hermes" /> without
// caring about the plugin-host implementation.

function PluginsTab({ agentId = "hermes" } = {}) {
  if (!window.PluginTabHost) {
    return (
      <div className="card" style={{padding: 24, color: "var(--fg-3)"}}>
        Plugin host not available — extras still loading.
      </div>
    );
  }
  return (
    <div data-testid="plugins-tab">
      <window.PluginTabHost agentId={agentId} />
    </div>
  );
}

Object.assign(window, { PluginsTab });
