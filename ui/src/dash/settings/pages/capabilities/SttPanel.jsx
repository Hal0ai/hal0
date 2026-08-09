// STT — voice.stt capability slot. Ported from VoicePage (top panel).
// Deliberately absent: STT silence thresholds (no such endpoint param).
// Language support is engine-specific — copy keyed on the resolved provider
// (moonshine is English-only), never stated as blanket truth (#1470).
import { SRow } from '../../shared/SRow.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow } from './shared.jsx'

export function SttPanel() {
  const sel = useCapabilitySelection('voice', 'stt')
  const p = sel.resolvedProvider

  const doSave = async () => {
    try {
      await sel.save()
      window.__hal0Toast && window.__hal0Toast("STT settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`STT save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  return (
    <div className="s-panel">
      <PanelHeader title="STT" info="speech-to-text · voice.stt slot" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. moonshine-base)"
        emptyHint="no installed STT models — install one in the Models view" />
      <SRow k="Language" sub={
          p === "moonshine"
            ? "moonshine is English-only; the /v1/audio/transcriptions language param is accepted but ignored"
            : p
              ? `${p} — language support is engine-specific; check its docs before relying on the language param`
              : "select an STT model to see its language support"
        } mono v={<span style={{color: "var(--fg-4)"}}>{p === "moonshine" ? "English" : "engine-dependent"}</span>} />
      <SRow k="NPU mode" sub="device npu serves whisper from the FLM trio ([npu].asr on the anchor slot) — tune it in the NPU anchor panel below" mono
        v={<span style={{color: "var(--fg-4)"}}>{sel.selection.device === "npu" ? "FLM trio" : "—"}</span>} />
      <PanelFooter dirty={sel.dirty} onReset={sel.reset} onSave={doSave}
        disabled={!sel.dirty || sel.loading || sel.errored || sel.applyCapability.isPending}
        saving={sel.applyCapability.isPending} label="Save STT" />
    </div>
  )
}
