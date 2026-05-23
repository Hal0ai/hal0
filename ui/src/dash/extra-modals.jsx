// hal0 dashboard — Add-Secret modal + Onboarding tour
// Curated dropdown with auto-detect by token prefix; tour = 3-step coachmark overlay.

const { useState: useStateAS, useEffect: useEffectAS, useRef: useRefAS } = React;

// ─── Add Secret modal ───────────────────────────────────────────
const SECRET_PRESETS = [
  { id: "HF_TOKEN",           desc: "Hugging Face — gated repo auth (used by lemond for /v1/pull)", prefix: "hf_",       prefixLen: 37 },
  { id: "OPENAI_API_KEY",     desc: "Fallback provider — OpenAI",                                     prefix: "sk-",       prefixLen: 51 },
  { id: "ANTHROPIC_API_KEY",  desc: "Fallback provider — Anthropic",                                  prefix: "sk-ant-",   prefixLen: 95 },
  { id: "GOOGLE_API_KEY",     desc: "Fallback provider — Gemini",                                     prefix: "AIza",      prefixLen: 39 },
  { id: "GROQ_API_KEY",       desc: "Fallback provider — Groq",                                       prefix: "gsk_",      prefixLen: 56 },
  { id: "AWS_ACCESS_KEY_ID",  desc: "Bedrock provider — paired with AWS_SECRET_ACCESS_KEY",            prefix: "AKIA",      prefixLen: 20 },
  { id: "CUSTOM",             desc: "Custom — name it yourself",                                       prefix: "",          prefixLen: 0 },
];

function AddSecretModal({ open, onClose }) {
  const [picked, setPicked] = useStateAS("HF_TOKEN");
  const [customName, setCustomName] = useStateAS("");
  const [value, setValue] = useStateAS("");
  const [show, setShow] = useStateAS(false);

  useEffectAS(() => {
    if (open) { setPicked("HF_TOKEN"); setCustomName(""); setValue(""); setShow(false); }
  }, [open]);

  // Auto-detect: if value matches a known prefix, snap the picker
  useEffectAS(() => {
    if (!value) return;
    for (const p of SECRET_PRESETS) {
      if (p.prefix && value.startsWith(p.prefix)) {
        if (picked !== p.id) setPicked(p.id);
        return;
      }
    }
  }, [value]);

  const preset = SECRET_PRESETS.find(p => p.id === picked);
  const isCustom = picked === "CUSTOM";
  const finalName = isCustom ? customName.trim() : picked;

  const prefixOk = !preset.prefix || value.startsWith(preset.prefix);
  const lengthOk = !preset.prefixLen || value.length >= Math.max(preset.prefix.length + 8, preset.prefixLen - 8);
  const nameOk = !isCustom || /^[A-Z][A-Z0-9_]{2,40}$/.test(customName);

  const canSave = finalName && value && prefixOk && lengthOk && nameOk;

  const validations = [];
  if (value && !prefixOk) validations.push({ kind: "err", msg: `Expected prefix ${preset.prefix}…` });
  if (value && prefixOk && !lengthOk) validations.push({ kind: "warn", msg: `Token looks short for ${preset.id} (typical ${preset.prefixLen} chars)` });
  if (value && prefixOk && lengthOk) validations.push({ kind: "ok", msg: `✓ shape matches ${preset.id}` });
  if (isCustom && customName && !nameOk) validations.push({ kind: "err", msg: "Use SCREAMING_SNAKE_CASE · 3–40 chars · starts with a letter" });

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Settings · secrets"
      title="Add a secret"
      width={620}
      foot={
        <>
          <span>Stored encrypted on disk · accessible to lemond only.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" disabled={!canSave} onClick={() => { onClose(); window.__hal0Toast && window.__hal0Toast(`Secret ${finalName} stored`, "ok"); }}>
              Add secret
            </button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>Key <span className="req">*</span></span>
          <span className="sub">picker auto-snaps if the value matches a known prefix</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={picked} onChange={e => setPicked(e.target.value)}>
            {SECRET_PRESETS.map(p => (
              <option key={p.id} value={p.id}>{p.id}{p.prefix ? ` · ${p.prefix}…` : ""}</option>
            ))}
          </select>
          <div className="hint">{preset.desc}</div>
        </div>
      </div>

      {isCustom && (
        <div className="form-row">
          <div className="form-lbl">
            <span>Custom key name <span className="req">*</span></span>
            <span className="sub">SCREAMING_SNAKE_CASE · exported to lemond as env var</span>
          </div>
          <div className="form-ctl">
            <input
              className="input mono"
              value={customName}
              onChange={e => setCustomName(e.target.value.toUpperCase())}
              placeholder="MY_PROVIDER_TOKEN"
            />
          </div>
        </div>
      )}

      <div className="form-row">
        <div className="form-lbl">
          <span>Value <span className="req">*</span></span>
          <span className="sub">never shown again after save · paste from clipboard</span>
        </div>
        <div className="form-ctl">
          <div style={{position: "relative"}}>
            <input
              className="input mono"
              type={show ? "text" : "password"}
              value={value}
              onChange={e => setValue(e.target.value)}
              placeholder={preset.prefix ? `${preset.prefix}…` : "paste the token here"}
              style={{paddingRight: 60, fontFamily: "var(--jbm)", letterSpacing: show ? "normal" : "0.18em"}}
              autoFocus
            />
            <button
              type="button"
              onClick={() => setShow(s => !s)}
              style={{position: "absolute", right: 6, top: "50%", transform: "translateY(-50%)", background: "transparent", border: "none", color: "var(--fg-4)", cursor: "pointer", fontFamily: "var(--jbm)", fontSize: 11, padding: "4px 8px", borderRadius: 3}}
            >{show ? "hide" : "show"}</button>
          </div>
          {validations.map((v, i) => (
            <div key={i} className={v.kind}>{v.msg}</div>
          ))}
        </div>
      </div>

      <div style={{marginTop: 14, padding: "10px 12px", background: "var(--info-soft)", border: "1px solid var(--info-line)", borderRadius: "var(--rad-sm)", color: "var(--info)", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.5}}>
        After save, lemond is restarted in the background (~3s) so the new env reaches its children. Existing requests fail-soft and retry.
      </div>
    </Modal>
  );
}

