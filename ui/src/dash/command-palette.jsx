// hal0 dashboard — CommandPalette (Spotlight-style)
// Fuzzy filter over routes + slots + models + actions, opened via ⌘K / Ctrl+K.

const { useState: useStateCP, useEffect: useEffectCP, useRef: useRefCP, useMemo: useMemoCP } = React;

function CommandPalette({ open, onClose }) {
  const [q, setQ] = useStateCP("");
  const [idx, setIdx] = useStateCP(0);
  const inputRef = useRefCP(null);
  const listRef = useRefCP(null);

  useEffectCP(() => {
    if (open) {
      setQ("");
      setIdx(0);
      setTimeout(() => inputRef.current && inputRef.current.focus(), 0);
    }
  }, [open]);

  // Build the unified item list once per open
  const items = useMemoCP(() => buildCommandItems(), [open]);

  // Fuzzy-filter: characters in order, weighted by exact prefix
  const filtered = useMemoCP(() => {
    if (!q.trim()) return items;
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

  if (!open) return null;

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
  const sectionOrder = ["Routes", "Slots", "Models", "Settings", "Actions"];

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

// Build a unified list of palette items.
function buildCommandItems() {
  const items = [];

  // Routes
  const routes = [
    { id: "r-dashboard", route: "dashboard", label: "Dashboard",  icon: Icons.dashboard, sub: "chat + snapshot + health", keywords: "home chat overview" },
    { id: "r-slots",     route: "slots",     label: "Slots",      icon: Icons.slots,     sub: "inventory + capability rollups", keywords: "lifecycle" },
    { id: "r-models",    route: "models",    label: "Models",     icon: Icons.models,    sub: "catalog + downloads", keywords: "catalog hugging face" },
    { id: "r-hardware",  route: "hardware",  label: "Hardware",   icon: Icons.hardware,  sub: "cpu, gpu, npu, memory" },
    { id: "r-logs",      route: "logs",      label: "Logs",       icon: Icons.logs,      sub: "hal0 + lemond stream", keywords: "tail console output" },
    { id: "r-agent",     route: "agent",     label: "Agent",      icon: Icons.agent,     sub: "chat, personas, skills, memory, plugins" },
    { id: "r-settings",  route: "settings",  label: "Settings",   icon: Icons.settings,  sub: "auth, secrets, updates, lemond admin" },
    { id: "r-firstrun",  route: "firstrun",  label: "FirstRun picker", icon: Icons.flame, sub: "re-run the bundle picker", keywords: "setup install bundle" },
  ];
  routes.forEach(r => items.push({ ...r, section: "Routes", hint: "↵ jump" }));

  // Slots
  (HAL0_DATA.slots || []).forEach(s => {
    items.push({
      id: "s-" + s.name,
      section: "Slots",
      route: "slots/" + s.name,
      label: s.name,
      icon: <span className={"dot " + s.state} style={{display: "inline-block"}} />,
      sub: `${s.model} · ${s.type} · ${s.device}${s.isDefault ? " · default" : ""}`,
      tag: s.state === "serving" ? <span className="chip amber">{s.state}</span> : null,
      keywords: `${s.type} ${s.device} ${s.group}`,
      hint: "open edit drawer",
    });
  });

  // Models
  (HAL0_DATA.models || []).forEach(m => {
    items.push({
      id: "m-" + m.id,
      section: "Models",
      route: "models",
      action: () => window.__hal0Toast && window.__hal0Toast(`Selected ${m.longName} on /models`, "info"),
      label: m.longName,
      icon: <span className={"dot " + (m.installed ? "ready" : "empty")} style={{display: "inline-block"}} />,
      sub: `${m.repo} · ${m.size}`,
      tag: m.installed ? <span className="chip ok">installed</span> : <span className="chip">{m.ns}</span>,
      keywords: `${m.type} ${m.device} ${m.labels && m.labels.join(" ")}`,
    });
  });

  // Settings sections — anchor jumps
  [
    { id: "set-auth",      label: "Auth · token",        sub: "rotate Bearer token, allowed origins" },
    { id: "set-secrets",   label: "Secrets",              sub: "HF_TOKEN and provider keys" },
    { id: "set-updates",   label: "Updates",              sub: "hal0 / lemonade / flm versions" },
    { id: "set-lemonade",  label: "Lemonade admin",       sub: "max_loaded_models, ctx_size, args" },
    { id: "set-omni",      label: "OmniRouter tools",     sub: "8 tools · per-tool target" },
    { id: "set-agent",     label: "Agent policy",         sub: "per-capability approval" },
    { id: "set-memory",    label: "Memory (Cognee)",      sub: "namespace, store, records" },
  ].forEach(s => items.push({ ...s, section: "Settings", icon: Icons.settings, route: "settings" }));

  // Actions
  const action = (id, label, sub, fn, icon) => items.push({
    id, section: "Actions", label, sub, icon: icon || Icons.flame, action: fn, hint: "↵ run",
  });

  action("a-create-slot", "Create slot…", "name + type + device + model",
    () => window.dispatchEvent(new CustomEvent("hal0:create-slot")));
  action("a-add-hf", "Add model from HF…", "paste org/repo, inspect variants",
    () => { window.location.hash = "#models"; window.dispatchEvent(new CustomEvent("hal0:add-hf")); });
  action("a-restart-lemond", "Restart lemond", "expects ~8–12s outage",
    () => window.__hal0Toast && window.__hal0Toast("Restarting lemond — brief outage", "warn"));
  action("a-restart-flm", "Restart FLM (NPU trio)", "swaps the coresident chat model",
    () => window.__hal0Toast && window.__hal0Toast("Restarting FLM trio", "warn"));
  action("a-rotate-token", "Rotate hal0 token", "invalidates the current Bearer immediately",
    () => { window.location.hash = "#settings"; window.__hal0Toast && window.__hal0Toast("Routing to Auth → Rotate", "info"); });
  action("a-clear-downloads", "Clear completed downloads", "purges finished rows",
    () => window.__hal0Toast && window.__hal0Toast("Cleared completed downloads", "ok"));
  action("a-open-owui", "Open Chat Pro UI →", "external · OpenWebUI",
    () => window.__hal0Toast && window.__hal0Toast("Opening hal0-chat.thinmint.dev", "info"));
  action("a-docs", "Open docs →", "hal0.dev/docs/v0.2-upgrade",
    () => window.__hal0Toast && window.__hal0Toast("Opening hal0.dev/docs", "info"));
  action("a-toggle-tour", "Replay onboarding tour", "the 3-step intro",
    () => window.dispatchEvent(new CustomEvent("hal0:tour-start")));

  // v0.3 PR-8: dropped the "Review N pending approvals" shortcut. The
  // approvals surface lives in the sidebar pip (PR-6) and inline in the
  // chat composer (PR-10). The command palette item used the dead
  // HAL0_DATA.approvals fixture.

  return items;
}

Object.assign(window, { CommandPalette });
