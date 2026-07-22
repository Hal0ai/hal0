// INFERENCE ▸ Voice — STT/TTS slot configuration.
// Extracted verbatim from settings.jsx VoiceSection (P3-ui split phase 1).
//
// STT: pick model from capabilities.catalogs.voice.stt — persisted via
//   POST /api/capabilities/voice/stt {model, provider, enabled}.
// TTS: model/enabled via capabilities POST; request defaults
//   (default_voice / default_speed / default_response_format) via
//   PUT /api/slots/{name}/config. /v1/audio/speech injects them at request
//   time when the body omits the param, so saves apply immediately.
//   The voice picker prefers the live list from GET /api/slots/tts/voices
//   (engine /v1/audio/voices proxy) and falls back to the Kokoro seed pack.
//
// Not offered on purpose: STT language hints (moonshine is English-only —
// the request param is ignored), STT silence thresholds (no such endpoint
// param exists), TTS sample rate (fixed 24 kHz container constant).
import { useState, useEffect } from 'react'
import { useCapabilities, useCapabilityApply } from '@/api/hooks/useCapabilities'
import { useSlotEdit, useSlotConfig, useSlotVoices } from '@/api/hooks/useSlots'
import { SRow } from '../../shared/SRow.jsx'

// ─── Kokoro TTS voice list ──────────────────────────────────────────────────
// Remsky Kokoro-FastAPI af_bella default. Full list from kokoro-v1 pack.
// No backend API exposes the voice list — hardcoded against the upstream.
// See: https://github.com/remsky/Kokoro-FastAPI#voices
const KOKORO_VOICES = [
  { id: "af_bella",   label: "Bella (af) — American female, warm" },
  { id: "af_sarah",   label: "Sarah (af) — American female, clear" },
  { id: "af_nicole",  label: "Nicole (af) — American female" },
  { id: "am_adam",    label: "Adam (am) — American male" },
  { id: "am_michael", label: "Michael (am) — American male" },
  { id: "bf_emma",    label: "Emma (bf) — British female" },
  { id: "bf_isabella",label: "Isabella (bf) — British female" },
  { id: "bm_george",  label: "George (bm) — British male" },
  { id: "bm_lewis",   label: "Lewis (bm) — British male" },
];

