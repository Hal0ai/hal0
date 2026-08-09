// NPU anchor (FLM trio) — advanced panel, collapsed by default. Ported from
// NpuPage. One FLM process serves chat + embed + ASR on the XDNA2 NPU:
//   [model].context_size → HAL0_FLM_CTX → --ctx-len
//   [npu].embed → HAL0_FLM_LOAD_EMBED → --embed 1
//   [npu].asr   → HAL0_FLM_LOAD_ASR   → --asr 1
// All service-restart (slot bounce). The old hardcoded amber chip is now a
// real ApplyBadge (closes the NpuPage TODO / spec Risk #2 anti-pattern) —
// slot.model.context_size / slot.npu.* are classified in RELOAD_CLASS_FALLBACK.
// The [npu].asr/.embed booleans are also written by the NPU pane pills and
// the slot drawer; this panel is the settings-side writer of the same keys.
import { useState, useEffect } from 'react'
import { useSlots, useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import { useNpuOccupancy } from '@/api/hooks/useNpuOccupancy'
import { SRow } from '../../shared/SRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { PanelHeader, PanelFooter, inputStyle } from './shared.jsx'

const DEF_CTX = "16384"

export function NpuAnchorPanel({ registry }) {
  const [open, setOpen] = useState(false)
  const slotsQuery = useSlots()
  const editSlot = useSlotEdit()
  const occQuery = useNpuOccupancy()

  const npuSlots = (slotsQuery.data || []).filter(s => s.device === "npu")
  const npuName = npuSlots.length > 0 ? npuSlots[0].name : null
  const cfgQuery = useSlotConfig(npuName)
  const cfg = cfgQuery.data || {}
  const liveCtx = cfg.model?.context_size
  const liveNpu = cfg.npu || {}

  const origCtx = liveCtx != null ? String(liveCtx) : DEF_CTX
  const origAsr = !!liveNpu.asr
  const origEmbed = !!liveNpu.embed

  const [ctx, setCtx] = useState(DEF_CTX)
  const [asr, setAsr] = useState(false)
  const [embed, setEmbed] = useState(false)
  useEffect(() => {
    setCtx(liveCtx != null ? String(liveCtx) : DEF_CTX)
    setAsr(!!liveNpu.asr)
    setEmbed(!!liveNpu.embed)
  }, [cfgQuery.data])

  const ctxNum = parseInt(ctx, 10)
  const ctxValid = /^\d+$/.test(ctx.trim()) && ctxNum >= 512
  const dirty = !!npuName && (ctx !== origCtx || asr !== origAsr || embed !== origEmbed)

  const doSave = async () => {
    if (!npuName || !ctxValid) return
    try {
      await editSlot.mutateAsync({ name: npuName, body: { model: { context_size: ctxNum }, npu: { asr, embed } } })
      window.__hal0Toast && window.__hal0Toast("NPU settings saved — restart the slot to apply", "warn")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  const occ = occQuery.data

  return (
    <div className="s-panel">
      <PanelHeader
        title="NPU anchor (FLM trio)"
        info={npuName ? `${npuName} · device=npu · one FLM process multiplexes chat + embed + ASR` : "advanced · FastFlowLM on the AMD XDNA2 NPU"}
        chip={npuName
          ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{npuName}</span>
          : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>no NPU slot</span>}
        onToggle={() => setOpen(o => !o)} open={open}
      />
      {open && !npuName && !slotsQuery.isPending && (
        <div className="s-row" style={{padding: "6px 12px"}}>
          <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
            No NPU slot configured. Create a slot with device npu in the Slots view (or run hal0 setup with NPU opt-in) to tune FLM here.
          </span>
        </div>
      )}
      {open && npuName && (
        <>
          <SRow k="Context size" sub="FLM --ctx-len (tokens) · larger = more KV cache on the NPU"
            actions={<ApplyBadge settingsKey="slot.model.context_size" registry={registry} />} v={
            <input type="number" min={512} step={512} value={ctx}
              onChange={e => setCtx(e.target.value)} placeholder={DEF_CTX}
              className="mono" style={{...inputStyle(120), borderColor: ctxValid || !ctx ? "var(--line)" : "var(--err)"}} />
          } />
          <SRow k="Load embeddings" sub="Serve /v1/embeddings from the FLM trio (--embed 1) · mirrors device=npu on the Embeddings selection"
            actions={<ApplyBadge settingsKey="slot.npu.embed" registry={registry} />} v={
            <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
              <input type="checkbox" checked={embed} onChange={e => setEmbed(e.target.checked)} style={{accentColor: "var(--accent)"}} />
              <span>{embed ? "enabled" : "disabled"}</span>
            </label>
          } />
          <SRow k="Load ASR" sub="Serve /v1/audio/transcriptions from the FLM trio (--asr 1) · mirrors device=npu on the STT selection"
            actions={<ApplyBadge settingsKey="slot.npu.asr" registry={registry} />} v={
            <label className="mono" style={{display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer", color: "var(--fg-2)"}}>
              <input type="checkbox" checked={asr} onChange={e => setAsr(e.target.checked)} style={{accentColor: "var(--accent)"}} />
              <span>{asr ? "enabled" : "disabled"}</span>
            </label>
          } />
          {occ?.present && (
            <>
              <SRow k="Occupancy" mono v={
                <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: occ.cols_used > 0 ? "var(--ok)" : "var(--fg-4)", borderColor: occ.cols_used > 0 ? "var(--ok)" : "var(--line)"}}>
                  {occ.cols_used}/{occ.cols_total} cols
                </span>
              } />
              <SRow k="Peak" mono v={`${occ.tops_peak} TOPS · ${occ.tiles} tiles (${occ.rows}×${occ.cols})`} />
              {(occ.slots || []).map(s => (
                <SRow key={s.name} k={s.name} sub={s.model || "—"} mono
                  v={<>
                    <span style={{color: s.state === "serving" || s.state === "ready" ? "var(--ok)" : "var(--fg-4)"}}>{s.state}</span>
                    <span style={{color: "var(--fg-4)"}}> · {s.cols?.length || 0} cols{s.gb != null ? ` · ${s.gb} GB` : ""}</span>
                  </>} />
              ))}
            </>
          )}
          <PanelFooter dirty={dirty} onReset={() => { setCtx(origCtx); setAsr(origAsr); setEmbed(origEmbed) }}
            onSave={doSave} disabled={!dirty || !ctxValid || editSlot.isPending}
            saving={editSlot.isPending} label="Save NPU settings" />
        </>
      )}
    </div>
  )
}
