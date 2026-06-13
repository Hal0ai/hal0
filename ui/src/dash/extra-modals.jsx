// hal0 dashboard — Add-Secret modal
// Curated dropdown with auto-detect by token prefix.

import { useSecretSet } from '@/api/hooks/useSecrets'

const { useState: useStateAS, useEffect: useEffectAS } = React;

// ─── Add Secret modal ───────────────────────────────────────────
const SECRET_PRESETS = [
  { id: "HF_TOKEN",           desc: "Hugging Face — gated repo auth (used for model pulls)",        prefix: "hf_",       prefixLen: 37 },
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
  const secretSet = useSecretSet();

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
          <span>Stored encrypted on disk · accessible to hal0 only.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose} disabled={secretSet.isPending}>Cancel</button>
            <button
              className="btn sm"
              disabled={!canSave || secretSet.isPending}
              onClick={async () => {
                try {
                  await secretSet.mutateAsync({ name: finalName, value });
                  window.__hal0Toast && window.__hal0Toast(`Secret ${finalName} stored`, "ok");
                  onClose();
                } catch (e) {
                  window.__hal0Toast && window.__hal0Toast(
                    `Failed to store secret — ${e?.message || "see logs"}`,
                    "err"
                  );
                }
              }}
            >
              {secretSet.isPending ? "Saving…" : "Add secret"}
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
            <span className="sub">SCREAMING_SNAKE_CASE · exported to slot containers as env var</span>
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
        After save, affected slots restart in the background (~3s) so the new env reaches their containers. Existing requests fail-soft and retry.
      </div>
    </Modal>
  );
}

Object.assign(window, { AddSecretModal });