// ─── Onboarding Tour (anchored coachmarks) ───────────────────────
function OnboardingTour({ open, onClose, steps }) {
  const [idx, setIdx] = useStateAS(0);
  const [pos, setPos] = useStateAS(null);
  const stepRef = useRefAS(null);

  useEffectAS(() => { if (open) setIdx(0); }, [open]);

  useEffectAS(() => {
    if (!open) return;
    const place = () => {
      const sel = steps[idx]?.selector;
      const el = sel ? document.querySelector(sel) : null;
      if (!el) { setPos({ centered: true }); return; }
      const rect = el.getBoundingClientRect();
      setPos({
        targetTop: rect.top,
        targetLeft: rect.left,
        targetWidth: rect.width,
        targetHeight: rect.height,
        side: rect.top > window.innerHeight / 2 ? "above" : "below",
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, idx, steps]);

  if (!open) return null;
  const step = steps[idx];
  if (!step) { onClose(); return null; }

  const last = idx === steps.length - 1;
  const halo = pos && !pos.centered ? (
    <div
      className="tour-halo"
      style={{
        top: pos.targetTop - 6,
        left: pos.targetLeft - 6,
        width: pos.targetWidth + 12,
        height: pos.targetHeight + 12,
      }}
    />
  ) : null;

  let cardStyle;
  if (!pos || pos.centered) {
    cardStyle = { top: "50%", left: "50%", transform: "translate(-50%, -50%)" };
  } else if (pos.side === "above") {
    cardStyle = { bottom: window.innerHeight - pos.targetTop + 14, left: Math.min(Math.max(pos.targetLeft, 16), window.innerWidth - 360) };
  } else {
    cardStyle = { top: pos.targetTop + pos.targetHeight + 14, left: Math.min(Math.max(pos.targetLeft, 16), window.innerWidth - 360) };
  }

  return (
    <div className="tour-backdrop" onClick={(e) => { if (e.target.classList.contains("tour-backdrop")) onClose(); }}>
      {halo}
      <div className="tour-card" style={cardStyle} ref={stepRef}>
        <div className="tour-card-h mono">
          <span>Tour · step {idx + 1} of {steps.length}</span>
          <button onClick={onClose} aria-label="Close" style={{background: "transparent", border: "none", color: "var(--fg-4)", cursor: "pointer", padding: 2}}>{Icons.close}</button>
        </div>
        <div className="tour-card-title mono">{step.title}</div>
        <div className="tour-card-body">{step.body}</div>
        <div className="tour-card-foot">
          <div className="tour-dots">
            {steps.map((_, i) => (
              <span key={i} className={"tour-dot" + (i === idx ? " on" : "")} />
            ))}
          </div>
          <div style={{display: "inline-flex", gap: 6}}>
            <button className="btn ghost sm" onClick={onClose}>Skip</button>
            {idx > 0 && <button className="btn ghost sm" onClick={() => setIdx(i => i - 1)}>← Back</button>}
            <button
              className="btn sm"
              onClick={() => { if (last) onClose(); else setIdx(i => i + 1); }}
            >{last ? "Finish" : "Next →"}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

const TOUR_STEPS = [
  {
    title: "Persona dropdown",
    body: <span>Open the dropdown to swap which slot serves the next message. Changes are session-only by default; opt-in to persist in <span className="mono">Settings → OmniRouter</span>.</span>,
    selector: ".persona",
  },
  {
    title: "Slot snapshot",
    body: <span>The strip is your at-a-glance status for every configured slot. Click any row to jump straight into its edit drawer.</span>,
    selector: ".snap",
  },
  {
    title: "Attach + voice",
    body: <span>Drop a file or hold the mic to send audio. OmniRouter routes the tool call to the right slot automatically — image goes to <span className="mono">img</span>, audio to <span className="mono">stt</span>, etc.</span>,
    selector: ".composer-bar",
  },
  {
    title: "Command palette",
    body: <span>Press <kbd className="kbd">⌘K</kbd> anywhere to jump between routes, slots, and models — or run actions like "Restart lemond" without leaving the keyboard.</span>,
    selector: ".tb-cmdk",
  },
];

Object.assign(window, { AddSecretModal, OnboardingTour, TOUR_STEPS });
