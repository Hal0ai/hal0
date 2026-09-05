// INTEGRATIONS ▸ Realtime — the OpenAI-Realtime WebSocket surface ([realtime]).
//
// hal0 has shipped a WS /v1/realtime voice endpoint (schema.py's
// RealtimeConfig) since HP-realtime inc-1, but every one of its 13 knobs —
// including the enabled kill switch — had no dashboard page at all; the
// only way to see or change them was `hal0 config edit`. This is the exact
// "settings fields exist only for hand-picked keys" gap the schema-driven
// renderer closes: every row here comes from GET /api/settings/schema (via
// AdvRow / useSettingsForm, the same machinery AdvancedPage uses), grouped
// by the operator's own questions rather than the TOML's flat key order.
import { Fragment } from 'react'
import { useSettingsClient } from '../../data/settingsClient.js'
import { useSettingsForm } from '../../data/useSettingsForm.js'
import { ConfirmDialog } from '../../../primitives.jsx'
import { AdvRow, _getIn } from '../../shared/SchemaRow.jsx'

const REALTIME_GROUPS = [
  { title: "Core", sub: "Kill switch, sample rate, and which slots serve STT/TTS", keys: [
    "realtime.enabled", "realtime.sample_rate", "realtime.default_model",
    "realtime.stt_model", "realtime.tts_model", "realtime.tts_voice",
  ]},
  { title: "Voice detection", sub: "Zero-dependency energy-RMS turn detector", keys: [
    "realtime.vad_energy_threshold", "realtime.vad_silence_ms",
    "realtime.vad_min_speech_ms", "realtime.vad_window_ms",
  ]},
  { title: "Turn behavior", sub: "Output framing and how long a gated tool may leave the session silent", keys: [
    "realtime.frame_ms", "realtime.approval_wait_s", "realtime.max_buffer_seconds",
  ]},
];

export function RealtimePage() {
  const client = useSettingsClient({ schema: true });
  const { settings, update, schema: schemaQuery, registry } = client;
  const live = settings.data || null;

  const allKeys = REALTIME_GROUPS.flatMap(g => g.keys);
  const form = useSettingsForm(client, allKeys);
  const { buf, fields, dirtyKeys, invalidKeys, canSave, confirmKeys } = form;
  const onChange = form.set;

  const doSave = async () => {
    try {
      const { needsRestart } = await form.commit();
      window.__hal0Toast && window.__hal0Toast(
        needsRestart ? "Saved — restart to apply the marked changes" : "Realtime settings saved",
        needsRestart ? "warn" : "ok",
      );
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const onSaveClick = async () => {
    const manual = dirtyKeys.filter(k => client.reloadClass(k)?.apply_class === "manual-restart");
    if (manual.length > 0) { form.submit(); return; }
    doSave();
  };

  const loading = settings.isPending || schemaQuery.isPending;

  return (
    <div className="s-section">
      <h2>Realtime</h2>
      <p className="desc">
        The <span className="mono">WS /v1/realtime</span> voice endpoint: which local slots serve
        speech-to-text and text-to-speech, the turn-detection thresholds, and the output framing.
        Every key here is read live on each connection, so changes apply immediately.
      </p>

      {loading && <div style={{padding: 16, color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>Loading config schema…</div>}
      {(settings.isError || schemaQuery.isError) && (
        <div className="err">{settings.error?.message || schemaQuery.error?.message || "Failed to load settings"}</div>
      )}

      {!loading && !settings.isError && !schemaQuery.isError && (
        <>
          {REALTIME_GROUPS.map(g => (
            <Fragment key={g.title}>
              <div className="s-panel" style={{marginBottom: 12}}>
                <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
                  <div className="k"><span>{g.title}</span><FieldInfoIcon description={g.sub} /></div>
                </div>
                {g.keys.map(k => (
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
              </div>
            </Fragment>
          ))}

          <div style={{marginTop: 2, marginBottom: 18, display: "flex", justifyContent: "space-between", alignItems: "center"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
              Stored at <span style={{color: "var(--fg-3)"}}>/etc/hal0/hal0.toml</span> · [realtime]
              {dirtyKeys.length > 0 && (
                <span style={{marginLeft: 8, color: invalidKeys.length ? "var(--err)" : "var(--warn)"}}>
                  · {invalidKeys.length ? `${invalidKeys.length} invalid value${invalidKeys.length === 1 ? "" : "s"}` : `${dirtyKeys.length} unsaved change${dirtyKeys.length === 1 ? "" : "s"}`}
                </span>
              )}
            </span>
            <div style={{display: "inline-flex", gap: 8}}>
              <button className="btn ghost sm" disabled={dirtyKeys.length === 0 || update.isPending} onClick={form.reset}>Reset</button>
              <button className="btn" disabled={!canSave} onClick={onSaveClick}>{update.isPending ? "Saving…" : "Save changes"}</button>
            </div>
          </div>

          <ConfirmDialog
            open={!!confirmKeys}
            onCancel={form.clearConfirm}
            onConfirm={() => { form.clearConfirm(); doSave(); }}
            title="Manual restart required"
            message={
              <span>
                {confirmKeys && confirmKeys.length === 1
                  ? <>The setting <b className="mono">{confirmKeys[0]}</b> requires</>
                  : <>These settings ({confirmKeys && confirmKeys.map(k => <b className="mono" key={k}>{k} </b>)}) require</>}{" "}
                a <b>manual operator restart</b> to take effect. Values are persisted now.
              </span>
            }
            confirmLabel="Save anyway"
          />
        </>
      )}
    </div>
  );
}
