// hal0 dashboard — Dashboard view (chat, snapshot, hero)
//
// Phase B1: SnapshotStrip + PersonaPicker drive off `useSlots()`; the
// chat composer keeps its prototype shell + scripted demo bubbles
// (Phase B2 owns real chat wiring against /v1/chat/completions).

import { useSlots } from '@/api/hooks/useSlots'
import { useLemondRollup } from '@/api/hooks/useLemonade'
import { useHardware } from '@/api/hooks/useHardware'

const { useState: useStateD, useRef: useRefD, useEffect: useEffectD } = React;

// ─── Snapshot strip ───
function SnapshotStrip({ slots, onGo }) {
  return (
    <div className="snap">
      <div className="snap-head">
        <span>Slot snapshot</span>
        <span className="ct mono">{slots.filter(s => s.state === "ready" || s.state === "serving" || s.state === "idle").length}/{slots.length} ready</span>
        <span className="right mono" onClick={() => onGo("slots")}>Manage slots →</span>
      </div>
      <div className="snap-rows">
        {slots.map(s => (
          <div key={s.name} className="snap-row" onClick={() => onGo("slots/" + s.name)}>
            <span className={"dot " + s.state} />
            <span className="name mono">{s.name}</span>
            <span className="model mono">{s.model}</span>
            <span className={"chip dev-" + (s.device || "cpu").replace("gpu-", "")}>{s.device}</span>
            <span className="badge">
              {s.isDefault && <span className="chip outlined amber">default</span>}
              {s.coresident && <span className="chip" style={{color: "var(--dev-npu)", borderColor: "rgba(200,150,255,0.30)", background: "rgba(200,150,255,0.06)"}}>coresident</span>}
              {s.cpuOnly && <span className="chip">[CPU]</span>}
            </span>
            <span className="num mono" style={{color: "var(--fg-3)", fontSize: 11, textAlign: "right"}}>
              {s.state === "serving" ? `${s.metrics.toks} tok/s` : s.state}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Persona dropdown ───
function PersonaPicker({ slots, current, onPick, open, onToggle, noTools }) {
  if (!slots || slots.length === 0) return null;
  const cur = slots.find(s => s.name === current) || slots[0];
  const chatSlots = slots.filter(s => s.type === "llm");
  return (
    <div className="persona" onClick={onToggle}>
      <span className="dot" />
      <span className="nm">
        Persona <b>{cur.name}</b><span className="sub">· {(cur.device || "cpu").replace("gpu-", "")}</span>
      </span>
      {noTools && <span className="chip warn" style={{marginLeft: 6}}>no tools</span>}
      <span className="chev">{Icons.chev}</span>
      {open && (
        <div className="persona-menu" onClick={e => e.stopPropagation()}>
          <div className="pm-h">Chat personas</div>
          {chatSlots.map(s => {
            const isNpu = s.device === "npu";
            const isCur = s.name === cur.name;
            return (
              <div
                key={s.name}
                className={"pm-item" + (isCur ? " active" : "")}
                onClick={() => { onPick(s.name); onToggle(); }}
              >
                <span className={"dot " + s.state} />
                <div>
                  <div className="name">{s.name} {s.isDefault && <span style={{color: "var(--accent)", fontSize: 10, marginLeft: 4}}>· default</span>}</div>
                  <div className="sub">{s.model} · {(s.device || "cpu").replace("gpu-", "")}</div>
                  {isNpu && !isCur && cur.device === "npu" && (
                    <div className="warn">Pauses voice + embed ~14s while FLM swaps</div>
                  )}
                  {isNpu && !isCur && cur.device !== "npu" && (
                    <div className="sub" style={{color: "var(--dev-npu)"}}>coresident with stt-npu + embed-npu</div>
                  )}
                </div>
                <span style={{color: "var(--fg-4)", fontSize: 10}}>{s.metrics.toks ? `${s.metrics.toks}t/s` : ""}</span>
              </div>
            );
          })}
          <div className="pm-add" onClick={() => { onToggle(); window.location.hash = "#slots?new=llm"; window.__hal0Toast && window.__hal0Toast("Create-slot modal — coming in next batch", "info"); }}>{Icons.plus} <span>Add chat slot</span></div>
        </div>
      )}
    </div>
  );
}

// ─── Composer ───
function Composer({ slots, persona, onPersona, draft, setDraft, onSend, placeholder, placement = "composer-left", state = "idle" }) {
  const [open, setOpen] = useStateD(false);
  useEffectD(() => {
    const off = () => setOpen(false);
    document.addEventListener("click", off);
    return () => document.removeEventListener("click", off);
  }, []);
  const cur = slots.find(s => s.name === persona);
  const isOffline   = state === "offline";
  const isSending   = state === "sending";
  const isStreaming = state === "streaming";
  const isSwapping  = state === "swap";
  const noTools     = state === "no-tools";
  const dimmed = isOffline || isSwapping;

  // Above-composer banner for swap / offline states
  const stateBanner = (() => {
    if (isOffline) return (
      <div className="composer-banner err">
        <span>{Icons.warn}</span>
        <span><b>lemond is offline.</b> Slot state is stale. Sending is disabled.</span>
        <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Restart lemond — stubbed", "info")}>Restart lemond</button>
      </div>
    );
    if (isSwapping) return (
      <div className="composer-banner warn">
        <span>{Icons.warn}</span>
        <span><b>Swapping NPU chat: gemma3:1b → llama-3.2-3b-npu.</b> Voice + embed paused ~14s.</span>
      </div>
    );
    if (noTools) return (
      <div className="composer-banner info">
        <span>{Icons.warn}</span>
        <span><b>Persona has no tool-calling.</b> Attach / mic disabled. Pick a tool-calling-labeled model to enable tools.</span>
      </div>
    );
    return null;
  })();

  const personaCtl = (
    <PersonaPicker
      slots={slots}
      current={persona}
      onPick={onPersona}
      open={open}
      onToggle={() => setOpen(v => !v)}
      noTools={noTools}
    />
  );

  return (
    <div className={"composer" + (dimmed ? " dimmed" : "")}>
      {stateBanner}
      {placement === "above" && (
        <div className="composer-persona-row" onClick={e => e.stopPropagation()}>
          {personaCtl}
          <span className="mono" style={{fontSize: 10, color: "var(--fg-5)", marginLeft: "auto"}}>persona surfaced above input</span>
        </div>
      )}
      <div className="composer-bar" onClick={e => e.stopPropagation()}>
        {placement !== "above" && personaCtl}
        <div className={"composer-ic" + (noTools ? " disabled" : "")} title="Attach" onClick={() => !noTools && window.__hal0Toast && window.__hal0Toast("Attachment picker — coming in next batch", "info")}>{Icons.attach}</div>
        <div className={"composer-ic" + (noTools ? " disabled" : "")} title="Voice input" onClick={() => !noTools && window.__hal0Toast && window.__hal0Toast("Voice input — coming in next batch", "info")}>{Icons.mic}</div>
        <div className="composer-input-wrap">
          <textarea
            className="composer-input"
            placeholder={isOffline ? "lemond is offline — cannot send" : (placeholder || `Ask ${cur?.name || "hal0"}…`)}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            rows={1}
            disabled={isOffline || isSending}
          />
        </div>
        {isStreaming ? (
          <button className="composer-stop" onClick={() => window.__hal0Toast && window.__hal0Toast("Stopped stream", "info")}>
            <span className="stop-sq" />
            Stop
          </button>
        ) : (
          <button className="composer-send" onClick={onSend} disabled={isOffline || isSending} aria-label="Send">
            {isSending ? <span className="spinner-sm" /> : Icons.send}
          </button>
        )}
      </div>
      <div className="composer-meta mono">
        <span>routing → <span style={{color: "var(--accent)"}}>{cur?.name}</span></span>
        <span className="spacer" />
        {noTools
          ? <span style={{color: "var(--warn)"}}>● tools: 0 (persona has no tool-calling label)</span>
          : <span><span className="ok">●</span> tools: 6 active</span>}
        {isStreaming && <span style={{color: "var(--accent)"}}>● streaming · 38 tok/s</span>}
        {isSending && <span style={{color: "var(--info)"}}>● sending…</span>}
        <span><kbd className="kbd">enter</kbd> send · <kbd className="kbd">⇧↵</kbd> newline</span>
      </div>
    </div>
  );
}

// ─── Chat view (active conversation) ───
function ChatActive({ slots, persona, onPersona, placement, composerState }) {
  const [draft, setDraft] = useStateD("");
  return (
    <div className="chat">
      <div className="chat-head mono">
        <span>Conversation</span>
        <span className="state-pill"><span className="dot" style={{background: "currentColor"}} />streaming</span>
        <span className="ct" style={{marginLeft: 8, color: "var(--fg-5)"}}>· session #ftr-104</span>
        <div className="right">
          <span className="ic" title="New chat">{Icons.plus}</span>
          <span className="ic" title="Export">{Icons.ext}</span>
        </div>
      </div>
      <div className="chat-body">
        <div className="msg">
          <div className="meta mono"><b>primary</b> · 14:01:42 · qwen3.6-27b-mtp · 22 tok/s</div>
          <div className="bubble">Sure — I can refactor that. Drop the file here or paste the class and tell me what you want me to optimise for (readability, perf, type safety).</div>
        </div>

        <div className="msg user">
          <div className="meta mono">you · 14:02:08</div>
          <div className="bubble">{`Refactor src/hal0/launchers/slot_manager.py to use dataclasses for SlotState. Keep the public interface stable.`}</div>
        </div>

        <div className="swap-line mono">
          ⟳ persona <b>primary</b> → <b>coder</b> · qwen3-coder-30b-a3b · gpu-rocm
        </div>

        <div className="toolblock">
          <div className="tb-h">
            <span>tool call</span>
            <span className="arr">→</span>
            <span><b>read_file</b></span>
            <span className="arr">→</span>
            <span>fs (read-only)</span>
            <span className="right">14:02:09 · 38ms</span>
          </div>
          <div className="tb-body">
            <div className="kv">
              <span className="k">path</span><span className="v">src/hal0/launchers/slot_manager.py</span>
              <span className="k">bytes</span><span className="v">8,412</span>
              <span className="k">lines</span><span className="v">237</span>
            </div>
          </div>
          <div className="tb-foot">
            <span>●</span><b>ok</b><span>· returned 237 lines · cached for follow-up</span>
          </div>
        </div>

        <div className="msg">
          <div className="meta mono"><b>coder</b> · 14:02:11 · qwen3-coder-30b-a3b · 38 tok/s</div>
          <div className="bubble">{`Got it. Here's the structure I'd recommend — converting SlotState to a frozen dataclass and keeping the manager API surface intact:

\`\`\`python
@dataclass(frozen=True, slots=True)
class SlotState:
    name: str
    type_: SlotType
    device: Device
    model: str | None = None
    enabled: bool = False
    default: bool = False
\`\`\`

The frozen + slots combo prevents accidental mutation in the dispatcher and trims memory ~12% for the slot table. Want me to write out the full file or stage the changes through \`edit_file\`?`}</div>
        </div>

        <div className="toolblock">
          <div className="tb-h">
            <span>tool call</span>
            <span className="arr">→</span>
            <span><b>generate_image</b></span>
            <span className="arr">→</span>
            <span>img (sd-turbo)</span>
            <span className="right">14:02:18 · 4.1s</span>
          </div>
          <div className="tb-body">
            <div className="kv">
              <span className="k">prompt</span><span className="v">"diagram of slot dispatcher routing requests through lemond, minimal"</span>
              <span className="k">steps</span><span className="v">20</span>
              <span className="k">cfg</span><span className="v">7.0</span>
              <span className="k">size</span><span className="v">512×512</span>
            </div>
          </div>
          <div className="tb-foot">
            <span>●</span><b>ok</b><span>· 2.4 MB · /v1/uploads/img-7f3e.png</span>
          </div>
        </div>

        <div className="msg">
          <div className="meta mono"><b>coder</b> · 14:02:22</div>
          <div className="bubble">Here's a quick architecture sketch alongside the refactor — same slot model, more legible call graph:</div>
          <div style={{marginTop: 8}}>
            <div className="attach">
              <div className="img-ph">/v1/uploads · img-7f3e.png</div>
              <div className="img-meta">
                <span style={{color: "var(--fg)"}}>slot-dispatch.png</span>
                <span>· 2.4 MB</span>
                <span style={{marginLeft: "auto", color: "var(--accent)", cursor: "pointer"}}>open</span>
              </div>
            </div>
          </div>
        </div>
      </div>
      <Composer slots={slots} persona={persona} onPersona={onPersona} draft={draft} setDraft={setDraft} onSend={() => setDraft("")} placement={placement} state={composerState} />
    </div>
  );
}

// ─── Empty chat ───
function ChatEmpty({ slots, persona, onPersona, placement, composerState }) {
  const [draft, setDraft] = useStateD("");
  const prompts = [
    "Refactor a file in this repo",
    "Generate a hero image",
    "Transcribe this audio",
    "Embed and rerank this passage",
    "What can you do?",
  ];
  return (
    <div className="chat">
      <div className="chat-head mono">
        <span>New conversation</span>
        <span className="state-pill" style={{background: "var(--accent-soft)", color: "var(--accent)", borderColor: "var(--accent-line)"}}>
          <span className="dot" style={{background: "currentColor"}} />ready
        </span>
        <div className="right">
          <span className="ic" title="History">{Icons.logs}</span>
        </div>
      </div>
      <div className="empty-chat">
        <div className="glyph mono">
          <Wordmark size={32} />
        </div>
        <h3>What should we build?</h3>
        <p>hal0 is running locally on <span className="mono" style={{color: "var(--fg)"}}>{HAL0_DATA.host.name}</span>. Default persona is <span className="mono" style={{color: "var(--accent)"}}>{persona}</span>. Type below or pick a starting prompt.</p>
        <div className="prompts">
          {prompts.map((p, i) => (
            <div key={i} className="prompt" onClick={() => setDraft(p)}>{p}</div>
          ))}
        </div>
      </div>
      <Composer
        slots={slots}
        persona={persona}
        onPersona={onPersona}
        draft={draft}
        setDraft={setDraft}
        onSend={() => setDraft("")}
        placeholder="Type a message…"
        placement={placement}
        state={composerState}
      />
    </div>
  );
}

// ─── Memory / health side cards ───
function MemoryMap({ slots }) {
  // Live OS-level memory from /api/hardware (used / total). Per-slot
  // segments stay informational — they sum the bookkeeping each slot
  // reports in `metrics.mem`. The bar then splits into:
  //   [per-slot segments] + [other used] + [free]
  // so "used" tracks reality (e.g. shows the few GB the OS itself eats
  // when zero slots are loaded) rather than the static 128 GB the
  // HAL0_DATA fixture used to render.
  const hw = useHardware();
  const ram = hw.data?.ram;
  const fallbackTotal = HAL0_DATA.host.ram.total;
  const total = ram && ram.total > 0 ? ram.total : fallbackTotal;
  const loaded = slots.filter(s => s.state === "ready" || s.state === "serving" || s.state === "idle");
  const segs = loaded.map(s => ({ name: s.name, sz: s.metrics.mem || 0, color: s.device }));
  const slotsUsed = segs.reduce((a, s) => a + s.sz, 0);
  // Prefer the OS reading; fall back to the slot sum until /api/hardware
  // has resolved (matches the H = hwQuery.data || HAL0_DATA.host pattern
  // used by HardwareView in extras.jsx).
  const used = ram ? ram.used : slotsUsed;
  const free = Math.max(0, total - used);
  const otherUsed = Math.max(0, used - slotsUsed);
  const pct = n => total > 0 ? `${(n / total) * 100}%` : '0%';
  const colorFor = d => d === "npu" ? "var(--dev-npu)" : d === "cpu" ? "var(--dev-cpu)" : d === "gpu-vulkan" ? "var(--dev-vulkan)" : "var(--dev-rocm)";
  return (
    <div className="side-card">
      <div className="side-card-h">
        <span>Memory map</span>
        <span className="right mono">{used.toFixed(1)} / {total.toFixed(0)} GB</span>
      </div>
      <div className="side-card-b">
        <div className="memmap">
          <div className="memmap-h mono">
            <span>unified ram</span>
            <span><b>{free.toFixed(1)} GB</b> free</span>
          </div>
          <div className="memmap-bar">
            {segs.map((s, i) => (
              <i key={i} style={{ width: pct(s.sz), background: colorFor(s.color) }} />
            ))}
            {otherUsed > 0 && (
              <i style={{ width: pct(otherUsed), background: "var(--fg-5)" }} />
            )}
            <i style={{ width: pct(free), background: "var(--bg-4)" }} />
          </div>
          <div className="memmap-legend">
            {segs.map((s, i) => (
              <div key={i} className="ln mono">
                <span className="sw" style={{background: colorFor(s.color)}} />
                <span className="name">{s.name}</span>
                <span className="sz">{s.sz < 1 ? `${(s.sz * 1024).toFixed(0)} MB` : `${s.sz.toFixed(1)} GB`}</span>
              </div>
            ))}
            {otherUsed > 0 && (
              <div className="ln mono">
                <span className="sw" style={{background: "var(--fg-5)"}} />
                <span className="name">other</span>
                <span className="sz">{otherUsed.toFixed(1)} GB</span>
              </div>
            )}
            <div className="ln mono">
              <span className="sw" style={{background: "var(--bg-4)"}} />
              <span className="name">free</span>
              <span className="sz">{free.toFixed(1)} GB</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function HealthCard() {
  return (
    <div className="side-card">
      <div className="side-card-h">
        <span>Health</span>
        <span className="right mono">poll 287ms</span>
      </div>
      <div className="side-card-b" style={{paddingTop: 4, paddingBottom: 4}}>
        <div className="health-row">
          <span className="k">lemond</span>
          <span className="v up"><span className="dot" />up · {HAL0_DATA.lemond.version}</span>
        </div>
        <div className="health-row">
          <span className="k">hal0-api</span>
          <span className="v up"><span className="dot" />up · v0.2.1</span>
        </div>
        <div className="health-row">
          <span className="k">flm:npu</span>
          <span className="v up"><span className="dot" />trio · 0.9.42</span>
        </div>
        <div className="health-row">
          <span className="k">cognee</span>
          <span className="v up"><span className="dot" />2,847 records</span>
        </div>
        <div className="health-row">
          <span className="k">disk</span>
          <span className="v">412 GB free</span>
        </div>
      </div>
    </div>
  );
}

function ThroughputCard() {
  // Live last-request tok/s from /v1/stats (via useLemondRollup).
  // Lemonade does not expose a rolling-60s history — we build one
  // client-side by appending each new sample to an in-component ring
  // buffer (cap 21 entries to match the original spark width).
  // When no sample has been observed yet, headline renders "—" and
  // the spark is empty per the dashboard's "no data" convention.
  const lemond = useLemondRollup();
  const value = lemond.lastTokPerSec;
  const historyRef = useRefD([]);
  const lastRef = useRefD(null);
  const [, force] = useStateD(0);
  useEffectD(() => {
    if (value == null) return;
    // Dedupe identical back-to-back samples so the spark only advances
    // when /v1/stats reports a new (or updated) measurement.
    if (lastRef.current === value) return;
    lastRef.current = value;
    historyRef.current = [...historyRef.current, value].slice(-21);
    force(n => n + 1);
  }, [value]);

  const ticks = historyRef.current;
  const hasData = value != null;
  const max = ticks.length > 0 ? Math.max(...ticks, 1) : 1;
  const peak = ticks.length > 0 ? Math.max(...ticks) : null;

  return (
    <div className="side-card">
      <div className="side-card-h">
        <span>Throughput</span>
        <span className="right mono">
          <b style={{color: hasData ? "var(--accent)" : "var(--fg-4)"}}>
            {hasData ? value.toFixed(1) : "—"}
          </b> tok/s
        </span>
      </div>
      <div className="side-card-b" style={{padding: "12px 16px"}}>
        <div className="spark">
          {ticks.map((t, i) => (
            <i key={i} style={{ height: `${(t / max) * 100}%`, opacity: i > ticks.length - 4 ? 1 : 0.5 + (i / ticks.length) * 0.5 }} />
          ))}
        </div>
        <div className="mono" style={{display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--fg-4)", marginTop: 6}}>
          <span>last request</span>
          <span>{peak != null ? `peak ${peak.toFixed(1)} t/s` : "no samples yet"}</span>
        </div>
      </div>
    </div>
  );
}

// ─── Dashboard view shell ───
function DashboardView({ chatState, setChatState, slots: slotsProp, persona, setPersona, onGo, showHero, onDismissHero, personaPlacement, composerState }) {
  // Phase B1: live slot list; fall back to the prop (HAL0_DATA.slots
  // from main.jsx) until /api/slots returns. Keeps the surface usable
  // on first paint and in mock dev.
  const slotsQuery = useSlots();
  const slots = (slotsQuery.data && slotsQuery.data.length > 0) ? slotsQuery.data : slotsProp;
  // Lemond rollup so the hero strip / chip read live state instead of
  // the static HAL0_DATA.lemond fixture.
  const lemond = useLemondRollup();
  // Skip-path: no slots configured → render empty hero, no chat surface
  if (chatState === "skip") {
    return (
      <div className="view">
        <div className="dash-empty">
          <div className="dash-empty-glyph"><Wordmark size={56} /></div>
          <h1 className="mono">No models configured yet</h1>
          <p>hal0 is ready, but no slot has a model loaded. Pick a bundle to get going, or configure slots one at a time.</p>
          <div className="dash-empty-meta mono">
            <span><span style={{color: "var(--fg-3)"}}>host</span> {HAL0_DATA.host.name}</span>
            <span style={{color: "var(--fg-5)"}}>·</span>
            <span><span style={{color: "var(--fg-3)"}}>ram</span> {HAL0_DATA.host.ram.total} GB</span>
            <span style={{color: "var(--fg-5)"}}>·</span>
            <span><span style={{color: "var(--fg-3)"}}>npu</span> ready</span>
          </div>
          <div className="dash-empty-cta">
            <button className="btn lg" onClick={() => window.location.hash = "#firstrun"}>Pick a bundle</button>
            <button className="btn ghost lg" onClick={() => onGo("slots")}>Configure slots</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="view">
      {showHero && (
        <div className="hero-strip" style={{marginBottom: 16}}>
          <div className="greet">
            <span className="dim">Welcome back, </span>
            <b>halo</b>
            <span className="dim">. <span className="mono" style={{color: "var(--fg-2)"}}>{persona}</span> is your active persona</span>
            <span className="dim"> · last message <span className="mono">14:02:22</span></span>
          </div>
          <div className="spacer" />
          <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>steady · {slots.filter(s => s.state !== "empty").length} slots up · lemond {lemond.status}</span>
          <span className="close" onClick={onDismissHero} role="button" aria-label="Dismiss hero">{Icons.close}</span>
        </div>
      )}

      <div className="vh" style={{marginTop: showHero ? 4 : 0, marginBottom: 16, display: "flex", gap: 12, alignItems: "center"}}>
        <div className="mono" style={{display: "inline-flex", border: "1px solid var(--line)", borderRadius: "var(--rad-sm)", overflow: "hidden", fontSize: 11}}>
          <button
            onClick={() => setChatState("empty")}
            style={{padding: "5px 11px", background: chatState === "empty" ? "var(--accent-soft)" : "transparent", color: chatState === "empty" ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: "1px solid var(--line)", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}
          >empty composer</button>
          <button
            onClick={() => setChatState("active")}
            style={{padding: "5px 11px", background: chatState === "active" ? "var(--accent-soft)" : "transparent", color: chatState === "active" ? "var(--accent)" : "var(--fg-3)", border: "none", borderRight: "1px solid var(--line)", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}
          >active conversation</button>
          <button
            onClick={() => setChatState("skip")}
            style={{padding: "5px 11px", background: chatState === "skip" ? "var(--accent-soft)" : "transparent", color: chatState === "skip" ? "var(--accent)" : "var(--fg-3)", border: "none", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11}}
          >skip-path empty</button>
        </div>
        <span className="mono" style={{fontSize: 10, color: "var(--fg-4)"}}>← chat surface state · both ship</span>
      </div>

      <div className="dash">
        <div className="dash-main">
          {chatState === "empty"
            ? <ChatEmpty slots={slots} persona={persona} onPersona={setPersona} placement={personaPlacement} composerState={composerState} />
            : <ChatActive slots={slots} persona={persona} onPersona={setPersona} placement={personaPlacement} composerState={composerState} />}
        </div>
        <div className="dash-side">
          <SnapshotStrip slots={slots} onGo={onGo} />
          <MemoryMap slots={slots} />
          <ThroughputCard />
          <HealthCard />
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { DashboardView, SnapshotStrip, MemoryMap, HealthCard, ThroughputCard, ChatActive, ChatEmpty, Composer, PersonaPicker });
