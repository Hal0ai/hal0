// hal0 dashboard — reusable primitives
// Modal, Drawer, ConfirmDialog, Banner, BannerStack, Dropdown menu

import { useUpdateState } from '@/api/hooks/useUpdates'
import { useInstallState, bundleNameOr } from '@/api/hooks/useInstallState'

const { useState: useStateP, useEffect: useEffectP, useRef: useRefP, createContext: createContextP, useContext: useContextP } = React;

// ─── Portal-less Modal ────────────────────────────────────────────────────
// Click backdrop or Esc to close. Focus restored on close. Width auto-sized.
function Modal({ open, onClose, title, eyebrow, children, foot, width = 640, dismissable = true }) {
  const overlayRef = useRefP(null);
  useEffectP(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape" && dismissable) onClose(); };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, dismissable, onClose]);
  if (!open) return null;
  return (
    <div
      className="modal-backdrop"
      ref={overlayRef}
      onMouseDown={(e) => { if (dismissable && e.target === overlayRef.current) onClose(); }}
    >
      <div className="modal-shell" style={{ maxWidth: width }} onMouseDown={(e) => e.stopPropagation()}>
        {(title || eyebrow) && (
          <div className="modal-h">
            {eyebrow && <div className="modal-h-eye mono">{eyebrow}</div>}
            {title && <h2 className="mono">{title}</h2>}
            {dismissable && (
              <button className="modal-close" onClick={onClose} aria-label="Close">{Icons.close}</button>
            )}
          </div>
        )}
        <div className="modal-body">{children}</div>
        {foot && <div className="modal-foot mono">{foot}</div>}
      </div>
    </div>
  );
}

// ─── Right-side Drawer ────────────────────────────────────────────────────
function Drawer({ open, onClose, title, eyebrow, children, foot, width = 520 }) {
  useEffectP(() => {
    if (!open) return;
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  return (
    <>
      <div
        className={"drawer-backdrop" + (open ? " open" : "")}
        onClick={onClose}
      />
      <aside
        className={"drawer" + (open ? " open" : "")}
        style={{ width }}
        role="dialog"
        aria-modal="true"
        aria-hidden={!open}
      >
        <div className="drawer-h">
          {eyebrow && <div className="modal-h-eye mono">{eyebrow}</div>}
          {title && <h2 className="mono">{title}</h2>}
          <button className="modal-close" onClick={onClose} aria-label="Close">{Icons.close}</button>
        </div>
        <div className="drawer-body">{children}</div>
        {foot && <div className="drawer-foot mono">{foot}</div>}
      </aside>
    </>
  );
}

// ─── ConfirmDialog (recoverable + destructive) ───────────────────────────
function ConfirmDialog({ open, onCancel, onConfirm, title, message, confirmLabel = "Confirm", cancelLabel = "Cancel", destructive = false, typeToConfirm = null }) {
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
const BANNER_CATALOG = [
  // Global
  {
    id: "lemond-offline", scope: "global", kind: "err",
    eyebrow: "Runtime · critical",
    heading: "lemond is offline",
    body: "Slot state is stale and inference requests will fail. Restart lemond or inspect the runtime logs to diagnose.",
    actions: [
      { label: "Restart lemond", primary: true },
      { label: "View status" },
      { label: "Troubleshooting docs" },
    ],
  },
  {
    id: "update-available", scope: "global", kind: "info",
    eyebrow: "Update available",
    heading: "hal0 v0.2.2 is available",
    body: "Includes lemonade v10.7.0 pin bump and one FLM CHANGELOG note. Update expects a brief outage during lemond + hal0-api restart.",
    actions: [
      { label: "Update now", primary: true },
      { label: "Read release notes" },
      { label: "Remind me later" },
    ],
  },
  {
    id: "restart-required", scope: "global", kind: "warn",
    eyebrow: "Restart required",
    heading: "Lemonade restart required to apply config changes",
    body: <span><span className="mono">ctx_size</span> and <span className="mono">llamacpp.args</span> changed on <span className="mono">primary</span>. Changes apply on next restart.</span>,
    actions: [
      { label: "Restart now", primary: true },
      { label: "Later" },
    ],
  },

  // Slots view
  {
    id: "nuclear-evict", scope: "slots", kind: "warn",
    eyebrow: "Lemonade · nuclear evict",
    heading: "Lemonade evicted all loaded models",
    body: <span>At <span className="mono">14:23:01</span> a model load triggered the runtime's nuclear evict policy. Cause: <span className="mono">CUDA out of memory while loading sd-turbo</span>. Affected slots (4): <b>primary, embed, rerank, agent</b>. Reload to restore.</span>,
    actions: [
      { label: "View logs", primary: true },
      { label: "Reload all" },
    ],
  },
  {
    id: "npu-swap", scope: "slots", kind: "warn",
    eyebrow: "NPU trio · swap in progress",
    heading: "Swapping NPU chat: gemma3:1b → llama-3.2-3b-npu",
    body: "Voice + embed paused for ~14s while FLM restarts. Coresident slots will resume automatically.",
    dismissable: false,
  },
  {
    id: "load-queue", scope: "slots", kind: "warn",
    eyebrow: "Lemonade · queue depth",
    heading: "3 slots queued to load",
    body: "Lemonade serialises model loads. The runtime will process queued slots one at a time; this banner clears when the queue empties.",
  },
  {
    id: "llamacpp-args-drift", scope: "slots", kind: "warn",
    eyebrow: "Lemonade · config drift",
    heading: "llamacpp.args is missing the mandatory baseline",
    body: <span>Required: <span className="mono">--parallel 1 --threads N</span>. Without it, concurrent llama-server children can deadlock the GPU.</span>,
    actions: [
      { label: "Restore baseline", primary: true },
      { label: "View config" },
    ],
  },
  {
    id: "catalog-drift", scope: "slots", kind: "warn",
    eyebrow: "Catalog · drift",
    heading: "registry.toml is newer than server_models.json",
    body: "Models added or removed in registry.toml won't appear until you sync. Sync will restart lemond.",
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
    heading: "Lost connection to lemond — logs are paused",
    body: "WebSocket /logs/stream closed unexpectedly. Reconnecting in 5s…",
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
    body: "hal0 needs at least 16 GB of unified RAM to load any bundled chat model. You can still install hal0 — Settings → Lemonade admin can point at an external model store.",
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
    body: <span>You skipped the bundle picker. Each seeded slot below has a <b>Configure</b> button that opens the Create-slot modal pre-filled. Or run the bundle picker again from <span className="mono">Settings → FirstRun</span>.</span>,
    actions: [
      { label: "Run picker", primary: true, onClick: () => window.location.hash = "#firstrun" },
    ],
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
function UpdateBanner() {
  const { data: state } = useUpdateState();
  const [dismissed, setDismissed] = useStateP(false);
  const hal0 = state && state.hal0;
  const current = hal0 && hal0.current;
  const available = hal0 && hal0.available;
  const hasUpdate = !!available && available !== current;
  if (!hasUpdate || dismissed) return null;
  const channel = (hal0 && hal0.channel) || "stable";
  return (
    <Banner
      kind="info"
      eyebrow="Update available"
      heading={`hal0 ${available} available`}
      body={
        <span>
          New release on the <span className="mono">{channel}</span> channel.
          Update expects a brief outage during lemond + hal0-api restart.
        </span>
      }
      actions={
        <button
          className="btn ghost sm"
          onClick={() =>
            window.__hal0Toast && window.__hal0Toast(`Opening hal0 ${available} release notes`, "info")
          }
        >Read release notes</button>
      }
      onDismiss={() => setDismissed(true)}
    />
  );
}

// ─── Dropdown menu ───────────────────────────────────────────────────────
function Menu({ anchor = "right", items, onClose, style }) {
  return (
    <div className={"hal0-menu " + anchor} style={style} onClick={e => e.stopPropagation()}>
      {items.map((it, i) => {
        if (it.divider) return <div key={i} className="hal0-menu-divider" />;
        return (
          <div
            key={i}
            className={"hal0-menu-item" + (it.danger ? " danger" : "")}
            onClick={() => { it.onClick && it.onClick(); onClose && onClose(); }}
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

Object.assign(window, { Modal, Drawer, ConfirmDialog, Banner, BannerStack, BannerProvider, useBanners, BANNER_CATALOG, Menu, UpdateBanner });
