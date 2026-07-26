// hal0 dashboard — reusable primitives
// Modal, Drawer, ConfirmDialog, Banner, BannerStack, Dropdown menu

import { useUpdateState, useUpdateApply, useUpdateJob } from '@/api/hooks/useUpdates'
import { useInstallState, bundleNameOr } from '@/api/hooks/useInstallState'
import { useComfyui } from '@/api/hooks/useComfyui'

const { useState: useStateP, useEffect: useEffectP, useRef: useRefP, createContext: createContextP, useContext: useContextP } = React;

// ─── Shared leaf utilities (single source of truth for the dash editors) ───
// Slug/name rule — mirrors the API regex ^[a-z0-9][a-z0-9_-]{0,31}$. Consumed
// by the Profiles/Stacks drawers + import dialogs (was duplicated per-file).
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;

// Toast shim — dash modules fire user-facing toasts through the global
// installed by chrome.jsx. Safe no-op when it isn't present (SSR/tests).
function toast(msg, kind = "info") {
  if (typeof window !== "undefined" && window.__hal0Toast) window.__hal0Toast(msg, kind);
}

// ─── useFocusTrap — shared focus management for modal surfaces ──────────────
// On open: remembers the previously-focused element and, unless something
// inside the container already holds focus (e.g. an autoFocus input), moves
// focus to the container (which must carry tabIndex=-1). While open: Tab /
// Shift+Tab cycle within the container's focusables. On close/unmount: focus
// returns to the previously-focused element. Used by Modal, Drawer, FormDrawer
// so the role="dialog" aria-modal="true" contract is actually honoured.
const FOCUSABLE_SEL =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
function useFocusTrap(ref, open) {
  useEffectP(() => {
    if (!open) return;
    const node = ref.current;
    if (!node) return;
    const prev = document.activeElement;
    // Initial focus: honour an existing autoFocus inside the container;
    // otherwise focus the container itself so the surface is announced
    // without stealing focus from a specific control.
    if (!node.contains(document.activeElement)) {
      try { node.focus(); } catch { /* jsdom / detached node */ }
    }
    const onKey = (e) => {
      if (e.key !== "Tab") return;
      const items = Array.from(node.querySelectorAll(FOCUSABLE_SEL))
        .filter(el => el.offsetParent !== null || el === document.activeElement);
      if (items.length === 0) { e.preventDefault(); node.focus(); return; }
      const first = items[0];
      const last = items[items.length - 1];
      const active = document.activeElement;
      if (e.shiftKey) {
        if (active === first || !node.contains(active)) { e.preventDefault(); last.focus(); }
      } else if (active === last || !node.contains(active)) {
        e.preventDefault(); first.focus();
      }
    };
    node.addEventListener("keydown", onKey);
    return () => {
      node.removeEventListener("keydown", onKey);
      // Return focus only if it's still inside the closing surface — avoid
      // yanking focus if the user has already clicked elsewhere.
      if (prev && typeof prev.focus === "function" && node.contains(document.activeElement)) {
        try { prev.focus(); } catch { /* element gone */ }
      }
    };
  }, [open, ref]);
}

// ─── DiscardGuardDialog — shared unsaved-changes confirm ────────────────────
// Dialog-based replacement for the window.confirm the Modal/Drawer/FormDrawer
// `dirty` prop used to fire (same ConfirmDialog idiom the slot drawer adopted
// in slot-modals.jsx). `message` is the caller's `confirmDiscard` copy, kept
// as the dialog body so the existing prop API stays backward compatible.
function DiscardGuardDialog({ open, message, onCancel, onDiscard }) {
  return (
    <ConfirmDialog
      open={open}
      onCancel={onCancel}
      onConfirm={onDiscard}
      title="Unsaved changes"
      message={message || "Discard unsaved changes?"}
      confirmLabel="Discard"
      cancelLabel="Keep editing"
    />
  );
}

