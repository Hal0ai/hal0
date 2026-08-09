// TTS — voice.tts capability slot + request defaults. Ported from VoicePage.
// Model/enabled persist via capability apply; default_voice / default_speed /
// default_response_format persist via PUT /api/slots/tts/config and are
// injected per-request by /v1/audio/speech (immediate — see the
// slot.tts.* rows in reloadClass.js RELOAD_CLASS_FALLBACK).
// Voice picker prefers the live GET /api/slots/tts/voices list; the Kokoro
// seed pack is only the cold-slot fallback when the provider is kokoro (#1470).
import { useState, useEffect } from 'react'
import { useSlotEdit, useSlotConfig, useSlotVoices } from '@/api/hooks/useSlots'
import { SRow } from '../../shared/SRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow, selStyle, inputStyle } from './shared.jsx'

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

export function TtsPanel({ registry }) {
  const sel = useCapabilitySelection('voice', 'tts')
  const ttsSlotCfgQuery = useSlotConfig("tts")
  const ttsVoicesQuery = useSlotVoices("tts")
  const editSlot = useSlotEdit()
  const ttsCfg = ttsSlotCfgQuery.data || {}

  const [voice, setVoice] = useState("")
  const [speed, setSpeed] = useState("")
  const [format, setFormat] = useState("")

  useEffect(() => {
    if (ttsCfg.default_voice != null) setVoice(String(ttsCfg.default_voice))
    if (ttsCfg.default_speed != null) setSpeed(String(ttsCfg.default_speed))
    if (ttsCfg.default_response_format != null) setFormat(String(ttsCfg.default_response_format))
  }, [ttsCfg.default_voice, ttsCfg.default_speed, ttsCfg.default_response_format])

  const origVoice = ttsCfg.default_voice ? String(ttsCfg.default_voice) : ""
  const origSpeed = ttsCfg.default_speed != null ? String(ttsCfg.default_speed) : ""
  const origFormat = ttsCfg.default_response_format ? String(ttsCfg.default_response_format) : ""
  const speedNum = parseFloat(speed)
  const speedValid = speed.trim() === "" || (!isNaN(speedNum) && speedNum >= 0.25 && speedNum <= 4)
  const dirty = sel.dirty || voice !== origVoice || speed !== origSpeed || format !== origFormat

  const isKokoro = sel.resolvedProvider === "kokoro"
  const isQwen3 = sel.resolvedProvider === "qwen3tts"

  const doSave = async () => {
    try {
      await sel.save()
      // Only the changed defaults; empty string clears back to the engine's
      // own default (null on the wire; /v1/audio/speech skips null/empty).
      const patch = {}
      if (voice !== origVoice) patch.default_voice = voice || null
      if (speed !== origSpeed) patch.default_speed = speed.trim() === "" ? null : speedNum
      if (format !== origFormat) patch.default_response_format = format || null
      if (Object.keys(patch).length > 0) {
        await editSlot.mutateAsync({ name: "tts", body: patch })
      }
      window.__hal0Toast && window.__hal0Toast("TTS settings saved — applies to the next /v1/audio/speech request", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`TTS save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  const resetAll = () => { sel.reset(); setVoice(origVoice); setSpeed(origSpeed); setFormat(origFormat) }

  const liveVoices = ttsVoicesQuery.data?.source === "live" ? (ttsVoicesQuery.data.voices || []) : []
  const kokoroish = !sel.model || isKokoro
  const options = liveVoices.length > 0
    ? liveVoices.map(v => {
        const seed = KOKORO_VOICES.find(k => k.id === v)
        return { id: v, label: seed ? seed.label : v }
      })
    : (kokoroish ? KOKORO_VOICES : null)
  const srcNote = liveVoices.length > 0
    ? "voices reported live by the tts slot"
    : (kokoroish ? "bundled voices (Kokoro v1) · slot offline — list is the seed pack" : "model-specific voice id")
  const defaultVoiceLabel = kokoroish
    ? "— use engine default (af_bella) —"
    : isQwen3 ? "— use engine default (Ryan) —" : "— use engine default —"

  return (
    <div className="s-panel">
      <PanelHeader title="TTS" info="text-to-speech · voice.tts slot" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. kokoro-v1)"
        emptyHint="no installed TTS models — install one in the Models view" />
      <SRow k="Default voice" sub={`applied when /v1/audio/speech omits the voice param · ${srcNote}`}
        actions={<ApplyBadge settingsKey="slot.tts.default_voice" registry={registry} />} v={
        options ? (
          <select value={voice} onChange={e => setVoice(e.target.value)} style={selStyle}>
            <option value="">{defaultVoiceLabel}</option>
            {voice && !options.some(o => o.id === voice) && (
              <option value={voice}>{voice} (saved)</option>
            )}
            {options.map(v => <option key={v.id} value={v.id}>{v.label}</option>)}
          </select>
        ) : (
          <input value={voice} onChange={e => setVoice(e.target.value)} placeholder="empty = engine default"
            className="mono" style={inputStyle(220)} />
        )
      } />
      <SRow k="Default speed"
        sub={`applied when the request omits speed · ${(isKokoro || isQwen3) ? "the engine clamps to 0.5–2.0" : "clamp range is engine-specific"} · empty = engine default (1.0)`}
        actions={<ApplyBadge settingsKey="slot.tts.default_speed" registry={registry} />} v={
        <input type="number" min={0.25} max={4} step={0.05} value={speed}
          onChange={e => setSpeed(e.target.value)} placeholder="1.0"
          className="mono" style={{...inputStyle(100), border: `1px solid ${speedValid ? "var(--line)" : "var(--err)"}`}} />
      } />
      <SRow k="Default format" sub="applied when the request omits response_format · empty = engine default (mp3)"
        actions={<ApplyBadge settingsKey="slot.tts.default_response_format" registry={registry} />} v={
        <select value={format} onChange={e => setFormat(e.target.value)} style={selStyle}>
          <option value="">— engine default (mp3) —</option>
          {["mp3", "wav", "opus", "flac", "pcm"].map(f => <option key={f} value={f}>{f}</option>)}
        </select>
      } />
      <SRow k="Sample rate" sub={
          isKokoro ? "fixed by the Kokoro engine — not configurable"
            : isQwen3 ? "set by the loaded Qwen3-TTS model at startup — not configurable"
            : "not configurable — determined by the active engine"
        } mono v={<span style={{color: "var(--fg-4)"}}>{isKokoro ? "24 kHz" : "engine-dependent"}</span>} />
      <PanelFooter dirty={dirty} onReset={resetAll} onSave={doSave}
        disabled={!dirty || !speedValid || sel.loading || sel.errored || sel.applyCapability.isPending || editSlot.isPending}
        saving={sel.applyCapability.isPending || editSlot.isPending} label="Save TTS" />
    </div>
  )
}
