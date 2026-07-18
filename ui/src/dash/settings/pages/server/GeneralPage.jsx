// SERVER ▸ General — platform identity + privacy (telemetry opt-in).
// Extracted verbatim from settings.jsx GeneralSection (P3-ui split phase 1).
import { useState, useEffect } from 'react'
import { useSettings, useSettingsUpdate, useApplyPlan } from '@/api/hooks/useSettings'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'

export function GeneralPage() {
  const settings = useSettings();
  const update = useSettingsUpdate();
  const applyPlanQuery = useApplyPlan();
  const registry = applyPlanQuery.data?.registry || {};
  const liveTelemetry = settings.data?.telemetry;

  const [telemetry, setTelemetry] = useState(false);
  useEffect(() => {
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
