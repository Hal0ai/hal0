// hal0 dashboard — root App, hash routing, tweaks panel, keyboard shortcuts
const { useState: useStateA, useEffect: useEffectA } = React;

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
    ts: e.enqueued_at ? e.enqueued_at.slice(11, 19) : "—",
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

  const onFirstRunComplete = () => {
    setFrStage("pick");
    setFrBundle(null);
    go("dashboard");
  };

  // route → view
  const renderView = () => {
    switch (route) {
      case "dashboard":
        return (
          <DashboardView
            slots={HAL0_DATA.slots}
            onGo={go}
            showHero={tweaks.showHero && !heroDismissed}
            onDismissHero={() => setHeroDismissed(true)}
          />
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
      case "agent":
        // 0.4: hidden from the sidebar when memory is off. Stale deep links
        // / bookmarks bounce to dashboard rather than showing a dead-text page.
        // Guard: memoryStatusPending prevents redirect during the transient
        // loading window (useMemoryEnabled returns false while the /api/status
        // query is in-flight; we must not redirect until it settles).
        if (!memoryEnabled && !memoryStatusPending) {
          if (typeof window !== "undefined") window.location.hash = "#dashboard";
          return null;
        }
        if (memoryStatusPending) return null; // loading — render nothing briefly
        return <AgentView />;
      case "memory":
        // Same gate as #agent — Hindsight surface only exists when the
        // memory subsystem is live.
        if (!memoryEnabled && !memoryStatusPending) {
          if (typeof window !== "undefined") window.location.hash = "#dashboard";
          return null;
        }
        if (memoryStatusPending) return null;
        return <MemoryView param={param} />;
      case "profiles":  return <ProfilesView />;
      case "mcp":      return <McpView />;
      case "settings": return <SettingsView />;
      case "connections": return <ConnectionsView />;
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
        {!isFirstrun && <Sidebar route={route} onGo={go} />}
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
