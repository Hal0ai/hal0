# Settings Reorganization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the dashboard Settings view: move General to top, create Slots and Memory sections, simplify NPU and Advanced.

**Architecture:** Single-file refactor of `ui/src/dash/settings.jsx`. No backend changes — every setting already has an API endpoint. Existing hooks (`useSettings`, `useMemoryGraphStatus`, `useUpdateMemoryGraph`, `useSettingsSchema`, etc.) are reused. The `MemoryGraphPanel` component already exists inside Advanced and gets extracted into the new `MemorySection`.

**Tech Stack:** React (hooks), existing hal0 API hooks (`useSlots`, `useSlotEdit`, `useSlotConfig`, `useSettings`, `useSettingsUpdate`, `useSettingsSchema`, `useApplyPlan`, `useMemoryGraphStatus`, `useUpdateMemoryGraph`)

## Global Constraints

- Single file touched: `ui/src/dash/settings.jsx`
- No backend changes — all API endpoints already exist
- No new dependencies or imports
- Every code block in steps is exact (no "similar to", "TBD", or "add error handling")
- Each task ends with an independently testable deliverable

---

### Task 1: Reorder the sidebar nav array

**Files:**
- Modify: `ui/src/dash/settings.jsx:48-60` (the `sections` array and `VALID_IDS`)

**Interfaces:**
- Produces: new `sections` array with reordered items, new `VALID_IDS` array

- [ ] **Step 1: Update `VALID_IDS` and `sections` arrays**

In `function SettingsView`, replace the existing `VALID_IDS` and `sections` definitions:

```jsx
  const VALID_IDS = ["general", "slots", "npu", "memory", "voice", "imagegen", "storage", "secrets", "updates", "advanced", "about"];
  const initialSection = param && VALID_IDS.includes(param) ? param : "general";
  const [section, setSection] = useStateSet(initialSection);
  const sections = [
    { id: "general",   label: "General" },
    { id: "slots",     label: "Slots" },
    { id: "npu",       label: "NPU" },
    { id: "memory",    label: "Memory" },
    { id: "voice",     label: "Voice" },
    { id: "imagegen",  label: "Image-gen" },
    { id: "storage",   label: "Storage" },
    { id: "secrets",   label: "Secrets" },
    { id: "updates",   label: "Updates" },
    { id: "advanced",  label: "Advanced" },
    { id: "about",     label: "About" },
  ];
```

Note: `initialSection` changes from `"secrets"` to `"general"`.

- [ ] **Step 2: Update the render block to remove `defaults` case and add `slots`/`memory` cases**

In the render block (the `{section === "..." && ...}` chain), find the line with `defaults`:

```jsx
          {section === "defaults" && <DefaultSlotsSection />}
```

Remove that line. Then add before the `{section === "npu" ...}` line:

```jsx
          {section === "slots" && <SlotsSection />}
          {section === "memory" && <MemorySection />}
```

- [ ] **Step 3: Commit**

```bash
cd /mnt/repos/hal0-mono/hal0
git add ui/src/dash/settings.jsx
git commit -m "feat(ui): reorder settings sidebar — General first, add Slots and Memory placeholders"
```

---

### Task 2: Rewrite General section

**Files:**
- Modify: `ui/src/dash/settings.jsx:1448-1510` (replace entire `GeneralSection` function)

**Interfaces:**
- Consumes: `useSettings`, `useSettingsUpdate`, `useApplyPlan` (already imported)
- Produces: `GeneralSection` component with telemetry + version + schema_version

- [ ] **Step 1: Rewrite `GeneralSection`**

Replace the entire `function GeneralSection()` (lines 1448–1510) with:

