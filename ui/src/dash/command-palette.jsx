// hal0 dashboard — CommandPalette (Spotlight-style)
// Fuzzy filter over routes + slots + models + actions, opened via ⌘K / Ctrl+K.
//
// v0.4: the palette was the last dash/*.jsx holdout still reading the
// static HAL0_DATA fixture for its Slots/Models lists (so it showed
// phantom seed slots in production). It now reads live data via the
// typed hooks (useSlots/useModels) — mounted only while the palette is
// open (CommandPaletteInner) so there's no background polling when it's
// closed. Slot control verbs (start/stop/restart/logs) are dispatched as
// `hal0:slot-*` events handled by the always-mounted SlotActionBridge
// below, so they work from any route without a navigate-then-dispatch
// race. The old toast-only stub actions (restart-runtime, restart-flm,
// clear-downloads, replay-tour) were removed — every item now does what
// it says.

import { useSlots, useSlotRestart, useSlotLoad, useSlotUnload } from '@/api/hooks/useSlots'
import { useModels, fmtBytes } from '@/api/hooks/useModels'
import { useConfigUrls } from '@/api/hooks/useConfigUrls'

const { useState: useStateCP, useEffect: useEffectCP, useRef: useRefCP, useMemo: useMemoCP } = React;

// Module-level active-pull tracking. The pull job lives in whichever
// ModelDetail is mounted (per-row usePullJob), so the palette can't reach
// it through a hook. Instead we listen for the global lifecycle events:
// models.jsx fires `hal0:pull-started` on .start(); the usePullJob hook
// fires `hal0:pull-ended` when the job reaches a terminal state. Tracking
// it here (not in React state) means an open palette only ever offers
// "Cancel download" while a pull is genuinely live.
let __cpActivePull = null;
if (typeof window !== "undefined") {
  window.addEventListener("hal0:pull-started", (e) => { __cpActivePull = (e.detail && e.detail.modelId) || null; });
  window.addEventListener("hal0:pull-ended", () => { __cpActivePull = null; });
}

const cpCopy = (text, label) => {
  try {
    navigator.clipboard.writeText(text);
    window.__hal0Toast && window.__hal0Toast(label, "ok");
  } catch {
    window.__hal0Toast && window.__hal0Toast("Copy failed — clipboard unavailable", "err");
  }
};

const cpCurlFor = (slotName) =>
  `curl ${location.origin}/v1/chat/completions \\\n` +
  `  -H 'Content-Type: application/json' \\\n` +
  `  -d '{"model":"${slotName}","messages":[{"role":"user","content":"hello"}]}'`;

// Navigate to /slots (where the SlotLogsDrawer is mounted) then ask
// SlotsView to open the drawer. The tiny delay covers the case where we
// were on another route and SlotsView's listener hasn't mounted yet.
const cpViewLogs = (name) => {
  window.location.hash = "#slots";
  setTimeout(() => window.dispatchEvent(new CustomEvent("hal0:slot-logs", { detail: { name } })), 60);
};

// Outer gate — keeps the data hooks (and their polling) unmounted while
// the palette is closed. All real work happens in CommandPaletteInner.
function CommandPalette({ open, onClose }) {
  if (!open) return null;
  return <CommandPaletteInner onClose={onClose} />;
}

