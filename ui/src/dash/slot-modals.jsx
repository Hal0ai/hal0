// hal0 dashboard — Slot interactive surface
// Create-slot modal, Edit-slot drawer, inline swap popover, overflow menu,
// empty/error SlotCard variants. Wired into slots.jsx via window globals.

const { useState: useStateSM, useEffect: useEffectSM, useRef: useRefSM } = React;

// ─── Create-slot modal ──────────────────────────────────────────
function CreateSlotModal({ open, onClose, defaults = {} }) {
  const [name, setName] = useStateSM(defaults.name || "");
  const [type, setType] = useStateSM(defaults.type || "llm");
  const [device, setDevice] = useStateSM(defaults.device || "gpu-rocm");
  const [model, setModel] = useStateSM(defaults.model || "");
  const [group, setGroup] = useStateSM(defaults.group || "chat");
  const [advOpen, setAdvOpen] = useStateSM(false);
  const [makeDefault, setMakeDefault] = useStateSM(false);
  const [ctx, setCtx] = useStateSM(8192);
  const [extraArgs, setExtraArgs] = useStateSM("--flash-attn on");

  useEffectSM(() => {
    if (open) {
      setName(defaults.name || "");
      setType(defaults.type || "llm");
      setDevice(defaults.device || "gpu-rocm");
      setGroup(defaults.group || "chat");
      setModel("");
      setAdvOpen(false);
      setMakeDefault(false);
    }
  }, [open, defaults]);

  // validation
  const existing = HAL0_DATA.slots.map(s => s.name);
  const nameCollision = existing.includes(name);
  const nameInvalid = name && !/^[a-z][a-z0-9-]{0,30}$/.test(name);
  const nameError = nameCollision ? "name already in use" : nameInvalid ? "lowercase + dashes only" : null;

  const compatible = HAL0_DATA.models.filter(m =>
    m.type === type &&
    (device === "cpu" || m.device === (device || "cpu").replace("gpu-", "") || (device === "npu" && m.device === "npu"))
  );

  const npuAvailable = HAL0_DATA.host.npu.present;
  // Empty-state save is allowed — model is optional, slot saves in `empty` state.
  const canSave = !!name && !nameError;

  // Next available port after the highest currently-allocated
  const nextPort = Math.max(...HAL0_DATA.slots.map(s => s.port || 8090)) + 1;

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Slots · new"
      title="Create slot"
      width={640}
      foot={
        <>
          <span>capabilities.toml will be written on save.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Slot "${name}" created`, "ok"); }} disabled={!canSave}>Create slot</button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>Name <span className="req">*</span></span>
          <span className="sub">bare · kebab-case · unique across the host</span>
        </div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="coder-large"
            autoFocus
          />
          {nameError && <div className="err">{nameError}</div>}
          {!nameError && name && <div className="ok">✓ available</div>}
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Type <span className="req">*</span></span>
          <span className="sub">drives the model filter + OmniRouter tool</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={type} onChange={e => setType(e.target.value)}>
            <option value="llm">llm</option>
            <option value="embedding">embedding</option>
            <option value="reranking">reranking</option>
            <option value="transcription">transcription</option>
            <option value="tts">tts</option>
            <option value="image">image</option>
          </select>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Device <span className="req">*</span></span>
          <span className="sub">{!npuAvailable && device === "npu" ? <span style={{color: "var(--warn)"}}>NPU disabled — FLM not installed</span> : "hardware preference for this slot"}</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={device} onChange={e => setDevice(e.target.value)}>
            <option value="gpu-rocm">gpu-rocm</option>
            <option value="gpu-vulkan">gpu-vulkan</option>
            <option value="cpu">cpu</option>
            <option value="npu" disabled={!npuAvailable}>npu{!npuAvailable ? " — install FLM first" : ""}</option>
          </select>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Model</span>
          <span className="sub">filtered to compatible · {compatible.length} match{compatible.length !== 1 ? "es" : ""}</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={model} onChange={e => setModel(e.target.value)}>
            <option value="">— Select later (slot saves in `empty` state)</option>
            {compatible.map(m => (
              <option key={m.id} value={m.id}>
                {m.longName} · {m.size} {m.installed ? "· on disk" : "· will pull"}
              </option>
            ))}
          </select>
          {model && compatible.find(m => m.id === model) && (
            <div className="ok">✓ fits in available memory ({HAL0_DATA.host.ram.free} GB free)</div>
          )}
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Port (auto-assigned)</span>
          <span className="sub">child process port hal0 will allocate</span>
        </div>
        <div className="form-ctl">
          <span className="mono" style={{padding: "6px 10px", background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", display: "inline-block", color: "var(--fg-3)", fontSize: 12}}>:{nextPort}</span>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Group</span>
          <span className="sub">pure UI rollup label</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={group} onChange={e => setGroup(e.target.value)}>
            <option value="chat">chat</option>
            <option value="embed">embed</option>
            <option value="voice">voice</option>
            <option value="img">img</option>
            <option value="custom">custom</option>
          </select>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Default for type {type}?</span>
          <span className="sub">flips `default = true`; demotes the current one</span>
        </div>
        <div className="form-ctl">
          <label className="checkbox-row">
            <input type="checkbox" checked={makeDefault} onChange={e => setMakeDefault(e.target.checked)} />
            <span>Set as default</span>
          </label>
        </div>
      </div>

      <div className="form-section" onClick={() => setAdvOpen(v => !v)} style={{cursor: "pointer", display: "flex", alignItems: "center", gap: 8}}>
        <span style={{transform: advOpen ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s ease"}}>{Icons.chevR}</span>
        <span>Recipe options</span>
        <span style={{marginLeft: "auto", color: "var(--fg-5)", letterSpacing: 0, textTransform: "none"}}>{advOpen ? "collapse" : "expand"}</span>
      </div>
      {advOpen && (
        <>
          <div className="form-row">
            <div className="form-lbl">
              <span>ctx_size</span>
              <span className="warn">⟳ restart required</span>
            </div>
            <div className="form-ctl">
              <input className="input mono" value={ctx} onChange={e => setCtx(Number(e.target.value))} />
            </div>
          </div>
          <div className="form-row">
            <div className="form-lbl">
              <span>llamacpp_args</span>
              <span className="sub">merged with --parallel 1 --threads 8 baseline</span>
            </div>
            <div className="form-ctl">
              <input className="input mono" value={extraArgs} onChange={e => setExtraArgs(e.target.value)} />
              <div className="hint">Denied flags: <span className="mono">-m / --port / --ctx-size / -ngl / --jinja / --mmproj / --embeddings / --reranking</span></div>
            </div>
          </div>
        </>
      )}
    </Modal>
  );
}

// ─── Edit-slot drawer ───────────────────────────────────────────
function EditSlotDrawer({ open, slot, onClose }) {
  if (!slot) return null;
  return (
    <Drawer
      open={open}
      onClose={onClose}
      eyebrow={`Slots · /slots/${slot.name}`}
      title={`Edit ${slot.name}`}
      width={560}
      foot={
        <>
          <button className="btn danger sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Delete confirm — slot "${slot.name}"`, "warn")}>{Icons.unload} Delete slot</button>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Slot "${slot.name}" saved — restart required for ctx_size`, "warn"); }}>Save</button>
          </span>
        </>
      }
    >
      {/* Provider + port strip — read-only */}
      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 0, border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", overflow: "hidden", marginBottom: 16}}>
        <ReadOnlyStrip k="provider" v="lemonade" />
        <ReadOnlyStrip k="port" v={`:${slot.port || "—"}`} />
        <ReadOnlyStrip k="state" v={<span className="chip ok">{slot.state}</span>} />
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>Name</span><span className="sub">seeded slots can't be renamed</span></div>
        <div className="form-ctl"><input className="input mono" value={slot.name} disabled /></div>
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>Type</span></div>
        <div className="form-ctl">
          <select className="input mono" defaultValue={slot.type} disabled>
            <option>{slot.type}</option>
          </select>
          <div className="hint">Type is immutable. Create a new slot to change.</div>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>Device</span><span className="warn">⟳ restart required</span></div>
        <div className="form-ctl">
          <select className="input mono" defaultValue={slot.device}>
            <option value="gpu-rocm">gpu-rocm</option>
            <option value="gpu-vulkan">gpu-vulkan</option>
            <option value="cpu">cpu</option>
            <option value="npu">npu</option>
          </select>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>Model</span><span className="sub">use inline swap from the card for live changes</span></div>
        <div className="form-ctl">
          <input className="input mono" value={slot.modelLong || slot.model} readOnly />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Default for type {slot.type}?</span>
          <span className="sub">{slot.isDefault ? "currently default" : "another slot is default"}</span>
        </div>
        <div className="form-ctl">
          <label className="checkbox-row">
            <input type="checkbox" defaultChecked={slot.isDefault} />
            <span>Set as default</span>
          </label>
        </div>
      </div>

      <div className="form-section">Advanced</div>

      <div className="form-row">
        <div className="form-lbl"><span>ctx_size</span><span className="warn">⟳ restart required</span></div>
        <div className="form-ctl">
          <input className="input mono" defaultValue={slot.metrics.ctx || 4096} />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>idle_timeout_s</span><span className="sub">unload after N seconds idle</span></div>
        <div className="form-ctl">
          <input className="input mono" defaultValue={900} />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>workers</span><span className="sub">concurrent inflight per slot · 1 = serial</span></div>
        <div className="form-ctl">
          <input className="input mono" defaultValue={1} />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>extra_args</span><span className="sub">slot-level llamacpp_args overlay</span></div>
        <div className="form-ctl">
          <input className="input mono" defaultValue="--flash-attn on --no-mmap" />
          <div className="hint">Merged with model recipe defaults + the global baseline.</div>
        </div>
      </div>

      <div className="form-section">Effective flags preview</div>
      <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-3)", lineHeight: 1.6, whiteSpace: "pre-wrap"}}>
        {effectiveFlagsFor(slot)}
      </div>
      <div className="hint" style={{paddingTop: 6, fontSize: 10.5, color: "var(--fg-5)", fontFamily: "var(--jbm)"}}>
        Merge order: lemond baseline → backend default → model recipe → slot extra_args. Read-only.
      </div>
    </Drawer>
  );
}

// Per-slot-type effective flags preview
function effectiveFlagsFor(slot) {
  const port = `--port ${slot.port || "auto"}`;
  const baseline = "--parallel 1 --threads 8";
  if (slot.type === "llm") {
    return `${baseline} --flash-attn on --no-mmap\n--ctx-size ${slot.metrics.ctx || 4096}\n-m ${slot.modelLong || slot.model}\n${port}\n-ngl ${slot.device === "cpu" ? 0 : 999}`;
  }
  if (slot.type === "embedding" || slot.type === "reranking") {
    return `${baseline} --embeddings${slot.type === "reranking" ? " --reranking" : ""}\n--ctx-size ${slot.metrics.dim || 8192}\n-m ${slot.modelLong || slot.model}\n${port}\n-ngl ${slot.device === "cpu" ? 0 : 999}`;
  }
  if (slot.type === "transcription") {
    return `--backend ${slot.device === "npu" ? "flm" : "whispercpp:vulkan"}\n--model ${slot.modelLong || slot.model}\n--precision ${slot.metrics.precision || "Q4_K_M"}\n${port}`;
  }
  if (slot.type === "tts") {
    return `--backend kokoro:cpu\n--voice ${slot.metrics.voice || "af_heart"}\n--model ${slot.modelLong || slot.model}\n${port}`;
  }
  if (slot.type === "image") {
    return `--backend sdcpp:${slot.device === "cpu" ? "cpu" : "rocm"}\n--model ${slot.modelLong || slot.model}\n--steps 20 --cfg 7.0\n--w 512 --h 512\n${port}`;
  }
  return `${baseline}\n${port}`;
}

function ReadOnlyStrip({ k, v }) {
  return (
    <div style={{padding: "10px 12px", borderRight: "1px solid var(--line-soft)", background: "var(--bg)"}}>
      <div className="mono" style={{fontSize: 9, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 3}}>{k}</div>
      <div className="mono" style={{fontSize: 12, color: "var(--fg)"}}>{v}</div>
    </div>
  );
}

// ─── Inline swap popover ────────────────────────────────────────
function InlineSwapPopover({ slot, open, onClose, onPick }) {
  if (!open) return null;
  const compatible = HAL0_DATA.models.filter(m => m.type === slot.type);
  return (
    <div className="swap-pop" onClick={e => e.stopPropagation()}>
      <div className="swap-pop-h">Swap model · type {slot.type}</div>
      {compatible.map(m => {
        const isCur = slot.model_id === m.id;
        const fits = HAL0_DATA.host.ram.free > parseSizeGB(m.size);
        return (
          <div
            key={m.id}
            className={"swap-pop-item" + (isCur ? " cur" : "")}
            onClick={() => { onPick(m); onClose(); }}
          >
            <div className="nm">
              {m.longName}
              <span className="sub">{m.repo}</span>
            </div>
            <div className="sz num">{m.size}</div>
            <div className={"fit" + (fits ? "" : " no")}>{m.installed ? (fits ? "fits ✓" : "tight") : "will pull"}</div>
          </div>
        );
      })}
      <div className="swap-pop-h" style={{cursor: "pointer", color: "var(--accent)"}}
           onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast("Browse all models — routing to /models", "info"); }}>
        + Browse all models →
      </div>
    </div>
  );
}

// ─── Overflow menu (⋯) ──────────────────────────────────────────
function SlotOverflowMenu({ slot, onClose }) {
  return (
    <Menu
      anchor="right"
      onClose={onClose}
      items={[
        { icon: Icons.logs, label: "View slot logs", onClick: () => window.__hal0Toast && window.__hal0Toast(`Filtering /logs to slot:${slot.name}`, "info") },
        { icon: Icons.flame, label: slot.isDefault ? "Already default" : "Set as default", onClick: () => window.__hal0Toast && window.__hal0Toast(`${slot.name} set as default for ${slot.type}`, "ok") },
        { icon: Icons.ext, label: "Copy curl example", onClick: () => window.__hal0Toast && window.__hal0Toast("curl example copied to clipboard", "ok") },
        { divider: true },
        { icon: Icons.unload, label: "Delete slot", danger: true, onClick: () => window.__hal0Toast && window.__hal0Toast(`Delete confirm — seeded slot "${slot.name}" can only be disabled`, "warn") },
      ]}
    />
  );
}

// ─── Empty SlotCard (no model loaded) ────────────────────────────
function EmptySlotCard({ name, type, group, device, onConfigure }) {
  return (
    <div className="slot" style={{borderStyle: "dashed", borderColor: "var(--line)"}}>
      <div className="slot-h">
        <span className="dot empty" />
        <div className="slot-name"><span className="nm" style={{color: "var(--fg-3)"}}>{name}</span></div>
      </div>
      <div style={{padding: "8px 10px", background: "var(--bg)", border: "1px dashed var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)", fontStyle: "italic"}}>
        no model loaded
      </div>
      <div className="slot-chips">
        <span className="chip">{type}</span>
        <span className={"chip dev-" + (device || "cpu").replace("gpu-", "")}>{device}</span>
        <span className="chip">{group}</span>
      </div>
      <div style={{padding: "10px 12px", background: "var(--accent-soft)", border: "1px solid var(--accent-line)", borderRadius: "var(--rad-sm)", display: "flex", alignItems: "center", gap: 8}}>
        <span className="mono" style={{fontSize: 11, color: "var(--accent)", flex: 1}}>seeded · ready to configure</span>
        <button className="btn sm" onClick={onConfigure}>{Icons.plus} Configure</button>
      </div>
    </div>
  );
}

// ─── Error SlotCard ─────────────────────────────────────────────
function ErrorSlotCardBanner({ slot, message }) {
  return (
    <div style={{padding: "10px 12px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", display: "flex", alignItems: "flex-start", gap: 8}}>
      <span style={{color: "var(--err)", display: "inline-flex"}}>{Icons.warn}</span>
      <div style={{flex: 1, fontFamily: "var(--jbm)", fontSize: 11.5, color: "var(--fg-2)", lineHeight: 1.5}}>
        <div style={{color: "var(--err)", fontWeight: 500, marginBottom: 2}}>load failed</div>
        <div>{message}</div>
        <div style={{display: "flex", gap: 6, marginTop: 6}}>
          <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Retrying load for ${slot.name}`, "info")}>{Icons.restart} Retry</button>
          <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Re-pulling model for ${slot.name}`, "info")}>{Icons.download} Re-pull</button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { CreateSlotModal, EditSlotDrawer, InlineSwapPopover, SlotOverflowMenu, EmptySlotCard, ErrorSlotCardBanner });