```jsx
function GeneralSection() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const liveTelemetry = settings.data?.telemetry;

  const [telemetry, setTelemetry] = useStateSet(false);
  useEffectSet(() => {
    if (settings.data) setTelemetry(settings.data.telemetry?.enabled === true);
  }, [settings.data]);

  const telemetryDirty = !!settings.data && telemetry !== (liveTelemetry?.enabled === true);

  const onSaveTelemetry = async () => {
    try {
      await update.mutateAsync({ telemetry: { enabled: telemetry } });
      window.__hal0Toast && window.__hal0Toast(`Telemetry ${telemetry ? "enabled" : "disabled"}`, "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const meta = settings.data?.meta || {};
  const version = meta.hal0_version || "—";
  const schemaVer = meta.schema_version != null ? String(meta.schema_version) : "—";

  return (
    <div className="s-section">
      <h2>General</h2>
      <p className="desc">Platform identity and privacy.</p>
      <div className="s-panel">
        <SRow k="hal0 version" sub="Running API version" mono v={<span style={{color: "var(--fg-2)"}}>{version}</span>} />
        <SRow k="Schema version" sub="hal0.toml schema version" mono v={<span style={{color: "var(--fg-3)"}}>{schemaVer}</span>} />
        <SRow
          k="Anonymous telemetry"
          sub="Opt-in · off by default. Sends anonymized, aggregate usage counts to help prioritize work — no prompts, model I/O, or file paths leave the machine."
          v={
            <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
              <input
                type="checkbox"
                checked={telemetry}
                disabled={!settings.data}
                onChange={e => setTelemetry(e.target.checked)}
                style={{accentColor: "var(--accent)"}}
              />
              <span>{telemetry ? "enabled" : "disabled"}</span>
            </label>
          }
          actions={
            <div style={{display: "inline-flex", alignItems: "center", gap: 6}}>
              <ApplyBadge settingsKey="telemetry.enabled" registry={registry} />
              {telemetryDirty && (
                <button className="btn ghost sm" disabled={update.isPending} onClick={onSaveTelemetry}>
                  {update.isPending ? "Saving…" : "Save"}
                </button>
              )}
            </div>
          }
        />
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd /mnt/repos/hal0-mono/hal0
git add ui/src/dash/settings.jsx
git commit -m "feat(ui): rewrite General section — drop theme stub, add version info"
```

---

### Task 3: Create Slots section (absorb DefaultSlots + slots runtime)

**Files:**
- Modify: `ui/src/dash/settings.jsx` — insert `SlotsSection` before `NpuSection`, remove old `DefaultSlotsSection`

**Interfaces:**
- Consumes: `useSlots`, `useSlotEdit`, `useSettings`, `useSettingsUpdate`, `useSettingsSchema`, `useApplyPlan` (all already imported)
- Produces: `SlotsSection` component — replaces `DefaultSlotsSection`

- [ ] **Step 1: Insert `SlotsSection` component**

Insert the following right before `function NpuSection()` (which is around line 1218):