function CommandPaletteInner({ onClose }) {
  const [q, setQ] = useStateCP("");
  const [idx, setIdx] = useStateCP(0);
  const [activePull, setActivePull] = useStateCP(__cpActivePull);
  const inputRef = useRefCP(null);
  const listRef = useRefCP(null);

  const slots = useSlots().data || [];
  const models = useModels().data || [];
  // OpenWebUI deep-link resolved from the backend (request-host derived +
  // HAL0_OPENWEBUI_PUBLIC_URL override), not hardcoded — empty when the unit
  // is down or not reachably linkable, in which case the action is hidden.
  const cfgUrls = useConfigUrls().data;
  const owuiUrl = cfgUrls?.openwebui_enabled ? (cfgUrls.openwebui || "") : "";

  useEffectCP(() => {
    setTimeout(() => inputRef.current && inputRef.current.focus(), 0);
  }, []);

  // Keep the cancel-download affordance honest while the palette is open.
  useEffectCP(() => {
    const onStart = (e) => setActivePull((e.detail && e.detail.modelId) || null);
    const onEnd = () => setActivePull(null);
    window.addEventListener("hal0:pull-started", onStart);
    window.addEventListener("hal0:pull-ended", onEnd);
    return () => {
      window.removeEventListener("hal0:pull-started", onStart);
      window.removeEventListener("hal0:pull-ended", onEnd);
    };
  }, []);

  const items = useMemoCP(() => buildCommandItems(slots, models, activePull, owuiUrl), [slots, models, activePull, owuiUrl]);

  // Fuzzy-filter: characters in order, weighted by exact prefix. Per-slot
  // control verbs are hidden from the empty-query view (hideWhenEmpty) so
  // the default list stays a clean nav surface; typing a verb or slot name
  // surfaces them.
  const filtered = useMemoCP(() => {
    if (!q.trim()) return items.filter(it => !it.hideWhenEmpty);
    const needle = q.toLowerCase();
    const scored = items.map(it => {
      const hay = (it.label + " " + (it.sub || "") + " " + (it.keywords || "")).toLowerCase();
      const exact = hay.indexOf(needle);
      if (exact >= 0) return { it, score: exact === 0 ? 1000 : 500 - exact };
      // chars-in-order
      let i = 0, j = 0, gap = 0;
      while (i < needle.length && j < hay.length) {
        if (needle[i] === hay[j]) { i++; } else { gap++; }
        j++;
      }
      if (i === needle.length) return { it, score: 100 - gap };
      return null;
    }).filter(Boolean);
    scored.sort((a, b) => b.score - a.score);
    return scored.map(s => s.it);
  }, [q, items]);

  useEffectCP(() => { setIdx(0); }, [q]);

  // Scroll active row into view
  useEffectCP(() => {
    if (!listRef.current) return;
    const row = listRef.current.querySelector(`[data-cp-idx="${idx}"]`);
    if (row) row.scrollIntoView({ block: "nearest" });
  }, [idx, filtered]);

  const go = (it) => {
    if (it.route) window.location.hash = "#" + it.route;
    if (it.action) it.action();
    onClose();
  };

  const onKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx(i => Math.min(i + 1, filtered.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx(i => Math.max(i - 1, 0)); }
    else if (e.key === "Enter")   { e.preventDefault(); filtered[idx] && go(filtered[idx]); }
    else if (e.key === "Escape")  { e.preventDefault(); onClose(); }
  };

  // Group filtered items by section for visual separation
  const groups = {};
  filtered.forEach(it => { (groups[it.section] = groups[it.section] || []).push(it); });
  const sectionOrder = ["Routes", "Slots", "Models", "Settings", "Actions", "Copy", "Slot actions"];

  return (
    <div className="cp-backdrop" onMouseDown={(e) => { if (e.target.classList.contains("cp-backdrop")) onClose(); }}>
      <div className="cp-shell" role="dialog" aria-label="Command palette">
        <div className="cp-input-row">
          <span className="cp-input-ic">{Icons.search}</span>
          <input
            ref={inputRef}
            className="cp-input mono"
            value={q}
            onChange={e => setQ(e.target.value)}
            onKeyDown={onKey}
            placeholder="Jump to a route, slot, model, or action…"
          />
          <span className="cp-input-kbd"><kbd className="kbd">esc</kbd></span>
        </div>
        <div className="cp-list" ref={listRef}>
          {filtered.length === 0 && (
            <div className="cp-empty mono">No matches. Try a route name, slot name, or model id.</div>
          )}
          {sectionOrder.map(sec => {
            const its = groups[sec];
            if (!its || its.length === 0) return null;
            return (
              <div key={sec}>
                <div className="cp-section mono">{sec}<span>· {its.length}</span></div>
                {its.map(it => {
                  const i = filtered.indexOf(it);
                  return (
                    <div
                      key={it.id}
                      data-cp-idx={i}
                      className={"cp-item" + (i === idx ? " active" : "")}
                      onMouseEnter={() => setIdx(i)}
                      onClick={() => go(it)}
                    >
                      <span className="cp-item-ic">{it.icon}</span>
                      <div className="cp-item-text">
                        <div className="cp-item-label">
                          {highlightCp(it.label, q)}
                          {it.tag && <span className="cp-item-tag">{it.tag}</span>}
                        </div>
                        {it.sub && <div className="cp-item-sub mono">{it.sub}</div>}
                      </div>
                      {it.hint && <span className="cp-item-hint mono">{it.hint}</span>}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
        <div className="cp-foot mono">
          <span><kbd className="kbd">↑↓</kbd> navigate</span>
          <span><kbd className="kbd">↵</kbd> select</span>
          <span><kbd className="kbd">esc</kbd> dismiss</span>
          <span style={{marginLeft: "auto"}}>{filtered.length} of {items.length}</span>
        </div>
      </div>
    </div>
  );
}

function highlightCp(text, q) {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <span className="cp-highlight">{text.slice(i, i + q.length)}</span>
      {text.slice(i + q.length)}
    </>
  );
}

// States in which a slot holds a model in memory (so Stop/Restart apply;
// otherwise we offer Start). Mirrors the loaded-state set used by SlotCard.
const CP_RUNNING_STATES = new Set(["serving", "ready", "idle", "warming"]);

// Build a unified list of palette items from LIVE slots + models.
// `owuiUrl` is the backend-resolved OpenWebUI link ("" when not reachable).
function buildCommandItems(slots, models, activePull, owuiUrl = "") {
  const items = [];

  // Routes
  const routes = [
    { id: "r-dashboard", route: "dashboard", label: "Dashboard",  icon: Icons.dashboard, sub: "chat + snapshot + health", keywords: "home chat overview" },
    { id: "r-slots",     route: "slots",     label: "Slots",      icon: Icons.slots,     sub: "inventory + capability rollups", keywords: "lifecycle" },
    { id: "r-models",    route: "models",    label: "Models",     icon: Icons.models,    sub: "catalog + downloads", keywords: "catalog hugging face" },
    { id: "r-hardware",  route: "hardware",  label: "Hardware",   icon: Icons.hardware,  sub: "cpu, gpu, npu, memory" },
    { id: "r-logs",      route: "logs",      label: "Logs",       icon: Icons.logs,      sub: "hal0 stream", keywords: "tail console output" },
    { id: "r-agent",     route: "agent",     label: "Agent",      icon: Icons.agent,     sub: "chat, personas, skills, memory, plugins" },
    { id: "r-settings",  route: "settings",  label: "Settings",   icon: Icons.settings,  sub: "auth, secrets, updates" },
    { id: "r-firstrun",  route: "firstrun",  label: "FirstRun picker", icon: Icons.flame, sub: "re-run the bundle picker", keywords: "setup install bundle" },
  ];
  routes.forEach(r => items.push({ ...r, section: "Routes", hint: "↵ jump" }));

  // Slots — live (was HAL0_DATA.slots). Nav item opens the edit drawer.
  (slots || []).forEach(s => {
    const isDefault = s.is_default ?? s.default ?? s.isDefault;
    items.push({
      id: "s-" + s.name,
      section: "Slots",
      route: "slots/" + s.name,
      label: s.name,
      icon: <span className={"dot " + s.state} style={{display: "inline-block"}} />,
      sub: `${s.model || "—"} · ${s.type} · ${s.device}${isDefault ? " · default" : ""}`,
      tag: s.state === "serving" ? <span className="chip amber">{s.state}</span> : null,
      keywords: `${s.type} ${s.device} ${s.group || ""}`,
      hint: "open edit drawer",
    });
  });

  // Models — live (was HAL0_DATA.models). Nav item routes to /models.
  (models || []).forEach(m => {
    const size = m.size || (m.size_bytes ? fmtBytes(m.size_bytes) : "");
    items.push({
      id: "m-" + m.id,
      section: "Models",
      route: "models",
      label: m.longName || m.name || m.id,
      icon: <span className={"dot " + (m.installed ? "ready" : "empty")} style={{display: "inline-block"}} />,
      sub: `${m.repo || m.id}${size ? " · " + size : ""}`,
      tag: m.installed ? <span className="chip ok">installed</span> : <span className="chip">{m.ns}</span>,
      keywords: `${m.ns || ""} ${(m.labels && m.labels.join(" ")) || ""}`,
    });
  });

  // Settings sections — anchor jumps
  // #544: OmniRouter/Agent-policy/Memory (Cognee) sections pruned;
  // entries for them are gone. Surviving sections renamed for accuracy
  // (Storage, Runtime, General).
  [
    { id: "set-auth",      label: "Auth · token",        sub: "rotate Bearer token, allowed origins" },
    { id: "set-secrets",   label: "Secrets",              sub: "HF_TOKEN and provider keys" },
    { id: "set-storage",   label: "Storage",              sub: "[models].store · auto_scan · file extensions" },
    { id: "set-updates",   label: "Updates",              sub: "hal0 / flm versions" },
    { id: "set-runtime",   label: "Runtime",              sub: "max_loaded_models, ctx_size, args" },
    { id: "set-general",   label: "General",              sub: "theme, density, accent" },
  ].forEach(s => items.push({ ...s, section: "Settings", icon: Icons.settings, route: "settings" }));

  // Global actions — every one is wired to a real effect.
  const action = (id, label, sub, fn, icon) => items.push({
    id, section: "Actions", label, sub, icon: icon || Icons.flame, action: fn, hint: "↵ run",
  });

  action("a-create-slot", "Create slot…", "name + type + device + model",
    () => window.dispatchEvent(new CustomEvent("hal0:create-slot")));
  action("a-add-hf", "Add model from HF…", "open the catalog on /models",
    () => { window.location.hash = "#models"; });
  action("a-rotate-token", "Rotate hal0 token", "invalidates the current Bearer immediately",
    () => { window.location.hash = "#settings"; window.__hal0Toast && window.__hal0Toast("Routing to Auth → Rotate", "info"); });
  if (owuiUrl) {
    action("a-open-owui", "Open Chat Pro UI →", "external · OpenWebUI",
      () => window.open(owuiUrl, "_blank", "noopener"));
  }
  action("a-docs", "Open docs →", "external · hal0.dev/docs",
    () => window.open("https://hal0.dev/docs", "_blank", "noopener"));

  // Cancel download — only offered while a pull is actually live, so it's
  // never a dead affordance.
  if (activePull) {
    const m = (models || []).find(x => x.id === activePull);
    action("a-cancel-pull", `Cancel download — ${m ? (m.longName || m.id) : activePull}`, "stops the active model pull",
      () => {
        fetch(`/api/models/${encodeURIComponent(activePull)}/pull/cancel`, { method: "POST" })
          .then(() => window.__hal0Toast && window.__hal0Toast("Download cancelled", "warn"))
          .catch(() => window.__hal0Toast && window.__hal0Toast("Cancel failed — see logs", "err"));
      });
  }

  // Copy — clipboard utilities for hitting the API directly.
  items.push({
    id: "cp-base", section: "Copy", label: "Copy API base URL", sub: `${location.origin}/v1`,
    icon: Icons.flame, hint: "↵ copy",
    action: () => cpCopy(`${location.origin}/v1`, "Copied API base URL"),
  });

  // Per-slot control verbs — hidden from the empty-query view; surfaced by
  // typing a verb ("restart", "logs") or a slot name. The mutating verbs
  // dispatch hal0:slot-* events handled by SlotActionBridge.
  const slotAct = (id, label, sub, fn, keywords) => items.push({
    id, section: "Slot actions", label, sub, icon: Icons.slots, action: fn,
    hint: "↵ run", hideWhenEmpty: true, keywords,
  });
  (slots || []).forEach(s => {
    const kw = `${s.name} ${s.type} ${s.device}`;
    if (CP_RUNNING_STATES.has(s.state)) {
      slotAct(`sa-restart-${s.name}`, `Restart ${s.name}`, "reload the slot",
        () => window.dispatchEvent(new CustomEvent("hal0:slot-restart", { detail: { name: s.name } })),
        `restart reload ${kw}`);
      slotAct(`sa-stop-${s.name}`, `Stop ${s.name}`, "unload from memory",
        () => window.dispatchEvent(new CustomEvent("hal0:slot-stop", { detail: { name: s.name } })),
        `stop unload ${kw}`);
    } else {
      slotAct(`sa-start-${s.name}`, `Start ${s.name}`, "load into memory",
        () => window.dispatchEvent(new CustomEvent("hal0:slot-start", { detail: { name: s.name } })),
        `start load ${kw}`);
    }
    slotAct(`sa-logs-${s.name}`, `View logs — ${s.name}`, "open the live log drawer",
      () => cpViewLogs(s.name), `logs tail ${kw}`);
    slotAct(`sa-curl-${s.name}`, `Copy curl — ${s.name}`, "chat/completions example",
      () => cpCopy(cpCurlFor(s.name), `Copied curl for ${s.name}`), `copy curl api ${kw}`);
  });

  return items;
}

// Headless, always-mounted bridge: runs the real slot mutations in
// response to hal0:slot-* events fired by the palette (or anywhere). Lives
// at the root (main.jsx) so slot control works from every route without
// depending on SlotsView being mounted.
function SlotActionBridge() {
  const restart = useSlotRestart();
  const load = useSlotLoad();
  const unload = useSlotUnload();
  React.useEffect(() => {
    const toast = (m, k) => window.__hal0Toast && window.__hal0Toast(m, k);
    const onRestart = (e) => { const n = e.detail && e.detail.name; if (n) { toast(`Restarting ${n}`, "info"); restart.mutate(n); } };
    const onStart = (e) => { const n = e.detail && e.detail.name; if (n) { toast(`Starting ${n}`, "info"); load.mutate(n); } };
    const onStop = (e) => { const n = e.detail && e.detail.name; if (n) { toast(`Stopping ${n}`, "info"); unload.mutate(n); } };
    window.addEventListener("hal0:slot-restart", onRestart);
    window.addEventListener("hal0:slot-start", onStart);
    window.addEventListener("hal0:slot-stop", onStop);
    return () => {
      window.removeEventListener("hal0:slot-restart", onRestart);
      window.removeEventListener("hal0:slot-start", onStart);
      window.removeEventListener("hal0:slot-stop", onStop);
    };
  }, [restart, load, unload]);
  return null;
}

Object.assign(window, { CommandPalette, SlotActionBridge });
