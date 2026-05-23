// hal0 dashboard — Slots view (SlotCard, NPU trio variants, group sections)
//
// Phase B1: live slot list via `useSlots`. The prototype JSX passes a
// `slots` prop from main.jsx (HAL0_DATA.slots fallback); we union the
// hook on top so first paint + mock-mode still works.

import { useSlots } from '@/api/hooks/useSlots'

const { useState: useStateS } = React;

// ─── Mini sparkline for slot card ───
function Spark({ data, height = 18 }) {
  if (!data || data.length === 0) return null;
  const max = Math.max(...data, 1);
  return (
    <div className="spark" style={{ height }}>
      {data.map((v, i) => (
        <i key={i} style={{ height: `${Math.max((v / max) * 100, 8)}%` }} />
      ))}
    </div>
  );
}

// ─── SlotCard (instrument variant) ───
function SlotCard({ slot, onSwap, onEdit, onOverflow, swapOpen, onCloseSwap, menuOpen, onCloseMenu, errorMsg }) {
  const { type, device, model, state, isDefault, coresident, cpuOnly, metrics } = slot;
  const isLlm = type === "llm";

  const metricsRow = (() => {
    if (type === "llm") return [
      { l: "tok/s",  v: metrics.toks, u: "", spark: slot.spark },
      { l: "ttft",   v: metrics.ttft ? metrics.ttft : "—", u: metrics.ttft ? "ms" : "" },
      { l: "ctx",    v: metrics.ctx, u: "" },
      { l: "kv",     v: metrics.kv === null ? "—" : metrics.kv, u: metrics.kv === null ? "" : "%", dim: metrics.kv === null },
    ];
    if (type === "embedding") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "p50",     v: metrics.lat || "—", u: metrics.lat ? "ms" : "" },
      { l: "dim",     v: metrics.dim, u: "" },
      { l: "size",    v: metrics.mem * 1024 < 1000 ? (metrics.mem * 1024).toFixed(0) : metrics.mem.toFixed(1), u: metrics.mem * 1024 < 1000 ? "MB" : "GB" },
    ];
    if (type === "reranking") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "p50",     v: metrics.lat, u: "ms" },
      { l: "max/req", v: metrics.maxDocs, u: "" },
      { l: "size",    v: (metrics.mem * 1024).toFixed(0), u: "MB" },
    ];
    if (type === "transcription") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "xrt",     v: metrics.xrt, u: "" },
      { l: "prec",    v: metrics.precision, u: "" },
      { l: "size",    v: (metrics.mem * 1024).toFixed(0), u: "MB" },
    ];
    if (type === "tts") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "sec/min", v: metrics.secs, u: "" },
      { l: "voice",   v: metrics.voice, u: "" },
      { l: "size",    v: (metrics.mem * 1024).toFixed(0), u: "MB" },
    ];
    if (type === "image") return [
      { l: "req/min", v: metrics.rpm, u: "" },
      { l: "avg",     v: metrics.avg, u: "s" },
      { l: "res",     v: metrics.res, u: "" },
      { l: "size",    v: metrics.mem.toFixed(1), u: "GB" },
    ];
    return [];
  })();

  return (
    <div className={"slot" + (state === "serving" ? " serving" : "")}>
      <div className="slot-h">
        <span className={"dot " + state} />
        <div className="slot-name">
          <span className="nm">{slot.name}</span>
        </div>
        <div className="right" style={{position: "relative"}}>
          {isDefault && <div className="default-badge">★ default</div>}
          {coresident && <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>coresident</span>}
          <button className="more" onClick={e => { e.stopPropagation(); onOverflow && onOverflow(); }}>{Icons.more}</button>
          {menuOpen && <SlotOverflowMenu slot={slot} onClose={onCloseMenu} />}
        </div>
      </div>
      <div className="slot-model mono" onClick={onSwap} style={{position: "relative"}}>
        <span className="mid">{model}</span>
        <span className="chev">{Icons.chev}</span>
        {swapOpen && (
          <InlineSwapPopover
            slot={slot}
            open={swapOpen}
            onClose={onCloseSwap}
            onPick={(m) => window.__hal0Toast && window.__hal0Toast(`Swapping ${slot.name} → ${m.longName}`, "info")}
          />
        )}
      </div>
      <div className="slot-chips">
        <span className="chip">{type}</span>
        <span className={"chip dev-" + (device || "cpu").replace("gpu-", "")}>{device}</span>
        {cpuOnly && <span className="chip">[CPU]</span>}
        <span className="chip" style={{color: state === "serving" ? "var(--accent)" : state === "ready" ? "var(--ok)" : state === "idle" ? "var(--fg-3)" : "var(--fg-3)"}}>
          {state}
        </span>
      </div>
      <div className="slot-metrics">
        {metricsRow.map((m, i) => (
          <div key={i} className="slot-met">
            <div className="l">{m.l}</div>
            <div className={"v mono num" + (m.dim ? " dim" : "")}>
              {m.v}<span className="u">{m.u}</span>
            </div>
            {i === 0 && isLlm && slot.spark && <Spark data={slot.spark} height={12} />}
          </div>
        ))}
      </div>
      <div className="slot-actions">
        <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Restarting ${slot.name}`, "info")}>{Icons.restart} Restart</button>
        <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Unloading ${slot.name}`, "info")}>{Icons.unload} Unload</button>
        <button className="btn ghost sm" onClick={onEdit}>{Icons.edit} Edit</button>
        <span className="spacer" />
      </div>
      {errorMsg && <div style={{marginTop: 4}}><ErrorSlotCardBanner slot={slot} message={errorMsg} /></div>}
    </div>
  );
}

