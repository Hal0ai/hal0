// hal0 dashboard — secondary views: Hardware, Logs, Backends, Agent
//
// Phase B1: Hardware / Backends / Logs read from real hooks. AgentView
// stays on HAL0_DATA mock (deferred; follow-up issue tracks Phase B2 +).

import { useHardware } from '@/api/hooks/useHardware'
import { useBackends } from '@/api/hooks/useBackends'
import { useLogsHistorical, useLogsStream } from '@/api/hooks/useLogs'
import { useLemondRollup } from '@/api/hooks/useLemonade'

const { useState: useStateX } = React;

// ════════════════════════════════════════════════════════════════════
// HARDWARE
// ════════════════════════════════════════════════════════════════════
function HardwareView() {
  // Phase B1: live /api/hardware; mock fallback retains design fixture.
  const hwQuery = useHardware();
  const H = hwQuery.data || HAL0_DATA.host;
  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">System</span>
        <h1>Hardware</h1>
        <span className="vh-spacer" />
        <span className="hint mono">read-only · sourced from /v1/system-info</span>
      </div>

      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16}}>
        <HwCard title="Host" eyebrow="machine">
          <HwRow k="hostname" v={H.name} />
          <HwRow k="kernel" v="Linux 6.17.13-11-pve" />
          <HwRow k="distro" v="Debian 13 (trixie)" />
          <HwRow k="uptime" v={H.uptime} />
          <HwRow k="boot id" v="b3f1a9e2-…-4c81" mono />
        </HwCard>

        <HwCard title="CPU" eyebrow="x86-64">
          <HwRow k="model" v={H.cpu} />
          <HwRow k="cores" v={`${H.cores}`} />
          <HwRow k="clock" v="3.0 GHz base · 5.1 GHz boost" />
          <HwRow k="cache" v="L3 · 64 MB" />
          <HwRow k="recommended" v={<span className="chip ok">llamacpp:cpu</span>} />
        </HwCard>

        <HwCard title="GPU" eyebrow="iGPU · unified memory" full>
          <HwRow k="device" v="AMD Radeon Graphics (gfx1151, Strix Halo)" />
          <HwRow k="vendor stack" v={<>ROCm <span style={{color: "var(--ok)"}}>6.4 ✓</span> · Vulkan <span style={{color: "var(--ok)"}}>present</span></>} />
          <HwRow k="vram model" v="unified · shares system RAM (128 GB)" />
          <HwRow k="recommended" v={<><span className="chip ok">llamacpp:rocm</span> <span className="chip ok">sdcpp:rocm</span></>} />
          <HwRow k="fallback" v={<span className="chip">llamacpp:vulkan</span>} sub="if ROCm fails to load a model" />
        </HwCard>

        <HwCard title="NPU" eyebrow="XDNA2 · coresident trio" full purple>
          <HwRow k="device" v="AMDXDNA2" />
          <HwRow k="topology" v={`${H.npu.columns} columns · ${H.npu.ctx} hardware context`} />
          <HwRow k="runtime" v={<><b>FLM v0.9.42</b> · trio mode (--asr 1 --embed 1)</>} />
          <HwRow k="currently loaded" v="gemma3:1b · whisper-v3-turbo · embed-gemma-300m" mono />
          <HwRow k="recommended" v={<span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>flm:npu</span>} />
        </HwCard>

        <HwCard title="Memory" eyebrow="unified" full>
          <HwRow k="total" v={<><span className="num">{H.ram.total}</span> GB</>} />
          <HwRow k="used" v={<><span className="num">{H.ram.used}</span> GB · 3 models loaded</>} />
          <HwRow k="free" v={<><span className="num" style={{color: "var(--ok)"}}>{H.ram.free}</span> GB</>} />
          <HwRow k="per-type budget" v="4 loaded models" />
          <div style={{padding: "10px 18px", borderTop: "1px solid var(--line-soft)"}}>
            <div style={{display: "flex", height: 6, borderRadius: 1, overflow: "hidden", background: "var(--bg-3)"}}>
              <div style={{width: `${(18.8 / H.ram.total) * 100}%`, background: "var(--dev-rocm)"}} />
              <div style={{width: `${(1.0 / H.ram.total) * 100}%`, background: "var(--dev-npu)"}} />
              <div style={{width: `${(0.35 / H.ram.total) * 100}%`, background: "var(--dev-rocm)", opacity: 0.6}} />
              <div style={{width: `${(0.4 / H.ram.total) * 100}%`, background: "var(--dev-cpu)"}} />
              <div style={{width: `${(33.45 / H.ram.total) * 100}%`, background: "var(--bg-4)"}} />
            </div>
            <div className="mono" style={{display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--fg-4)", marginTop: 6}}>
              <span>primary · agent · embed · tts · free</span>
              <span>{H.ram.used} / {H.ram.total} GB</span>
            </div>
          </div>
        </HwCard>

        <HwCard title="Storage" eyebrow="model cache" full>
          <HwRow k="model dir" v="/var/lib/hal0/models" mono />
          <HwRow k="size" v="46.2 GB · 9 models" />
          <HwRow k="free on /var" v="412 GB" />
          <HwRow k="hf cache" v="/root/.cache/huggingface — 8.4 GB" mono />
        </HwCard>
      </div>
    </div>
  );
}

function HwCard({ title, eyebrow, children, full, purple }) {
  return (
    <div className="card" style={{
      gridColumn: full ? "span 2" : "auto",
      overflow: "hidden",
      ...(purple ? { borderColor: "rgba(200, 150, 255, 0.25)" } : {})
    }}>
      <div style={{padding: "14px 18px", borderBottom: "1px solid var(--line-soft)", background: "var(--bg)"}}>
        <div className="mono" style={{fontSize: 10, color: purple ? "var(--dev-npu)" : "var(--accent)", textTransform: "uppercase", letterSpacing: "0.1em", marginBottom: 4}}>{eyebrow}</div>
        <div className="mono" style={{fontSize: 16, fontWeight: 500, letterSpacing: "-0.02em"}}>{title}</div>
      </div>
      {children}
    </div>
  );
}

