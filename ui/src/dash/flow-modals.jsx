// hal0 dashboard — FirstRun + Backends + Agent flow modals
// Skip confirm, post-install hero, backend install/uninstall, FLM .deb guide, persona edit, namespace reset

const { useState: useStateFM, useEffect: useEffectFM } = React;

// ────────────────────────────────────────────────────────────────
// FIRSTRUN — Skip confirmation
// ────────────────────────────────────────────────────────────────
function SkipBundleDialog({ open, onCancel, onConfirm }) {
  return (
    <ConfirmDialog
      open={open}
      onCancel={onCancel}
      onConfirm={onConfirm}
      title="Skip the bundle picker?"
      message={
        <span>
          You'll land on the dashboard with no models loaded. The six seeded slots
          (<span className="mono">primary, embed, rerank, stt, tts, img</span>)
          will show <b>Configure</b> buttons. You can run the picker again later
          from <span className="mono">Settings → Run bundle picker again</span>.
        </span>
      }
      confirmLabel="Skip and configure manually"
      cancelLabel="Cancel"
    />
  );
}

// ────────────────────────────────────────────────────────────────
// BACKENDS — Install / Uninstall / FLM .deb guide
// ────────────────────────────────────────────────────────────────
function BackendInstallModal({ open, onClose, backend }) {
  if (!backend) return null;
  const isFlm = backend.kind === "flm";
  if (isFlm) return <FlmDebGuideModal open={open} onClose={onClose} backend={backend} />;
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={`Backends · install`}
      title={`Install ${backend.name}?`}
      width={580}
      foot={
        <>
          <span>Backend ships pinned with sha-256 verification.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Installing ${backend.name} — ETA ~2 min`, "info"); }}>{Icons.download} Install</button>
          </span>
        </>
      }
    >
      <p style={{fontSize: 13, color: "var(--fg-2)", lineHeight: 1.6, margin: "0 0 14px"}}>
        This downloads the <span className="mono" style={{color: "var(--fg)"}}>{backend.name}</span> binary and places it under <span className="mono" style={{color: "var(--fg)"}}>/opt/lemonade/bin</span>.
      </p>
      <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.7}}>
        <div><span style={{color: "var(--fg-4)"}}>version</span> · <span style={{color: "var(--fg)"}}>{backend.ver}</span></div>
        <div><span style={{color: "var(--fg-4)"}}>size</span> · <span style={{color: "var(--fg)"}}>~210 MB</span></div>
        <div><span style={{color: "var(--fg-4)"}}>eta</span> · <span style={{color: "var(--fg)"}}>~2 min on a 100 Mbps link</span></div>
        <div><span style={{color: "var(--fg-4)"}}>verify</span> · <span style={{color: "var(--ok)"}}>sha-256 pinned ✓</span></div>
        <div><span style={{color: "var(--fg-4)"}}>restart</span> · <span style={{color: "var(--warn)"}}>lemond restart required after install</span></div>
      </div>
      {backend.kind === "llamacpp" && backend.device === "rocm" && (
        <div style={{marginTop: 12, padding: "10px 12px", background: "var(--info-soft)", border: "1px solid var(--info-line)", borderRadius: "var(--rad-sm)", fontSize: 12, color: "var(--info)"}}>
          ROCm 6.4 detected on this host. The install will use gfx1151 build.
        </div>
      )}
    </Modal>
  );
}

function BackendUninstallModal({ open, onClose, backend }) {
  if (!backend) return null;
  const slotsUsing = HAL0_DATA.slots.filter(s => {
    if (backend.kind === "llamacpp" && s.modelLong && s.modelLong.includes("GGUF")) return true;
    if (backend.kind === "whispercpp" && s.type === "transcription" && s.device !== "npu") return true;
    if (backend.kind === "sdcpp" && s.type === "image") return true;
    if (backend.kind === "kokoro" && s.type === "tts") return true;
    if (backend.kind === "flm" && s.device === "npu") return true;
    return false;
  });
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Backends · uninstall"
      title={`Uninstall ${backend.name}?`}
      width={580}
      foot={
        <>
          <span style={{color: "var(--err)"}}>{slotsUsing.length} slot{slotsUsing.length === 1 ? "" : "s"} will lose this backend.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            {slotsUsing.length > 0 && (
              <button className="btn ghost sm" onClick={() => { onClose(); window.location.hash = "#slots"; }}>Move slots first →</button>
            )}
            <button className="btn danger sm" onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Uninstalling ${backend.name}`, "warn"); }}>Uninstall anyway</button>
          </span>
        </>
      }
    >
      <p style={{fontSize: 13, color: "var(--fg-2)", lineHeight: 1.6, margin: "0 0 14px"}}>
        Removes <span className="mono" style={{color: "var(--fg)"}}>{backend.name}</span> from <span className="mono" style={{color: "var(--fg)"}}>/opt/lemonade/bin</span>. Models on disk are not touched; they just won't have a backend to load through.
      </p>
      {slotsUsing.length > 0 ? (
        <div style={{padding: "12px 14px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)"}}>
          <div className="mono" style={{fontSize: 11, color: "var(--err)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 8}}>⚠ Slots using this backend</div>
          {slotsUsing.map(s => (
            <div key={s.name} style={{display: "grid", gridTemplateColumns: "100px 1fr auto", gap: 12, padding: "6px 0", fontFamily: "var(--jbm)", fontSize: 12, borderBottom: "1px solid rgba(239,107,107,0.15)"}}>
              <span style={{color: "var(--fg)", fontWeight: 500, display: "flex", alignItems: "center", gap: 6}}>
                <span className={"dot " + s.state} />
                {s.name}
              </span>
              <span style={{color: "var(--fg-3)"}}>{s.model}</span>
              <span style={{color: "var(--err)"}}>will go offline</span>
            </div>
          ))}
        </div>
      ) : (
        <div style={{padding: "10px 12px", background: "var(--ok-soft)", border: "1px solid var(--ok-line)", borderRadius: "var(--rad-sm)", color: "var(--ok)", fontSize: 12}}>
          No slots currently use this backend. Safe to uninstall.
        </div>
      )}
    </Modal>
  );
}