// ─── SlotCard compact list variant ───
function SlotListRow({ slot, onEdit }) {
  const { type, device, model, state, isDefault, metrics } = slot;
  const tps = type === "llm" ? `${metrics.toks || 0} t/s` :
              type === "embedding" ? `${metrics.rpm} r/m` :
              type === "transcription" ? `${metrics.xrt} xrt` :
              type === "image" ? `${metrics.avg}s avg` :
              `${metrics.rpm || 0} r/m`;
  return (
    <div className="slot-list-row" onClick={onEdit}>
      <span className={"dot " + state} />
      <span className="nm">
        {slot.name}
        {isDefault && <span className="chip outlined amber" style={{fontSize: 9, padding: "1px 4px"}}>def</span>}
      </span>
      <span className="ml">{model}</span>
      <span className="ch">
        <span className="chip">{type}</span>
        <span className={"chip dev-" + (device || "cpu").replace("gpu-", "")}>{device}</span>
      </span>
      <span className="met">
        <b>{tps}</b>
        {type === "llm" && metrics.ttft && <span>· {metrics.ttft}ms ttft</span>}
        {type === "llm" && metrics.ctx && <span>· {metrics.ctx} ctx</span>}
      </span>
      <span className="ac">
        <button className="btn ghost sm" onClick={e => { e.stopPropagation(); }}>{Icons.restart}</button>
        <button className="btn ghost sm" onClick={e => { e.stopPropagation(); }}>{Icons.edit}</button>
      </span>
    </div>
  );
}

