// hal0 dashboard — Settings shell.
//
// Owns nav + section routing: `SettingsNav` renders the grouped rail
// (4 groups — GENERAL / MODELS & INFERENCE / SYSTEM / INTEGRATIONS), and each
// section is its own page module under `pages/<group>/`.
//
// Routing (GH #1438): `section` is a pure derivation of the `param` prop —
// no local state. The outer router (main.jsx) keeps `route` at "settings"
// across every #settings/<section> hash (it only changes on the ROUTE head,
// not the sub-path), so this component is never remounted when the section
// changes — only re-rendered with a new `param`. `onSelect` writes the hash
// (mirroring every other hash write in main.jsx) so the existing
// `hashchange` listener there is the single feedback loop for both
// directions: nav click → hash → re-render, and hash change → re-render,
// are the same code path.
//
// Legacy ids resolve through SECTION_ALIASES (settings-panel cleanup merged
// several pages), so e.g. #settings/general and #settings/health both land
// on Overview. An unrecognized or missing param falls back to "overview".
import { SettingsNav, VALID_IDS, SECTION_ALIASES } from './SettingsNav.jsx'

import { OverviewPage } from './pages/general/OverviewPage.jsx'
import { SecurityPage } from './pages/server/SecurityPage.jsx'
import { DoctorPage } from './pages/diagnostics/DoctorPage.jsx'
import { LoadedModelsPage } from './pages/models/LoadedModelsPage.jsx'
import { ModelDefaultsPage } from './pages/models/ModelDefaultsPage.jsx'
import { CapabilitiesPage } from './pages/capabilities/CapabilitiesPage.jsx'
import { HardwareRuntimesPage } from './pages/system/HardwareRuntimesPage.jsx'
import { StoragePage } from './pages/data/StoragePage.jsx'
import { MemoryPage } from './pages/data/MemoryPage.jsx'
import { UpdatesPage } from './pages/diagnostics/UpdatesPage.jsx'
import { AdvancedPage } from './pages/diagnostics/AdvancedPage.jsx'
import { SecretsPage } from './pages/integrations/SecretsPage.jsx'
import { AgentsBrainPage } from './pages/routing/AgentsBrainPage.jsx'

export function SettingsShell({ param }) {
  const resolved = SECTION_ALIASES[param] || param;
  const section = resolved && VALID_IDS.includes(resolved) ? resolved : "overview";

  const onSelect = (id) => {
    if (typeof window !== "undefined") window.location.hash = "#settings/" + id;
  };

  const renderPage = () => {
    switch (section) {
      case "overview": return <OverviewPage />;
      case "security": return <SecurityPage />;
      case "doctor": return <DoctorPage />;
      case "slots": return <LoadedModelsPage />;
      case "modeldefaults": return <ModelDefaultsPage />;
      case "capabilities": return <CapabilitiesPage />;
      case "hardware": return <HardwareRuntimesPage />;
      case "storage": return <StoragePage />;
      case "memory": return <MemoryPage />;
      case "updates": return <UpdatesPage />;
      case "advanced": return <AdvancedPage />;
      case "secrets": return <SecretsPage />;
      case "agents": return <AgentsBrainPage />;
      default: return <OverviewPage />;
    }
  };

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Configure</span>
        <h1>Settings</h1>
        <span className="vh-spacer" />
      </div>

      <div className="settings-layout">
        <SettingsNav section={section} onSelect={onSelect} />
        <div className="settings-content">
          {renderPage()}
        </div>
      </div>
    </div>
  );
}