function FlmDebGuideModal({ open, onClose, backend }) {
  const cmd = `# 1. Download the FLM Linux .deb from AMD
wget https://amd.com/flm/flm_${(backend && backend.ver) || "0.9.42"}_amd64.deb

# 2. Install (requires sudo)
sudo dpkg -i flm_${(backend && backend.ver) || "0.9.42"}_amd64.deb

# 3. Add your user to the xdna group
sudo usermod -aG xdna $USER

# 4. Reboot or re-login, then restart lemond
sudo systemctl restart hal0-lemonade`;
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="FLM · manual install"
      title="Install FLM (.deb) — Linux"
      width={680}
      foot={
        <>
          <span>FLM's auto-installer is Windows-only. Linux requires this manual flow.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast("Commands copied to clipboard", "ok")}>Copy commands</button>
            <button className="btn sm" onClick={onClose}>Close</button>
          </span>
        </>
      }
    >
      <p style={{fontSize: 13, color: "var(--fg-2)", lineHeight: 1.6, margin: "0 0 14px"}}>
        FLM ships as a .deb directly from AMD. Run these from a shell on{" "}
        <span className="mono" style={{color: "var(--fg)"}}>{HAL0_DATA.host.name}</span>.
        After the reboot, the NPU trio slots (<span className="mono">agent</span>, <span className="mono">stt-npu</span>, <span className="mono">embed-npu</span>) will become configurable.
      </p>
      <pre style={{margin: 0, padding: 14, background: "#070707", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.65, color: "var(--fg-2)", overflowX: "auto", whiteSpace: "pre"}}>{cmd}</pre>
      <div style={{marginTop: 14, padding: "10px 12px", background: "var(--info-soft)", border: "1px solid var(--info-line)", borderRadius: "var(--rad-sm)", fontSize: 12, color: "var(--info)", fontFamily: "var(--jbm)"}}>
        After reboot, hal0 detects FLM automatically. You'll see a toast: <span style={{color: "var(--fg)"}}>"FLM v0.9.42 detected — NPU slots available"</span>.
      </div>
    </Modal>
  );
}

