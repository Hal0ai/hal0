// hal0 dashboard — Settings shell (P3-ui split phase 1).
//
// Replaces the 2598-line window-globals `settings.jsx` monolith
// (`SettingsView`) with a real ES module: this shell owns nav + section
// routing, `SettingsNav` renders the grouped rail (spec (c) target tree),
// and each section is its own page module under `pages/<group>/`.
//
// Routing: kept the original `useState(section)` approach rather than
// introducing real `/settings/:group/:page` routes — the dashboard's outer
// router (main.jsx) is still hash-based (`#settings`) and doesn't thread a
// sub-path into this view beyond the single `param` the old SettingsView
// already accepted, so real nested routes would mean touching the outer
// router too. That's more churn than this phase-1 "split settings.jsx into
// ESM pages, preserve behavior" pass calls for — noted as a deferred
// follow-up, not a limitation of this shell's design.
//
// `param` → initial-section behavior is preserved exactly: an unrecognized
// or missing param falls back to "general", same as the old SettingsView.
import { useState } from 'react'
import { SettingsNav, VALID_IDS } from './SettingsNav.jsx'

import { GeneralPage } from './pages/server/GeneralPage.jsx'
import { SecurityPage } from './pages/server/SecurityPage.jsx'
import { LoadedModelsPage } from './pages/models/LoadedModelsPage.jsx'
import { LibraryDownloadsPage } from './pages/models/LibraryDownloadsPage.jsx'
import { ModelDefaultsPage } from './pages/models/ModelDefaultsPage.jsx'
import { BackendGpuPage } from './pages/inference/BackendGpuPage.jsx'
import { HardwareTuningPage } from './pages/inference/HardwareTuningPage.jsx'
import { NpuPage } from './pages/inference/NpuPage.jsx'
import { VoicePage } from './pages/inference/VoicePage.jsx'
import { ImageGenPage } from './pages/inference/ImageGenPage.jsx'
import { AgentsBrainPage } from './pages/routing/AgentsBrainPage.jsx'
import { HealthStatsPage } from './pages/observability/HealthStatsPage.jsx'
import { StoragePage } from './pages/data/StoragePage.jsx'
import { MemoryPage } from './pages/data/MemoryPage.jsx'
import { DoctorPage } from './pages/diagnostics/DoctorPage.jsx'
import { UpdatesPage } from './pages/diagnostics/UpdatesPage.jsx'
import { RuntimesPage } from './pages/diagnostics/RuntimesPage.jsx'
import { AdvancedPage } from './pages/diagnostics/AdvancedPage.jsx'
import { AboutPage } from './pages/diagnostics/AboutPage.jsx'
import { SecretsPage } from './pages/integrations/SecretsPage.jsx'

export function SettingsShell({ param }) {
  const initialSection = param && VALID_IDS.includes(param) ? param : "general";
  const [section, setSection] = useState(initialSection);

  const renderPage = () => {
    switch (section) {
      case "general": return <GeneralPage />;
      case "security": return <SecurityPage />;
      case "slots": return <LoadedModelsPage />;
      case "library": return <LibraryDownloadsPage />;
      case "modeldefaults": return <ModelDefaultsPage />;
      case "backend": return <BackendGpuPage />;
      case "hwtuning": return <HardwareTuningPage />;
      case "npu": return <NpuPage />;
      case "voice": return <VoicePage />;
      case "imagegen": return <ImageGenPage />;
      case "agents": return <AgentsBrainPage />;
      case "health": return <HealthStatsPage />;
      case "storage": return <StoragePage />;
      case "memory": return <MemoryPage />;
      case "doctor": return <DoctorPage />;
      case "updates": return <UpdatesPage />;
      case "runtimes": return <RuntimesPage />;
      case "advanced": return <AdvancedPage />;
      case "about": return <AboutPage />;
      case "secrets": return <SecretsPage />;
      default: return <GeneralPage />;
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
        <SettingsNav section={section} onSelect={setSection} />
        <div className="settings-content">
          {renderPage()}
        </div>
      </div>
    </div>
  );
}