export function VoicePage() {
  const capsQuery = useCapabilities();
  const applyCapability = useCapabilityApply();
  const ttsSlotCfgQuery = useSlotConfig("tts");
  const ttsVoicesQuery = useSlotVoices("tts");
  const editSlot = useSlotEdit();

  const caps = capsQuery.data;
  const voiceCatalogs = caps?.catalogs?.voice || {};
  const voiceSelections = caps?.selections?.voice || {};

  const sttSelection = voiceSelections.stt || {};
  const ttsSelection = voiceSelections.tts || {};
  const ttsCfg = ttsSlotCfgQuery.data || {};

  // STT local edit state
  const [sttModel, setSttModel] = useState("");
  const [sttEnabled, setSttEnabled] = useState(false);
  // TTS local edit state
  const [ttsModel, setTtsModel] = useState("");
  const [ttsEnabled, setTtsEnabled] = useState(false);
  const [ttsVoice, setTtsVoice] = useState("");
  const [ttsSpeed, setTtsSpeed] = useState("");
  const [ttsFormat, setTtsFormat] = useState("");

  // Populate from live data
  useEffect(() => {
    if (sttSelection.model != null) setSttModel(sttSelection.model || "");
    if (sttSelection.enabled != null) setSttEnabled(!!sttSelection.enabled);
  }, [sttSelection.model, sttSelection.enabled]);

  useEffect(() => {
    if (ttsSelection.model != null) setTtsModel(ttsSelection.model || "");
    if (ttsSelection.enabled != null) setTtsEnabled(!!ttsSelection.enabled);
  }, [ttsSelection.model, ttsSelection.enabled]);

  useEffect(() => {
    const v = ttsCfg.default_voice;
    if (v != null) setTtsVoice(String(v));
    if (ttsCfg.default_speed != null) setTtsSpeed(String(ttsCfg.default_speed));
    if (ttsCfg.default_response_format != null) setTtsFormat(String(ttsCfg.default_response_format));
  }, [ttsCfg.default_voice, ttsCfg.default_speed, ttsCfg.default_response_format]);

  const origVoice = ttsCfg.default_voice ? String(ttsCfg.default_voice) : "";
  const origSpeed = ttsCfg.default_speed != null ? String(ttsCfg.default_speed) : "";
  const origFormat = ttsCfg.default_response_format ? String(ttsCfg.default_response_format) : "";
  const speedNum = parseFloat(ttsSpeed);
  const speedValid = ttsSpeed.trim() === "" || (!isNaN(speedNum) && speedNum >= 0.25 && speedNum <= 4);
  const sttDirty = sttModel !== (sttSelection.model || "") || sttEnabled !== !!sttSelection.enabled;
  const ttsDirty = ttsModel !== (ttsSelection.model || "") || ttsEnabled !== !!ttsSelection.enabled
    || ttsVoice !== origVoice || ttsSpeed !== origSpeed || ttsFormat !== origFormat;

  const sttCatalogItems = voiceCatalogs.stt?.items || voiceCatalogs.stt?.models || [];
  const ttsCatalogItems = voiceCatalogs.tts?.items || voiceCatalogs.tts?.models || [];

  const doSaveStt = async () => {
    try {
      await applyCapability.mutateAsync({ slot: "voice", child: "stt", body: { model: sttModel, enabled: sttEnabled } });
      window.__hal0Toast && window.__hal0Toast("STT settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`STT save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const doSaveTts = async () => {
    try {
      // Persist model + enabled via capability apply
      await applyCapability.mutateAsync({ slot: "voice", child: "tts", body: { model: ttsModel, enabled: ttsEnabled } });
      // Persist request defaults via slot config — only the changed fields.
      // Empty string intentionally clears a default back to the engine's own
      // (null on the wire; /v1/audio/speech skips null/empty on injection).
      const patch = {};
      if (ttsVoice !== origVoice) patch.default_voice = ttsVoice || null;
      if (ttsSpeed !== origSpeed) patch.default_speed = ttsSpeed.trim() === "" ? null : speedNum;
      if (ttsFormat !== origFormat) patch.default_response_format = ttsFormat || null;
      if (Object.keys(patch).length > 0) {
        await editSlot.mutateAsync({ name: "tts", body: patch });
      }
      window.__hal0Toast && window.__hal0Toast("TTS settings saved — applies to the next /v1/audio/speech request", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`TTS save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const loading = capsQuery.isLoading;
  const sttStatus = sttSelection.status || "offline";
  const ttsStatus = ttsSelection.status || "offline";

  const statusChip = (st) => {
    const color = st === "ready" || st === "serving" ? "var(--ok)" : st === "starting" || st === "warming" ? "var(--warn)" : "var(--fg-4)";
    return <span className="chip mono" style={{borderColor: color, color, fontSize: 10, padding: "1px 6px"}}>{st}</span>;
  };

  return (
    <div className="s-section">
      <h2>Voice</h2>
      <p className="desc">STT (speech-to-text) and TTS (text-to-speech) slot configuration. Changes persist to the voice.stt and voice.tts capability slots.</p>

      {/* ── STT ── */}
      <div className="s-panel" style={{marginBottom: 12}}>
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>STT</span><FieldInfoIcon description="speech-to-text · voice.stt slot" /></div>
          <div className="v">{statusChip(sttStatus)}</div>
        </div>
        <SRow k="Enabled" v={
          <input type="checkbox" checked={sttEnabled} onChange={e => setSttEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
        } />
        <SRow k="Model" v={
          sttCatalogItems.length > 0 ? (
            <select value={sttModel} onChange={e => setSttModel(e.target.value)}
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
              <option value="">— unset —</option>
              {sttCatalogItems.map(m => (
                <option key={m.id || m.model_id || m} value={m.id || m.model_id || m}>{m.id || m.model_id || m}</option>
              ))}
            </select>
          ) : (
            <input value={sttModel} onChange={e => setSttModel(e.target.value)} placeholder="model id (e.g. moonshine-base)"
              className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 260}} />
          )
        } sub={sttCatalogItems.length === 0 ? "no installed STT models — install one in the Models view" : undefined} />
        <SRow k="Language" sub="moonshine is English-only; the /v1/audio/transcriptions language param is accepted but ignored" mono v={<span style={{color: "var(--fg-4)"}}>English</span>} />
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {sttDirty && (
            <button className="btn ghost sm" onClick={() => { setSttModel(sttSelection.model || ""); setSttEnabled(!!sttSelection.enabled); }}>Reset</button>
          )}
          <button className="btn sm" disabled={!sttDirty || loading || applyCapability.isPending} onClick={doSaveStt}>Save STT</button>
        </div>
      </div>

      {/* ── TTS ── */}
      <div className="s-panel">
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>TTS</span><FieldInfoIcon description="text-to-speech · voice.tts slot" /></div>
          <div className="v">{statusChip(ttsStatus)}</div>
        </div>
        <SRow k="Enabled" v={
          <input type="checkbox" checked={ttsEnabled} onChange={e => setTtsEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
        } />
        <SRow k="Model" v={
          ttsCatalogItems.length > 0 ? (
            <select value={ttsModel} onChange={e => setTtsModel(e.target.value)}
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
              <option value="">— unset —</option>
              {ttsCatalogItems.map(m => (
                <option key={m.id || m.model_id || m} value={m.id || m.model_id || m}>{m.id || m.model_id || m}</option>
              ))}
            </select>
          ) : (
            <input value={ttsModel} onChange={e => setTtsModel(e.target.value)} placeholder="model id (e.g. kokoro-v1)"
              className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 260}} />
          )
        } sub={ttsCatalogItems.length === 0 ? "no installed TTS models — install one in the Models view" : undefined} />
        {/* Voice options come from the live slot when it answers
            (GET /api/slots/tts/voices proxies the engine's /v1/audio/voices);
            the hardcoded Kokoro pack is only the cold-slot fallback for the
            bundled default engine. An explicitly non-Kokoro model with no
            live list gets a free-form voice id input. */}
        {(() => {
          const liveVoices = ttsVoicesQuery.data?.source === "live" ? (ttsVoicesQuery.data.voices || []) : [];
          const kokoroish = !ttsModel || ttsModel.toLowerCase().includes("kokoro");
          const options = liveVoices.length > 0
            ? liveVoices.map(v => {
                const seed = KOKORO_VOICES.find(k => k.id === v);
                return { id: v, label: seed ? seed.label : v };
              })
            : (kokoroish ? KOKORO_VOICES : null);
          const srcNote = liveVoices.length > 0
            ? "voices reported live by the tts slot"
            : (kokoroish ? "bundled voices (Kokoro v1) · slot offline — list is the seed pack" : "model-specific voice id");
          return (
            <SRow k="Default voice" sub={`applied when /v1/audio/speech omits the voice param · ${srcNote}`} v={
              options ? (
                <select value={ttsVoice} onChange={e => setTtsVoice(e.target.value)}
                  style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
                  <option value="">— use engine default (af_bella) —</option>
                  {/* keep a saved voice selectable even if the live list lost it */}
                  {ttsVoice && !options.some(o => o.id === ttsVoice) && (
                    <option value={ttsVoice}>{ttsVoice} (saved)</option>
                  )}
                  {options.map(v => (
                    <option key={v.id} value={v.id}>{v.label}</option>
                  ))}
                </select>
              ) : (
                <input value={ttsVoice} onChange={e => setTtsVoice(e.target.value)}
                  placeholder="empty = engine default"
                  className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 220}} />
              )
            } />
          );
        })()}
        <SRow k="Default speed" sub="applied when the request omits speed · Kokoro clamps to 0.5–2.0 · empty = engine default (1.0)" v={
          <input type="number" min={0.25} max={4} step={0.05} value={ttsSpeed}
            onChange={e => setTtsSpeed(e.target.value)} placeholder="1.0"
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: `1px solid ${speedValid ? "var(--line)" : "var(--err)"}`, borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 100}} />
        } />
        <SRow k="Default format" sub="applied when the request omits response_format · empty = engine default (mp3)" v={
          <select value={ttsFormat} onChange={e => setTtsFormat(e.target.value)}
            style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
            <option value="">— engine default (mp3) —</option>
            {["mp3", "wav", "opus", "flac", "pcm"].map(f => <option key={f} value={f}>{f}</option>)}
          </select>
        } />
        <SRow k="Sample rate" sub="fixed by the Kokoro engine — not configurable" mono v={<span style={{color: "var(--fg-4)"}}>24 kHz</span>} />
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {ttsDirty && (
            <button className="btn ghost sm" onClick={() => {
              setTtsModel(ttsSelection.model || "");
              setTtsEnabled(!!ttsSelection.enabled);
              setTtsVoice(origVoice);
              setTtsSpeed(origSpeed);
              setTtsFormat(origFormat);
            }}>Reset</button>
          )}
          <button className="btn sm" disabled={!ttsDirty || !speedValid || loading || applyCapability.isPending || editSlot.isPending} onClick={doSaveTts}>Save TTS</button>
        </div>
      </div>
    </div>
  );
}