// ─── Portal-less Modal ────────────────────────────────────────────────────
// Click backdrop or Esc to close. Captures + restores focus on close and traps
// Tab within the shell. Width auto-sized. Pass `dirty` (+ optional
// `confirmDiscard`) to guard the dismiss paths against unsaved changes —
// the guard confirms through the shared DiscardGuardDialog (state-driven),
// not window.confirm.
function Modal({ open, onClose, title, eyebrow, children, foot, width = 640, dismissable = true, dirty = false, confirmDiscard = "Discard unsaved changes?" }) {
  const overlayRef = useRefP(null);
  const shellRef = useRefP(null);
  const [discardOpen, setDiscardOpen] = useStateP(false);
  useEffectP(() => { if (!open) setDiscardOpen(false); }, [open]);
  const requestClose = () => {
    if (dirty) { setDiscardOpen(true); return; }
    onClose();
  };
  useEffectP(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape" && dismissable) requestClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, dismissable, onClose, dirty]);
  useFocusTrap(shellRef, open);
  if (!open) return null;
  return (
    <>
    <div
      className="modal-backdrop"
      ref={overlayRef}
      onMouseDown={(e) => { if (dismissable && e.target === overlayRef.current) requestClose(); }}
    >
      <div className="modal-shell" ref={shellRef} tabIndex={-1} role="dialog" aria-modal="true" style={{ maxWidth: width }} onMouseDown={(e) => e.stopPropagation()}>
        {(title || eyebrow) && (
          <div className="modal-h">
            {eyebrow && <div className="modal-h-eye mono">{eyebrow}</div>}
            {title && <h2 className="mono">{title}</h2>}
            {dismissable && (
              <button className="modal-close" onClick={requestClose} aria-label="Close">{Icons.close}</button>
            )}
          </div>
        )}
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot mono">{foot}</div>}
      </div>
    </div>
    {dirty && (
      <DiscardGuardDialog
        open={discardOpen}
        message={confirmDiscard}
        onCancel={() => setDiscardOpen(false)}
        onDiscard={() => { setDiscardOpen(false); onClose(); }}
      />
    )}
    </>
  );
}

// ─── Right-side Drawer ────────────────────────────────────────────────────
// Captures + restores focus and traps Tab within the drawer (honours
// role="dialog" aria-modal). Pass `dirty` (+ optional `confirmDiscard`) to
// guard Esc/backdrop/close against unsaved changes; `dismissable={false}`
// disables backdrop dismissal.
function Drawer({ open, onClose, title, eyebrow, children, foot, width = 520, headRight, dirty = false, dismissable = true, confirmDiscard = "Discard unsaved changes?" }) {
  const shellRef = useRefP(null);
  const [discardOpen, setDiscardOpen] = useStateP(false);
  useEffectP(() => { if (!open) setDiscardOpen(false); }, [open]);
  const requestClose = () => {
    if (dirty) { setDiscardOpen(true); return; }
    onClose();
  };
  useEffectP(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") requestClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, onClose, dirty]);
  useFocusTrap(shellRef, open);
  return (
    <>
      <div
        className={"drawer-backdrop" + (open ? " open" : "")}
        onClick={() => { if (dismissable) requestClose(); }}
      />
      {dirty && (
        <DiscardGuardDialog
          open={discardOpen}
          message={confirmDiscard}
          onCancel={() => setDiscardOpen(false)}
          onDiscard={() => { setDiscardOpen(false); onClose(); }}
        />
      )}
      <aside
        ref={shellRef}
        tabIndex={-1}
        className={"drawer" + (open ? " open" : "")}
        style={{ width }}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
      >
        <div className="drawer-h">
          {eyebrow && <div className="modal-h-eye mono">{eyebrow}</div>}
          {title && <h2 className="mono">{title}</h2>}
          {headRight && <div className="drawer-h-right">{headRight}</div>}
          <button className="modal-close" onClick={requestClose} aria-label="Close">{Icons.close}</button>
        </div>
        <div className="drawer-body">{children}</div>
        {foot && <div className="drawer-foot mono">{foot}</div>}
      </aside>
    </>
  );
}

// ─── ConfirmDialog (recoverable + destructive) ───────────────────────────
// Exported for real ESM consumers (P3-ui split, settings/ pages) in addition
// to the window-globals publish below, which the not-yet-migrated dash/*.jsx
// files still rely on.
export function ConfirmDialog({ open, onCancel, onConfirm, title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", destructive = false, typeToConfirm = null }) {
  const [typed, setTyped] = useStateP("");
  useEffectP(() => { if (open) setTyped(""); }, [open]);
  const canConfirm = !typeToConfirm || typed === typeToConfirm;
  return (
    <Modal
      open={open}
      onClose={onCancel}
      eyebrow={destructive ? "Destructive · cannot be undone" : null}
      title={title}
      width={520}
      foot={
        <>
          <span style={{color: "var(--fg-4)"}}>{destructive ? "This action is permanent." : "You can undo this later."}</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onCancel}>{cancelLabel}</button>
            <button
              className={"btn sm" + (destructive ? " danger" : "")}
              onClick={onConfirm}
              disabled={!canConfirm}
              style={destructive ? {background: "var(--err)", borderColor: "var(--err)", color: "#0a0a0a"} : {}}
            >{confirmLabel}</button>
          </span>
        </>
      }
    >
      <div style={{fontSize: 13, color: "var(--fg-2)", lineHeight: 1.6, marginBottom: typeToConfirm ? 16 : 0}}>{message}</div>
      {typeToConfirm && (
        <div>
          <div className="mono" style={{fontSize: 11, color: "var(--fg-4)", marginBottom: 6}}>
            Type <span style={{color: "var(--err)"}}>{typeToConfirm}</span> to confirm:
          </div>
          <input
            className="input mono"
            value={typed}
            onChange={e => setTyped(e.target.value)}
            placeholder={typeToConfirm}
            autoFocus
          />
        </div>
      )}
    </Modal>
  );
}

// ─── Banner ───────────────────────────────────────────────────────────────
// Reusable shell: icon + heading + body + actions + dismiss × · amber/red tones.
function Banner({ kind = "warn", heading, body, actions, onDismiss, eyebrow }) {
  return (
    <div className={"banner banner-" + kind} role={kind === "err" ? "alert" : "status"}>
      <div className="banner-ic">
        {kind === "err" ? Icons.warn : kind === "info" ? Icons.bell : Icons.warn}
      </div>
      <div className="banner-content">
        {eyebrow && <div className="banner-eye mono">{eyebrow}</div>}
        {heading && <div className="banner-heading mono">{heading}</div>}
        {body && <div className="banner-body">{body}</div>}
        {actions && <div className="banner-actions">{actions}</div>}
      </div>
      {onDismiss && (
        <button className="banner-dismiss" onClick={onDismiss} aria-label="Dismiss">{Icons.close}</button>
      )}
    </div>
  );
}

// ─── Banner registry (global) ────────────────────────────────────────────
// Views call useBanners() to read; demo controls in Tweaks call window.__hal0Banners.toggle(id).
const BannerContext = createContextP({ active: {}, toggle: () => {} });
function BannerProvider({ children }) {
  const [active, setActive] = useStateP({});
  const toggle = (id, on) => setActive(a => ({ ...a, [id]: on === undefined ? !a[id] : on }));
  useEffectP(() => {
    window.__hal0Banners = { toggle, get: () => active };
    return () => { delete window.__hal0Banners; };
  }, [active]);
  return <BannerContext.Provider value={{ active, toggle }}>{children}</BannerContext.Provider>;
}
function useBanners() {
  return useContextP(BannerContext);
}

// ─── Banner template substitution ────────────────────────────────────────
// Banner catalog entries embed `{bundleName}` (and similar `{key}` slots)
// so the heading/body can carry live state without per-banner branching.
// Substituted at render time from the install/firstrun stores so a fresh
// `/api/install/state` keeps banner copy in sync (issue #214).
function _interpolateBannerString(s, vars) {
  if (typeof s !== "string") return s;
  return s.replace(/\{(\w+)\}/g, (m, k) => (vars && vars[k] != null ? String(vars[k]) : m));
}

// ─── BannerStack — renders the active banners for a given view scope ─────
function BannerStack({ scope = "global", route, vars: extraVars }) {
  const { active, toggle } = useBanners();
  const installQuery = useInstallState();
  // Merge install-derived defaults (bundleName) with caller-supplied vars so
  // a specific view (FirstRun confirm) can override with an in-flight pick.
  const vars = { bundleName: bundleNameOr(installQuery.data), ...(extraVars || {}) };
  const items = BANNER_CATALOG.filter(b =>
    active[b.id] && (
      b.scope === "global" ||
      b.scope === scope ||
      (route && b.scope === route)
    )
  );
  if (!items.length) return null;
  return (
    <div className="banner-stack">
      {items.map(b => (
        <Banner
          key={b.id}
          kind={b.kind}
          eyebrow={_interpolateBannerString(b.eyebrow, vars)}
          heading={_interpolateBannerString(b.heading, vars)}
          body={_interpolateBannerString(b.body, vars)}
          actions={b.actions && b.actions.map((a, i) => (
            <button
              key={i}
              className={a.primary ? "btn sm" : "btn ghost sm"}
              onClick={() => {
                if (a.onClick) { a.onClick(); return; }
                window.__hal0Toast && window.__hal0Toast(`${a.label} — stubbed`, "info");
              }}
            >{a.label}</button>
          ))}
          onDismiss={b.dismissable !== false ? () => toggle(b.id, false) : null}
        />
      ))}
    </div>
  );
}

// ─── Banner catalog — every state the brief calls out ────────────────────
// Issue #339: catalog entries are static demo copy for the Tweaks panel.
// Use MOCK_VERSION so a version literal never lands in the production
// bundle (the real UpdateBanner reads useUpdateState instead).
const MOCK_VERSION = "<demo>";
const BANNER_CATALOG = [
  // Global
  {
    id: "update-available", scope: "global", kind: "info",
    eyebrow: "Update available",
    heading: `hal0 ${MOCK_VERSION} is available`,
    body: "Includes one FLM CHANGELOG note. Update expects a brief outage during the hal0-api restart.",
    actions: [
      { label: "Update now", primary: true },
      { label: "Read release notes" },
      { label: "Remind me later" },
    ],
  },
  {
    // Live surface is <GpuImageModeBanner> (reads the /api/comfyui/status
    // arbiter block); this entry keeps the Tweaks-panel demo toggle working.
    id: "gpu-image-mode", scope: "global", kind: "info",
    eyebrow: "GPU · arbiter",
    heading: "GPU: image mode",
    body: "LLM slots are stopped while image generation holds the GPU — they restore automatically after idle.",
    actions: [
      { label: "View slots", primary: true, onClick: () => window.location.hash = "#slots" },
    ],
  },
  {
    // D5 (post-R3 surface rework): flag-migration refusal. The live surface is
    // <MigrationBanner> (reads useMigrationReport); this catalog entry is the
    // Tweaks-panel demo toggle, and its Resolve fires the window event
    // <MigrationResolveHost> listens for to open the resolution view off the
    // demo report. Carries the same HAL0 id as the doctor diagnosis.
    id: "migration-unresolved", scope: "global", kind: "warn",
    eyebrow: "Migration · needs resolution",
    heading: "2 models need flag-migration resolution",
    body: "Slots shared them with different launch overrides. They keep their old behavior until you resolve. (HAL0-0142)",
    actions: [
      { label: "Resolve", primary: true, onClick: () => window.dispatchEvent(new CustomEvent("hal0:migration-resolve")) },
      { label: "Snooze" },
    ],
  },
  // Slots view
  {
    id: "npu-swap", scope: "slots", kind: "warn",
    eyebrow: "NPU trio · swap in progress",
    heading: "Swapping NPU chat: gemma3:1b → llama-3.2-3b-npu",
    body: "Voice + embed paused for ~14s while FLM restarts. Coresident slots will resume automatically.",
    dismissable: false,
  },
  {
    id: "catalog-drift", scope: "slots", kind: "warn",
    eyebrow: "Catalog · drift",
    heading: "registry.toml is newer than server_models.json",
    body: "Models added or removed in registry.toml won't appear until you sync. Sync will restart the affected slots.",
    actions: [
      { label: "Sync now", primary: true },
      { label: "Diff catalog" },
    ],
  },
  {
    id: "all-slots-disabled", scope: "slots", kind: "warn",
    eyebrow: "Slots · no active targets",
    heading: "All slots are disabled",
    body: "hal0 has no active inference targets. Enable at least one slot to use chat, embed, transcription, etc.",
  },
  {
    id: "model-missing", scope: "slots", kind: "err",
    eyebrow: "Slot · file not found",
    heading: "Model file missing on disk for slot primary",
    body: <span>Expected: <span className="mono">/var/lib/hal0/models/qwen3.6-27b-mtp-q4_k_m.gguf</span>. The file was removed externally. Delete the slot or re-pull the model.</span>,
    actions: [
      { label: "Re-pull from /models", primary: true },
      { label: "Delete slot" },
    ],
  },

  // Models view
  {
    id: "hf-gated", scope: "models", kind: "warn",
    eyebrow: "HuggingFace · gated repo",
    heading: "HF_TOKEN required to pull this model",
    body: "The repository requires authentication. Add HF_TOKEN in Settings, then re-attempt the download.",
    actions: [
      { label: "Add HF token", primary: true },
    ],
  },
  {
    id: "disk-full", scope: "models", kind: "err",
    eyebrow: "Disk · ENOSPC",
    heading: "Disk full — downloads paused",
    body: <span>Only <span className="mono">2.1 GB</span> free on <span className="mono">/var</span>. Free at least <span className="mono">38 GB</span> to resume.</span>,
    actions: [
      { label: "Pause all", primary: true },
      { label: "Resume after freeing space" },
    ],
  },

  // Logs view
  {
    id: "ws-disconnect", scope: "logs", kind: "err",
    eyebrow: "Stream · disconnected",
    heading: "Lost connection to the journal stream — logs are paused",
    body: "The /api/journal/stream connection closed unexpectedly. Reconnecting in 5s…",
    actions: [
      { label: "Reconnect now", primary: true },
    ],
  },

  // FirstRun
  {
    id: "fr-reentered", scope: "firstrun", kind: "warn",
    eyebrow: "Picker · post-install",
    heading: "You currently have {bundleName} installed",
    body: "Picking another tier will replace your slot selections. Models already on disk won't be re-downloaded.",
  },
  {
    id: "fr-ram-low", scope: "firstrun", kind: "warn",
    eyebrow: "Hardware · low RAM",
    heading: "Detected RAM is below the Lite minimum (16 GB)",
    body: "hal0 needs at least 16 GB of unified RAM to load any bundled chat model. You can still install hal0 — Settings → Storage can point at an external model store.",
  },

  // Agent
  {
    id: "cognee-degraded", scope: "agent", kind: "warn",
    eyebrow: "Memory · degraded",
    heading: "Cognee memory DB is in degraded mode",
    body: "Reads are working; writes are failing. Recent records may be missing. Restart Cognee or inspect logs.",
    actions: [
      { label: "Restart Cognee", primary: true },
      { label: "View logs" },
    ],
  },
  {
    id: "no-agent", scope: "agent", kind: "info",
    eyebrow: "Agent · not installed",
    heading: "No bundled agent installed yet",
    body: "Install Hermes (service) or pi-coder (CLI) to enable approval flows, memory writes, and persona dispatch.",
    actions: [
      { label: "Install Hermes", primary: true },
    ],
  },

  // Dashboard
  {
    id: "post-install", scope: "dashboard", kind: "info",
    eyebrow: "FirstRun · just installed",
    heading: "Welcome to hal0 — {bundleName} is loaded",
    body: <span>Try a message below. <span className="mono" style={{color: "var(--fg)"}}>primary</span> is your default chat persona. The persona dropdown lets you swap to <span className="mono">coder</span> or the NPU <span className="mono">agent</span>.</span>,
    actions: [
      { label: "Take the tour", primary: true, onClick: () => window.dispatchEvent(new CustomEvent("hal0:tour-start")) },
      { label: "Dismiss" },
    ],
  },
  {
    id: "skip-path", scope: "slots", kind: "info",
    eyebrow: "Slots · skip-path",
    heading: "Six seeded slots, none configured",
    body: <span>You skipped the bundle picker. Each seeded slot below has a <b>Configure</b> button that opens the Create-slot modal pre-filled. Or run <span className="mono">hal0 setup</span> in your terminal to configure a bundle.</span>,
    actions: [],
  },
];

// ─── UpdateBanner — live-data wrapper around <Banner> ───────────────────
// Phase 2 of epic #322: replaces the prototype's hardcoded
// "hal0 v0.2.2 is available" catalog entry with a live read of
// `useUpdateState()`. Self-hides when there's no newer release than the
// current install, and tracks its own dismiss state so the banner stays
// out until the next session even if the hook continues to report an
// available upgrade.
//
// The catalog entry of the same id is kept around so the Tweaks panel
// can still preview-toggle a static demo banner, but the source of truth
// for the real surface is this component.
// Public changelog on the marketing site (same URL the Settings → Updates
// surface links to). Kept as a bare page URL — the site owns per-version
// anchoring, and an unknown hash degrades gracefully to the page top.
const HAL0_CHANGELOG_URL = "https://hal0.dev/changelog";

function UpdateBanner() {
  const { data: state } = useUpdateState();
  const [dismissed, setDismissed] = useStateP(false);
  const applyM = useUpdateApply();
  const [jobId, setJobId] = useStateP(null);
  const { job, terminal } = useUpdateJob(jobId);

  // Fire one terminal toast when the self-update job resolves. hal0-api
  // restarts mid-apply, so useUpdateJob tolerates transient poll failures;
  // we only react once it lands on applied/failed.
  useEffectP(() => {
    if (!terminal || !job) return;
    if (job.state === "applied") {
      toast(`hal0 ${job.version || ""} applied — reload to load the new dashboard`, "ok");
    } else if (job.state === "failed") {
      toast(`Update failed: ${job.error || "see server logs"}`, "err");
    }
  }, [terminal, job]);

  const hal0 = state && state.hal0;
  const current = hal0 && hal0.current;
  const available = hal0 && hal0.available;
  const hasUpdate = !!available && available !== current;
  if (!hasUpdate || dismissed) return null;
  const channel = (hal0 && hal0.channel) || "stable";

  // Busy across both phases: the POST /apply round-trip and the queued job
  // running to a terminal state.
  const updating = applyM.isPending || (!!jobId && !terminal);
  const onUpdateNow = () => {
    if (updating) return;
    applyM.mutate(undefined, {
      onSuccess: (j) => {
        setJobId(j && j.id);
        toast(`Updating hal0 to ${available}… expect a brief outage during restart`, "info");
      },
      onError: (e) => toast(`Couldn't start update: ${(e && e.message) || "see server logs"}`, "err"),
    });
  };

  return (
    <Banner
      kind="info"
      eyebrow="Update available"
      heading={`hal0 ${available} available`}
      body={
        <span>
          New release on the <span className="mono">{channel}</span> channel.
          Update expects a brief outage during the hal0-api restart.
        </span>
      }
      actions={
        <>
          <button
            className="btn sm"
            onClick={onUpdateNow}
            disabled={updating}
          >{updating ? "Updating…" : "Update now"}</button>
          <a
            className="btn ghost sm"
            href={HAL0_CHANGELOG_URL}
            target="_blank"
            rel="noopener noreferrer"
          >Read release notes ↗</a>
        </>
      }
      onDismiss={() => setDismissed(true)}
    />
  );
}

// ─── FirstRunBanner — passive nudge to run `hal0 setup` ─────────────────
// Task 7.1: shows when /api/install/state returns first_run===true.
// Passive only — no auto-route. Dismiss is per-session (resets on reload).
function FirstRunBanner() {
  const q = useInstallState();
  const [dismissed, setDismissed] = useStateP(false);
  if (dismissed || !q.data?.first_run) return null;
  return (
    <Banner
      kind="info"
      eyebrow="Setup · first run"
      heading="No models configured yet"
      body={<span>Run <span className="mono">hal0 setup</span> in your terminal to add models, apps, and agents.</span>}
      onDismiss={() => setDismissed(true)}
    />
  );
}

// ─── GpuImageModeBanner — live-data wrapper around <Banner> ─────────────
// Phase D8: mirrors the UpdateBanner pattern — the catalog entry of the
// same id ("gpu-image-mode") stays around for the Tweaks demo toggle, but
// the real surface is this component, fed by the polled /api/comfyui/status
// arbiter block. Self-shows while the GPU arbiter holds the iGPU for image
// generation (arbiter.mode === "img"); fails soft (renders nothing) when the
// arbiter block is null (gate off / older backend). Dismiss is per-episode:
// it resets when the GPU returns to llm mode so the next switchover
// re-surfaces the banner.
function GpuImageModeBanner() {
  const q = useComfyui();
  const [dismissed, setDismissed] = useStateP(false);
  const isImg = q.data?.arbiter?.mode === "img";
  useEffectP(() => { if (!isImg) setDismissed(false); }, [isImg]);
  if (!isImg || dismissed) return null;
  return (
    <Banner
      kind="info"
      eyebrow="GPU · arbiter"
      heading="GPU: image mode"
      body="LLM slots are stopped while image generation holds the GPU — they restore automatically after idle."
      actions={
        <button className="btn sm" onClick={() => { window.location.hash = "#slots"; }}>
          View slots
        </button>
      }
      onDismiss={() => setDismissed(true)}
    />
  );
}

// ─── FieldGroup — a labeled config section ───────────────────────────────
// Groups fields by owner (slot/model/…).
function FieldGroup({ label, hint, children }) {
  return (
    <div className="field-group">
      <div className="field-group-head">
        <span className="field-group-label">{label}</span>
        {hint && <span className="field-group-hint">{hint}</span>}
      </div>
      {children}
    </div>
  );
}

// ─── PillToggle — two-state sliding pill ─────────────────────────────────
// Generalized from slots.jsx NpuSwitch.
// Fixed label; the on/off STATE is shown by the pill, never by a changing label.
// ─── FieldInfoIcon — hover/focus-only help for drawer field descriptions ───
function FieldInfoIcon({ description }) {
  const descriptionId = React.useId();
  return (
    <span className="field-info-wrap">
      <button
        type="button"
        className="field-info-btn"
        aria-label="Info"
        aria-describedby={descriptionId}
        onPointerDown={(event) => event.preventDefault()}
      >
        i
      </button>
      <span id={descriptionId} role="tooltip" className="field-info-pop">
        {description}
      </span>
    </span>
  );
}


export function PillToggle({ on, disabled, label, stateText, onToggle }) {
  return (
    <div className="pill-toggle-row">
      <button
        type="button"
        className="npu-switch"
        role="switch"
        aria-checked={!!on}
        aria-label={label}
        disabled={disabled}
        data-on={on ? "1" : "0"}
        onClick={() => onToggle(!on)}
      >
        <span className="knob" />
      </button>
      {stateText && <span className="pill-toggle-state mono">{stateText}</span>}
    </div>
  );
}

// ─── MtpControl — tri-state MTP override (Auto / On / Off) ────────────────
// After the profile↔model MTP separation, whether a slot speculates is decided
// by model eligibility (MTP heads) × profile opt-in (profile.mtp) × this slot
// override. This control edits the OVERRIDE: Auto (null) defers to the derived
// decision; On/Off (true/false) force it. Under Auto we surface whether MTP is
// actually effective so "Auto" never masks an inactive state.
//   value: null (auto) | true (on) | false (off)
//   autoActive: whether the derived decision is currently ON (model eligible AND
//               profile opts in) — only shown as context while value is Auto.
//   inactiveReason: optional precise reason Auto is inactive ("model has no MTP
//               heads", "profile doesn't enable MTP", …) so the hint explains
//               itself instead of a generic requirement blurb.
//   forceOnRisky: when true and value===true, warn that the force will fail at
//               launch (model doesn't advertise MTP heads — the escape hatch is
//               for models the eligibility heuristics miss, not headless ones).
function MtpControl({ value, autoActive, disabled, onChange, inactiveReason, forceOnRisky }) {
  const isAuto = value == null;
  const OPTS = [
    { key: "auto", v: null, label: "Auto" },
    { key: "on", v: true, label: "On" },
    { key: "off", v: false, label: "Off" },
  ];
  const eff = isAuto
    ? (autoActive
        ? "Auto · MTP active"
        : `Auto · off — ${inactiveReason || "needs an MTP model on an MTP profile"}`)
    : (value ? "Forced on" : "Forced off");
  return (
    <div className="mtp-ctl">
      <div className="mtp-seg" role="radiogroup" aria-label="MTP speculative decoding">
        {OPTS.map((o) => {
          const active = (o.v === null && isAuto) || o.v === value;
          return (
            <button
              key={o.key}
              type="button"
              className={"mtp-seg-btn" + (active ? " on" : "")}
              role="radio"
              aria-checked={active}
              disabled={disabled}
              data-testid={`mtp-seg-${o.key}`}
              onClick={() => { if (!active && !disabled) onChange(o.v); }}
            >
              {o.label}
            </button>
          );
        })}
      </div>
      <span className={"mtp-eff mono" + (isAuto && !autoActive ? " muted" : "")}>{eff}</span>
      {value === true && forceOnRisky && (
        <span className="mtp-eff mono" style={{ color: "var(--warn)" }} data-testid="mtp-force-warn">
          ⚠ model doesn't advertise MTP heads — launch fails unless it truly has them
        </span>
      )}
    </div>
  );
}

// ─── Dropdown menu ───────────────────────────────────────────────────────
function Menu({ anchor = "right", items, onClose, style }) {
  return (
    <div className={"hal0-menu " + anchor} style={style} onClick={e => e.stopPropagation()}>
      {items.map((it, i) => {
        if (it.divider) return <div key={i} className="hal0-menu-divider" />;
        const isDisabled = !!it.disabled;
        return (
          <div
            key={i}
            className={"hal0-menu-item"
              + (it.danger ? " danger" : "")
              + (isDisabled ? " disabled" : "")}
            title={it.hint || undefined}
            aria-disabled={isDisabled || undefined}
            style={isDisabled ? { opacity: 0.5, cursor: "not-allowed" } : undefined}
            onClick={() => {
              if (isDisabled) return;
              it.onClick && it.onClick();
              onClose && onClose();
            }}
          >
            {it.icon && <span className="hal0-menu-ic">{it.icon}</span>}
            <span className="hal0-menu-lbl">{it.label}</span>
            {it.kbd && <span className="hal0-menu-kbd kbd">{it.kbd}</span>}
          </div>
        );
      })}
    </div>
  );
}

// ─── FormRow — labelled config row (shared by Profiles + Stacks drawers) ───
// The superset variant: supports error / warn / ok / counter affordances.
// Renders a real <label htmlFor> wired to a single input/select/textarea child
// (id auto-generated) so the visible label is a genuine control label; non-
// control children (segmented buttons, switches) are passed through untouched.
function FormRow({ label, sub, req, children, error, warn, ok, counter }) {
  const autoId = React.useId();
  const genId = "fr-" + autoId;
  let control = children;
  let htmlFor;
  if (
    React.isValidElement(children) &&
    (children.type === "input" || children.type === "select" || children.type === "textarea")
  ) {
    const childId = children.props.id || genId;
    control = React.cloneElement(children, { id: childId });
    htmlFor = childId;
  }
  return (
    <div className={"pf-row" + (error ? " has-err" : "")}>
      <label className="pf-row-lbl" htmlFor={htmlFor}>
        <span>{label}{req && <span className="pf-req" title="required">*</span>}</span>
        {sub && <span className="pf-row-sub mono">{sub}</span>}
      </label>
      <div className="pf-row-ctl">
        <div className={"pf-field" + (ok ? " ok" : "") + (error ? " err" : "")}>
          {control}
          {ok && <span className="pf-field-ok" aria-hidden="true">{Icons.check}</span>}
        </div>
        {(error || warn || counter) && (
          <div className="pf-row-foot">
            {error
              ? <span className="pf-msg err mono hint err">{Icons.alert}{error}</span>
              : warn
              ? <span className="pf-msg warn mono">{Icons.alert}{warn}</span>
              : <span />}
            {counter && <span className={"pf-counter mono" + (counter.warn ? " warn" : "")}>{counter.text}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── useForm — shared form-state hook for the dash editors ──────────────────
// Owns: values (single object), touched map, submitted/submitting/closing
// flags, a set(k,v) that also marks touched, touch(k), reset(), derived
// errors/warns (validation rules stay in the CALLER — passed in verbatim), a
// submitted/touched `show(field)` gate, a `blocking` flag, and an `isDirty`
// snapshot diff (the field that unlocks the unsaved-changes guard). Re-derives
// the initial snapshot whenever `resetKey` changes — replicating the editors'
// existing [mode, source] / [open, defaults] reset effects.
function useForm({ initial, deriveInitial, resetKey, validate, warn }) {
  const compute = () => (typeof deriveInitial === "function" ? deriveInitial() : initial);
  const initialRef = useRefP(null);
  const [values, setValues] = useStateP(() => {
    const v = compute();
    initialRef.current = v;
    return v;
  });
  const [touched, setTouched] = useStateP({});
  const [submitted, setSubmitted] = useStateP(false);
  const [submitting, setSubmitting] = useStateP(false);
  const [closing, setClosing] = useStateP(false);

  useEffectP(() => {
    const v = compute();
    initialRef.current = v;
    setValues(v);
    setTouched({});
    setSubmitted(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resetKey]);

  const set = (k, v) => { setValues(f => ({ ...f, [k]: v })); setTouched(t => ({ ...t, [k]: true })); };
  const touch = (k) => setTouched(t => ({ ...t, [k]: true }));
  const reset = () => { setValues(initialRef.current); setTouched({}); setSubmitted(false); };

  const errors = validate ? (validate(values) || {}) : {};
  const warns = warn ? (warn(values) || {}) : {};
  const blocking = Object.keys(errors).length > 0;
  const show = (f) => submitted || !!touched[f];
  const isDirty = JSON.stringify(values) !== JSON.stringify(initialRef.current);

  return {
    values, setValues, set, touch, touched,
    submitted, setSubmitted, submitting, setSubmitting,
    closing, setClosing,
    errors, warns, blocking, show, reset, isDirty,
    initial: initialRef.current,
  };
}

// ─── FormDrawer — the shared pf-drawer form shell (Profiles + Stacks) ───────
// Owns the pf-scrim / pf-drawer / pf-form-panel chrome, the closing + 200ms
// animation on dismiss, eyebrow/title head, the aria-busy panel, focus trap +
// return-focus, and the unsaved-changes guard on the scrim + X. The per-view
// body (children) and foot (render-prop receiving `requestClose` so a Cancel
// button routes through the same guard) stay with the caller, keeping each
// editor's submit + validation exactly as they were.
function FormDrawer({ eyebrow, title, ariaLabel, panelClassName = "", submitting = false, dirty = false, confirmDiscard = "Discard unsaved changes?", onClose, children, foot }) {
  const [closing, setClosing] = useStateP(false);
  const [discardOpen, setDiscardOpen] = useStateP(false);
  const shellRef = useRefP(null);
  const beginClose = () => {
    setClosing(true);
    setTimeout(onClose, 200);
  };
  const requestClose = () => {
    if (submitting) return;
    if (dirty) { setDiscardOpen(true); return; }
    beginClose();
  };
  useFocusTrap(shellRef, true);
  return (
    <>
    <div className={"pf-scrim" + (closing ? " out" : "")} onMouseDown={requestClose}>
      <div
        ref={shellRef}
        tabIndex={-1}
        className={("pf-drawer pf-form-panel " + panelClassName).trim() + (closing ? " out" : "")}
        onMouseDown={e => e.stopPropagation()}
        role="dialog"
        aria-label={ariaLabel || title}
        aria-busy={submitting}
      >
        <div className="pf-drawer-head">
          <div>
            <div className="pf-drawer-eye mono">{eyebrow}</div>
            <div className="pf-drawer-title pf-form-title mono">{title}</div>
          </div>
          <button className="pf-x" onClick={requestClose} aria-label="Close" disabled={submitting}>{Icons.close}</button>
        </div>
        {children}
        <div className="pf-drawer-foot">
          {typeof foot === "function" ? foot({ requestClose }) : foot}
        </div>
      </div>
    </div>
    {/* Rendered after the scrim — equal z-index overlays stack by DOM order. */}
    {dirty && (
      <DiscardGuardDialog
        open={discardOpen}
        message={confirmDiscard}
        onCancel={() => setDiscardOpen(false)}
        onDiscard={() => { setDiscardOpen(false); beginClose(); }}
      />
    )}
    </>
  );
}

// ─── ImportDialog — shared stk-dialog import shell (file → dry-run → commit) ─
// Wraps the .stk-scrim / .stk-dialog / .stk-dlg-* chrome + the file picker,
// dry-run report, "Save as" name/slug input, and commit button. Per-view
// specifics are injected: `deriveName(file, report)` seeds the name field,
// `renderPreview(report)` draws the resolutions/model rows (Stacks) or nothing
// (Profiles), and `onFileError` supplies the "not a valid envelope" copy. The
// caller owns the actual mutations via `dryRun` / `commit` callbacks so the
// hook stays in the per-view wrapper.
function ImportDialog({
  title = "Import",
  ariaLabel,
  fileAccept = ".json,application/json",
  fileHint = "Choose a file",
  fileTestid,
  nameTestid,
  confirmTestid,
  namePlaceholder = "name",
  existing = [],
  invalidCopy = "Not a valid envelope",
  deriveName,
  renderPreview,
  dryRun,
  commit,
  onClose,
  onImported,
}) {
  const [envelope, setEnvelope] = useStateP(null);
  const [report, setReport] = useStateP(null);
  const [name, setName] = useStateP("");
  const [busy, setBusy] = useStateP(false);
  const [err, setErr] = useStateP("");

  async function onFile(file) {
    setErr("");
    try {
      const text = await file.text();
      const env = JSON.parse(text);
      setEnvelope(env);
      const r = await dryRun(env);
      setReport(r);
      setName(deriveName ? deriveName(file, r) : (r?.name || ""));
    } catch (e) {
      setErr(e?.message || invalidCopy);
      setEnvelope(null);
      setReport(null);
    }
  }

  const taken = existing.includes(name);
  const nameValid = NAME_RE.test(name) && !taken;
  const canCommit = !!report && nameValid && !busy;

  async function onCommit() {
    if (!canCommit) return;
    setBusy(true);
    try {
      await commit({ envelope, name });
      onImported();
    } catch (e) {
      // The caller's commit() may rethrow with a friendly inline message for
      // collisions; otherwise fall back to a generic toast.
      if (e?.inline) setErr(e.inline);
      else toast(e?.message || "Import failed", "err");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="stk-scrim" onMouseDown={() => { if (!busy) onClose(); }}>
      <div className="stk-dialog" onMouseDown={e => e.stopPropagation()} role="dialog" aria-label={ariaLabel || title} aria-busy={busy}>
        <div className="stk-dlg-h">
          <span className="stk-dlg-eye">{title}</span>
          <button className="stk-dlg-x" onClick={onClose} aria-label="Close" disabled={busy}>{Icons.close}</button>
        </div>
        <div className="stk-dlg-b">
          {!report ? (
            <label className="stk-drop">
              <input type="file" accept={fileAccept} style={{ display: "none" }}
                onChange={e => e.target.files?.[0] && onFile(e.target.files[0])} data-testid={fileTestid} />
              <span className="stk-drop-glyph">{Icons.attach}</span>
              <span className="mono">{fileHint}</span>
              {err && <span className="stk-dlg-warn">{Icons.alert}{err}</span>}
            </label>
          ) : (
            <>
              {renderPreview && renderPreview(report)}
              <div className="stk-slot-list">
                <div className="stk-slot-row">
                  <span className="sname">Save as</span>
                  <input className={"pf-input mono" + (name && !NAME_RE.test(name) ? " err" : "")} value={name}
                    onChange={e => { setName(e.target.value); setErr(""); }} maxLength={32} placeholder={namePlaceholder}
                    style={{ flex: 1, background: "transparent", border: "none", color: "var(--fg)", fontFamily: "var(--jbm)" }}
                    data-testid={nameTestid} />
                </div>
              </div>
              {name && !NAME_RE.test(name) && <div className="stk-dlg-warn">{Icons.alert}lowercase · digits · - · _ · ≤32</div>}
              {taken && NAME_RE.test(name) && <div className="stk-dlg-warn">{Icons.alert}“{name}” already exists</div>}
              {err && <div className="stk-dlg-warn">{Icons.alert}{err}</div>}
            </>
          )}
        </div>
        <div className="stk-dlg-f">
          <button className="btn ghost sm" onClick={onClose} disabled={busy}>Cancel</button>
          {report && (
            <button className="btn sm" onClick={onCommit} disabled={!canCommit} data-testid={confirmTestid}>
              {busy ? "Importing…" : "Import"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { Modal, Drawer, ConfirmDialog, Banner, BannerStack, BannerProvider, useBanners, BANNER_CATALOG, Menu, UpdateBanner, GpuImageModeBanner, FirstRunBanner, FieldGroup, FieldInfoIcon, PillToggle, MtpControl, NAME_RE, toast, useFocusTrap, FormRow, useForm, FormDrawer, ImportDialog });