// ─── NPU trio — Block variant (default per brief) ───
function NpuBlock({ slots }) {
  const npuSlots = slots.filter(s => s.device === "npu");
  const chat = npuSlots.find(s => s.type === "llm");
  const flm = chat || npuSlots[0];
  return (
    <div className="card npu-card live">
      <div className="npu-h">
        <span className="npu-glyph mono">NPU</span>
        <span className="title mono">
          FLM trio<span className="sub">one process · three roles · {chat ? chat.model : "no chat model"} active</span>
        </span>
        <div className="right">
          <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.08)"}}>
            <span className="dot" style={{width: 5, height: 5, background: "currentColor", boxShadow: "0 0 6px currentColor"}} />
            coresident
          </span>
          <span className="pid mono">pid {flm?.pid || "—"} · port :{flm?.port || "—"}</span>
        </div>
      </div>
      <div className="npu-body">
        {npuSlots.map((s, i) => (
          <div key={s.name} className={"npu-subrow" + (s.type === "llm" ? " lead" : "")}>
            <span className={"dot " + (i === 0 ? "ready" : "coresident")} />
            <div className="role mono">
              {s.name}
              <span className="sub">{s.type}</span>
            </div>
            <div className="model mono">
              {s.model}
              {s.type === "llm" && <span className="chev">{Icons.chev}</span>}
            </div>
            <div className="met mono">
              {s.type === "llm" && <span><b>{s.metrics.toks}</b> tok/s · TTFT <b>{s.metrics.ttft}</b>ms · KV <b>{s.metrics.kv}</b>%</span>}
              {s.type === "transcription" && <span><b>{s.metrics.xrt}</b> xrt · {s.metrics.precision}</span>}
              {s.type === "embedding" && <span>{s.metrics.dim}-dim · ready</span>}
            </div>
            <div className="st">
              <span className="chip" style={{color: s.type === "llm" ? "var(--ok)" : "var(--dev-npu)", borderColor: s.type === "llm" ? "var(--ok-line)" : "rgba(200,150,255,0.30)", background: s.type === "llm" ? "var(--ok-soft)" : "rgba(200,150,255,0.06)"}}>
                {s.type === "llm" ? "ready · default" : "coresident"}
              </span>
            </div>
          </div>
        ))}
      </div>
      <div className="npu-foot mono">
        <span className="item"><b>~2 GB</b> NPU memory</span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item"><b>~14s</b> swap penalty on chat-model change</span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item">disabling stt-npu/embed-npu frees a role at next FLM restart</span>
      </div>
    </div>
  );
}

// ─── NPU trio — Reactor variant ───
function NpuReactor({ slots }) {
  const npuSlots = slots.filter(s => s.device === "npu");
  const chat = npuSlots.find(s => s.type === "llm");
  const stt = npuSlots.find(s => s.type === "transcription");
  const emb = npuSlots.find(s => s.type === "embedding");
  return (
    <div className="card npu-card live">
      <div className="npu-h">
        <span className="npu-glyph mono">NPU</span>
        <span className="title mono">FLM trio<span className="sub">reactor view · one process driving three roles</span></span>
        <div className="right">
          <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.08)"}}>
            <span className="dot" style={{width: 5, height: 5, background: "currentColor", boxShadow: "0 0 6px currentColor"}} />
            coresident
          </span>
          <span className="pid mono">pid {chat?.pid || "—"}</span>
        </div>
      </div>
      <div className="npu-reactor">
        <div className="reactor-core">
          <div className="reactor-disc">
            <div className="lbl">
              FLM<b>v0.9.42</b>
              <div style={{marginTop: 4, color: "var(--fg-4)"}}>--asr 1 --embed 1</div>
            </div>
          </div>
          <div className="reactor-meta">XDNA2 · 8 columns · 1 ctx</div>
        </div>
        <div className="reactor-roles">
          <div className="reactor-role lead">
            <span className="dot ready" />
            <div className="lbl">
              {chat.name}
              <span className="sub">chat · llm · default</span>
            </div>
            <div className="md">{chat.model}</div>
            <div className="met">
              <div><b style={{color: "var(--fg)"}}>{chat.metrics.toks}</b> tok/s</div>
              <div style={{color: "var(--fg-4)"}}>KV {chat.metrics.kv}%</div>
            </div>
          </div>
          <div className="reactor-role">
            <span className="dot coresident" />
            <div className="lbl">
              {stt.name}
              <span className="sub">transcription · passenger</span>
            </div>
            <div className="md">{stt.model}</div>
            <div className="met">
              <div><b style={{color: "var(--fg)"}}>{stt.metrics.xrt}</b> xrt</div>
              <div style={{color: "var(--fg-4)"}}>{stt.metrics.precision}</div>
            </div>
          </div>
          <div className="reactor-role">
            <span className="dot coresident" />
            <div className="lbl">
              {emb.name}
              <span className="sub">embedding · passenger</span>
            </div>
            <div className="md">{emb.model}</div>
            <div className="met">
              <div><b style={{color: "var(--fg)"}}>{emb.metrics.dim}</b> dim</div>
              <div style={{color: "var(--fg-4)"}}>ready</div>
            </div>
          </div>
        </div>
      </div>
      <div className="npu-foot mono">
        <span className="item"><b>~2 GB</b> NPU memory</span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item">swap <b>{chat.name}</b> ▾ <span style={{color: "var(--accent)", cursor: "pointer"}}>change chat model →</span></span>
        <span style={{color: "var(--fg-5)"}}>·</span>
        <span className="item">pauses voice + embed ~14s on swap</span>
      </div>
    </div>
  );
}