// ────────────────────────────────────────────────────────────────
// AGENT — Persona Edit · No-bundled-agent state · Reset namespace
// ────────────────────────────────────────────────────────────────
function PersonaEditModal({ open, onClose, persona }) {
  const [systemPrompt, setSystemPrompt] = useStateFM(
    "You are hal0, an operator-direct AI assistant running locally on the user's hardware. Be terse, technical, and surface the slots/tools you use as you work."
  );
  const [tone, setTone] = useStateFM(persona?.tone || "operator");
  const [slot, setSlot] = useStateFM(persona?.slot || "primary");

  useEffectFM(() => {
    if (open && persona) {
      setSlot(persona.slot || "primary");
      setTone(persona.tone || "operator");
    }
  }, [open, persona]);

  const llmSlots = HAL0_DATA.slots.filter(s => s.type === "llm");

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Agent · persona"
      title={persona?.isAdd ? "New persona" : `Edit ${persona?.name || "persona"}`}
      width={680}
      foot={
        <>
          <span>Personas route to a chat slot and carry their own system prompt + tone.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Persona saved`, "ok"); }}>Save</button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>Name</span>
          <span className="sub">unique within personas</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" defaultValue={persona?.name || ""} placeholder="hermes-coder" />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Routes to slot</span>
          <span className="sub">only llm-type slots are eligible</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={slot} onChange={e => setSlot(e.target.value)}>
            {llmSlots.map(s => (
              <option key={s.name} value={s.name}>{s.name} · {s.model} · {s.device}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Tone</span>
          <span className="sub">descriptive label · doesn't affect routing</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={tone} onChange={e => setTone(e.target.value)}>
            <option value="operator">operator — terse + technical</option>
            <option value="code-focused">code-focused — refactors, reviews</option>
            <option value="low-latency">low-latency — NPU coresident</option>
            <option value="vision">vision-first — image-aware</option>
            <option value="conversational">conversational — slower, fuller</option>
          </select>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>System prompt</span>
          <span className="sub">prepended on every request to this persona</span>
        </div>
        <div className="form-ctl">
          <textarea
            className="input mono"
            value={systemPrompt}
            onChange={e => setSystemPrompt(e.target.value)}
            rows={6}
            style={{resize: "vertical", minHeight: 100}}
          />
          <div className="hint">{systemPrompt.length} chars · ~{Math.round(systemPrompt.length / 4)} tokens</div>
        </div>
      </div>

      <div className="form-section">Tool set</div>
      <div className="form-row">
        <div className="form-lbl">
          <span>Allowed tools</span>
          <span className="sub">subset of OmniRouter tools this persona can call</span>
        </div>
        <div className="form-ctl" style={{display: "flex", flexWrap: "wrap", gap: 8}}>
          {["read_file", "write_file", "edit_file", "shell_exec", "generate_image", "transcribe_audio", "text_to_speech", "embed_text"].map(t => (
            <label key={t} className="checkbox-row">
              <input type="checkbox" defaultChecked={["read_file", "edit_file", "embed_text"].includes(t)} />
              <span className="mono">{t}</span>
            </label>
          ))}
        </div>
      </div>
    </Modal>
  );
}

function NoBundledAgentCard() {
  const [pick, setPick] = useStateFM("hermes");
  return (
    <div className="card" style={{padding: 24, marginBottom: 18, borderStyle: "dashed"}}>
      <div style={{display: "flex", alignItems: "center", gap: 14, marginBottom: 14}}>
        <div style={{width: 44, height: 44, borderRadius: 8, background: "var(--bg-2)", border: "1px solid var(--line)", display: "inline-flex", alignItems: "center", justifyContent: "center", color: "var(--fg-3)"}}>
          {Icons.agent}
        </div>
        <div>
          <div className="mono" style={{fontSize: 16, fontWeight: 500, letterSpacing: "-0.02em"}}>No bundled agent installed</div>
          <div className="mono" style={{fontSize: 11.5, color: "var(--fg-3)", marginTop: 2}}>Pick an agent shape · install once · agents persist across reboots</div>
        </div>
      </div>
      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 14}}>
        {[
          { id: "pi-coder", name: "pi-coder", shape: "CLI shape · invoked per task", tools: 4, src: "@earendil-works/pi-coding-agent" },
          { id: "hermes",   name: "Hermes-Agent", shape: "Service shape · resident systemd unit", tools: 12, src: "hal0-agent-hermes.service" },
        ].map(opt => (
          <label
            key={opt.id}
            className="card"
            style={{padding: 16, cursor: "pointer", borderColor: pick === opt.id ? "var(--accent-line)" : "var(--line)"}}
          >
            <input
              type="radio"
              checked={pick === opt.id}
              onChange={() => setPick(opt.id)}
              style={{accentColor: "var(--accent)", marginRight: 8}}
            />
            <span className="mono" style={{fontSize: 14, fontWeight: 500}}>{opt.name}</span>
            <div className="mono" style={{fontSize: 11.5, color: "var(--fg-3)", marginTop: 6}}>{opt.shape}</div>
            <div style={{display: "flex", gap: 6, marginTop: 8, fontFamily: "var(--jbm)", fontSize: 10.5}}>
              <span className="chip">{opt.tools} tools</span>
              <span className="chip" style={{color: "var(--fg-4)"}}>{opt.src}</span>
            </div>
          </label>
        ))}
      </div>
      <div style={{display: "flex", justifyContent: "flex-end"}}>
        <button className="btn" onClick={() => window.__hal0Toast && window.__hal0Toast(`Installing ${pick === "pi-coder" ? "pi-coder CLI" : "Hermes-Agent service"} — ETA ~1 min`, "info")}>
          {Icons.download} Install {pick === "pi-coder" ? "pi-coder" : "Hermes"}
        </button>
      </div>
    </div>
  );
}

Object.assign(window, { SkipBundleDialog, BackendInstallModal, BackendUninstallModal, FlmDebGuideModal, PersonaEditModal, NoBundledAgentCard });
