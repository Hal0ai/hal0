// hal0 dashboard — root App, hash routing, tweaks panel, keyboard shortcuts
const { useState: useStateA, useEffect: useEffectA } = React;

const TWEAK_DEFAULTS = /*EDITMODE-BEGIN*/{
  "slotVariant": "instrument",
  "npuVariant": "block",
  "showHero": true,
  "firstRunLayout": "grid",
  "personaPlacement": "composer-left"
}/*EDITMODE-END*/;

// ─── hash routing — supports #slots or #slots/<name> ───
// v0.3 adds "mcp" so the MCP page renders inside the main SPA chrome.
// We also accept "agents/mcp" as an alias so the canonical URL path stays
// readable (`/agents/mcp` from the spec). Any unknown head falls back to
// the dashboard.
const ROUTES = ["dashboard", "chat", "firstrun", "slots", "models", "backends", "logs", "agent", "settings", "mcp"];
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
  const [{ route, param, query }, setRouteState] = useStateA(parseRoute());
  const [chatState, setChatState] = useStateA("active");
  const [persona, setPersona] = useStateA("primary");
  const [bellOpen, setBellOpen] = useStateA(false);
  const [frStage, setFrStage] = useStateA("pick");
  const [frBundle, setFrBundle] = useStateA(null);
  const [heroDismissed, setHeroDismissed] = useStateA(false);
  const [toast, setToast] = useStateA(null);
  const [composerState, setComposerState] = useStateA("idle");
  const [footerOpen, setFooterOpen] = useStateA(false);
  const [paletteOpen, setPaletteOpen] = useStateA(false);

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
      if (e.key === "Escape") { setBellOpen(false); return; }
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
            chatState={chatState}
            setChatState={setChatState}
            slots={HAL0_DATA.slots}
            onGo={go}
            showHero={tweaks.showHero && !heroDismissed}
            onDismissHero={() => setHeroDismissed(true)}
          />
        );
      case "chat":
        return (
          <ChatView
            slots={HAL0_DATA.slots}
            persona={persona}
            setPersona={setPersona}
            personaPlacement={tweaks.personaPlacement}
            composerState={composerState}
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
            npuVariant={tweaks.npuVariant}
            slotParam={param}
          />
        );
      case "models":   return <ModelsView />;
      case "backends": return <BackendsView />;
      case "logs":     return <LogsView />;
      case "agent":    return <AgentView />;
      case "mcp":      return <McpView />;
      case "settings": return <SettingsView />;
      default:         return <div className="view">Not found.</div>;
    }
  };

  const isFirstrun = route === "firstrun";
  const isPopout = route === "chat" && query.popout === "1";

  // Popout chat window: render only the ChatView, no chrome. Same origin
  // + same hash routing so reload still works.
  if (isPopout) {
    return (
      <div className="app popout">
        <div className="main popout-main">
          <ChatView
            slots={HAL0_DATA.slots}
            persona={persona}
            setPersona={setPersona}
            personaPlacement={tweaks.personaPlacement}
            composerState={composerState}
            popout
          />
        </div>
      </div>
    );
  }

  return (
    <>
      <div className={"app" + (isFirstrun ? " firstrun" : "")}>
        <TopBar
          route={route}
          onBell={() => setBellOpen(true)}
          onCmdK={() => setPaletteOpen(true)}
          approvals={HAL0_DATA.approvals.length}
        />
        {!isFirstrun && <Sidebar route={route} onGo={go} />}
        <div className="main">
          <div className="view-banners">
            {/* Phase 2 of #322: UpdateBanner self-renders when the
                `useUpdateState()` hook reports a newer hal0 release than
                the current install. BannerStack continues to drive the
                Tweaks-panel demo toggles for every other banner state. */}
            <UpdateBanner />
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

      {!isFirstrun && <BottomTabs route={route} onGo={go} />}

      <ApprovalModal
        open={bellOpen}
        onClose={() => setBellOpen(false)}
        items={HAL0_DATA.approvals}
      />

      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />

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

        <TweakSection title="NPU trio">
          <TweakRadio
            label="Layout"
            value={tweaks.npuVariant}
            onChange={v => setTweak("npuVariant", v)}
            options={[
              { value: "block",   label: "Block (sub-rows)" },
              { value: "reactor", label: "Reactor diagram" },
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
              { value: "offline",   label: "lemond offline" },
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
          <TweakButton onClick={() => setBellOpen(true)}>Open approval inbox</TweakButton>
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