// ─── Slots view ───
function SlotsView({ slots: slotsProp, slotVariant, npuVariant, slotParam }) {
  const slotsQuery = useSlots();
  const slots = (slotsQuery.data && slotsQuery.data.length > 0) ? slotsQuery.data : slotsProp;
  const [createOpen, setCreateOpen] = useStateS(false);
  const [createDefaults, setCreateDefaults] = useStateS({});
  const [editName, setEditName] = useStateS(null);
  const [swapName, setSwapName] = useStateS(null);
  const [menuName, setMenuName] = useStateS(null);
  const { active: activeBanners } = useBanners();
  const skipPath = !!activeBanners["skip-path"];

  // Open Edit drawer when route is #slots/:name
  React.useEffect(() => {
    if (slotParam) {
      const exists = HAL0_DATA.slots.find(s => s.name === slotParam);
      if (exists) setEditName(slotParam);
    } else {
      setEditName(null);
    }
  }, [slotParam]);

  // Listen for the N hotkey via global event (wired by main.jsx)
  React.useEffect(() => {
    const onOpen = (e) => {
      const d = (e && e.detail) || {};
      setCreateDefaults(d);
      setCreateOpen(true);
    };
    window.addEventListener("hal0:create-slot", onOpen);
    return () => window.removeEventListener("hal0:create-slot", onOpen);
  }, []);

  // Close menus on outside click
  React.useEffect(() => {
    const off = () => { setSwapName(null); setMenuName(null); };
    document.addEventListener("click", off);
    return () => document.removeEventListener("click", off);
  }, []);

  const groups = {
    chat:  slots.filter(s => s.group === "chat"),
    embed: slots.filter(s => s.group === "embed"),
    voice: slots.filter(s => s.group === "voice"),
    img:   slots.filter(s => s.group === "img"),
    npu:   slots.filter(s => s.group === "npu"),
  };

  const editSlot = HAL0_DATA.slots.find(s => s.name === editName);

  // Seeded slot identities for the skip-path empty layout.
  const SEEDED = [
    { name: "primary", type: "llm",           device: "gpu-rocm", group: "chat"  },
    { name: "coder",   type: "llm",           device: "gpu-rocm", group: "chat"  },
    { name: "embed",   type: "embedding",     device: "gpu-rocm", group: "embed" },
    { name: "rerank",  type: "reranking",     device: "gpu-rocm", group: "embed" },
    { name: "stt",     type: "transcription", device: "cpu",      group: "voice" },
    { name: "tts",     type: "tts",           device: "cpu",      group: "voice" },
    { name: "img",     type: "image",         device: "gpu-rocm", group: "img"   },
  ];
  const openCreatePrefilled = (def) => { setCreateDefaults(def); setCreateOpen(true); };

  const slotWithState = (s, errorMsg) => (
    <SlotCard
      key={s.name}
      slot={s}
      errorMsg={errorMsg}
      swapOpen={swapName === s.name}
      onSwap={(e) => { e.stopPropagation(); setSwapName(swapName === s.name ? null : s.name); setMenuName(null); }}
      onCloseSwap={() => setSwapName(null)}
      menuOpen={menuName === s.name}
      onOverflow={() => { setMenuName(menuName === s.name ? null : s.name); setSwapName(null); }}
      onCloseMenu={() => setMenuName(null)}
      onEdit={() => { window.location.hash = "#slots/" + s.name; }}
    />
  );

  // Skip-path layout: render six seeded empty cards under their default groups.
  if (skipPath) {
    const seededByGroup = {
      chat:  SEEDED.filter(s => s.group === "chat"),
      embed: SEEDED.filter(s => s.group === "embed"),
      voice: SEEDED.filter(s => s.group === "voice"),
      img:   SEEDED.filter(s => s.group === "img"),
    };
    return (
      <div className="view">
        <div className="vh">
          <span className="vh-eye mono">Lifecycle</span>
          <h1>Slots</h1>
          <span className="vh-spacer" />
          <span className="hint mono" style={{color: "var(--accent)"}}>skip-path · six slots seeded · none configured</span>
          <button className="btn ghost" onClick={() => window.location.hash = "#firstrun"}>Pick a bundle instead</button>
          <button className="btn" onClick={() => setCreateOpen(true)}>{Icons.plus} New slot</button>
        </div>

        {["chat", "embed", "voice", "img"].map(g => {
          const cards = seededByGroup[g];
          if (!cards.length) return null;
          return (
            <section key={g} style={{marginBottom: 24}}>
              <div className="sec">
                <h2>{g[0].toUpperCase() + g.slice(1)}<span className="ct mono">{cards.length}</span></h2>
                <div className="rule" />
              </div>
              <div className="slots-grid">
                {cards.map(c => (
                  <EmptySlotCard
                    key={c.name}
                    name={c.name}
                    type={c.type}
                    device={c.device}
                    group={c.group}
                    onConfigure={() => openCreatePrefilled({ name: c.name, type: c.type, device: c.device, group: c.group })}
                  />
                ))}
              </div>
            </section>
          );
        })}

        <CreateSlotModal open={createOpen} onClose={() => setCreateOpen(false)} defaults={createDefaults} />
      </div>
    );
  }

  const renderSlot = (s) => slotVariant === "list"
    ? <SlotListRow key={s.name} slot={s} />
    : slotVariant === "spec"
      ? <SlotCard key={s.name} slot={s} />
      : <SlotCard key={s.name} slot={s} />;

  const renderGroup = (label, items) => {
    if (!items.length) return null;
    if (slotVariant === "list") {
      return (
        <section key={label} style={{marginBottom: 18}}>
          <div className="sec">
            <h2>{label}<span className="ct mono">{items.length}</span></h2>
            <div className="rule" />
          </div>
          <div className="slots-list">
            <div className="slots-list-h">
              <span />
              <span>name</span>
              <span>model</span>
              <span>type · device</span>
              <span>metrics</span>
              <span style={{textAlign: "right"}}>actions</span>
            </div>
            {items.map(s => <SlotListRow key={s.name} slot={s} />)}
          </div>
        </section>
      );
    }
    return (
      <section key={label} style={{marginBottom: 24}}>
        <div className="sec">
          <h2>{label}<span className="ct mono">{items.length}</span></h2>
          <div className="rule" />
        </div>
        <div className={"slots-grid" + (slotVariant === "spec" ? " spec" : "")}>
          {items.map(s => {
            // Demo: show error banner on a single slot if a banner-state would fire
            const errMsg = (window.__hal0Banners && window.__hal0Banners.get && window.__hal0Banners.get()["model-missing"] && s.name === "primary")
              ? "sha256 mismatch on shard 2 — verify the model on /models then retry"
              : null;
            return slotWithState(s, errMsg);
          })}
        </div>
      </section>
    );
  };

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Lifecycle</span>
        <h1>Slots</h1>
        <span className="vh-spacer" />
        <span className="hint">Press <kbd>N</kbd> to create</span>
        <button className="btn" onClick={() => setCreateOpen(true)}>{Icons.plus} New slot</button>
      </div>

      {renderGroup("Chat",  groups.chat)}
      {renderGroup("Embed", groups.embed)}
      {renderGroup("Voice", groups.voice)}
      {renderGroup("Image", groups.img)}

      {groups.npu.length > 0 && (
        <section style={{marginBottom: 24}}>
          <div className="sec">
            <h2>NPU<span className="ct mono">trio · 1 process · 3 roles</span></h2>
            <div className="rule" />
          </div>
          {npuVariant === "reactor" ? <NpuReactor slots={slots} /> : <NpuBlock slots={slots} />}
        </section>
      )}

      <CreateSlotModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        defaults={createDefaults}
      />
      <EditSlotDrawer
        open={!!editSlot}
        slot={editSlot}
        onClose={() => { setEditName(null); window.location.hash = "#slots"; }}
      />
    </div>
  );
}

Object.assign(window, { SlotsView, SlotCard, SlotListRow, NpuBlock, NpuReactor, Spark });
