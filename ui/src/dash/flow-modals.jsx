// hal0 dashboard — FirstRun + Backends + Agent flow modals
// Skip confirm, post-install hero, backend install/uninstall, FLM .deb guide, persona edit, namespace reset

import { useAgentPersona, useAgentPersonas, usePersonaUpdate } from '@/api/hooks/useAgents'
import { useNpuLoad, useNpuUnload } from '@/api/hooks/useBackends'
import { useSlots } from '@/api/hooks/useSlots'

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
  // No dashboard install path exists for non-FLM backends. Show honest state.
  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow={`Backends · install`}
      title={`Install ${backend.name}`}
      width={580}
      foot={
        <>
          <span>Use the CLI to manage non-NPU backends.</span>
          <button className="btn sm" onClick={onClose}>Close</button>
        </>
      }
    >
      <div style={{padding: "14px 16px", background: "var(--bg-2)", border: "1px solid var(--line)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 12.5, color: "var(--fg-3)", lineHeight: 1.6}}>
        <div style={{color: "var(--fg)", fontWeight: 500, marginBottom: 8}}>not installable from dashboard</div>
        <div>Dashboard install is only available for the FLM / NPU backend. For other backends, use:</div>
        <pre style={{margin: "10px 0 0", padding: 10, background: "#070707", borderRadius: "var(--rad-sm)", color: "var(--fg-2)", fontSize: 11}}>hal0 backend install {backend.name || backend.id}</pre>
      </div>
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

# 4. Reboot or re-login, then restart the NPU slot
hal0 slot restart npu`;
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
function PersonaEditModal({ open, onClose, personaId }) {
  // "hermes" is the only agent with a persona surface in v0.3 (single-tenant).
  const AGENT_ID = "hermes";
  const personaUpdate = usePersonaUpdate(AGENT_ID);

  // Edits the persona named by `personaId`; without one, the active persona.
  const personas = useAgentPersonas(personaId ? null : AGENT_ID);
  const pid = personaId || personas.data?.active || null;
  const personaQ = useAgentPersona(AGENT_ID, pid, { enabled: open && !!pid });
  const persona = personaQ.data;

  // The form edits exactly the fields PUT /api/agents/{id}/personas/{pid}
  // acts on; every other field of the body is ignored server-side.
  const [displayName, setDisplayName] = useStateFM("");
  const [systemPrompt, setSystemPrompt] = useStateFM("");
  const [toolPatterns, setToolPatterns] = useStateFM("");
  const [slot, setSlot] = useStateFM("");

  useEffectFM(() => {
    if (open && persona) {
      setDisplayName(persona.display_name || "");
      setSystemPrompt(persona.system_prompt || "");
      setToolPatterns((persona.tools_allowed || []).join("\n"));
      setSlot(persona.preferred_model || "");
    }
  }, [open, persona]);

  // tools_allowed is a list of fnmatch patterns matched against the admin MCP
  // tool names (ToolPolicy.allows, hal0/mcp/admin.py) — not a pick-list, so it
  // is edited as text. Blank lines drop out, and an empty result posts ["*"]
  // so the stored list says what it means rather than relying on ToolPolicy
  // reading an empty list as "*".
  const toolsAllowed = () => {
    const patterns = toolPatterns.split("\n").map(l => l.trim()).filter(Boolean);
    return patterns.length ? patterns : ["*"];
  };

  // Routes to the *live* configured slots (GET /api/slots), not the static
  // HAL0_DATA mock — the mock carried leftover dev-test slots (coder,
  // warming-demo) that leaked into this picker on the real dashboard.
  const slotsQ = useSlots();
  const llmSlots = (slotsQ.data || []).filter(s => s.type === "llm");

  // Why the form isn't showing yet. A missing persona (404) is surfaced, not
  // papered over with an empty form that would save over it.
  const unavailable = persona ? null
    : personaQ.isError ? `Couldn't load persona — ${personaQ.error?.message || "request failed"}`
    : (personaQ.isLoading || personas.isLoading) ? "Loading persona…"
    : "No persona to edit.";

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Agent · persona"
      title={`Edit ${persona?.display_name || pid || "persona"}`}
      width={680}
      foot={
        <>
          <span>Personas route to a chat slot and carry their own system prompt + tool set.</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose} disabled={personaUpdate.isPending}>Cancel</button>
            <button
              className="btn sm"
              disabled={personaUpdate.isPending || !persona || !displayName.trim()}
              onClick={async () => {
                try {
                  await personaUpdate.mutateAsync({
                    pid,
                    body: {
                      display_name: displayName.trim(),
                      system_prompt: systemPrompt,
                      tools_allowed: toolsAllowed(),
                      preferred_model: slot,
                    },
                  });
                  window.__hal0Toast && window.__hal0Toast(`Persona saved`, "ok");
                  onClose();
                } catch (e) {
                  window.__hal0Toast && window.__hal0Toast(
                    `Save failed — ${e?.message || "endpoint not yet available"}`,
                    "err"
                  );
                }
              }}
            >{personaUpdate.isPending ? "Saving…" : "Save"}</button>
          </span>
        </>
      }
    >
      {unavailable ? (
        <div className="mono" style={{fontSize: 12, color: personaQ.isError ? "var(--err, #c66)" : "var(--fg-4)"}}>{unavailable}</div>
      ) : (
        <>
          <div className="form-row">
            <div className="form-lbl">
              <span>Display name</span>
              <FieldInfoIcon description="shown in the persona list · the id never changes" />
            </div>
            <div className="form-ctl">
              <input className="input mono" value={displayName} onChange={e => setDisplayName(e.target.value)} placeholder="Hermes coder" />
            </div>
          </div>

          <div className="form-row">
            <div className="form-lbl">
              <span>Routes to slot</span>
              <FieldInfoIcon description="only llm-type slots are eligible" />
            </div>
            <div className="form-ctl">
              <select className="input mono" value={slot} onChange={e => setSlot(e.target.value)}>
                {llmSlots.map(s => (
                  <option key={s.name} value={s.name}>{s.name} · {s.model} · {s.device}</option>
                ))}
                {!llmSlots.some(s => s.name === slot) && (
                  <option value={slot}>{slot || "— not set —"}</option>
                )}
              </select>
            </div>
          </div>

          <div className="form-row">
            <div className="form-lbl">
              <span>System prompt</span>
              <FieldInfoIcon description="prepended on every request to this persona" />
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
              <span>Allowed tool patterns</span>
              <FieldInfoIcon description="fnmatch patterns over MCP tool names · a tool outside every pattern is hidden from this persona" />
            </div>
            <div className="form-ctl">
              <textarea
                className="input mono"
                value={toolPatterns}
                onChange={e => setToolPatterns(e.target.value)}
                rows={4}
                spellCheck={false}
                placeholder="*"
                style={{resize: "vertical", minHeight: 72}}
              />
              <div className="hint">One pattern per line, matched against MCP tool names; <span className="mono">*</span> allows every tool.</div>
            </div>
          </div>
        </>
      )}
    </Modal>
  );
}

Object.assign(window, { SkipBundleDialog, BackendInstallModal, FlmDebGuideModal, PersonaEditModal });
