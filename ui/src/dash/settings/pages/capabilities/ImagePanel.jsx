// Image generation — img.img capability slot (ComfyUI engine) + [image]
// generation defaults on the img slot TOML (#599 ImageGenConfig). Ported from
// ImageGenPage. default_size/default_steps are per-request fallbacks
// (immediate); idle_restore_minutes feeds the GpuArbiter at construction
// (service-restart hal0-api) — badges come from reloadClass.js fallback rows.
// Workflows, queue, and inventory live on the ComfyUI pane, not here.
import { useState, useEffect } from 'react'
import { useSlots, useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import { SRow } from '../../shared/SRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow, selStyle, inputStyle } from './shared.jsx'

const DEF_SIZE = "1024x1024"
const DEF_STEPS = "0"
const DEF_IDLE = "60"

export function ImagePanel({ registry }) {
  const sel = useCapabilitySelection('img', 'img', { withProvider: true })
  const slotsQuery = useSlots()
  const editSlot = useSlotEdit()

  // Discover the img slot so the [image] read/write targets a real slot.
  const imgSlotName =
    (slotsQuery.data || []).find(s => s.name === "img" || s.type === "image" || s.group === "img")?.name || null
  const imgCfgQuery = useSlotConfig(imgSlotName)
  const imgCfgImage = (imgCfgQuery.data?.image) || {}

  const origSize = imgCfgImage.default_size != null ? String(imgCfgImage.default_size) : DEF_SIZE
  const origSteps = imgCfgImage.default_steps != null ? String(imgCfgImage.default_steps) : DEF_STEPS
  const origIdle = imgCfgImage.idle_restore_minutes != null ? String(imgCfgImage.idle_restore_minutes) : DEF_IDLE

  const [size, setSize] = useState(DEF_SIZE)
  const [steps, setSteps] = useState(DEF_STEPS)
  const [idle, setIdle] = useState(DEF_IDLE)

  useEffect(() => {
    const img = imgCfgQuery.data?.image || {}
    setSize(img.default_size != null ? String(img.default_size) : DEF_SIZE)
    setSteps(img.default_steps != null ? String(img.default_steps) : DEF_STEPS)
    setIdle(img.idle_restore_minutes != null ? String(img.idle_restore_minutes) : DEF_IDLE)
  }, [imgCfgQuery.data])

  const defaultsDirty = !!imgSlotName && (size !== origSize || steps !== origSteps || idle !== origIdle)
  const dirty = sel.dirty || defaultsDirty

  const doSave = async () => {
    try {
      await sel.save()
      if (defaultsDirty) {
        // Coerce to ImageGenConfig field types (steps/idle non-negative ints).
        await editSlot.mutateAsync({ name: imgSlotName, body: { image: {
          default_size: size.trim() || DEF_SIZE,
          default_steps: Math.max(0, parseInt(steps, 10) || 0),
          idle_restore_minutes: Math.max(0, parseInt(idle, 10) || 0),
        } } })
      }
      window.__hal0Toast && window.__hal0Toast("Image generation settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Image generation save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  const resetAll = () => { sel.reset(); setSize(origSize); setSteps(origSteps); setIdle(origIdle) }

  return (
    <div className="s-panel">
      <PanelHeader title="Image generation" info="img.img slot · ComfyUI engine" chip={statusChip(sel.status)} />
      <EnabledRow enabled={sel.enabled} setEnabled={sel.setEnabled} />
      <SRow k="Engine" sub="provider for the img slot" v={
        <select value={sel.provider} onChange={e => sel.setProvider(e.target.value)} style={selStyle}>
          <option value="">— auto —</option>
          <option value="comfyui">comfyui</option>
        </select>
      } />
      <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel}
        placeholder="model id (e.g. sdxl-turbo-fp16)"
        emptyHint="no capability-tagged image models installed — ComfyUI workflows pick their own checkpoint, so leaving this unset is normal" />

      <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
        <div className="k">
          <span>Generation defaults</span>
          <FieldInfoIcon description="img slot [image] table · applied when a /v1/images request omits the param" />
        </div>
        <div className="v">
          {imgSlotName
            ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{imgSlotName}</span>
            : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>no img slot</span>}
        </div>
      </div>
      <SRow k="Default size" sub="Output resolution as WxH (e.g. 1024x1024)"
        actions={<ApplyBadge settingsKey="slot.image.default_size" registry={registry} />} v={
        <input value={size} onChange={e => setSize(e.target.value)} placeholder={DEF_SIZE}
          disabled={!imgSlotName} className="mono" style={inputStyle(140)} />
      } />
      <SRow k="Default steps" sub="Sampler steps · 0 = use the model-class default"
        actions={<ApplyBadge settingsKey="slot.image.default_steps" registry={registry} />} v={
        <input type="number" min={0} value={steps} onChange={e => setSteps(e.target.value)} placeholder={DEF_STEPS}
          disabled={!imgSlotName} className="mono" style={inputStyle(100)} />
      } />
      <SRow k="Idle restore" sub="Minutes of img inactivity before the GPU arbiter restores LLM slots · 0 = manual only"
        actions={<ApplyBadge settingsKey="slot.image.idle_restore_minutes" registry={registry} />} v={
        <input type="number" min={0} value={idle} onChange={e => setIdle(e.target.value)} placeholder={DEF_IDLE}
          disabled={!imgSlotName} className="mono" style={inputStyle(100)} />
      } />
      {!imgSlotName && (
        <div className="s-row" style={{padding: "6px 12px"}}>
          <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>No img slot configured — create one in the Slots view to edit generation defaults.</span>
        </div>
      )}
      <PanelFooter dirty={dirty} onReset={resetAll} onSave={doSave} probe={sel.capsQuery}
        disabled={!dirty || sel.loading || sel.errored || sel.applyCapability.isPending || editSlot.isPending}
        saving={sel.applyCapability.isPending || editSlot.isPending} label="Save image generation" />
    </div>
  )
}
