// INFERENCE ▸ Image-gen — img.img capability slot configuration.
// Extracted verbatim from settings.jsx ImageGenSection (P3-ui split phase 1).
//
// Image-gen exposes enable/engine(provider)/model picks for the img.img slot.
// Persisted via POST /api/capabilities/img/img {model, provider, enabled}.
//
// Generation defaults (#599 ImageGenConfig — [image] table on the img slot
// TOML): default_size / default_steps / idle_restore_minutes. These persist
// via PUT /api/slots/{name}/config { image: {...} } through useSlotEdit,
// mirroring how the Voice page writes TTS default_voice. The img slot name
// is discovered from useSlots (type "image" / name "img"); when no img slot
// exists the controls degrade to disabled with a hint.
import { useState, useEffect } from 'react'
import { useCapabilities, useCapabilityApply } from '@/api/hooks/useCapabilities'
import { useSlots, useSlotEdit, useSlotConfig } from '@/api/hooks/useSlots'
import { SRow } from '../../shared/SRow.jsx'

export function ImageGenPage() {
  const capsQuery = useCapabilities();
  const applyCapability = useCapabilityApply();
  const slotsQuery = useSlots();
  const editSlot = useSlotEdit();

  const caps = capsQuery.data;
  const imgCatalogs = caps?.catalogs?.img || {};
  const imgSelections = caps?.selections?.img || {};
  const imgSelection = imgSelections.img || {};

  // Discover the img slot name so the [image] config read/write targets a real
  // slot. Prefer an explicit "img" name, else the first image-type slot.
  const imgSlotName =
    (slotsQuery.data || []).find(s => s.name === "img" || s.type === "image" || s.group === "img")?.name || null;
  const imgCfgQuery = useSlotConfig(imgSlotName);
  const imgCfgImage = (imgCfgQuery.data?.image) || {};

  // Schema defaults (ImageGenConfig): an all-defaults [image] table is elided
  // from the dumped config, so fall back to the same defaults the backend uses.
  const DEF_SIZE = "1024x1024";
  const DEF_STEPS = "0";
  const DEF_IDLE = "60";
  const origSize = imgCfgImage.default_size != null ? String(imgCfgImage.default_size) : DEF_SIZE;
  const origSteps = imgCfgImage.default_steps != null ? String(imgCfgImage.default_steps) : DEF_STEPS;
  const origIdle = imgCfgImage.idle_restore_minutes != null ? String(imgCfgImage.idle_restore_minutes) : DEF_IDLE;

  const [imgModel, setImgModel] = useState("");
  const [imgEnabled, setImgEnabled] = useState(false);
  const [imgProvider, setImgProvider] = useState("");
  const [defaultSize, setDefaultSize] = useState(DEF_SIZE);
  const [defaultSteps, setDefaultSteps] = useState(DEF_STEPS);
  const [idleRestore, setIdleRestore] = useState(DEF_IDLE);

  useEffect(() => {
    if (imgSelection.model != null) setImgModel(imgSelection.model || "");
    if (imgSelection.enabled != null) setImgEnabled(!!imgSelection.enabled);
    if (imgSelection.provider != null) setImgProvider(imgSelection.provider || "");
  }, [imgSelection.model, imgSelection.enabled, imgSelection.provider]);

  useEffect(() => {
    const img = imgCfgQuery.data?.image || {};
    setDefaultSize(img.default_size != null ? String(img.default_size) : DEF_SIZE);
    setDefaultSteps(img.default_steps != null ? String(img.default_steps) : DEF_STEPS);
    setIdleRestore(img.idle_restore_minutes != null ? String(img.idle_restore_minutes) : DEF_IDLE);
  }, [imgCfgQuery.data]);

  const imgDirty = imgModel !== (imgSelection.model || "") || imgEnabled !== !!imgSelection.enabled || imgProvider !== (imgSelection.provider || "");
  const defaultsDirty = !!imgSlotName && (defaultSize !== origSize || defaultSteps !== origSteps || idleRestore !== origIdle);
  const imgCatalogItems = imgCatalogs.img?.items || imgCatalogs.img?.models || [];
  const imgStatus = imgSelection.status || "offline";

  const doSave = async () => {
    try {
      const body = { model: imgModel, enabled: imgEnabled };
      if (imgProvider) body.provider = imgProvider;
      await applyCapability.mutateAsync({ slot: "img", child: "img", body });
      window.__hal0Toast && window.__hal0Toast("Image-gen settings saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Image-gen save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const doSaveDefaults = async () => {
    if (!imgSlotName) return;
    // Coerce to the ImageGenConfig field types before writing. Steps / idle are
    // non-negative ints (schema ge=0); size is a freeform "WxH" string.
    const image = {
      default_size: defaultSize.trim() || DEF_SIZE,
      default_steps: Math.max(0, parseInt(defaultSteps, 10) || 0),
      idle_restore_minutes: Math.max(0, parseInt(idleRestore, 10) || 0),
    };
    try {
      await editSlot.mutateAsync({ name: imgSlotName, body: { image } });
      window.__hal0Toast && window.__hal0Toast("Image-gen defaults saved", "ok");
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
    }
  };

  const resetDefaults = () => {
    setDefaultSize(origSize);
    setDefaultSteps(origSteps);
    setIdleRestore(origIdle);
  };

  const statusChip = (st) => {
    const color = st === "ready" || st === "serving" ? "var(--ok)" : st === "starting" || st === "warming" ? "var(--warn)" : "var(--fg-4)";
    return <span className="chip mono" style={{borderColor: color, color, fontSize: 10, padding: "1px 6px"}}>{st}</span>;
  };

  const loading = capsQuery.isLoading;

  return (
    <div className="s-section">
      <h2>Image-gen</h2>
      <p className="desc">ComfyUI / stable-diffusion image generation slot configuration. Changes persist to the img.img capability slot.</p>

      <div className="s-panel">
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k"><span>Image-gen</span><FieldInfoIcon description="img.img slot · ComfyUI engine" /></div>
          <div className="v">{statusChip(imgStatus)}</div>
        </div>
        <SRow k="Enabled" v={
          <input type="checkbox" checked={imgEnabled} onChange={e => setImgEnabled(e.target.checked)} style={{accentColor: "var(--accent)"}} />
        } />
        <SRow k="Engine" sub="provider for the img slot" v={
          <select value={imgProvider} onChange={e => setImgProvider(e.target.value)}
            style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
            <option value="">— auto —</option>
            <option value="comfyui">comfyui</option>
          </select>
        } />
        <SRow k="Model" v={
          imgCatalogItems.length > 0 ? (
            <select value={imgModel} onChange={e => setImgModel(e.target.value)}
              style={{fontFamily: "var(--jbm)", fontSize: 11, background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px"}}>
              <option value="">— unset —</option>
              {imgCatalogItems.map(m => (
                <option key={m.id || m.model_id || m} value={m.id || m.model_id || m}>{m.id || m.model_id || m}</option>
              ))}
            </select>
          ) : (
            <input value={imgModel} onChange={e => setImgModel(e.target.value)} placeholder="model id (e.g. sdxl-turbo-fp16)"
              className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 260}} />
          )
        } sub={imgCatalogItems.length === 0 ? "no installed image models — install one in the Models view" : undefined} />

        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {imgDirty && (
            <button className="btn ghost sm" onClick={() => {
              setImgModel(imgSelection.model || "");
              setImgEnabled(!!imgSelection.enabled);
              setImgProvider(imgSelection.provider || "");
            }}>Reset</button>
          )}
          <button className="btn sm" disabled={!imgDirty || loading || applyCapability.isPending} onClick={doSave}>Save Image-gen</button>
        </div>
      </div>

      {/* ── Generation defaults (#599 ImageGenConfig — [image] on the img slot) ── */}
      <div className="s-panel" style={{marginTop: 12}}>
        <div className="s-row" style={{paddingBottom: 4, borderBottom: "1px solid var(--line)"}}>
          <div className="k">
            <span>Generation defaults</span>
            <FieldInfoIcon description="img slot config · applied when a /v1/images request omits the param" />
          </div>
          <div className="v">
            {imgSlotName
              ? <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-3)"}}>{imgSlotName}</span>
              : <span className="chip mono" style={{fontSize: 10, padding: "1px 6px", color: "var(--fg-4)"}}>no img slot</span>}
          </div>
        </div>
        <SRow k="Default size" sub="Output resolution as WxH (e.g. 1024x1024)" v={
          <input value={defaultSize} onChange={e => setDefaultSize(e.target.value)} placeholder={DEF_SIZE}
            disabled={!imgSlotName}
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 140}} />
        } />
        <SRow k="Default steps" sub="Sampler steps · 0 = use the model-class default" v={
          <input type="number" min={0} value={defaultSteps} onChange={e => setDefaultSteps(e.target.value)} placeholder={DEF_STEPS}
            disabled={!imgSlotName}
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 100}} />
        } />
        <SRow k="Idle restore" sub="Minutes of img inactivity before the GPU arbiter restores LLM slots · 0 = manual only" v={
          <input type="number" min={0} value={idleRestore} onChange={e => setIdleRestore(e.target.value)} placeholder={DEF_IDLE}
            disabled={!imgSlotName}
            className="mono" style={{background: "var(--bg-2)", color: "var(--fg)", border: "1px solid var(--line)", borderRadius: 4, padding: "3px 6px", fontSize: 11, width: 100}} />
        } />
        {!imgSlotName && (
          <div className="s-row" style={{padding: "6px 12px"}}>
            <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>No img slot configured — create one in the Slots view to edit generation defaults.</span>
          </div>
        )}
        <div style={{display: "flex", justifyContent: "flex-end", gap: 8, padding: "8px 12px 4px"}}>
          {defaultsDirty && (
            <button className="btn ghost sm" onClick={resetDefaults}>Reset</button>
          )}
          <button className="btn sm" disabled={!defaultsDirty || editSlot.isPending} onClick={doSaveDefaults}>
            {editSlot.isPending ? "Saving…" : "Save defaults"}
          </button>
        </div>
      </div>
    </div>
  );
}
