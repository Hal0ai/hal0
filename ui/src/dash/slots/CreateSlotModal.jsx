// hal0 dashboard — Create-slot modal (D2, decomposed from slot-modals.jsx).
//
// A slot is now a PURE INSTANCE: (slot_id, name, model, port, state). Creating
// one is "pick a model (it already carries tune/device/runner), name it, done."
// The old profile / image / device fields are gone — device rides the model, so
// reaching for a device here redirects to the model's duplicate-for-device flow
// (teaching the mental model instead of dead-ending). Port is assigned by
// PortAuthority on create and shown read-only.
//
// Backend: POST /api/slots requires only `name`; model/device are optional flat-
// body fields the normalizer folds into the nested TOML. We derive `device` from
// the model (the model's stamped tune is device-flavoured) so the launch stays
// correct without exposing a slot-level device knob.

import { useSlotCreate } from '@/api/hooks/useSlots'
import { useModels } from '@/api/hooks/useModels'
import { localModels, deviceFromModel, deviceHue } from './slot-shared.js'

const { useState: useStateC, useEffect: useEffectC, useMemo: useMemoC } = React;

const NAME_RE = /^[a-z][a-z0-9-]{0,30}$/;

function CreateSlotModal({ open, onClose, defaults = {}, existingSlots = [] }) {
  const createMut = useSlotCreate();
  const modelsQuery = useModels();

  const [name, setName] = useStateC("");
  const [modelId, setModelId] = useStateC("");
  const [submitErr, setSubmitErr] = useStateC(null);

  const models = useMemoC(() => localModels(modelsQuery.data), [modelsQuery.data]);

  useEffectC(() => {
    if (open) {
      setName(defaults.name || "");
      setModelId(defaults.model || "");
      setSubmitErr(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  // Preselect the selected/derived type's default model — but only once the
  // models list has actually loaded (modelsQuery.data can still be pending
  // when `open` flips true, so this can't live in the reset effect above).
  // Models list rows carry `default: bool` (the per-type marker set via
  // POST /api/models/{id}/default), so opening "New slot" for a known type
  // (e.g. the empty-slot-card "Configure" flow, which prefills
  // `defaults.type`) starts on the operator's chosen default instead of a
  // blank picker. Guarded on `!modelId` so it never overrides an explicit
  // `defaults.model` (already applied above) or a model the operator has
  // since picked themselves.
  useEffectC(() => {
    if (!open || defaults.model || modelId) return;
    const targetType = defaults.type || "";
    if (!targetType) return;
    const typeDefault = models.find((m) => m.type === targetType && m.default);
    if (typeDefault) setModelId(typeDefault.id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, models]);

  const selModel = useMemoC(() => models.find((m) => m.id === modelId) || null, [models, modelId]);
  const device = selModel ? deviceFromModel(selModel) : null;
  const hue = device ? deviceHue(device) : null;

  const existing = (existingSlots || []).map((s) => s.name);
  const nameCollision = existing.includes(name);
  const nameInvalid = name && !NAME_RE.test(name);
  const nameError = nameCollision ? "name already in use" : nameInvalid ? "lowercase + dashes only" : null;

  const dirty = !!name || !!modelId;
  const canSave = !!name && !nameError && !!modelId && !createMut.isPending;

  async function onCreate() {
    setSubmitErr(null);
    const body = {
      name,
      type: selModel?.type || "llm",
      runtime: "container",
      model: modelId,
      // Device rides the model — derived, not a slot choice.
      // `default` is deliberately NOT sent: the backend silently defaults the
      // FIRST slot of a type, and re-pointing the default afterwards is the
      // explicit "Set as default" row action on the slot list.
      device: device || "gpu-rocm",
    };
    try {
      await createMut.mutateAsync(body);
      window.__hal0Toast && window.__hal0Toast(`Slot "${name}" created`, "ok");
      onClose();
    } catch (err) {
      setSubmitErr(err?.message || "create failed");
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      dirty={dirty}
      eyebrow="New slot · a pure instance"
      title="Create inference slot"
      width={560}
      confirmDiscard="The new slot has not been created — closing now discards what you entered."
      foot={
        <>
          <span className="mono" style={{ fontSize: 10.5, color: "var(--fg-5)" }}>
            {submitErr
              ? <span style={{ color: "var(--err)" }}>{submitErr}</span>
              : `unit hal0-slot@${name || "<name>"}.service`}
          </span>
          <span style={{ display: "inline-flex", gap: 8 }}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" data-testid="create-slot-submit" onClick={onCreate} disabled={!canSave}>
              {createMut.isPending ? "Creating…" : "Create slot"}
            </button>
          </span>
        </>
      }
    >
      {/* Model — carries the tune, device & runner */}
      <div className="form-row">
        <div className="form-lbl">
          <span>model <span className="req">*</span></span>
          <FieldInfoIcon description="carries the tune, device &amp; runner" />
        </div>
        <div className="form-ctl">
          <select className="input mono" data-testid="create-slot-model" value={modelId} onChange={(e) => setModelId(e.target.value)}>
            <option value="">— select a model</option>
            {models.map((m) => (
              <option key={m.id} value={m.id}>
                {m.longName}{m.size ? ` · ${m.size}` : ""}{m.installed ? " · on disk" : " · will pull"}
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Name — a display label */}
      <div className="form-row">
        <div className="form-lbl">
          <span>name <span className="req">*</span></span>
          <FieldInfoIcon description="a display label — you can change it later" />
        </div>
        <div className="form-ctl">
          <input className="input mono" data-testid="create-slot-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="coder" autoFocus />
          {nameError && <div className="err">{nameError}</div>}
          {!nameError && name && <div className="ok">✓ available</div>}
        </div>
      </div>

      {/* Port — read-only, PortAuthority */}
      <div className="form-row">
        <div className="form-lbl"><span>port</span></div>
        <div className="form-ctl">
          <span className="mono" data-testid="create-slot-port" style={{ fontSize: 12.5, color: "var(--fg-3)", display: "inline-flex", alignItems: "center", gap: 8 }}>
            assigned on create
            <span className="tag" style={{ color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 9, letterSpacing: ".05em", textTransform: "uppercase", padding: "2px 6px", borderRadius: 3, border: "1px solid var(--line)", background: "var(--bg-2)" }}>by PortAuthority</span>
          </span>
        </div>
      </div>

      {/* Device redirect teach — device rides the model */}
      <div data-testid="create-slot-device-redirect" style={{ marginTop: 8, border: "1px solid var(--info-line)", background: "var(--info-soft)", borderRadius: 6, padding: "11px 13px", display: "flex", gap: 10, alignItems: "flex-start" }}>
        <span style={{ color: "var(--info)", fontSize: 13, lineHeight: 1.4 }}>◎</span>
        <div style={{ fontSize: 11.5, lineHeight: 1.5, color: "var(--fg-2)" }}>
          Looking for a <b style={{ color: "var(--fg)" }}>device</b>? It rides the model.
          {device ? (
            <> This slot runs on <span className="mono" style={{ fontSize: 10.5, color: `var(${hue.cssVar})` }}>{hue.label}</span> because the model is stamped that way.</>
          ) : (
            <> Pick a model — its stamped tune sets the device.</>
          )}
          {" "}To run on another device, <a href="#models" onClick={onClose} data-testid="create-slot-duplicate-link">duplicate the model for that device</a>.
        </div>
      </div>

      {/* (The "default for <type>?" checkbox is gone. The first slot of a type
          becomes that type's default on the backend; changing it afterwards is
          the explicit "Set as default" action on the slot's row.) */}
    </Modal>
  );
}

Object.assign(window, { CreateSlotModal });
