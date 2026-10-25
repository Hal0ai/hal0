// Image generation — img.img capability slot (ComfyUI engine) + [image]
// generation defaults on the img slot TOML (#599 ImageGenConfig). Ported from
// ImageGenPage. default_size/default_steps are per-request fallbacks
// (immediate) — badges come from reloadClass.js fallback rows. Image mode has
// no setting: it ends when generation stops (ADR-0038).
// Workflows, queue, and inventory live on the ComfyUI pane, not here.
import { useState, useEffect } from 'react'
import { useSlots, useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import { useComfyuiModels } from '@/api/hooks/useComfyui'
import { SIDE_RANGE, snapDim } from '@/dash/image/generate-pure'
import { parseSize } from '@/dash/image/image-models-pure'
import { SRow } from '../../shared/SRow.jsx'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { useCapabilitySelection } from './useCapabilitySelection.js'
import { statusChip, PanelHeader, PanelFooter, EnabledRow, ModelRow, selStyle, inputStyle } from './shared.jsx'

// Empty = the model's native size (SD 1.5 / SDXL Turbo are 512x512). Writing a
// literal size back on any save would pin every model to it.
const DEF_SIZE = ""
const DEF_STEPS = "0"

// The sizes the select lists besides "Model native" (the sizes the catalog's models render at).
// Anything else the slot holds opens as "Custom…" with that size in the field.
const SIZE_PRESETS = ["512x512", "768x768", "1024x1024", "1328x1328"]
const CUSTOM = "custom"

// A custom size is saved exactly as typed, so it must be a size the server renders as is: a
// lower-case WxH with each side 64..2048 and a multiple of 8 (the server clamps and floors
// anything else, which would leave the field naming a size nothing renders).
function validCustomSize(text) {
  const p = parseSize(text)
  return !!p && `${p.width}x${p.height}` === text && snapDim(p.width) === p.width && snapDim(p.height) === p.height
}

// The img catalog is empty only on a host with no ComfyUI engine (no ROCm lane): any
// other host lists every catalog model, marked when its weights are not on disk. So an
// empty catalog is not something installing a model or typing an id can fix — the
// server refuses an id it does not advertise — and the row says why, read-only. Its own
// component so the engine query runs only in that state.
function NoEngineRow() {
  const reason = useComfyuiModels().data?.engine?.reason
  return (
    <SRow k="Model" v={
      <span data-testid="img-model-no-engine" className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
        {reason ? `No ComfyUI image engine: ${reason}` : "No ComfyUI image engine on this host"}
      </span>
    } />
  )
}

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

  const [size, setSize] = useState(DEF_SIZE)
  const [steps, setSteps] = useState(DEF_STEPS)
  // "Custom…" picked from the select. A saved size outside the list is custom too, without a pick.
  const [customPicked, setCustomPicked] = useState(false)

  useEffect(() => {
    const img = imgCfgQuery.data?.image || {}
    setSize(img.default_size != null ? String(img.default_size) : DEF_SIZE)
    setSteps(img.default_steps != null ? String(img.default_steps) : DEF_STEPS)
    setCustomPicked(false)
  }, [imgCfgQuery.data])

  const customSize = customPicked || (size !== DEF_SIZE && !SIZE_PRESETS.includes(size))
  const sizeText = size.trim()
  // The field only has a say while it is open; an unreadable size blocks the save rather than
  // being written (the server would store it, and the size fit would then fail to read it).
  const sizeInvalid = customSize && !validCustomSize(sizeText)
  const stepsAreModelDefault = (parseInt(steps, 10) || 0) === 0

  const pickSize = (value) => {
    if (value === CUSTOM) { setCustomPicked(true); return }
    setCustomPicked(false)
    setSize(value)
  }

  const defaultsDirty = !!imgSlotName && (size !== origSize || steps !== origSteps)
  const dirty = sel.dirty || defaultsDirty

  const doSave = async () => {
    try {
      await sel.save()
      if (defaultsDirty) {
        // Coerce to ImageGenConfig field types (steps is a non-negative int).
        await editSlot.mutateAsync({ name: imgSlotName, body: { image: {
          default_size: sizeText,
          default_steps: Math.max(0, parseInt(steps, 10) || 0),
        } } })
      }
      window.__hal0Toast && window.__hal0Toast("Image generation settings saved", "ok")
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Image generation save failed — ${e?.message || "see logs"}`, "err")
    }
  }

  const resetAll = () => { sel.reset(); setSize(origSize); setSteps(origSteps); setCustomPicked(false) }

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
      {/* The img selection is what /v1/images defaults and Open WebUI's image model
          follow, and the one place the dashboard picks it: the slot drawer and swap
          popover link here for an image slot. Rows are the API's image picker rows
          (catalog models + the operator's own ckpt/<path> checkpoints). Inventory
          and workflows live on the ComfyUI pane. */}
      {sel.catalogItems.length > 0
        ? <ModelRow items={sel.catalogItems} value={sel.model} onChange={sel.setModel} />
        // Nothing while the capabilities load (the catalog is [] until they do) or when
        // the probe failed: the footer already says so and Save is gated.
        : !sel.loading && !sel.errored && <NoEngineRow />}

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
      <SRow k="Default size" sub="Output resolution · Model native uses the model's own size (SD 1.5 and SDXL Turbo are 512x512)"
        actions={<ApplyBadge settingsKey="slot.image.default_size" registry={registry} />} v={
        <span style={{display: "inline-flex", alignItems: "center", gap: 8, flexWrap: "wrap"}}>
          <select data-testid="img-default-size" value={customSize ? CUSTOM : size} onChange={e => pickSize(e.target.value)}
            disabled={!imgSlotName} style={selStyle}>
            <option value="">Model native</option>
            {SIZE_PRESETS.map(s => <option key={s} value={s}>{s}</option>)}
            <option value={CUSTOM}>Custom…</option>
          </select>
          {customSize && (
            <input data-testid="img-default-size-custom" value={size} onChange={e => setSize(e.target.value)}
              placeholder="WxH" aria-label="Custom default size, width x height" aria-invalid={sizeInvalid}
              disabled={!imgSlotName} className="mono" style={inputStyle(110)} />
          )}
          {sizeInvalid && sizeText !== "" && (
            <span data-testid="img-default-size-error" className="mono" style={{fontSize: 11, color: "var(--err)"}}>
              {SIDE_RANGE.min}–{SIDE_RANGE.max} a side, a multiple of {SIDE_RANGE.multiple} (e.g. 1024x768)
            </span>
          )}
        </span>
      } />
      <SRow k="Default steps" sub="Sampler steps · 0 = use the model-class default"
        actions={<ApplyBadge settingsKey="slot.image.default_steps" registry={registry} />} v={
        <span style={{display: "inline-flex", alignItems: "center", gap: 8}}>
          <input data-testid="img-default-steps" type="number" min={0} value={steps} onChange={e => setSteps(e.target.value)}
            placeholder={DEF_STEPS} disabled={!imgSlotName} className="mono" style={inputStyle(100)} />
          {stepsAreModelDefault && (
            <span data-testid="img-default-steps-hint" className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>model default</span>
          )}
        </span>
      } />
      {!imgSlotName && (
        <div className="s-row" style={{padding: "6px 12px"}}>
          <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>No img slot configured — create one in the Slots view to edit generation defaults.</span>
        </div>
      )}
      <PanelFooter dirty={dirty} onReset={resetAll} onSave={doSave} probe={sel.capsQuery}
        disabled={!dirty || sizeInvalid || sel.loading || sel.errored || sel.applyCapability.isPending || editSlot.isPending}
        saving={sel.applyCapability.isPending || editSlot.isPending} label="Save image generation" />
    </div>
  )
}
