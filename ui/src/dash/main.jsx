// hal0 dashboard — root App, hash routing, tweaks panel, keyboard shortcuts
const { useState: useStateA, useEffect: useEffectA } = React;

// ApprovalEntry.as_dict() emits ``enqueued_at`` as epoch seconds (a float),
// not an ISO string — calling .slice() on it threw "enqueued_at.slice is not
// a function" and black-screened the whole dashboard whenever an approval was
// queued. Format defensively: epoch float → UTC HH:MM:SS, tolerate an ISO
// string (mock fixtures) and missing values.
function fmtApprovalTs(v) {
  if (v == null) return "—";
  if (typeof v === "number") {
    const d = new Date(v * 1000);
    return Number.isNaN(d.getTime()) ? "—" : d.toISOString().slice(11, 19);
  }
  if (typeof v === "string" && v.length >= 19) return v.slice(11, 19);
  return "—";
}

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "slotVariant": "instrument",
  "showHero": true,
  "firstRunLayout": "grid",
  "personaPlacement": "composer-left"
}/*EDITMODE-END*/;

// ─── hash routing — supports #slots or #slots/<name> ───
// v0.3 adds "mcp" so the MCP page renders inside the main SPA chrome.
// We also accept "agents/mcp" as an alias so the canonical URL path stays
// readable (`/agents/mcp` from the spec). Any unknown head falls back to
// the dashboard.
const ROUTES = ["dashboard", "firstrun", "slots", "profiles", "models", "logs", "agent", "memory", "settings", "mcp", "connections"];
function parseRoute() {
  const raw = (window.location.hash || "#dashboard").replace(/^#/, "");
  const [path, qs] = raw.split("?");
  const parts = path.split("/");
  let head = parts[0];
  let rest = parts.slice(1);
  // alias: #agents/mcp → mcp (keeps the prototype's flat route map intact)
  if (head === "agents" && rest[0] === "mcp") {
    head = "mcp";
    rest = rest.slice(1);
  }
  // v0.5 nav: the Connections page was dissolved — its Local-endpoints section
  // moved to Slots ▸ Endpoints and its MCP section to Agent ▸ MCP. Redirect old
  // #connections deep-links to the Endpoints tab.
  if (head === "connections") {
    if (typeof window !== "undefined" && window.location.hash !== "#slots/endpoints") {
      window.location.hash = "#slots/endpoints";
    }
    head = "slots";
    rest = ["endpoints"];
  }
  // v0.5 nav: Profiles moved into a Slots tab. Redirect old #profiles links.
  if (head === "profiles") {
    if (typeof window !== "undefined" && window.location.hash !== "#slots/profiles") {
      window.location.hash = "#slots/profiles";
    }
    head = "slots";
    rest = ["profiles"];
  }
  // v0.3 PR-8: legacy #peers route redirects to the Peer memory
  // subsection inside the Memory tab on the agent route. We mutate
  // the hash here so deep links from older docs / bookmarks land on
  // the new shape.
  if (head === "peers") {
    if (typeof window !== "undefined") {
      const next = "#agent/memory?subsection=peer";
      if (window.location.hash !== next) {
        window.location.hash = next;
      }
    }
    head = "agent";
    rest = ["memory"];
  }
  const route = ROUTES.includes(head) ? head : "dashboard";
  const query = {};
  if (qs) {
    for (const kv of qs.split("&")) {
      if (!kv) continue;
      const [k, v = ""] = kv.split("=");
      query[decodeURIComponent(k)] = decodeURIComponent(v);
    }
  }
  return { route, param: rest.join("/") || null, query };
}

function App() {
  const [tweaks, setTweak] = useTweaks(TWEAK_DEFAULTS);
  const { active: activeBanners } = useBanners();
  const [{ route, param }, setRouteState] = useStateA(parseRoute());
  const [bellOpen, setBellOpen] = useStateA(false);
  const [frStage, setFrStage] = useStateA("pick");
  const [frBundle, setFrBundle] = useStateA(null);
  const [heroDismissed, setHeroDismissed] = useStateA(false);
  const [toast, setToast] = useStateA(null);
  const [composerState, setComposerState] = useStateA("idle");
  const [footerOpen, setFooterOpen] = useStateA(false);
  const [paletteOpen, setPaletteOpen] = useStateA(false);
  const [navOpen, setNavOpen] = useStateA(false);

  // 0.4 gate: the Agent route is reduced to the Memory tab, so it only
  // renders when the memory subsystem is live (HAL0_MEMORY_ENABLED, surfaced
  // via /api/status). Read through the window bridge to keep this strict
  // no-ES-imports prototype file within the dash/*.jsx contract — the bridge
  // is imported in main.tsx before main.jsx evaluates, so the ref is stable.
  const useMemEnabled = (typeof window !== "undefined" && window.__hal0UseMemoryEnabled) || null;
  const memoryEnabled = useMemEnabled ? useMemEnabled() : false;
  // Companion pending flag: useMemoryEnabled() returns false during loading
  // AND when truly disabled. Guard the redirect so we don't bounce on the
  // transient loading false — only redirect once the query has settled.
  const useMemEnabledPending = (typeof window !== "undefined" && window.__hal0UseMemoryEnabledPending) || null;
  const memoryStatusPending = useMemEnabledPending ? useMemEnabledPending() : true;

  // FirstRun auto-route (design D6): on the first dashboard visit of a fresh
  // install (/api/install/state.first_run), drop the operator straight into
  // the wizard instead of an empty dashboard. Read via the window bridge to
  // stay inside the no-ES-imports dash/*.jsx contract.
  const useInstallStateBridge = (typeof window !== "undefined" && window.__hal0UseInstallState) || null;
  const installState = useInstallStateBridge ? useInstallStateBridge() : { firstRun: false, pending: true };

  // Live approval queue — bridges installed by chrome.jsx (loaded before main.jsx).
  // TODO endpoints.ts (ui-sweep-b owns) — inline paths live in useAgents.ts hooks.
  const useApprovalListHook = (typeof window !== "undefined" && window.__hal0UseApprovalList) || null;
  const useApproveApprovalHook = (typeof window !== "undefined" && window.__hal0UseApproveApproval) || null;
  const useDenyApprovalHook = (typeof window !== "undefined" && window.__hal0UseDenyApproval) || null;

  const approvalQuery = useApprovalListHook ? useApprovalListHook() : { data: null };
  const approveApproval = useApproveApprovalHook ? useApproveApprovalHook() : null;
  const denyApproval = useDenyApprovalHook ? useDenyApprovalHook() : null;

  // Map ApprovalEntry → shape expected by ApprovalModal
  const approvalItems = (approvalQuery.data?.approvals ?? []).map(e => ({
    id: e.id,
    ts: fmtApprovalTs(e.enqueued_at),
    agent: e.client_id || "hermes",
    tool: e.tool,
    arg: e.args ? JSON.stringify(e.args).slice(0, 120) : "—",
  }));

  useEffectA(() => {
    const onHash = () => setRouteState(parseRoute());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // expose a global toast for any view to ping
  useEffectA(() => {
    window.__hal0Toast = (msg, kind = "info") => {
      const id = Date.now() + Math.random();
      setToast({ msg, kind, id });
      setTimeout(() => {
        setToast(t => (t && t.id === id) ? null : t);
      }, 4000);
    };
    return () => { delete window.__hal0Toast; };
  }, []);

  useEffectA(() => {
    // global keyboard shortcuts
    const onKey = (e) => {
      if (e.key === "Escape") { setBellOpen(false); setNavOpen(false); return; }
      const tgt = e.target;
      const typing = tgt && (tgt.tagName === "INPUT" || tgt.tagName === "TEXTAREA" || tgt.isContentEditable);
      if (typing) return;
      // "N" on /slots — open create-slot modal
      if (e.key === "n" && route === "slots") {
        e.preventDefault();
        window.dispatchEvent(new CustomEvent("hal0:create-slot"));
      }
      // ⌘K / Ctrl+K — command palette
      if (e.key === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setPaletteOpen(o => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [route]);

  useEffectA(() => {
    // bridge command-palette actions into App state
    const onApprovals = () => setBellOpen(true);
    window.addEventListener("hal0:open-approvals", onApprovals);
    return () => window.removeEventListener("hal0:open-approvals", onApprovals);
  }, []);

  // Close the mobile nav drawer on any route change (deep links, command
  // palette). Direct taps also close it via the onGo wrapper below.
  useEffectA(() => { setNavOpen(false); }, [route]);

  const go = (id) => {
    window.location.hash = "#" + id;
  };

  // FirstRun auto-route (design D6): redirect into the wizard on a fresh
  // install once /api/install/state has settled. Guard on `pending` so we
  // don't bounce during the transient loading window, and only when not
  // already on the firstrun route (so the manual Back/Skip can leave it).
  useEffectA(() => {
    if (!installState.pending && installState.firstRun && route !== "firstrun") {
      go("firstrun");
    }
  }, [installState.pending, installState.firstRun, route]);

  const onFirstRunComplete = () => {
    setFrStage("pick");
    setFrBundle(null);
    go("dashboard");
  };

  // route → view
  const renderView = () => {
    switch (route) {
      case "dashboard":
        // Dashboard overhaul (feat/dashboard-overhaul): the customizable
        // widget board (DashboardOverhaulView, dash-grid.jsx) replaces the
        // old static DashboardView. It owns its own edit-mode state and a
        // Customize/Done toggle in the hero, so it drops in without topbar
        // threading. Falls back to the legacy view if the global hasn't
        // registered (e.g. a stale bundle) so the route never blanks.
        return (
          typeof DashboardOverhaulView === "function" ? (
            <DashboardOverhaulView
              onGo={go}
              showHero={tweaks.showHero && !heroDismissed}
              onDismissHero={() => setHeroDismissed(true)}
            />
          ) : (
            <DashboardView
              slots={HAL0_DATA.slots}
              onGo={go}
              showHero={tweaks.showHero && !heroDismissed}
              onDismissHero={() => setHeroDismissed(true)}
            />
          )
        );
      case "firstrun":
        return (
          <FirstRunView
            frStage={frStage}
            setFrStage={setFrStage}
            frBundle={frBundle}
            setFrBundle={setFrBundle}
            onComplete={onFirstRunComplete}
            layout={tweaks.firstRunLayout}
          />
        );
      case "slots":
        return (
          <SlotsView
            slots={HAL0_DATA.slots}
            slotVariant={tweaks.slotVariant}
            slotParam={param}
            onGo={go}
          />
        );
      case "models":   return <ModelsView />;
      case "logs":     return <LogsView />;
      // v0.5 nav: Agent is a tabbed shell hosting Memory + MCP. #memory and
      // #mcp are kept as routes that resolve to their tab inside AgentView (so
      // MemoryView's internal #memory/<section> nav round-trips and old
      // deep-links still land on the right surface). The memory gate is
      // enforced inside AgentView — the Memory tab only renders when enabled.
      case "agent":
      case "memory":
      case "mcp":
        return <AgentView />;
      case "settings": return <SettingsView param={param} />;
      default:         return <div className="view">Not found.</div>;
    }
  };

  const isFirstrun = route === "firstrun";

  return (
    <>
      <div className={"app" + (isFirstrun ? " firstrun" : "")}>
        {/* v0.3 PR-8: approvals are now sourced from the sidebar agent
            rollup (PR-6 SidebarAgentBlock + live /api/agent/approvals
            poll). The topbar bell stays as a launcher for the modal
            view; its badge counter is suppressed until the live hook is
            bridged here in PR-10 (it's already alive in the sidebar). */}
        <TopBar
          route={route}
          onBell={() => setBellOpen(true)}
          onCmdK={() => setPaletteOpen(true)}
          onMenu={() => setNavOpen(true)}
          menuOpen={navOpen}
          approvals={approvalItems.length}
        />
        {!isFirstrun && <Sidebar route={route} param={param} onGo={go} />}
        <div className="main">
          <div className="view-banners">
            {/* Phase 2 of #322: UpdateBanner self-renders when the
                `useUpdateState()` hook reports a newer hal0 release than
                the current install. BannerStack continues to drive the
                Tweaks-panel demo toggles for every other banner state. */}
            <UpdateBanner />
            {/* Phase D8: self-renders while the GPU arbiter reports image
                mode (/api/comfyui/status arbiter.mode === "img"). */}
            <GpuImageModeBanner />
            <BannerStack scope="global" route={route} />
          </div>
          {renderView()}
        </div>
        {/*
          Phase 3 of #322: Footer is self-driven. The update chip reads
          `useUpdateState()` directly (no more hardcoded "v0.2.2 available"),
          and the journal pane streams /api/journal/stream. The legacy
          `updateAvailable` prop linkage to the BannerStack dismiss state
          is intentionally dropped — Phase 2's UpdateBanner owns its own
          dismiss memory and the chip is allowed to keep nagging until
          a new release is installed.
        */}
        <Footer
          expanded={footerOpen}
          onToggle={() => setFooterOpen(o => !o)}
        />
      </div>

      {!isFirstrun && (
        <NavDrawer
          open={navOpen}
          route={route}
          param={param}
          onGo={(id) => { setNavOpen(false); go(id); }}
          onClose={() => setNavOpen(false)}
          onCmdK={() => { setNavOpen(false); setPaletteOpen(true); }}
        />
      )}

      <ApprovalModal
        open={bellOpen}
        onClose={() => setBellOpen(false)}
        items={approvalItems}
        onApprove={id => approveApproval && approveApproval.mutate(id)}
        onDeny={id => denyApproval && denyApproval.mutate(id)}
      />

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <SlotActionBridge />

      {toast && (
        <div className={"hal0-toast " + (toast.kind || "info")} role="status" aria-live="polite">
          <span className="toast-dot" />
          <span className="toast-msg mono">{toast.msg}</span>
          <button className="toast-close" onClick={() => setToast(null)} aria-label="Dismiss">×</button>
        </div>
      )}

      <TweaksPanel title="hal0 dashboard — tweaks">
        <TweakSection title="Slot card">
          <TweakRadio
            label="Card variant"
            value={tweaks.slotVariant}
            onChange={v => setTweak("slotVariant", v)}
            options={[
              { value: "instrument", label: "Instrument" },
              { value: "list",       label: "Compact list" },
              { value: "spec",       label: "Spec card" },
            ]}
          />
        </TweakSection>

        <TweakSection title="Dashboard">
          <TweakToggle
            label="Hero strip"
            value={tweaks.showHero}
            onChange={v => setTweak("showHero", v)}
          />
          <TweakRadio
            label="Persona placement"
            value={tweaks.personaPlacement}
            onChange={v => setTweak("personaPlacement", v)}
            options={[
              { value: "composer-left", label: "In composer" },
              { value: "above",         label: "Above input" },
            ]}
          />
          <TweakSelect
            label="Composer state"
            value={composerState}
            onChange={v => setComposerState(v)}
            options={[
              { value: "idle",      label: "Idle (default)" },
              { value: "sending",   label: "Sending" },
              { value: "streaming", label: "Streaming" },
              { value: "swap",      label: "NPU swap in progress" },
              { value: "no-tools",  label: "No tool-calling LLM" },
              { value: "offline",   label: "runtime offline" },
            ]}
          />
        </TweakSection>

        <TweakSection title="FirstRun">
          <TweakRadio
            label="Tier layout"
            value={tweaks.firstRunLayout}
            onChange={v => setTweak("firstRunLayout", v)}
            options={[
              { value: "grid",  label: "Cards" },
              { value: "table", label: "Matrix" },
            ]}
          />
        </TweakSection>

        <TweakSection title="Banners" label="Banners">
          <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", padding: "6px 0", lineHeight: 1.5}}>
            Flip on any banner state to see the surface. They render scoped to the current route plus global.
          </div>
          {BANNER_CATALOG.map(b => (
            <BannerToggle key={b.id} id={b.id} label={b.heading} eyebrow={b.eyebrow} scope={b.scope} />
          ))}
        </TweakSection>

        <TweakSection title="Demo navigation">
          <TweakButton onClick={() => go("firstrun")}>Jump to FirstRun</TweakButton>
          <TweakButton onClick={() => go("dashboard")}>Jump to Dashboard</TweakButton>
        </TweakSection>
      </TweaksPanel>
    </>
  );
}

// Phase B1: wrap in TanStack QueryClientProvider installed by
// globals-install.ts. The prototype's BannerProvider stays — it's still
// the source of truth for in-progress demo banner toggles.
const Hal0QueryClientProvider = window.Hal0QueryClientProvider;
const hal0QueryClient = window.Hal0QueryClient;

ReactDOM.createRoot(document.getElementById("root")).render(
  <Hal0QueryClientProvider client={hal0QueryClient}>
    <BannerProvider>
      <App />
    </BannerProvider>
  </Hal0QueryClientProvider>
);

// ── tiny banner toggle for the Tweaks panel ──
function BannerToggle({ id, label, eyebrow, scope }) {
  const { active, toggle } = useBanners();
  const on = !!active[id];
  return (
    <label className="twk-row twk-row-h" style={{cursor: "pointer"}}>
      <div className="twk-lbl" style={{display: "flex", flexDirection: "column", gap: 2}}>
        <span style={{fontSize: 11.5}}>{label}</span>
        <span style={{fontSize: 9, color: "var(--twk-fg-3, var(--fg-4))", textTransform: "uppercase", letterSpacing: "0.06em"}}>{eyebrow} · scope: {scope}</span>
      </div>
      <input
        type="checkbox"
        checked={on}
        onChange={() => toggle(id)}
        style={{accentColor: "var(--accent)", width: 14, height: 14}}
      />
    </label>
  );
}