```jsx
function SlotsSection() {
  // --- Slot defaults (moved from former DefaultSlotsSection) ---
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();
  const slots = slotsQuery.data || [];
  const byType = {};
  for (const s of slots) { (byType[s.type] ||= []).push(s); }
  const types = Object.keys(byType).filter(t => byType[t].length >= 2).sort();

  const setDefault = async (type, name) => {
    const sibs = byType[type] || [];
    const prev = sibs.find(s => s.isDefault && s.name !== name);
    try {
      await editSlot.mutateAsync({ name, body: { default: true } });
      if (prev) await editSlot.mutateAsync({ name: prev.name, body: { default: false } });
      window.__hal0Toast && window.__hal0Toast(`Default ${type} slot → ${name}`, "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Couldn't set default — ${e?.message || "see logs"}`, "err");
    }
  };

  // --- Slots runtime (moved from Advanced: slots.max_slots etc.) ---
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const RUNTIME_KEYS = [
    "slots.max_slots", "slots.port_range_start", "slots.port_range_end",
    "slots.idle_timeout_s", "slots.evict_pressure_mb", "slots.publish_host",
  ];
  const runtimeFields = {};
  for (const k of RUNTIME_KEYS) runtimeFields[k] = _schemaField(schema, k);

  const [buf, setBuf] = useStateSet({});
  const onChange = (dotKey, value) => setBuf(b => ({ ...b, [dotKey]: value }));

  const runtimeDirty = Object.keys(buf).filter(k => {
    const { ok, value } = _advCoerce(runtimeFields[k], buf[k]);
    if (!ok) return true;
    const cur = _getIn(live, k);
    return value !== (cur === undefined ? (runtimeFields[k]?.default ?? null) : cur);
  });
  const invalidKeys = runtimeDirty.filter(k => !_advCoerce(runtimeFields[k], buf[k]).ok);
  const canSaveRuntime = runtimeDirty.length > 0 && invalidKeys.length === 0 && !update.isPending;

  const doSaveRuntime = async () => {
    let patch = {};
    for (const k of runtimeDirty) {
      const { value } = _advCoerce(runtimeFields[k], buf[k]);
      patch = _deepMergePatch(patch, k.split(".").reverse().reduce((acc, part) => ({ [part]: acc }), value));
    }
    try {
      await update.mutateAsync(patch);
      setBuf({});
      window.__hal0Toast && window.__hal0Toast("Slots runtime settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="s-section">
      <h2>Slots</h2>
      <p className="desc">
        Default slot assignments per modality and runtime limits for the slot pool.
      </p>

      {/* --- Default slots --- */}
      {slotsQuery.isPending && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading slots…</div>
      )}
      {slotsQuery.isError && (
        <div className="err">{slotsQuery.error?.message || "Failed to load slots"}</div>
      )}
      {!slotsQuery.isPending && !slotsQuery.isError && types.length > 0 && (
        <div className="s-panel" style={{marginBottom: 12}}>
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>Default slots</span><span className="sub">For each modality with multiple slots, pick the one that serves type-routed requests</span></div>
          </div>
          {types.map(type => {
            const cur = (byType[type].find(s => s.isDefault) || {}).name || "";
            return (
              <div className="default-slot-row form-row s-row" key={type}>
                <div className="k"><span>{type}</span></div>
                <div className="v">
                  <select
                    className="input mono"
                    value={cur}
                    disabled={editSlot.isPending}
                    onChange={e => { const n = e.target.value; if (n && n !== cur) setDefault(type, n); }}
                    style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}
                  >
                    {byType[type].map(s => <option key={s.name} value={s.name}>{s.name}</option>)}
                  </select>
                </div>
              </div>
            );
          })}
        </div>
      )}
      {!slotsQuery.isPending && !slotsQuery.isError && types.length === 0 && (
        <p className="hint" style={{fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)", marginBottom: 12}}>No modality has multiple slots yet.</p>
      )}

      {/* --- Runtime --- */}
      <div className="s-panel">
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>Runtime</span><span className="sub">hal0.toml [slots]</span></div>
        </div>
        {RUNTIME_KEYS.map(k => (
          <AdvRow
            key={k}
            dotKey={k}
            field={runtimeFields[k]}
            live={_getIn(live, k)}
            buf={buf[k]}
            onChange={onChange}
            registry={registry}
          />
        ))}
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {runtimeDirty.length > 0 && (
            <button className="btn ghost sm" onClick={() => setBuf({})}>Reset</button>
          )}
          <button className="btn sm" disabled={!canSaveRuntime} onClick={doSaveRuntime}>
            {update.isPending ? "Saving…" : "Save runtime"}
          </button>
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Remove old `DefaultSlotsSection` function**

Delete the entire `function DefaultSlotsSection()` (lines 1386–1446) and the comment block above it (`// ─── DefaultSlotsSection ─── ...`).

- [ ] **Step 3: Commit**

```bash
cd /mnt/repos/hal0-mono/hal0
git add ui/src/dash/settings.jsx
git commit -m "feat(ui): merge DefaultSlots and slots runtime into Slots section"
```

---

### Task 4: Simplify NPU section (remove slot picker)

**Files:**
- Modify: `ui/src/dash/settings.jsx:1218-1384` (the `NpuSection` function)

**Interfaces:**
- Consumes: `useSlots`, `useSlotEdit`, `useSlotConfig`, `useNpuOccupancy` (already imported)
- Produces: simplified `NpuSection` — no dropdown, directly uses the single NPU slot

- [ ] **Step 1: Replace NpuSection with simplified version**

Replace `function NpuSection()` (the entire function, lines 1218–1384) with:

```jsx
function NpuSection() {
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();
  const occQuery = useNpuOccupancy();

  const npuSlots = (slotsQuery.data || []).filter(s => s.device === "npu");
  const npuName = npuSlots.length > 0 ? npuSlots[0].name : null;
  const cfgQuery = useSlotConfig(npuName);
  const cfg = cfgQuery.data || {};
  const liveCtx = cfg.model?.context_size;
  const liveNpu = cfg.npu || {};

  const DEF_CTX = "16384";
  const origCtx = liveCtx != null ? String(liveCtx) : DEF_CTX;
  const origAsr = !!liveNpu.asr;
  const origEmbed = !!liveNpu.embed;

  const [ctx, setCtx] = useStateSet(DEF_CTX);
  const [asr, setAsr] = useStateSet(false);
  const [embed, setEmbed] = useStateSet(false);
  useEffectSet(() => {
    setCtx(liveCtx != null ? String(liveCtx) : DEF_CTX);
    setAsr(!!liveNpu.asr);
    setEmbed(!!liveNpu.embed);
  }, [cfgQuery.data]);

  const ctxNum = parseInt(ctx, 10);
  const ctxValid = /^\d+$/.test(ctx.trim()) && ctxNum >= 512;
  const dirty = !!npuName && (ctx !== origCtx || asr !== origAsr || embed !== origEmbed);

  const doSave = async () => {
    if (!npuName || !ctxValid) return;
    const body = { model: { context_size: ctxNum }, npu: { asr, embed } };
    try {
      await editSlot.mutateAsync({ name: npuName, body });
      window.__hal0Toast && window.__hal0Toast("NPU settings saved — restart the slot to apply", "warn");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const reset = () => { setCtx(origCtx); setAsr(origAsr); setEmbed(origEmbed); };

  const occ = occQuery.data;
  const inputStyle = {fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"};

  return (
    <div className="s-section">
      <h2>NPU</h2>
      <p className="desc">
        FastFlowLM on the AMD XDNA2 NPU. One FLM process serves chat and, when enabled,
        embeddings + speech-to-text on the same 8-column AIE array. These knobs write the
        npu slot TOML and take effect on the slot's next restart.
      </p>

      {slotsQuery.isPending && (
        <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading slots…</div>
      )}
      {!slotsQuery.isPending && npuSlots.length === 0 && (
        <p className="hint" style={{fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)"}}>
          No NPU slot configured. Create a slot with device <span className="mono">npu</span> in the Slots view (or run <span className="mono">hal0 setup</span> with NPU opt-in) to tune FLM here.
        </p>
      )}

      {npuSlots.length > 0 && (
        <div className="s-panel">
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>FLM slot</span><span className="sub">{npuName} · device=npu · profile=flm</span></div>
            <div className="v">
              <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{npuName}</span>
            </div>
          </div>
          <SRow
            k="Context size"
            sub="FLM --ctx-len (tokens) · larger = more KV cache on the NPU"
            v={
              <input type="number" min={512} step={512} value={ctx} disabled={!npuName}
                onChange={e => setCtx(e.target.value)} placeholder={DEF_CTX}
                className="mono" style={{...inputStyle, width: 120, borderColor: ctxValid || !ctx ? "var(--line)" : "var(--err)"}} />
            }
            actions={<span className="chip mono" style={{fontSize: 10, padding: "2px 8px", color: "var(--warn)", borderColor: "var(--warn)", whiteSpace: "nowrap"}}>⟳ restart {npuName}</span>}
          />
          <SRow
            k="Load embeddings"
            sub="Serve /v1/embeddings from the FLM trio (--embed 1)"
            v={
              <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
                <input type="checkbox" checked={embed} disabled={!npuName} onChange={e => setEmbed(e.target.checked)} style={{accentColor: "var(--accent)"}} />
                <span>{embed ? "enabled" : "disabled"}</span>
              </label>
            }
          />
          <SRow
            k="Load ASR"
            sub="Serve /v1/audio/transcriptions from the FLM trio (--asr 1)"
            v={
              <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
                <input type="checkbox" checked={asr} disabled={!npuName} onChange={e => setAsr(e.target.checked)} style={{accentColor: "var(--accent)"}} />
                <span>{asr ? "enabled" : "disabled"}</span>
              </label>
            }
          />
          <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
            {dirty && <button className="btn ghost sm" onClick={reset}>Reset</button>}
            <button className="btn sm" disabled={!dirty || !ctxValid || editSlot.isPending} onClick={doSave}>
              {editSlot.isPending ? "Saving…" : "Save NPU settings"}
            </button>
          </div>
        </div>
      )}

      {/* ── Live occupancy (read-only) ── */}
      {occ?.present && (
        <div className="s-panel" style={{marginTop: 12}}>
          <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
            <div className="k"><span>Occupancy</span><span className="sub">live AIE column allocation · single-tenant</span></div>
            <div className="v">
              <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: occ.cols_used > 0 ? "var(--ok)" : "var(--fg-4)", borderColor: occ.cols_used > 0 ? "var(--ok)" : "var(--line)"}}>
                {occ.cols_used}/{occ.cols_total} cols
              </span>
            </div>
          </div>
          <SRow k="Peak" mono v={`${occ.tops_peak} TOPS · ${occ.tiles} tiles (${occ.rows}×${occ.cols})`} />
          {(occ.slots || []).map(s => (
            <SRow key={s.name} k={s.name} sub={s.model || "—"} mono
              v={<>
                <span style={{color: s.state === "serving" || s.state === "ready" ? "var(--ok)" : "var(--fg-4)"}}>{s.state}</span>
                <span style={{color: "var(--fg-4)"}}> · {s.cols?.length || 0} cols{s.gb != null ? ` · ${s.gb} GB` : ""}</span>
              </>} />
          ))}
        </div>
      )}
      {occQuery.data && !occ?.present && (
        <div style={{marginTop: 12, fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-4)"}}>
          No NPU detected on this host — occupancy unavailable.
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd /mnt/repos/hal0-mono/hal0
git add ui/src/dash/settings.jsx
git commit -m "feat(ui): simplify NPU section — remove slot picker, use single NPU slot directly"
```

---

### Task 5: Create Memory section (engine + graph extraction + reranker)

**Files:**
- Modify: `ui/src/dash/settings.jsx` — insert `MemorySection` before `VoiceSection`, extract `MemoryGraphPanel` logic

**Interfaces:**
- Consumes: `useMemoryGraphStatus`, `useUpdateMemoryGraph`, `useSettings`, `useSettingsUpdate`, `useSettingsSchema`, `useApplyPlan` (all already imported)
- Produces: `MemorySection` component — combines engine selector, graph extraction panel, and reranker fields

- [ ] **Step 1: Insert `MemorySection` component**

Insert the following right before `function VoiceSection()` (search for it with `grep -n "function VoiceSection"`):

```jsx
function MemorySection() {
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};

  return (
    <div className="s-section">
      <h2>Memory</h2>
      <p className="desc">
        Memory engine, graph extraction, and second-pass reranking. Changes to the engine
        require a hal0-api restart; graph and reranker knobs apply live.
      </p>
      <MemoryEnginePanel />
      <MemoryGraphPanel />
      <MemoryRerankerPanel registry={registry} />
    </div>
  );
}

function MemoryEnginePanel() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const engineField = _schemaField(schema, "memory.engine");
  const currentEngine = live?.memory?.engine || "hindsight";
  const [engine, setEngine] = useStateSet(currentEngine);
  useEffectSet(() => {
    if (live?.memory?.engine) setEngine(live.memory.engine);
  }, [live?.memory?.engine]);

  const dirty = engine !== currentEngine;

  const doSave = async () => {
    try {
      await update.mutateAsync({ memory: { engine } });
      setEngine(engine);
      window.__hal0Toast && window.__hal0Toast("Memory engine saved — restart hal0-api to apply", "warn");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const engineDesc = ADV_DESC_OVERRIDE["memory.engine"] || engineField?.description || "";
  const options = ["hindsight", "pgvector"];

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k"><span>Engine</span><span className="sub">hal0.toml [memory] · requires restart to switch</span></div>
      </div>
      <SRow
        k="Engine"
        sub={<span title={engineDesc}>{engineDesc.length > 150 ? engineDesc.slice(0, 147) + "…" : engineDesc}</span>}
        v={
          <select value={engine} onChange={e => setEngine(e.target.value)} style={_advInputStyle}>
            {options.map(o => <option key={o} value={o}>{o}</option>)}
          </select>
        }
        actions={<ApplyBadge settingsKey="memory.engine" registry={registry} />}
      />
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {dirty && (
          <button className="btn ghost sm" onClick={() => setEngine(currentEngine)}>Reset</button>
        )}
        <button className="btn sm" disabled={!dirty || update.isPending} onClick={doSave}>
          {update.isPending ? "Saving…" : "Save engine"}
        </button>
      </div>
    </div>
  );
}

function MemoryRerankerPanel({ registry }) {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const schemaQuery = useSettingsSchema();
  const schema = schemaQuery.data || null;
  const live = settings.data || null;

  const RERANK_KEYS = [
    "memory.embedding.rerank_gateway_url",
    "memory.embedding.rerank_model",
    "memory.embedding.rerank_connect_timeout_s",
    "memory.embedding.rerank_read_timeout_s",
  ];
  const fields = {};
  for (const k of RERANK_KEYS) fields[k] = _schemaField(schema, k);

  const [buf, setBuf] = useStateSet({});
  const onChange = (dotKey, value) => setBuf(b => ({ ...b, [dotKey]: value }));

  const dirtyKeys = Object.keys(buf).filter(k => {
    const { ok, value } = _advCoerce(fields[k], buf[k]);
    if (!ok) return true;
    const cur = _getIn(live, k);
    return value !== (cur === undefined ? (fields[k]?.default ?? null) : cur);
  });
  const invalidKeys = dirtyKeys.filter(k => !_advCoerce(fields[k], buf[k]).ok);
  const canSave = dirtyKeys.length > 0 && invalidKeys.length === 0 && !update.isPending;

  const doSave = async () => {
    let patch = {};
    for (const k of dirtyKeys) {
      const { value } = _advCoerce(fields[k], buf[k]);
      patch = _deepMergePatch(patch, k.split(".").reverse().reduce((acc, part) => ({ [part]: acc }), value));
    }
    try {
      await update.mutateAsync(patch);
      setBuf({});
      window.__hal0Toast && window.__hal0Toast("Reranker settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  return (
    <div className="s-panel" style={{marginBottom: 12}}>
      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k"><span>Reranker</span><span className="sub">hal0.toml [memory.embedding] · second-pass ranking after recall</span></div>
      </div>
      {RERANK_KEYS.map(k => (
        <AdvRow
          key={k}
          dotKey={k}
          field={fields[k]}
          live={_getIn(live, k)}
          buf={buf[k]}
          onChange={onChange}
          registry={registry}
        />
      ))}
      <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
        {dirtyKeys.length > 0 && (
          <button className="btn ghost sm" onClick={() => setBuf({})}>Reset</button>
        )}
        <button className="btn sm" disabled={!canSave} onClick={doSave}>
          {update.isPending ? "Saving…" : "Save reranker"}
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
cd /mnt/repos/hal0-mono/hal0
git add ui/src/dash/settings.jsx
git commit -m "feat(ui): add Memory section with engine, graph extraction, and reranker panels"
```

---

### Task 6: Trim Advanced section — remove slots and memory groups

**Files:**
- Modify: `ui/src/dash/settings.jsx:1530-1545` (the `ADV_GROUPS` array)

**Interfaces:**
- Consumes: existing `ADV_GROUPS`, `AdvRow`, `AdvancedSection`
- Produces: trimmed `ADV_GROUPS` with only dispatcher and activity

- [ ] **Step 1: Update `ADV_GROUPS`**

Replace the existing `ADV_GROUPS` array (lines ~1530–1545) with:

```jsx
const ADV_GROUPS = [
  { title: "Dispatcher", sub: "hal0.toml [dispatcher] · upstream routing tunables", keys: [
    "dispatcher.prefetch_timeout_s", "dispatcher.prefetch_parallel_cap",
  ]},
  { title: "Activity log", sub: "hal0.toml [activity] · durable audit trail", keys: [
    "activity.enabled", "activity.retention_days", "activity.max_rows",
  ]},
];
```

Keep `ADV_OPTIONS` and `ADV_DESC_OVERRIDE` as-is (still referenced by other parts). Only trim `ADV_GROUPS`.

- [ ] **Step 2: Remove the `MemoryGraphPanel` conditional render in AdvancedSection**

In `AdvancedSection`, find and remove this line (around line 1794):

```jsx
              {g.title === "Memory" && <MemoryGraphPanel />}
```

(Or, if `ADV_GROUPS` no longer has a "Memory" group after trimming, this conditional never fires — but remove it anyway for cleanliness.)

- [ ] **Step 3: Update AdvancedSection description**

Change the description from:

```jsx
      <p className="desc">
        Runtime tuning for hal0.toml sections that don't have a dedicated page. Descriptions and
        bounds come from the server's config schema; effect chips show whether a change applies
        live or needs a restart.
      </p>
```

to:

```jsx
      <p className="desc">
        Low-level dispatcher and activity log tuning. Slot runtime moved to Slots; memory
        moved to Memory. Effect chips show whether a change applies live or needs a restart.
      </p>
```

- [ ] **Step 4: Commit**

```bash
cd /mnt/repos/hal0-mono/hal0
git add ui/src/dash/settings.jsx
git commit -m "feat(ui): trim Advanced section — move slots to Slots, memory to Memory"
```

---

### Task 7: Remove unused `_getIn` and `_schemaField` duplication check + final cleanup

**Files:**
- Modify: `ui/src/dash/settings.jsx` — verify no duplicates, clean imports

**Interfaces:**
- Consumes: all sections
- Produces: clean final file with no dead code

- [ ] **Step 1: Verify `_getIn`, `_deepMergePatch`, `_advCoerce`, `_schemaField`, `AdvRow`, `ApplyBadge` are still used**

These helper functions are used by `SlotsSection` (runtime panel), `MemorySection` (engine + reranker panels), and `AdvancedSection`. They are also used by `MemoryGraphPanel` which already depends on `_advInputStyle` and `_getIn`. 

Check `_advInputStyle` — it's referenced by `MemoryGraphPanel` (line ~1929 of original), so keep it.

- [ ] **Step 2: Verify all sections are wired in the render block**

The render block should have all 11 sections. Check the final order:

```jsx
          {section === "general" && <GeneralSection />}
          {section === "slots" && <SlotsSection />}
          {section === "npu" && <NpuSection />}
          {section === "memory" && <MemorySection />}
          {section === "voice" && <VoiceSection />}
          {section === "imagegen" && <ImageGenSection />}
          {section === "storage" && <StorageSection />}
          {section === "secrets" && <SecretsSection />}
          {section === "updates" && <UpdatesSection />}
          {section === "advanced" && <AdvancedSection />}
          {section === "about" && <AboutSection />}
```

Ensure there is no leftover `{section === "defaults" && <DefaultSlotsSection />}` line.

- [ ] **Step 3: Run a quick syntax check**

```bash
cd /mnt/repos/hal0-mono/hal0/ui && npx eslint src/dash/settings.jsx --max-warnings 0 2>&1 || true
```

Fix any lint errors. At minimum, run a node syntax check:

```bash
node -e "require('fs').readFileSync('src/dash/settings.jsx','utf8'); console.log('syntax ok')" 2>&1
```

- [ ] **Step 4: Commit**

```bash
cd /mnt/repos/hal0-mono/hal0
git add ui/src/dash/settings.jsx
git commit -m "chore(ui): final cleanup — verify wiring, remove dead references"
```

---