function HwRow({ k, v, mono, sub }) {
  return (
    <div style={{padding: "10px 18px", borderBottom: "1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "180px 1fr", gap: 14}}>
      <div style={{fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)", textTransform: "lowercase", letterSpacing: "0.02em"}}>
        {k}
        {sub && <div style={{color: "var(--fg-5)", fontSize: 10, marginTop: 2}}>{sub}</div>}
      </div>
      <div className={mono ? "mono" : ""} style={{fontSize: 12.5, color: "var(--fg)"}}>{v}</div>
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// BACKENDS
// ════════════════════════════════════════════════════════════════════
function BackendsView() {
  const [installB, setInstallB] = useStateX(null);
  const [uninstallB, setUninstallB] = useStateX(null);
  const [flmOpen, setFlmOpen] = useStateX(false);
  // Phase B1: live /api/backends; mock retains fixture shape. The v3
  // hook returns rows shaped `{id, version, state, recommended, kind,
  // device, note}` (envelope matches both legacy + new API).
  const backendsQuery = useBackends();
  const lemond = useLemondRollup();
  const liveBackends = backendsQuery.data?.backends ?? [];
  const backends = liveBackends.length > 0
    ? liveBackends.map(b => ({
        // Coerce v3 envelope onto the prototype's BackendRow shape.
        name: b.id,
        kind: b.kind || (b.id?.split(':')[0] ?? ''),
        device: b.device || (b.id?.split(':')[1] ?? ''),
        ver: b.version,
        state: b.state,
        recommended: b.recommended,
        note: b.note,
      }))
    : HAL0_DATA.backends;
  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Runtime</span>
        <h1>Backends</h1>
        <span className="vh-spacer" />
        <button className="btn ghost">{Icons.search} Discover</button>
      </div>

      <div className="card" style={{padding: 16, marginBottom: 18, display: "flex", alignItems: "center", gap: 14}}>
        <div style={{display: "flex", alignItems: "center", gap: 10, flex: 1}}>
          <span className="dot ready" />
          <div>
            <div className="mono" style={{fontSize: 14, fontWeight: 500}}>lemonade <span style={{color: "var(--fg-3)"}}>· {lemond.version}</span></div>
            <div className="mono" style={{fontSize: 11, color: "var(--fg-4)", marginTop: 2}}>pinned · sha-256 verified · channel stable</div>
          </div>
        </div>
        <div className="mono" style={{fontSize: 11, color: "var(--fg-3)", marginRight: 12}}>uptime 14d</div>
        <button className="btn ghost sm" onClick={() => { window.location.hash = "#logs"; }}>{Icons.logs} Logs</button>
        <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Restarting lemond — brief outage", "warn")}>{Icons.restart} Restart</button>
        <button className="btn sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Checking for lemonade update…", "info")}>{Icons.download} Update</button>
      </div>

      <div className="sec">
        <h2>Backends<span className="ct mono">{backends.length}</span></h2>
        <div className="rule" />
      </div>

      <div className="card" style={{overflow: "hidden"}}>
        <div style={{padding: "10px 18px", borderBottom: "1px solid var(--line)", background: "var(--bg)", display: "grid", gridTemplateColumns: "1fr 220px 140px 1fr auto", gap: 16, fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em"}}>
          <span>backend</span>
          <span>version</span>
          <span>state</span>
          <span>used by</span>
          <span style={{textAlign: "right"}}>actions</span>
        </div>
        {backends.map(b => {
          const slotsUsing = HAL0_DATA.slots.filter(s => {
            if (b.kind === "llamacpp" && s.modelLong && s.modelLong.includes("GGUF") && s.device.includes(b.device)) return true;
            if (b.kind === "whispercpp" && s.type === "transcription" && s.device !== "npu") return true;
            if (b.kind === "sdcpp" && s.type === "image") return true;
            if (b.kind === "kokoro" && s.type === "tts") return true;
            if (b.kind === "flm" && s.device === "npu") return true;
            return false;
          });
          return (
            <div key={b.name} style={{padding: "12px 18px", borderBottom: "1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "1fr 220px 140px 1fr auto", gap: 16, alignItems: "center", fontFamily: "var(--jbm)", fontSize: 12, opacity: b.state === "unavailable" ? 0.55 : 1}}>
              <span style={{color: "var(--fg)", fontWeight: 500, display: "flex", alignItems: "center", gap: 8}}>
                {b.name}
                {b.recommended && <span className="chip amber">★ recommended</span>}
              </span>
              <span style={{color: "var(--fg-3)"}}>{b.ver}{b.note && <span style={{color: "var(--fg-5)", marginLeft: 6}}>· {b.note}</span>}</span>
              <span>
                {b.state === "installed" ? <span className="chip ok">installed</span> : <span className="chip">unavailable</span>}
              </span>
              <span style={{color: "var(--fg-3)", fontSize: 11}}>
                {slotsUsing.length > 0 ? slotsUsing.map(s => s.name).join(", ") : <span style={{color: "var(--fg-5)"}}>—</span>}
              </span>
              <span style={{display: "flex", gap: 4, justifyContent: "flex-end"}}>
                {b.state === "installed" ? (
                  <>
                    <button className="btn ghost sm" onClick={() => b.kind === "flm" ? setFlmOpen(true) : setInstallB(b)}>{Icons.restart} Reinstall</button>
                    <button className="btn ghost sm" onClick={() => setUninstallB(b)}>{Icons.unload}</button>
                  </>
                ) : (
                  <button className="btn ghost sm" disabled>Install</button>
                )}
              </span>
            </div>
          );
        })}
      </div>

      <BackendInstallModal open={!!installB} onClose={() => setInstallB(null)} backend={installB} />
      <BackendUninstallModal open={!!uninstallB} onClose={() => setUninstallB(null)} backend={uninstallB} />
      <FlmDebGuideModal open={flmOpen} onClose={() => setFlmOpen(false)} backend={HAL0_DATA.backends.find(b => b.kind === "flm")} />
    </div>
  );
}

// ════════════════════════════════════════════════════════════════════
// LOGS
// ════════════════════════════════════════════════════════════════════
function LogsView() {
  const [source, setSource] = useStateX("merged");
  const [level, setLevel] = useStateX(null);
  const [slotFilter, setSlotFilter] = useStateX(null);
  const [search, setSearch] = useStateX("");
  const [followTail, setFollowTail] = useStateX(true);
  const [paused, setPaused] = useStateX(false);
  const [pendingCount, setPendingCount] = useStateX(0);
  const scrollRef = React.useRef(null);

  // Phase B1: historical fetch (one-shot) + SSE tail (live).
  // includeLemondWs flips on when source=lemond is selected; that
  // satisfies the design's "raw lemond /logs/stream" requirement
  // without holding the WS open when not needed.
  const historical = useLogsHistorical();
  const live = useLogsStream({ follow: !paused, includeLemondWs: source === 'lemond' });

  // Merge static demo lines + live SSE + historical fetch. Static lines
  // keep the design's grouped-error block (request-id collapsing) visible
  // when no backend yet ships /api/logs.
  const demoLines = [
    { ts: "14:01:58.330", source: "lemond", level: "ok",   slot: "primary", msg: "POST /v1/load model=qwen3.6-27b-mtp-q4_k_m backend=llamacpp:rocm" },
    { ts: "14:01:58.341", source: "lemond", level: "info", slot: "primary", msg: "ggml_init_cublas: found 1 ROCm device gfx1151" },
    { ts: "14:02:00.812", source: "lemond", level: "info", slot: "primary", msg: "llm_load_tensors: offloaded 49/49 layers to GPU" },
    { ts: "14:02:11.290", source: "hal0",   level: "ok",   slot: "primary", msg: "slot:primary state loading → ready · 13.1s" },
    { ts: "14:02:12.117", source: "hal0",   level: "info", slot: null,      msg: "omnirouter: filtered tool set = [generate_image, embed_text, transcribe_audio, route_to_chat]" },
    { ts: "14:02:15.443", source: "hal0",   level: "info", slot: "primary", msg: "session ftr-104 opened persona=primary" },
    { ts: "14:02:18.117", source: "lemond", level: "ok",   slot: "coder",   msg: "POST /v1/load model=qwen3-coder-30b backend=llamacpp:rocm (persona swap)" },
    { ts: "14:02:19.290", source: "hal0",   level: "ok",   slot: "coder",   msg: "tool_call read_file path=src/hal0/launchers/slot_manager.py" },
    { ts: "14:02:20.812", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · vae load · falling back to cpu", group: "req-7f3a" },
    { ts: "14:02:20.890", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · UNet partial offload (ngl 28/32)", group: "req-7f3a" },
    { ts: "14:02:20.911", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · sampler init: euler-a", group: "req-7f3a" },
    { ts: "14:02:20.934", source: "lemond", level: "warn", slot: "img",     msg: "sd-turbo · scheduler: 20 steps cfg 7.0", group: "req-7f3a" },
    { ts: "14:02:22.443", source: "lemond", level: "ok",   slot: "coder",   msg: "/v1/chat/completions coder 38 tok/s TTFT 280ms" },
    { ts: "14:02:28.117", source: "lemond", level: "ok",   slot: "img",     msg: "POST /v1/load model=sd-turbo backend=sdcpp:rocm (tool dispatch)" },
    { ts: "14:02:32.290", source: "hal0",   level: "ok",   slot: "img",     msg: "tool_call generate_image · 4.1s · 2.4 MB" },
    { ts: "14:02:36.117", source: "hal0",   level: "info", slot: "img",     msg: "slot:img state serving → idle" },
  ];
  const sourceLines = (historical.data && historical.data.length > 0)
    ? historical.data
    : [...(HAL0_DATA.journal || []), ...demoLines];
  const buf = [...sourceLines, ...(live.ring || [])]
    .sort((a, b) => (a.ts || '').localeCompare(b.ts || ''));

  const fil = e => {
    if (source !== "merged" && e.source !== source) return false;
    if (level && e.level !== level) return false;
    if (slotFilter && e.slot !== slotFilter) return false;
    if (search && !e.msg.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  };
  const lines = buf.filter(fil);

  // Group adjacent same-group warns into a collapsible block
  const grouped = [];
  let curGroup = null;
  for (const ln of lines) {
    if (ln.group && curGroup && curGroup.id === ln.group) {
      curGroup.items.push(ln);
    } else if (ln.group) {
      curGroup = { id: ln.group, items: [ln], firstTs: ln.ts, source: ln.source, level: ln.level };
      grouped.push({ type: "group", group: curGroup });
    } else {
      curGroup = null;
      grouped.push({ type: "line", line: ln });
    }
  }

  const onScroll = (e) => {
    const el = e.target;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 50;
    if (atBottom !== followTail) setFollowTail(atBottom);
    if (atBottom) setPendingCount(0);
  };
  const jumpToLive = () => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      setFollowTail(true);
      setPendingCount(0);
    }
  };
  // simulate new lines arriving while user has scrolled up
  React.useEffect(() => {
    if (!followTail) {
      const t = setInterval(() => setPendingCount(c => c + 1), 1800);
      return () => clearInterval(t);
    } else {
      setPendingCount(0);
    }
  }, [followTail]);

  const allSlots = [...new Set(buf.map(e => e.slot).filter(Boolean))];

  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Runtime</span>
        <h1>Logs</h1>
        <span className="vh-spacer" />
        <span className="hint mono">{lines.length} lines{paused ? " · paused" : ""}</span>
      </div>

      <div className="card" style={{overflow: "hidden", marginBottom: 12, position: "relative"}}>
        <div style={{padding: "10px 14px", borderBottom: "1px solid var(--line)", display: "flex", alignItems: "center", gap: 8, background: "var(--bg)", flexWrap: "wrap"}}>
          <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden", fontSize: 11}}>
            {[["merged", "merged"], ["hal0", "hal0"], ["lemond", "lemond"]].map(([k, l]) => (
              <button key={k} onClick={() => setSource(k)} style={{padding: "4px 11px", background: source === k ? "var(--accent-soft)" : "transparent", color: source === k ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: k !== "lemond" ? "1px solid var(--line)" : "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}>{l}</button>
            ))}
          </div>
          <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: 4, overflow: "hidden", fontSize: 11, marginLeft: 8}}>
            {[["", "all"], ["ok", "ok"], ["info", "info"], ["warn", "warn"], ["error", "err"]].map(([k, l]) => (
              <button key={l} onClick={() => setLevel(k || null)} style={{padding: "4px 10px", background: (level || "") === k ? "var(--accent-soft)" : "transparent", color: (level || "") === k ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: l !== "err" ? "1px solid var(--line)" : "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}>{l}</button>
            ))}
          </div>
          <select
            className="input mono"
            value={slotFilter || ""}
            onChange={e => setSlotFilter(e.target.value || null)}
            style={{maxWidth: 140, height: 26, fontSize: 11, marginLeft: 8}}
          >
            <option value="">all slots</option>
            {allSlots.map(s => <option key={s} value={s}>slot: {s}</option>)}
          </select>
          <input
            className="input mono"
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="search…"
            style={{flex: 1, minWidth: 120, maxWidth: 280, marginLeft: 8, height: 26, fontSize: 11}}
          />
          <span className="mono" style={{marginLeft: "auto", display: "inline-flex", alignItems: "center", gap: 6, fontSize: 11, color: followTail ? "var(--ok)" : "var(--fg-4)"}}>
            <span className={"dot " + (followTail ? "ready" : "idle")} />
            <span>{followTail ? "follow tail" : "paused tail"}</span>
          </span>
          <button className="btn ghost sm" onClick={() => setPaused(p => !p)}>{paused ? "Resume" : "Pause"}</button>
          <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Exporting .log — stubbed", "info")}>{Icons.download}</button>
        </div>

        <div
          ref={scrollRef}
          onScroll={onScroll}
          style={{background: "#070707", maxHeight: "calc(100vh - 280px)", overflowY: "auto", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.6, position: "relative"}}
        >
          {grouped.map((g, i) => g.type === "line"
            ? <LogLine key={i} e={g.line} search={search} />
            : <LogGroup key={i} group={g.group} search={search} />
          )}
          {paused && (
            <div style={{padding: "12px 16px", textAlign: "center", color: "var(--warn)", fontSize: 11, background: "rgba(232,185,78,0.08)", borderTop: "1px solid var(--warn-line)"}}>
              ⏸ stream paused · resume to drain buffer
            </div>
          )}
        </div>

        {!followTail && (
          <button
            onClick={jumpToLive}
            style={{
              position: "absolute",
              right: 20,
              bottom: 20,
              background: "var(--accent)",
              color: "#0a0a0a",
              border: "1px solid var(--accent)",
              borderRadius: 999,
              padding: "8px 14px",
              fontFamily: "var(--jbm)",
              fontSize: 11.5,
              fontWeight: 600,
              cursor: "pointer",
              boxShadow: "0 8px 24px -4px rgba(0,0,0,0.5)",
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
            }}
          >
            ↓ Jump to live
            {pendingCount > 0 && <span style={{background: "#0a0a0a", color: "var(--accent)", padding: "1px 6px", borderRadius: 999, fontSize: 10}}>+{pendingCount}</span>}
          </button>
        )}
      </div>
    </div>
  );
}

function LogLine({ e, search }) {
  const msg = search && e.msg.toLowerCase().includes(search.toLowerCase())
    ? highlightSearch(e.msg, search)
    : e.msg;
  return (
    <div style={{padding: "2px 16px", display: "grid", gridTemplateColumns: "100px 78px 60px 80px 1fr", gap: 12, borderLeft: e.level === "warn" ? "2px solid var(--warn)" : e.level === "error" ? "2px solid var(--err)" : "2px solid transparent"}}>
      <span style={{color: "var(--fg-5)"}}>{e.ts}</span>
      <span style={{color: e.source === "lemond" ? "var(--dev-vulkan)" : "var(--accent)"}}>{e.source}</span>
      <span style={{color: e.level === "ok" ? "var(--ok)" : e.level === "warn" ? "var(--warn)" : e.level === "error" ? "var(--err)" : "var(--fg-3)"}}>{e.level}</span>
      <span style={{color: e.slot ? "var(--fg-2)" : "var(--fg-5)"}}>{e.slot || "—"}</span>
      <span style={{color: "var(--fg-2)"}}>{msg}</span>
    </div>
  );
}

function LogGroup({ group, search }) {
  const [open, setOpen] = useStateX(false);
  const head = group.items[0];
  const rest = group.items.length - 1;
  return (
    <>
      <div
        style={{padding: "2px 16px", display: "grid", gridTemplateColumns: "100px 78px 60px 80px 1fr", gap: 12, borderLeft: "2px solid var(--warn)", cursor: "pointer", background: open ? "rgba(232,185,78,0.05)" : "transparent"}}
        onClick={() => setOpen(o => !o)}
      >
        <span style={{color: "var(--fg-5)"}}>{head.ts}</span>
        <span style={{color: "var(--dev-vulkan)"}}>{head.source}</span>
        <span style={{color: "var(--warn)"}}>{head.level}</span>
        <span style={{color: "var(--fg-2)"}}>{head.slot || "—"}</span>
        <span style={{color: "var(--fg-2)", display: "flex", alignItems: "center", gap: 8}}>
          {open ? "▾" : "▸"} <b style={{color: "var(--fg)", fontWeight: 500}}>{head.msg}</b>
          <span style={{color: "var(--fg-4)", fontSize: 10, marginLeft: 4}}>+ {rest} more · request {group.id}</span>
        </span>
      </div>
      {open && group.items.slice(1).map((ln, i) => (
        <div key={i} style={{padding: "2px 16px 2px 32px", display: "grid", gridTemplateColumns: "84px 78px 60px 80px 1fr", gap: 12, color: "var(--fg-3)", borderLeft: "2px solid rgba(232,185,78,0.4)", background: "rgba(232,185,78,0.03)"}}>
          <span style={{color: "var(--fg-5)"}}>{ln.ts}</span>
          <span style={{color: "var(--dev-vulkan)"}}>{ln.source}</span>
          <span style={{color: "var(--warn)"}}>{ln.level}</span>
          <span>{ln.slot || "—"}</span>
          <span>{ln.msg}</span>
        </div>
      ))}
    </>
  );
}

function highlightSearch(text, q) {
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <span style={{background: "var(--accent-soft)", color: "var(--accent)", padding: "0 2px", borderRadius: 2}}>{text.slice(i, i + q.length)}</span>
      {text.slice(i + q.length)}
    </>
  );
}

// ════════════════════════════════════════════════════════════════════
// AGENT (chat, skills, memory, personas)
// ════════════════════════════════════════════════════════════════════
function AgentView() {
  const [tab, setTab] = useStateX("overview");
  const [editPersona, setEditPersona] = useStateX(null);
  const [resetOpen, setResetOpen] = useStateX(false);
  const noAgent = window.__hal0Banners && window.__hal0Banners.get && window.__hal0Banners.get()["no-agent"];
  const tabs = [
    { id: "overview", label: "Overview" },
    { id: "inbox",    label: "Inbox" },
    { id: "skills",   label: "Skills" },
    { id: "memory",   label: "Memory" },
    { id: "personas", label: "Personas" },
    { id: "peers",    label: "Peers" },
  ];
  return (
    <div className="view">
      <div className="vh">
        <span className="vh-eye mono">Tools</span>
        <h1>Agent</h1>
        <span className="vh-spacer" />
        <span className="hint mono">scaffolded · v0.2.1 · full surface in v0.3</span>
      </div>

      <div style={{display: "flex", gap: 0, borderBottom: "1px solid var(--line)", marginBottom: 18}}>
        {tabs.map(t => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            style={{
              padding: "10px 16px",
              background: "transparent",
              border: "none",
              borderBottom: tab === t.id ? "2px solid var(--accent)" : "2px solid transparent",
              color: tab === t.id ? "var(--accent)" : "var(--fg-3)",
              fontFamily: "var(--jbm)",
              fontSize: 12.5,
              cursor: "pointer",
              fontWeight: 500,
            }}
          >{t.label}</button>
        ))}
      </div>

      {tab === "overview" && (noAgent ? <NoBundledAgentCard /> : <AgentOverview />)}
      {tab === "inbox"    && (noAgent ? <EmptyInbox /> : <AgentInbox />)}
      {tab === "skills"   && <AgentSkills />}
      {tab === "memory"   && <AgentMemory onResetNs={() => setResetOpen(true)} />}
      {tab === "personas" && <AgentPersonas onEdit={(p) => setEditPersona(p)} />}
      {tab === "peers"    && <AgentPeers />}

      <PersonaEditModal
        open={!!editPersona}
        persona={editPersona}
        onClose={() => setEditPersona(null)}
      />
      <ConfirmDialog
        open={resetOpen}
        onCancel={() => setResetOpen(false)}
        onConfirm={() => { setResetOpen(false); window.__hal0Toast && window.__hal0Toast("Cognee namespace 'shared' reset — 2,847 records deleted", "warn"); }}
        title="Reset memory namespace 'shared'?"
        message={<span>This permanently deletes <span className="mono" style={{color: "var(--fg)"}}>2,847</span> Cognee records across SQLite + LanceDB + Kuzu. Cannot be undone.</span>}
        confirmLabel="Reset namespace"
        destructive
        typeToConfirm="shared"
      />
    </div>
  );
}

// Empty inbox state (no agent installed)
function EmptyInbox() {
  return (
    <div className="card" style={{padding: 40, textAlign: "center", borderStyle: "dashed"}}>
      <div className="mono" style={{fontSize: 14, color: "var(--fg-3)", marginBottom: 6}}>No pending approvals.</div>
      <div className="mono" style={{fontSize: 11, color: "var(--fg-5)"}}>Gated tool calls from agents will appear here once an agent is installed.</div>
    </div>
  );
}

function AgentOverview() {
  return (
    <div style={{display: "grid", gridTemplateColumns: "1fr 320px", gap: 16}}>
      <div>
        <div className="sec">
          <h2>Bundled agent</h2>
          <div className="rule" />
        </div>
        <div className="card" style={{padding: 22, marginBottom: 18}}>
          <div style={{display: "flex", alignItems: "center", gap: 14, marginBottom: 14}}>
            <div style={{width: 44, height: 44, borderRadius: 8, background: "var(--accent-soft)", border: "1px solid var(--accent-line)", display: "inline-flex", alignItems: "center", justifyContent: "center"}}>
              {Icons.agent}
            </div>
            <div>
              <div className="mono" style={{fontSize: 16, fontWeight: 500, letterSpacing: "-0.02em"}}>Hermes-Agent</div>
              <div className="mono" style={{fontSize: 11, color: "var(--fg-3)", marginTop: 2}}>service · hal0-agent-hermes.service · running</div>
            </div>
            <span style={{marginLeft: "auto"}} className="chip ok">running · 14d</span>
          </div>
          <p style={{fontSize: 13, color: "var(--fg-2)", margin: "0 0 16px", maxWidth: 640, lineHeight: 1.55}}>
            Hermes is the resident agent. It exposes a chat surface, executes skills via MCP, writes to Cognee memory, and respects the approval policy from Settings. It runs as a systemd service for v0.2.1; CLI shape (pi-coder) lands in v0.3.
          </p>
          <div style={{display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 0, border: "1px solid var(--line)", borderRadius: "var(--rad)", overflow: "hidden"}}>
            {[
              { l: "approvals", v: "3", sub: "pending" },
              { l: "skills",    v: "12", sub: "wired" },
              { l: "memory",    v: "847", sub: "writes" },
              { l: "persona",   v: "hermes", sub: "default" },
            ].map((s, i) => (
              <div key={i} style={{padding: 14, borderRight: i < 3 ? "1px solid var(--line)" : "none"}}>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em"}}>{s.l}</div>
                <div className="mono num" style={{fontSize: 20, color: "var(--fg)", letterSpacing: "-0.02em", marginTop: 4}}>{s.v}</div>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>{s.sub}</div>
              </div>
            ))}
          </div>
          <div style={{marginTop: 14, display: "flex", gap: 8}}>
            <button className="btn">{Icons.send} Open chat with Hermes</button>
            <button className="btn ghost">{Icons.logs} View activity</button>
            <button className="btn ghost">{Icons.restart} Restart service</button>
          </div>
        </div>

        <div className="sec">
          <h2>Alternative: pi-coder<span className="ct mono">CLI · v0.3</span></h2>
          <div className="rule" />
        </div>
        <div className="card" style={{padding: 18, opacity: 0.7}}>
          <div className="mono" style={{fontSize: 13, marginBottom: 6}}>@earendil-works/pi-coding-agent</div>
          <div className="mono" style={{fontSize: 11, color: "var(--fg-3)", marginBottom: 12}}>CLI shape · 4 tools · invoked per-task · not installed</div>
          <button className="btn ghost sm" disabled>Install pi-coder (v0.3)</button>
        </div>
      </div>

      <div>
        <div className="side-card">
          <div className="side-card-h">
            <span>Recent activity</span>
          </div>
          <div className="side-card-b">
            {[
              { ts: "14:02:09", text: "request model_pull user.Phi-4-Mini", st: "pending" },
              { ts: "14:01:42", text: "wrote /scratch/notes/draft.md", st: "approved" },
              { ts: "14:00:18", text: "shell rg \"TODO\" src/", st: "pending" },
              { ts: "13:58:11", text: "wrote cognee record (847)", st: "auto" },
              { ts: "13:54:33", text: "read src/hal0/launchers/", st: "auto" },
              { ts: "13:49:02", text: "denied: shell rm -rf /tmp/cache", st: "denied" },
            ].map((a, i) => (
              <div key={i} style={{padding: "8px 0", borderBottom: "1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "64px 1fr 70px", gap: 8, fontFamily: "var(--jbm)", fontSize: 11}}>
                <span style={{color: "var(--fg-5)"}}>{a.ts}</span>
                <span style={{color: "var(--fg-2)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap"}}>{a.text}</span>
                <span style={{textAlign: "right"}}>
                  {a.st === "pending"  && <span className="chip warn">pending</span>}
                  {a.st === "approved" && <span className="chip ok">ok</span>}
                  {a.st === "auto"     && <span className="chip">auto</span>}
                  {a.st === "denied"   && <span className="chip err">denied</span>}
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

function AgentInbox() {
  return (
    <div>
      <div className="card" style={{padding: 16, marginBottom: 14, display: "flex", alignItems: "center", gap: 12, borderColor: "var(--warn-line)", background: "var(--warn-soft)"}}>
        <span style={{color: "var(--warn)"}}>{Icons.warn}</span>
        <span className="mono" style={{fontSize: 12, color: "var(--warn)"}}>
          <b>3 pending approvals.</b> Hermes is paused on these calls until you resolve them.
        </span>
        <span className="mono" style={{marginLeft: "auto", fontSize: 11, color: "var(--fg-4)"}}>policy: registry-write · always</span>
      </div>
      {HAL0_DATA.approvals.map((a, i) => (
        <div key={i} className="card" style={{padding: 0, marginBottom: 10, overflow: "hidden"}}>
          <div style={{padding: "12px 16px", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", gap: 12, fontFamily: "var(--jbm)", fontSize: 12}}>
            <span style={{color: "var(--fg-4)"}}>{a.ts}</span>
            <span style={{color: "var(--accent)", fontWeight: 500}}>{a.agent}</span>
            <span style={{color: "var(--fg-4)"}}>requests</span>
            <span style={{color: "var(--fg)", fontWeight: 500}}>{a.tool}</span>
            <span style={{marginLeft: "auto"}}>
              <span className="chip warn">awaiting approval · 14s</span>
            </span>
          </div>
          <div style={{padding: "14px 16px", fontFamily: "var(--jbm)", fontSize: 12, display: "grid", gap: 6}}>
            <div style={{display: "grid", gridTemplateColumns: "120px 1fr", gap: 12}}>
              <span style={{color: "var(--fg-4)"}}>argument</span>
              <span style={{color: "var(--fg-2)"}}>{a.arg}</span>
            </div>
            <div style={{display: "grid", gridTemplateColumns: "120px 1fr", gap: 12}}>
              <span style={{color: "var(--fg-4)"}}>capability</span>
              <span style={{color: "var(--fg-2)"}}>{a.tool.startsWith("model_") ? "registry-write" : a.tool.startsWith("fs_") ? "fs-write" : "shell-exec"}</span>
            </div>
            <div style={{display: "grid", gridTemplateColumns: "120px 1fr", gap: 12}}>
              <span style={{color: "var(--fg-4)"}}>reason</span>
              <span style={{color: "var(--fg-2)"}}>{a.tool === "model_pull" ? "user asked Hermes to set up Phi-4-Mini for offline routing" : a.tool === "fs_write" ? "draft notes from current chat session" : "code search across src/"}</span>
            </div>
          </div>
          <div style={{padding: "10px 16px", borderTop: "1px solid var(--line-soft)", background: "var(--bg)", display: "flex", gap: 6, justifyContent: "flex-end"}}>
            <button className="btn danger sm">Deny</button>
            <button className="btn ghost sm">Deny + remember</button>
            <button className="btn ghost sm">Approve once</button>
            <button className="btn sm">Approve + remember</button>
          </div>
        </div>
      ))}
    </div>
  );
}

function AgentSkills() {
  const skills = [
    { name: "read_file",       cap: "fs-read",      policy: "remember", calls: 247, src: "builtin" },
    { name: "write_file",      cap: "fs-write",     policy: "always",   calls: 38,  src: "builtin" },
    { name: "edit_file",       cap: "fs-write",     policy: "always",   calls: 14,  src: "builtin" },
    { name: "list_dir",        cap: "fs-read",      policy: "remember", calls: 41,  src: "builtin" },
    { name: "shell_exec",      cap: "shell-exec",   policy: "always",   calls: 9,   src: "builtin" },
    { name: "model_pull",      cap: "registry-write", policy: "always", calls: 3,   src: "hal0-router" },
    { name: "restart_slot",    cap: "slot-control", policy: "always",   calls: 1,   src: "hal0-router" },
    { name: "generate_image",  cap: "tool-call",    policy: "auto",     calls: 18,  src: "omnirouter" },
    { name: "transcribe_audio",cap: "tool-call",    policy: "auto",     calls: 7,   src: "omnirouter" },
    { name: "text_to_speech",  cap: "tool-call",    policy: "auto",     calls: 22,  src: "omnirouter" },
    { name: "embed_text",      cap: "tool-call",    policy: "auto",     calls: 184, src: "omnirouter" },
    { name: "rerank_documents",cap: "tool-call",    policy: "auto",     calls: 41,  src: "omnirouter" },
  ];
  return (
    <div>
      <div className="card" style={{overflow: "hidden"}}>
        <div style={{padding: "10px 18px", background: "var(--bg)", borderBottom: "1px solid var(--line)", display: "grid", gridTemplateColumns: "200px 160px 1fr 120px 80px auto", gap: 16, fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em"}}>
          <span>skill</span>
          <span>capability</span>
          <span>source</span>
          <span>policy</span>
          <span style={{textAlign: "right"}}>calls</span>
          <span></span>
        </div>
        {skills.map(s => (
          <div key={s.name} style={{padding: "11px 18px", borderBottom: "1px solid var(--line-soft)", display: "grid", gridTemplateColumns: "200px 160px 1fr 120px 80px auto", gap: 16, alignItems: "center", fontFamily: "var(--jbm)", fontSize: 12}}>
            <span style={{color: "var(--fg)", fontWeight: 500}}>{s.name}</span>
            <span style={{color: "var(--fg-3)"}}>{s.cap}</span>
            <span style={{color: "var(--fg-4)"}}>{s.src}</span>
            <span>
              {s.policy === "always"   && <span className="chip warn">always</span>}
              {s.policy === "remember" && <span className="chip ok">remember</span>}
              {s.policy === "auto"     && <span className="chip">auto</span>}
              {s.policy === "deny"     && <span className="chip err">deny</span>}
            </span>
            <span style={{textAlign: "right", color: "var(--fg-2)"}} className="num">{s.calls}</span>
            <button className="btn ghost sm">{Icons.edit}</button>
          </div>
        ))}
      </div>
      <div style={{marginTop: 14, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
        12 skills wired · 8 require approval · 4 auto via OmniRouter · skill source includes builtin, hal0-router, omnirouter, and any user-added MCP servers (none configured).
      </div>
    </div>
  );
}

function AgentMemory({ onResetNs }) {
  return (
    <div style={{display: "grid", gridTemplateColumns: "1fr 320px", gap: 16}}>
      <div>
        <div className="card" style={{padding: 18, marginBottom: 14}}>
          <div style={{display: "flex", alignItems: "center", gap: 12, marginBottom: 14}}>
            <span className="mono" style={{fontSize: 10, color: "var(--accent)", textTransform: "uppercase", letterSpacing: "0.1em"}}>Cognee · shared</span>
            <span className="mono num" style={{fontSize: 24, color: "var(--fg)", letterSpacing: "-0.02em"}}>2,847</span>
            <span className="mono" style={{fontSize: 12, color: "var(--fg-3)"}}>records · 184 MB</span>
            <span style={{marginLeft: "auto"}} className="chip ok">healthy</span>
          </div>
          <div style={{display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 0, border: "1px solid var(--line)", borderRadius: "var(--rad)", overflow: "hidden"}}>
            {[
              { l: "SQLite", v: "847", sub: "indexed text" },
              { l: "LanceDB", v: "2,140", sub: "vectors · 768d" },
              { l: "Kuzu",   v: "412", sub: "graph edges" },
            ].map((s, i) => (
              <div key={i} style={{padding: 14, borderRight: i < 2 ? "1px solid var(--line)" : "none"}}>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em"}}>{s.l}</div>
                <div className="mono num" style={{fontSize: 22, color: "var(--fg)", marginTop: 4}}>{s.v}</div>
                <div className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>{s.sub}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="sec"><h2>Recent records</h2><div className="rule" /></div>
        <div className="card" style={{overflow: "hidden"}}>
          {[
            { ts: "14:02:11", source: "hermes", kind: "fact",       body: "user prefers frozen dataclasses for SlotState types" },
            { ts: "14:00:42", source: "hermes", kind: "convo",       body: "session ftr-104 — refactor of slot_manager.py" },
            { ts: "13:58:18", source: "hermes", kind: "code-ref",    body: "slot.py:42 — SlotState dataclass with slots=True" },
            { ts: "13:54:01", source: "hermes", kind: "skill-trace", body: "read_file → src/hal0/launchers/slot_manager.py (3 calls)" },
            { ts: "13:50:33", source: "user",   kind: "preference",  body: "models page: sort installed first" },
          ].map((r, i) => (
            <div key={i} style={{padding: "12px 18px", borderBottom: "1px solid var(--line-soft)", fontFamily: "var(--jbm)", fontSize: 12}}>
              <div style={{display: "flex", gap: 10, marginBottom: 4}}>
                <span style={{color: "var(--fg-5)"}}>{r.ts}</span>
                <span style={{color: "var(--accent)"}}>{r.source}</span>
                <span className="chip">{r.kind}</span>
              </div>
              <div style={{color: "var(--fg-2)", paddingLeft: 0}}>{r.body}</div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <div className="side-card">
          <div className="side-card-h"><span>Namespaces</span></div>
          <div className="side-card-b">
            {[
              { name: "shared", desc: "default · all agents", recs: 2847, active: true },
              { name: "scratch", desc: "ephemeral · auto-prune", recs: 84,  active: false },
              { name: "code",    desc: "code refs only",       recs: 412, active: false },
            ].map(n => (
              <div key={n.name} style={{padding: "10px 0", borderBottom: "1px solid var(--line-soft)", display: "flex", alignItems: "center", gap: 10, fontFamily: "var(--jbm)", fontSize: 12}}>
                <span className={"dot " + (n.active ? "ready" : "idle")} />
                <div>
                  <div style={{color: "var(--fg)", fontWeight: 500}}>{n.name}</div>
                  <div style={{color: "var(--fg-4)", fontSize: 10}}>{n.desc}</div>
                </div>
                <span style={{marginLeft: "auto", color: "var(--fg-3)"}} className="num">{n.recs}</span>
              </div>
            ))}
            <button className="btn ghost sm" style={{marginTop: 10, width: "100%", justifyContent: "center"}} onClick={() => window.__hal0Toast && window.__hal0Toast("New namespace modal — stubbed", "info")}>{Icons.plus} New namespace</button>
            <button className="btn danger sm" style={{marginTop: 6, width: "100%", justifyContent: "center"}} onClick={onResetNs}>{Icons.warn} Reset 'shared'</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function AgentPersonas({ onEdit }) {
  const personas = [
    { name: "hermes",        slot: "primary", model: "qwen3.6-27b-mtp",  tone: "operator", desc: "Default — terse, technical, runs skills aggressively. Wired to the dashboard chat surface.", active: true },
    { name: "hermes-coder",  slot: "coder",   model: "qwen3-coder-30b", tone: "code-focused", desc: "Swaps in when the persona dropdown picks coder. Optimised for refactors and review." },
    { name: "hermes-npu",    slot: "agent",   model: "gemma3:1b",       tone: "low-latency", desc: "NPU coresident · for short follow-ups while keeping voice+embed warm." },
    { name: "+ custom",      slot: null,      model: "",                tone: "",            desc: "Add a persona — pick a chat slot, set a system prompt, and pick a skill set.", isAdd: true },
  ];
  return (
    <div style={{display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 14}}>
      {personas.map((p, i) => (
        <div key={i} className="card" style={{padding: 18, position: "relative", borderColor: p.active ? "var(--accent-line)" : "var(--line)", borderStyle: p.isAdd ? "dashed" : "solid"}}>
          {p.active && <div style={{position: "absolute", top: 0, left: 0, right: 0, height: 2, background: "var(--accent)"}} />}
          <div style={{display: "flex", alignItems: "center", gap: 10, marginBottom: 10}}>
            <div style={{width: 36, height: 36, borderRadius: 6, background: p.isAdd ? "var(--bg-2)" : "var(--accent-soft)", border: "1px solid " + (p.isAdd ? "var(--line)" : "var(--accent-line)"), display: "inline-flex", alignItems: "center", justifyContent: "center", color: p.isAdd ? "var(--fg-4)" : "var(--accent)"}}>
              {p.isAdd ? Icons.plus : Icons.agent}
            </div>
            <div>
              <div className="mono" style={{fontSize: 14, fontWeight: 500, letterSpacing: "-0.01em"}}>{p.name}</div>
              {p.slot && <div className="mono" style={{fontSize: 11, color: "var(--fg-3)", marginTop: 2}}>routes to slot <b style={{color: "var(--accent)"}}>{p.slot}</b> · {p.model}</div>}
            </div>
            {p.active && <span style={{marginLeft: "auto"}} className="chip amber">active</span>}
          </div>
          <p style={{fontSize: 12.5, color: "var(--fg-2)", margin: "0 0 12px", lineHeight: 1.55}}>{p.desc}</p>
          {!p.isAdd && (
            <div style={{display: "flex", gap: 6, alignItems: "center"}}>
              <span className="chip">{p.tone}</span>
              <span style={{marginLeft: "auto", display: "flex", gap: 6}}>
                <button className="btn ghost sm" onClick={() => onEdit && onEdit(p)}>{Icons.edit} Edit</button>
                {!p.active && <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Persona ${p.name} activated`, "ok")}>Activate</button>}
              </span>
            </div>
          )}
          {p.isAdd && (
            <div style={{display: "flex", justifyContent: "flex-end"}}>
              <button className="btn ghost sm" onClick={() => onEdit && onEdit(p)}>{Icons.plus} Create persona</button>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

// ── AgentPeers (#247) ───────────────────────────────────────────────────────
//
// Reads identity cards from the `agents` Cognee dataset (ADR-0011) via
// the hal0-memory MCP. Renders one row per card with a TCP-ping
// reachability dot (no persistent stored field — pinged on render).
//
// Cards are immutable per ADR-0011 §2; this panel is read-only.

function AgentPeers() {
  const [cards, setCards] = useStateX([]);
  const [loading, setLoading] = useStateX(true);
  const [err, setErr] = useStateX(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await fetch("/mcp/memory", {
          method: "POST",
          headers: { "Content-Type": "application/json", "X-hal0-Agent": "hal0-dashboard" },
          body: JSON.stringify({
            jsonrpc: "2.0",
            id: 1,
            method: "tools/call",
            params: {
              name: "memory_search",
              arguments: {
                query: "agent identity",
                tags: ["agent-identity"],
                dataset: "agents",
                limit: 50,
              },
            },
          }),
        });
        const data = await resp.json();
        if (cancelled) return;
        const items = (data && data.result && data.result.items) || [];
        setCards(items);
        setLoading(false);
      } catch (e) {
        if (!cancelled) {
          setErr(String(e));
          setLoading(false);
        }
      }
    })();
    return () => { cancelled = true; };
  }, []);

  if (loading) {
    return <div className="card" style={{padding: 20, color: "var(--fg-3)"}}>Loading peers…</div>;
  }
  if (err) {
    return <div className="card" style={{padding: 20, color: "var(--err)"}}>memory MCP unreachable: {err}</div>;
  }
  if (!cards.length) {
    return (
      <div className="card" style={{padding: 40, textAlign: "center", borderStyle: "dashed"}}>
        <div className="mono" style={{fontSize: 14, color: "var(--fg-3)", marginBottom: 6}}>No agent identity cards published yet.</div>
        <div className="mono" style={{fontSize: 11, color: "var(--fg-5)"}}>Cards appear here when a bundled agent finishes <code>hal0 agent bootstrap</code>.</div>
      </div>
    );
  }
  return (
    <div style={{display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(300px, 1fr))", gap: 12}}>
      {cards.map((c, i) => <PeerCard key={i} card={c} />)}
    </div>
  );
}

function PeerCard({ card }) {
  const md = (card && card.metadata) || {};
  const endpoint = md.endpoint || {};
  const hs = md.hal0_state || {};
  const roles = md.roles || [];
  const [reach, setReach] = useStateX("checking");
  const [expanded, setExpanded] = useStateX(false);

  React.useEffect(() => {
    let cancelled = false;
    const url = endpoint.url;
    if (!url) { setReach("none"); return; }
    const ctrl = new AbortController();
    const tid = setTimeout(() => ctrl.abort(), 5000);
    (async () => {
      try {
        await fetch(url, { method: "HEAD", signal: ctrl.signal, mode: "no-cors" });
        if (!cancelled) setReach("ok");
      } catch (e) {
        if (cancelled) return;
        setReach(e.name === "AbortError" ? "timeout" : "error");
      } finally {
        clearTimeout(tid);
      }
    })();
    return () => { cancelled = true; ctrl.abort(); };
  }, [endpoint.url]);

  const dotColor = reach === "ok" ? "var(--ok)" : reach === "timeout" ? "var(--warn)" : reach === "error" ? "var(--err)" : "var(--fg-5)";

  return (
    <div className="card" style={{padding: 16, display: "flex", flexDirection: "column", gap: 8}}>
      <div style={{display: "flex", alignItems: "center", gap: 10}}>
        <span style={{width: 8, height: 8, borderRadius: "50%", background: dotColor}} aria-label={`endpoint ${reach}`} />
        <div className="mono" style={{fontSize: 14, fontWeight: 500}}>{md.display_name || md.agent_id || "(unnamed)"}</div>
      </div>
      <div className="mono" style={{fontSize: 11, color: "var(--fg-3)"}}>{md.agent_id || "—"}</div>
      {roles.length > 0 && (
        <div style={{display: "flex", flexWrap: "wrap", gap: 4}}>
          {roles.map((r, i) => <span key={i} className="chip">{r}</span>)}
        </div>
      )}
      <div className="mono" style={{fontSize: 10.5, color: "var(--fg-4)"}}>
        endpoint: {endpoint.url || "(none)"}<br />
        registered: {hs.registered_at || "—"}
      </div>
      <button
        onClick={() => setExpanded(e => !e)}
        className="mono"
        style={{
          marginTop: 4,
          padding: "4px 8px",
          fontSize: 10,
          background: "transparent",
          border: "1px solid var(--line)",
          borderRadius: 4,
          color: "var(--fg-3)",
          cursor: "pointer",
          alignSelf: "flex-start",
        }}
      >{expanded ? "hide" : "show"} metadata</button>
      {expanded && (
        <pre className="mono" style={{fontSize: 10, color: "var(--fg-3)", overflow: "auto", maxHeight: 220, margin: 0, padding: 8, background: "var(--bg-2)", borderRadius: 4}}>
          {JSON.stringify(md, null, 2)}
        </pre>
      )}
    </div>
  );
}

Object.assign(window, { HardwareView, BackendsView, LogsView, AgentView, AgentPeers, PeerCard });
