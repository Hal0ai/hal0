// ROUTING ▸ Agents / Brain — the dashboard steward chat ([brain_chat]).
// Extracted verbatim from settings.jsx AgentsBrainSection (P3-ui split
// phase 1). Filed under ROUTING (closest fit among the target IA groups —
// this is the only existing agent-policy surface in settings.jsx; the
// spec's Mode&Fallback/providers/agent-profiles ROUTING content is still
// G[§21 D1/§4] MISSING).
//
// Wires the [brain_chat] config (schema.BrainChatConfig) into the UI: the hard
// kill switch, read-only guardrail, target-slot override, and loop knobs. Every
// key applies live (board_chat._brain_chat_config reads app.state per turn), so
// the ApplyBadge shows "live". Saves via the generic PUT /api/settings.
import { useState, useEffect } from 'react'
import { useSettings, useSettingsUpdate, useApplyPlan } from '@/api/hooks/useSettings'
import { useSlots } from '@/api/hooks/useSlots'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'
import { _advInputStyle } from '../../shared/SchemaRow.jsx'

export function AgentsBrainPage() {
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const settings = useSettings();
  const update = useSettingsUpdate();
  const slotsQuery = useSlots();

  const live = settings.data?.brain_chat || {};
  const liveEnabled = live.enabled !== false;
  const liveReadOnly = live.read_only === true;
  const liveModel = live.model || "";
  const liveRounds = live.max_rounds ?? 8;
  const liveTimeout = live.completion_timeout_s ?? 300;

  const [enabled, setEnabled] = useState(liveEnabled);
  const [readOnly, setReadOnly] = useState(liveReadOnly);
  const [model, setModel] = useState(liveModel);
  const [rounds, setRounds] = useState(String(liveRounds));
  const [timeoutS, setTimeoutS] = useState(String(liveTimeout));

  useEffect(() => { setEnabled(liveEnabled); }, [liveEnabled]);
  useEffect(() => { setReadOnly(liveReadOnly); }, [liveReadOnly]);
  useEffect(() => { setModel(liveModel); }, [liveModel]);
  useEffect(() => { setRounds(String(liveRounds)); }, [liveRounds]);
  useEffect(() => { setTimeoutS(String(liveTimeout)); }, [liveTimeout]);

  // Slot picker: "" = persona default (hal0/brain), plus hal0/<slot> for every
  // configured slot so the operator can target e.g. the NPU chat slot.
  const slotModels = (slotsQuery.data || []).map(s => `hal0/${s.name}`);
  // Keep a currently-set custom value visible even if its slot isn't listed.
  const modelOptions = ["", ...new Set([...slotModels, ...(liveModel ? [liveModel] : [])])];

  const roundsNum = Number(rounds);
  const timeoutNum = Number(timeoutS);
  const roundsValid = Number.isInteger(roundsNum) && roundsNum >= 1 && roundsNum <= 100;
  const timeoutValid = timeoutNum > 0;
  const dirty =
    enabled !== liveEnabled || readOnly !== liveReadOnly || model !== liveModel ||
    roundsNum !== liveRounds || timeoutNum !== liveTimeout;

  const doSave = async () => {
    if (!roundsValid || !timeoutValid) {
      window.__hal0Toast && window.__hal0Toast("Fix the highlighted fields first", "err");
      return;
    }
    try {
      await update.mutateAsync({
        brain_chat: {
          enabled, read_only: readOnly, model,
          max_rounds: roundsNum, completion_timeout_s: timeoutNum,
        },
      });
      window.__hal0Toast && window.__hal0Toast("Brain chat settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const reset = () => {
    setEnabled(liveEnabled); setReadOnly(liveReadOnly); setModel(liveModel);
    setRounds(String(liveRounds)); setTimeoutS(String(liveTimeout));
  };

  return (
    <div className="s-section">
      <h2>Agents / Brain</h2>
      <p className="desc">
        The dashboard's agent-chat steward (<span className="mono">hal0-brain</span>): the
        slide-out chat that administers this instance via tools. These guardrails hold
        server-side, independent of the persona's tool allowlist, and apply live.
      </p>

      <div className="s-panel" style={{marginBottom: 12}}>
        <SRow
          k="Enabled"
          sub="Master switch. When off, the chat refuses every turn (no model call)."
          v={
            <input type="checkbox" checked={enabled}
              onChange={e => setEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
          }
          actions={<ApplyBadge settingsKey="brain_chat.enabled" registry={registry} />}
        />
        <SRow
          k="Read-only"
          sub="Reads still answer, but every mutating / admin-write tool is refused."
          v={
            <input type="checkbox" checked={readOnly}
              onChange={e => setReadOnly(e.target.checked)} style={{accentColor: "var(--accent)"}} />
          }
          actions={<ApplyBadge settingsKey="brain_chat.read_only" registry={registry} />}
        />
        <SRow
          k="Model / slot override"
          sub="Which slot the steward drives. Empty = persona default (hal0/brain → agent)."
          v={
            <select value={model} onChange={e => setModel(e.target.value)} style={_advInputStyle}>
              {modelOptions.map(m => (
                <option key={m || "__default"} value={m}>
                  {m === "" ? "Persona default (hal0/brain)" : m}
                </option>
              ))}
            </select>
          }
          actions={<ApplyBadge settingsKey="brain_chat.model" registry={registry} />}
        />
        <SRow
          k="Max rounds"
          sub="Per-turn tool-loop cap (1–100). Runaway backstop."
          v={
            <input type="number" min={1} max={100} value={rounds}
              onChange={e => setRounds(e.target.value)}
              style={{..._advInputStyle, width: 80, borderColor: roundsValid ? "var(--line)" : "var(--err)"}} />
          }
          actions={<ApplyBadge settingsKey="brain_chat.max_rounds" registry={registry} />}
        />
        <SRow
          k="Completion timeout (s)"
          sub="Transport timeout for each LLM round against the target slot."
          v={
            <input type="number" min={1} step={5} value={timeoutS}
              onChange={e => setTimeoutS(e.target.value)}
              style={{..._advInputStyle, width: 80, borderColor: timeoutValid ? "var(--line)" : "var(--err)"}} />
          }
          actions={<ApplyBadge settingsKey="brain_chat.completion_timeout_s" registry={registry} />}
        />
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {dirty && <button className="btn ghost sm" onClick={reset}>Reset</button>}
          <button className="btn sm" disabled={!dirty || update.isPending} onClick={doSave}>
            {update.isPending ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
