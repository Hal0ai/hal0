// hal0 dashboard — Slot interactive surface
// Create-slot modal, Edit-slot drawer, inline swap popover, overflow menu,
// empty/error SlotCard variants, log drawer. Wired into slots.jsx via
// window globals. All persistence + lifecycle calls go through the typed
// `useSlots` mutation hooks — no toast-only stubs survive in this file.

import {
  useSlotCreate,
  useSlotEdit,
  useSlotDefaults,
  useSlotDelete,
  useSlotImagePull,
  useSlotRestart,
  useSlotLoad,
  useSlotSwap,
  useSlotResolved,
} from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useModels } from '@/api/hooks/useModels'
import { useProfiles } from '@/api/hooks/useProfiles'
import { useChatTemplates } from '@/api/hooks/useChatTemplates'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { useSlotLogsStream } from '@/api/hooks/useLogs'
import { ENDPOINTS } from '@/api/endpoints'
import { normalizeApiModel, isUpstreamModel, isMtpEligibleModel } from '@/lib/normalizeApiModel'
import { stateChipClassForSlot, slotButtonPhase } from './slot-status.js'

const { useState: useStateSM, useEffect: useEffectSM } = React;

// Map a slot lifecycle state to a chip color class.
//   running healthy/serving → green (ok); starting/pulling → amber (warn);
//   crashed/error → red (err); stopped/anything else → neutral grey.
//
// N1: accepts either a state string or a full slot object; both delegate
// to stateChipClassForSlot() from slot-status.js (the string overload
// wraps it in a minimal slot shape).
function stateChipClass(stateOrSlot) {
  if (typeof stateOrSlot === "string" || stateOrSlot == null) {
    return stateChipClassForSlot({ state: String(stateOrSlot || "") });
  }
  return stateChipClassForSlot(stateOrSlot);
}

// Model rows come from /api/models and are normalized by the SHARED
// lib/normalizeApiModel (imported above), which tolerates both the
// registry/API shape (capabilities + backends + size_bytes + name +
// hf_repo) and the legacy HAL0_DATA seed shape (labels + device + size +
// longName + repo + type — mock.ts fallback / γ-suite race). This file
// used to carry its own divergent copy whose deriveType missed
// tool-calling/vision → 'llm', hiding those models from every slot's
// model picker. NEVER ship HAL0_DATA model ids to the backend — they're
// fictional (`qwen3.6-27b-mtp` etc.) and the slot orchestrator correctly
// rejects them against the real registry.

// One shared compatible-models filter for all three slot surfaces (create
// modal, edit drawer, swap popover). Takes the raw /api/models list, normalizes
// it, and filters to the requested `type`, hiding ROCmFP4-quantized models
// whenever the target backend isn't rocm (those weights only run on the rocm
// fork binary). Previously the create modal filtered on type ALONE and could
// offer rocmfp4 models that the backend then rejects — this closes that gap.
function compatibleModels(models, { type, backend }) {
  // Upstream-advertised rows are excluded outright: a slot binds a local
  // file path, and these rows have none (they'd render as "will pull" and
  // then 422 — there is no HF source to pull them from).
  return (models ?? []).map(normalizeApiModel).filter(m =>
    m.type === type &&
    !isUpstreamModel(m) &&
    !(Array.isArray(m.tags) && m.tags.includes("rocmfp4") && backend !== "rocm")
  );
}

// ─── Create-slot modal ──────────────────────────────────────────
function CreateSlotModal({ open, onClose, defaults = {}, existingSlots = [] }) {
  // Shared form-state hook (useForm): values + touched/submitted + isDirty (the
  // unsaved-changes guard) + a reset that re-derives from `defaults` when the
  // modal (re)opens. Field setters are thin wrappers so the JSX stays intact.
  const f = useForm({
    deriveInitial: () => ({
      name: defaults.name || "",
      type: defaults.type || "llm",
      profile: defaults.profile || "",
      model: "",
      makeDefault: false,
    }),
    resetKey: `${open ? "o" : "c"}:${JSON.stringify(defaults)}`,
  });
  const { name, type, profile, model, makeDefault } = f.values;
  const setName = (val) => f.set("name", val);
  // UI-21: changing Type must clear the selected model — a model compatible
  // with the old type is almost always incompatible with the new one, and the
  // stale id would otherwise ride into the create body. Use setValues so both
  // fields flip in one update (mark type touched to match f.set semantics).
  const setType = (val) => { f.setValues(v => ({ ...v, type: val, model: "" })); f.touch("type"); };
  const setProfile = (val) => f.set("profile", val);
  const setModel = (val) => f.set("model", val);
  const setMakeDefault = (val) => f.set("makeDefault", val);
  const [submitErr, setSubmitErr] = useStateSM(null);
  // UI: dirty-close confirms through the shared ConfirmDialog (state-driven)
  // instead of window.confirm. Every dismiss path (Cancel, ✕, Esc, backdrop)
  // funnels through requestClose below.
  const [discardOpen, setDiscardOpen] = useStateSM(false);

  const createMut = useSlotCreate();
  const hwQuery = useHardware();
  const modelsQuery = useModels();
  const profilesQuery = useProfiles();
  // Slot type vocabulary — meta-driven (GET /api/meta/enums, static fallback
  // when absent) instead of the old hardcoded <option> list.
  const enums = useMetaEnums();

  useEffectSM(() => { if (open) { setSubmitErr(null); setDiscardOpen(false); } }, [open]);

  const requestClose = () => {
    if (f.isDirty) { setDiscardOpen(true); return; }
    onClose();
  };

  // validation — slot collision uses the live slot list passed in from
  // the SlotsView (useSlots data), not HAL0_DATA.
  const existing = (existingSlots || []).map(s => s.name);
  const nameCollision = existing.includes(name);
  const nameInvalid = name && !/^[a-z][a-z0-9-]{0,30}$/.test(name);
  const nameError = nameCollision ? "name already in use" : nameInvalid ? "lowercase + dashes only" : null;

  const allProfiles = profilesQuery.data ?? [];
  // Compatible models: filter by type AND the selected profile's backend so
  // rocmfp4 models are hidden on non-rocm profiles (was type-only — the gap
  // UI-3 closes). Before a profile is picked, backend is undefined → rocmfp4
  // models stay hidden, the safe default.
  const selBackend = allProfiles.find(p => p.name === profile)?.backend;
  const compatible = compatibleModels(modelsQuery.data, { type, backend: selBackend });

  const canSave = !!name && !nameError && !createMut.isPending && !!profile;

  async function onCreateClick() {
    setSubmitErr(null);
    const body = {
      name,
      type,
      runtime: "container",
      profile,
      // Derive device from the selected profile's explicit `backend` field
      // (authoritative ROCm-vs-Vulkan selector) with device_class as the
      // fallback for non-GPU profiles:
      //   backend "vulkan" → "gpu-vulkan"; backend "rocm" → "gpu-rocm"
      //   else by device_class: npu → "npu", cpu → "cpu",
      //                         img → "gpu-rocm" (ComfyUI, ROCm-only for now),
      //                         gpu/other → "gpu-rocm"
      device: (() => {
        const meta = allProfiles.find(p => p.name === profile);
        if (meta?.backend === "vulkan") return "gpu-vulkan";
        if (meta?.backend === "rocm") return "gpu-rocm";
        const dc = meta?.device_class || "gpu";
        if (dc === "npu") return "npu";
        if (dc === "cpu") return "cpu";
        return "gpu-rocm";
      })(),
      ...(model ? { model } : {}),
      ...(makeDefault ? { default: true } : {}),
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
    <>
    <Modal
      open={open}
      onClose={requestClose}
      eyebrow="Slots · new"
      title="Create slot"
      width={640}
      foot={
        <>
          <span>
            {submitErr
              ? <span style={{color: "var(--err)"}}>{submitErr}</span>
              : "capabilities.toml will be written on save."}
          </span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={requestClose}>Cancel</button>
            <button
              className="btn sm"
              onClick={onCreateClick}
              disabled={!canSave}
            >{createMut.isPending ? "Creating…" : "Create slot"}</button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>Name <span className="req">*</span></span>
          <span className="sub">bare · kebab-case · unique across the host</span>
        </div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={name}
            onChange={e => setName(e.target.value)}
            placeholder="coder-large"
            autoFocus
          />
          {nameError && <div className="err">{nameError}</div>}
          {!nameError && name && <div className="ok">✓ available</div>}
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Type <span className="req">*</span></span>
          <span className="sub">drives the model filter + OmniRouter tool</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={type} onChange={e => setType(e.target.value)}>
            {/* keep an out-of-vocab persisted type selectable */}
            {type && !enums.slot_types.includes(type) && <option value={type}>{type}</option>}
            {enums.slot_types.map(t => <option key={t} value={t}>{t}</option>)}
          </select>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Profile <span className="req">*</span></span>
          <span className="sub">image + bench-tuned flags for this slot</span>
        </div>
        <div className="form-ctl">
          <select
            className="input mono"
            value={profile}
            onChange={e => setProfile(e.target.value)}
          >
            <option value="">— select a profile</option>
            {allProfiles.map(p => (
              <option key={p.name} value={p.name}>
                {p.name} · {p.image ? p.image.split(':').pop() : '—'}
              </option>
            ))}
          </select>
          {!profile && <div className="hint" style={{color: "var(--warn)"}}>Profile required for container slots.</div>}
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Model</span>
          <span className="sub">filtered to compatible · {compatible.length} match{compatible.length !== 1 ? "es" : ""}</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={model} onChange={e => setModel(e.target.value)}>
            <option value="">— Select later (slot saves in `empty` state)</option>
            {compatible.map(m => (
              <option key={m.id} value={m.id}>
                {m.longName} · {m.size} {m.installed ? "· on disk" : "· will pull"}
              </option>
            ))}
          </select>
          {model && (() => {
            // UI-6: real size-vs-RAM check, reusing the same parseSizeGB test
            // the InlineSwapPopover uses (data.jsx global). The old branch
            // rendered an unconditional "fits" claim regardless of model size.
            const selM = compatible.find(m => m.id === model);
            if (!selM) return null;
            const ramFreeGb = hwQuery.data?.ram?.free ?? 0;
            const fits = ramFreeGb > parseSizeGB(selM.size);
            return fits
              ? <div className="ok">✓ fits in available memory ({ramFreeGb} GB free)</div>
              : <div className="hint" style={{color: "var(--warn)"}}>⚠ may not fit — {selM.size} model vs {ramFreeGb} GB free</div>;
          })()}
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Port (auto-assigned)</span>
          <span className="sub">child process port hal0 will allocate</span>
        </div>
        <div className="form-ctl">
          {/* The create body intentionally omits `port` — hal0 allocates the
              next free slot port server-side (_next_free_slot_port). Showing a
              client-guessed number here implied a value the POST never sends
              and the backend need not honour, so we state the behaviour
              instead of fabricating a specific port. */}
          <span className="mono" style={{padding: "6px 10px", background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", display: "inline-block", color: "var(--fg-4)", fontSize: 12}}>auto · assigned on save</span>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Default for type {type}?</span>
          <span className="sub">flips `default = true`; demotes the current one</span>
        </div>
        <div className="form-ctl">
          <label className="checkbox-row">
            <input type="checkbox" checked={makeDefault} onChange={e => setMakeDefault(e.target.checked)} />
            <span>Set as default</span>
          </label>
        </div>
      </div>

    </Modal>
    <ConfirmDialog
      open={discardOpen}
      onCancel={() => setDiscardOpen(false)}
      onConfirm={() => { setDiscardOpen(false); onClose(); }}
      title="Discard unsaved changes?"
      message="The new slot has not been created — closing now discards what you entered."
      confirmLabel="Discard"
    />
    </>
  );
}

// ─── Edit-slot drawer ───────────────────────────────────────────
// Cheap client-side guard for the freeform extra_args field: catch the one
// error that would make the backend shlex.split() throw — unbalanced quotes.
// Anything subtler (unknown llama-server flags) is the server's job to reject;
// this just stops an obviously-malformed string from being saved/regenerated.
function validateExtraArgs(s) {
  if (!s) return null;
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < s.length; i++) {
    const c = s[i];
    if (c === "'" && !inDouble) inSingle = !inSingle;
    else if (c === '"' && !inSingle) inDouble = !inDouble;
  }
  if (inSingle || inDouble) return "Unbalanced quote";
  return null;
}

function EditSlotDrawer({ open, slot, onClose }) {
  // Hooks must execute every render — early `return null` would skip
  // them; render the drawer shell with a sentinel slot instead.
  const editMut = useSlotEdit();
  const defaultsMut = useSlotDefaults();
  const deleteMut = useSlotDelete();
  const restartMut = useSlotRestart();
  const swapMut = useSlotSwap();
  const profilesQuery = useProfiles();
  const modelsQuery = useModels();
  const chatTemplatesQuery = useChatTemplates(open);

  // Seed from the slot list payload when available (PR #587 — same fix
  // class as #584). llamacpp_args (profile base flags) is read-only;
  // n_gpu_layers is an editable per-slot override (PATCH /defaults →
  // [model].n_gpu_layers).
  const initialExtraArgs = slot?.llamacpp_args != null ? slot.llamacpp_args : "";

  // Seed from the PERSISTED context window (slot.ctx_max, from
  // [model].context_size) first — NOT the live runtime metric, which is 0
  // whenever the slot isn't actively serving and would otherwise snap the
  // field to a fabricated 16k on every cold (re)load. Fall back to the live
  // metric, then the backend's safe 8192 floor.
  const [ctx, setCtx] = useStateSM(slot?.ctx_max ?? (slot?.metrics?.ctx || 8192));
  // C4/C5: thinking is instant-apply (its own PUT); n_gpu_layers rides the Save
  // button through PATCH /defaults ([model].n_gpu_layers; -1/empty = unset →
  // sends null). Both seed from the slot list payload.
  const [thinking, setThinking] = useStateSM(slot?.enable_thinking === true);
  const [thinkingPending, setThinkingPending] = useStateSM(false);
  const [nGpuLayers, setNGpuLayers] = useStateSM(
    slot?.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1"
  );
  const [extraArgs, setExtraArgs] = useStateSM(initialExtraArgs);
  const [submitErr, setSubmitErr] = useStateSM(null);
  // Dirty-close confirms through the shared ConfirmDialog (state-driven),
  // replacing the raw window.confirm. Every dismiss path (Cancel, ✕, Esc,
  // backdrop) funnels through requestClose below.
  const [discardOpen, setDiscardOpen] = useStateSM(false);
  // UI-5 (state-driven): swapping the model on a LIVE container slot
  // cold-restarts it — stash the picked {id, label} here and confirm through
  // ConfirmDialog before firing the swap. null = no confirm pending.
  const [pendingSwap, setPendingSwap] = useStateSM(null);
  // Enable/disable is instant-apply via its own PUT (mirrors the slot card's
  // pill toggle, which the redesigned cards dropped). `enableBusy` gates the
  // header toggle against a double-trigger while the mutation is in flight.
  const [enableBusy, setEnableBusy] = useStateSM(false);
  // UI-16: destructive delete confirms through the shared ConfirmDialog
  // (type-to-confirm the slot name), mirroring DeleteModelDialog — replaces
  // the raw window.confirm that used to gate onDeleteClick.
  const [delOpen, setDelOpen] = useStateSM(false);
  // Inline error for the instant-apply thinking toggle (task 3): surface the
  // failure next to the control instead of only reverting state silently.
  const [thinkingErr, setThinkingErr] = useStateSM(null);
  // Per-field validation errors for numeric inputs (#548).
  const [fieldErrs, setFieldErrs] = useStateSM({});
  // C7: profile swap for GPU container slots.
  // Seeded from slot.profile; only sent on Save when changed. After a
  // profile-change save the slot is restarted (model swap semantics — same
  // cold-restart contract as profile image change).
  const [selectedProfile, setSelectedProfile] = useStateSM(slot?.profile || "");
  // Task 5: per-slot chat_template override.
  // chatTemplate seeds from slot.chat_template (empty = no override).
  // overrideOpen tracks whether the user has clicked [Override] to reveal the select.
  const [chatTemplate, setChatTemplate] = useStateSM(slot?.chat_template || "");
  const [overrideOpen, setOverrideOpen] = useStateSM(!!(slot?.chat_template));
  // MTP local state — TRI-STATE after the profile↔model separation:
  // null = Auto (defer to model-eligibility × profile opt-in), true = force on,
  // false = force off. Seeds from slot.mtp (undefined/null → Auto). Optimistic
  // set-before-mutate / revert-on-error, mirroring the reasoning toggle.
  const [mtp, setMtp] = useStateSM(slot?.mtp ?? null);
  // #901: per-slot vision toggle (instant-apply + cold restart). Default-ON:
  // the mmproj sidecar loads unless explicitly disabled, so null/undefined →
  // on. Optimistic local state with revert-on-error (mirrors reasoning).
  const [vision, setVision] = useStateSM(slot?.vision !== false);
  const [visionPending, setVisionPending] = useStateSM(false);
  const [visionErr, setVisionErr] = useStateSM(null);
  // Task 3 (NPU modality toggles): asr/embed instant-apply + cold restart for
  // device=npu slots. Seeded from slot.npu ({asr,embed}); optimistic with
  // revert-on-error.
  const [npuAsr, setNpuAsr] = useStateSM(slot?.npu?.asr === true);
  const [npuEmbed, setNpuEmbed] = useStateSM(slot?.npu?.embed === true);
  const [npuPending, setNpuPending] = useStateSM(false);
  const [npuErr, setNpuErr] = useStateSM(null);

  // Resolved command provenance — only fetched while the drawer is open.
  // Falls back gracefully when null (non-llama slots) or on error.
  const resolvedQuery = useSlotResolved(slot?.name, { enabled: !!open });

  useEffectSM(() => {
    if (slot) {
      setCtx(slot.ctx_max ?? (slot.metrics?.ctx || 8192));
      setThinking(slot.enable_thinking === true);
      setThinkingPending(false);
      setNGpuLayers(slot.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1");
      // #587: re-seed from the slot prop so the drawer tracks the real
      // on-disk values.
      setExtraArgs(slot.llamacpp_args != null ? slot.llamacpp_args : "");
      setSubmitErr(null);
      setDiscardOpen(false);
      setPendingSwap(null);
      setThinkingErr(null);
      setFieldErrs({});
      // C7: re-seed profile from the (possibly-updated) slot prop.
      setSelectedProfile(slot.profile || "");
      // Task 5: re-seed chat_template override from the slot prop.
      setChatTemplate(slot.chat_template || "");
      setOverrideOpen(!!(slot.chat_template));
      // Wave 8: re-seed the instant-apply toggles from the (possibly-updated)
      // slot prop.
      setMtp(slot.mtp ?? null);
      setVision(slot.vision !== false);
      setVisionPending(false);
      setVisionErr(null);
      setNpuAsr(slot.npu?.asr === true);
      setNpuEmbed(slot.npu?.embed === true);
      setNpuPending(false);
      setNpuErr(null);
    }
  }, [slot?.name]);

  if (!slot) return null;

  async function onSaveClick() {
    setSubmitErr(null);
    // Issue #548: validate numeric fields before any network call.
    // Invalid values surface inline and block Save.
    const ctxNum = Number(ctx);
    const errs = {};
    if (!Number.isFinite(ctxNum) || !Number.isInteger(ctxNum) || ctxNum < 128) {
      errs.ctx = "Must be an integer ≥ 128";
    }
    // n_gpu_layers: -1 or empty = "use model default / profile" (persisted as
    // null → backend unsets [model].n_gpu_layers); otherwise an integer ≥ 0.
    const nglRaw = String(nGpuLayers).trim();
    const nglNum = nglRaw === "" ? null : Number(nglRaw);
    if (nglRaw !== "" && (!Number.isFinite(nglNum) || !Number.isInteger(nglNum) || nglNum < -1)) {
      errs.ngl = "Must be an integer ≥ -1 (or empty)";
    }
    // Task 5: GPU-class slots have an editable profile select; mirror the
    // create-slot modal's guard and block Save when it's been cleared. NPU/CPU
    // slots render fixed text (no select) so they can never hit this.
    const allProfiles = profilesQuery.data ?? [];
    const currentProfileMeta = allProfiles.find(p => p.name === (slot.profile || ""));
    const slotDeviceIsGpu = !["npu", "cpu"].includes(slot.device || "");
    const profileDeviceClass = currentProfileMeta?.device_class
      ?? (slotDeviceIsGpu ? "gpu" : slot.device === "npu" ? "npu" : "cpu");
    if (profileDeviceClass === "gpu" && !selectedProfile) {
      errs.profile = "Profile is required";
    }
    // Block Save on malformed extra_args (unbalanced quotes) the same way
    // numeric fields block — the resolved command can't be built from it.
    if (extraArgsErr) {
      errs.extraArgs = extraArgsErr;
    }
    if (Object.keys(errs).length > 0) {
      setFieldErrs(errs);
      return;
    }
    setFieldErrs({});
    // C7: include profile only when changed; restart after save
    // (profile swap = cold restart, same semantics as model swap).
    const profileChanged = !!selectedProfile && selectedProfile !== (slot.profile || "");
    // Task 5: include chat_template only when the user has set/changed an override.
    // Dirty-track against slot.chat_template (mirrors profileChanged pattern).
    const chatTemplateChanged = overrideOpen && chatTemplate !== (slot.chat_template || "");
    // Per-slot extra_args override — ship only when changed, nested under
    // [server] so the backend one-level merge preserves sibling server keys.
    const extraArgsChanged = extraArgs !== extraArgsBaseline;
    // Only write ctx_size when the operator actually changed it. The old code
    // sent ctxNum unconditionally, so any unrelated save (profile, extra_args)
    // on a not-currently-serving slot clobbered the persisted context window
    // with the seeded fallback. Gate on the persisted baseline (ctxBaseline).
    const ctxChanged = ctxNum !== Number(ctxBaseline);
    // n_gpu_layers rides the same PATCH /defaults path as ctx_size
    // ([model].n_gpu_layers). -1 and empty both mean "unset" (→ null on the
    // wire); only ship when the NORMALIZED value differs from the persisted
    // baseline so a no-op Save stays quiet (#587 dirty-tracking contract).
    const nglValue = nglRaw === "" || nglNum === -1 ? null : nglNum;
    const nglChanged = nglValue !== nglBaselineValue;
    try {
      // Two-step: defaults (ctx_size / n_gpu_layers live under [model]) +
      // slot config for the top-level keys (profile, chat_template, server).
      // llamacpp_args base flags are owned by the profile — never included in
      // a save (extra_args is the per-slot override). These are fast on-disk
      // writes, so we await them and keep the drawer open to surface any
      // write error.
      const slotBody = {};
      if (profileChanged) {
        slotBody.profile = selectedProfile;
      }
      if (chatTemplateChanged) {
        slotBody.chat_template = chatTemplate;
      }
      if (extraArgsChanged) {
        slotBody.server = { extra_args: extraArgs };
      }
      const defaultsBody = {};
      if (ctxChanged) defaultsBody.ctx_size = ctxNum;
      if (nglChanged) defaultsBody.n_gpu_layers = nglValue;
      if (Object.keys(defaultsBody).length > 0) {
        await defaultsMut.mutateAsync({
          name: slot.name,
          body: defaultsBody,
        });
      }
      await editMut.mutateAsync({
        name: slot.name,
        body: slotBody,
      });
    } catch (err) {
      setSubmitErr(err?.message || "save failed");
      return;
    }
    // Non-blocking apply: a profile or chat_template change requires a cold
    // restart that can take model-load seconds-to-minutes. Fire it in the
    // BACKGROUND (do NOT await) and close the drawer immediately — the slots
    // list polls every 5s and reflects the transitional → running phase as
    // the restart progresses. Restart failures surface via toast since the
    // drawer is already gone.
    if (profileChanged || chatTemplateChanged) {
      restartMut.mutate(slot.name, {
        onError: (err) =>
          window.__hal0Toast && window.__hal0Toast(
            `Slot "${slot.name}" restart failed — ${err?.message || "see logs"}`,
            "err",
          ),
      });
      window.__hal0Toast && window.__hal0Toast(
        `Slot "${slot.name}" saved — restarting in the background`,
        "info",
      );
    } else {
      window.__hal0Toast && window.__hal0Toast(
        `Slot "${slot.name}" saved — restart required to apply changes`,
        "warn",
      );
    }
    onClose();
  }

  // Regenerate: persist the slot's freeform extra_args overlay (NOT the
  // profile) and let useSlotEdit's invalidation refetch the slot, which
  // recomputes resolved_command server-side. The drawer's `slot` prop is
  // derived live from the slots query, so on refetch the dirty overlay clears
  // (baseline now equals the typed value) and the fresh command renders. Does
  // NOT restart — a running slot keeps its old flags until the next restart.
  async function onRegenerateClick() {
    setSubmitErr(null);
    if (extraArgsErr) return;
    try {
      await editMut.mutateAsync({
        name: slot.name,
        body: { server: { extra_args: extraArgs } },
      });
    } catch (err) {
      setSubmitErr(err?.message || "regenerate failed");
      return;
    }
    window.__hal0Toast && window.__hal0Toast(
      `Slot "${slot.name}" extra_args saved — restart to run with the new flags`,
      "info",
    );
  }

  async function onDeleteConfirm() {
    setSubmitErr(null);
    try {
      await deleteMut.mutateAsync(slot.name);
      window.__hal0Toast && window.__hal0Toast(`Slot "${slot.name}" deleted`, "ok");
      setDelOpen(false);
      onClose();
    } catch (err) {
      setDelOpen(false);
      setSubmitErr(err?.message || "delete failed");
    }
  }

  // `saving` gates the Save button on the fast config writes only — the
  // restart is fired in the background (see onSaveClick) and must not keep the
  // drawer in a blocked "Saving…" state for the whole model-load.
  const saving = editMut.isPending || defaultsMut.isPending;
  const deleting = deleteMut.isPending;

  // Instant-apply enable/disable for the drawer header toggle. Mirrors the
  // card's onToggleEnabled — fire the PUT, toast the result, and let the slots
  // poll re-render from server truth. On error leave server state untouched
  // (e.g. the npu-exclusivity 409 when enabling a 2nd NPU LLM) and toast.
  const enabled = slot.enabled !== false;
  const onToggleEnabled = async (next) => {
    setEnableBusy(true);
    try {
      await editMut.mutateAsync({ name: slot.name, body: { enabled: next } });
      window.__hal0Toast &&
        window.__hal0Toast(`${slot.name} ${next ? "enabled" : "disabled"}`, "ok");
    } catch (err) {
      window.__hal0Toast &&
        window.__hal0Toast(
          err?.message ? `${slot.name}: ${err.message}` : `${slot.name}: toggle failed`,
          "warn",
        );
    } finally {
      setEnableBusy(false);
    }
  };

  // extra_args dirty-tracking: the resolved command is server-computed from the
  // persisted config, so any unsaved edit makes the displayed command stale.
  // Baseline is the on-disk value surfaced as `llamacpp_args` (wire key for
  // [server].extra_args). `validateExtraArgs` is a cheap client guard (balanced
  // quotes) — the backend shlex parse is the real validator.
  const extraArgsBaseline = slot.llamacpp_args != null ? slot.llamacpp_args : "";
  const extraArgsDirty = extraArgs !== extraArgsBaseline;
  const extraArgsErr = validateExtraArgs(extraArgs);
  // ctx dirty-tracking baseline: the PERSISTED context window (slot.ctx_max),
  // mirroring how the seed value is derived. Falls back to the live metric then
  // the 8192 floor only when nothing is persisted, so an untouched field on a
  // cold slot is never counted dirty (and never written — see ctxChanged).
  const ctxBaseline = slot.ctx_max ?? (slot.metrics?.ctx || 8192);
  // n_gpu_layers baseline, NORMALIZED: -1 and absent both mean "unset" (use
  // model default / profile), so they compare equal — an untouched "-1" seed
  // never counts dirty and never rides the PATCH.
  const nglBaselineValue =
    slot.n_gpu_layers == null || slot.n_gpu_layers === -1 ? null : slot.n_gpu_layers;
  const nglRawNow = String(nGpuLayers).trim();
  const nglValueNow =
    nglRawNow === "" || Number(nglRawNow) === -1 ? null : Number(nglRawNow);
  const nglDirty = nglValueNow !== nglBaselineValue;

  // UI-1: unsaved-changes guard. Aggregate ONLY the Save-batched fields
  // (extra_args, ctx, n_gpu_layers, profile, chat_template override). The
  // instant-apply toggles (thinking / MTP / enable) fire their own PUT/POST
  // outside Save and are intentionally excluded — a flipped toggle is
  // already persisted.
  // ctx dirty test matches the SAVE path's numeric comparison (ctxChanged in
  // onSaveClick: Number(ctx) !== Number(ctxBaseline)) — the old string compare
  // flagged "8192 " / "08192" as dirty even though Save would send nothing.
  // Trim + parse before comparing; an unparseable edit still counts dirty
  // (NaN !== N) so garbage input keeps the guard armed.
  const ctxDirty = Number(String(ctx).trim()) !== Number(ctxBaseline);
  const dirty =
    extraArgsDirty ||
    ctxDirty ||
    nglDirty ||
    (!!selectedProfile && selectedProfile !== (slot.profile || "")) ||
    (overrideOpen && chatTemplate !== (slot.chat_template || ""));
  const requestClose = () => {
    if (dirty) { setDiscardOpen(true); return; }
    onClose();
  };

  // Fire the model swap (POST /slots/{name}/swap). Non-blocking: a swap
  // cold-restarts container slots to load the model (slow) — fire it and let
  // the slots poll reflect the transitional phase; never freeze the drawer on
  // the load. Called directly for non-live swaps, or from the ConfirmDialog
  // once the operator confirms a live-container cold restart.
  const fireSwap = (id, label) => {
    setSubmitErr(null);
    swapMut.mutate({ name: slot.name, model_id: id }, {
      onError: (err) => setSubmitErr(err?.message || "model swap failed"),
    });
    window.__hal0Toast && window.__hal0Toast(
      slot.runtime === "container"
        ? `Restarting ${slot.name} to load ${label} — loading in the background`
        : `${slot.name} → ${label}`,
      "info",
    );
  };

  return (
    <>
    {/* The Drawer's Esc/backdrop/✕ paths call onClose — routed through
        requestClose so the dirty guard runs through this drawer's own
        ConfirmDialog copy below. (The primitive's `dirty` prop is now also
        dialog-based — DiscardGuardDialog in primitives.jsx — but this drawer
        keeps its slot-specific discard copy.) */}
    <Drawer
      open={open}
      onClose={requestClose}
      eyebrow={`Slots · /slots/${slot.name}`}
      title={`Edit ${slot.name}`}
      width={560}
      headRight={
        <label
          className="slot-enable-toggle drawer-enable"
          title={enabled ? "Disable slot" : "Enable slot"}
        >
          <span className="drawer-enable-label mono">{enabled ? "Enabled" : "Disabled"}</span>
          <input
            type="checkbox"
            checked={enabled}
            disabled={enableBusy}
            onChange={() => onToggleEnabled(!enabled)}
            aria-label={enabled ? "Disable slot" : "Enable slot"}
          />
          <span className="slot-enable-track" aria-hidden="true" />
        </label>
      }
      foot={
        <>
          <button
            className="btn danger sm"
            disabled={deleting}
            onClick={() => setDelOpen(true)}
          >{Icons.unload} {deleting ? "Deleting…" : "Delete slot"}</button>
          <span style={{display: "inline-flex", gap: 8, alignItems: "center"}}>
            {submitErr && <span style={{color: "var(--err)", fontSize: 11}}>{submitErr}</span>}
            <button className="btn ghost sm" onClick={requestClose}>Cancel</button>
            <button
              className="btn sm"
              disabled={saving || deleting}
              onClick={onSaveClick}
            >{saving ? "Saving…" : "Save"}</button>
          </span>
        </>
      }
    >
      {/* Image + port + state strip — read-only. */}
      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 0, border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", overflow: "hidden", marginBottom: 16}}>
        <ReadOnlyStrip k="image" v={slot.image ? slot.image.split(':').pop() : slot.profile || "—"} />
        <ReadOnlyStrip k="port" v={`:${slot.port || "—"}`} />
        <ReadOnlyStrip k="state" v={<span className={stateChipClass(slot)}>{slot.state}</span>} />
      </div>

      {/* Profile + image status strip — read-only. */}
      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0, border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", overflow: "hidden", marginBottom: 16}}>
        <ReadOnlyStrip k="profile" v={slot.profile || "—"} />
        <ReadOnlyStrip k="image status" v={slot.image_status || "present"} />
      </div>

      <FieldGroup label="Slot" hint="this instance">
      <div className="form-row">
        <div className="form-lbl"><span>Name</span><span className="sub">seeded slots can't be renamed</span></div>
        <div className="form-ctl"><input className="input mono" value={slot.name} disabled /></div>
      </div>

      <div className="form-row">
        <div className="form-lbl"><span>Type</span></div>
        <div className="form-ctl">
          <select className="input mono" defaultValue={slot.type} disabled>
            <option>{slot.type}</option>
          </select>
          <div className="hint">Type is immutable. Create a new slot to change.</div>
        </div>
      </div>

      {/* Profile is the configuration surface.
          GPU-class slots get an editable select filtered to device_class==="gpu"
          profiles. NPU/CPU/image-class slots are pinned by silicon/runtime —
          render fixed text (no select). Profile change triggers restart
          (same cold-restart semantics as a model swap). */}
      {(() => {
        const allProfiles = profilesQuery.data ?? [];
        // Find the current profile's device_class from the catalog.
        // Fall back to slot.device when the profiles query hasn't loaded:
        //   npu/cpu devices → not GPU; gpu-rocm/gpu-vulkan/unknown → treat as GPU.
        const currentProfileMeta = allProfiles.find(p => p.name === (slot.profile || ""));
        const slotDeviceIsGpu = !["npu", "cpu"].includes(slot.device || "");
        const profileDeviceClass = currentProfileMeta?.device_class
          ?? (slotDeviceIsGpu ? "gpu" : slot.device === "npu" ? "npu" : "cpu");
        const isGpuProfile = profileDeviceClass === "gpu";
        const gpuProfiles = allProfiles.filter(p => p.device_class === "gpu");
        const profileImageHint = (() => {
          const meta = gpuProfiles.find(p => p.name === selectedProfile);
          return meta?.image || slot.image || null;
        })();
        return (
          <div className="form-row">
            <div className="form-lbl">
              <span>Profile</span>
              {isGpuProfile
                ? <span className="sub warn">⟳ restart required on change</span>
                : <span className="sub">image + bench-tuned flags for this slot — runtime-pinned</span>
              }
            </div>
            <div className="form-ctl">
              {isGpuProfile ? (
                <select
                  className={"input mono" + (fieldErrs.profile ? " input-err" : "")}
                  value={selectedProfile}
                  onChange={e => { setSelectedProfile(e.target.value); setFieldErrs(p => ({...p, profile: undefined})); }}
                >
                  {/* Task 5: an empty option lets the field be cleared, which
                      the Save guard then rejects (mirrors the create modal). */}
                  {!selectedProfile && <option value="">— select a profile —</option>}
                  {gpuProfiles.map(p => (
                    <option key={p.name} value={p.name}>
                      {p.intent ? `${p.name} · ${p.intent}` : p.name}
                    </option>
                  ))}
                </select>
              ) : (
                <input className="input mono" value={slot.profile || "—"} readOnly />
              )}
              {fieldErrs.profile && (
                <div className="hint" style={{color: "var(--err)"}}>{fieldErrs.profile}</div>
              )}
              {profileImageHint && (
                <div className="hint mono">{profileImageHint}</div>
              )}
              {/* Task 2: announce the pending restart before Save fires it. */}
              {!!selectedProfile && selectedProfile !== (slot.profile || "") && (
                <div
                  className="hint"
                  style={{marginTop: 6, padding: "6px 10px", borderRadius: "var(--rad-sm)", color: "var(--warn)", border: "1px solid var(--warn-line)", background: "var(--warn-soft)"}}
                >
                  ⟳ Profile change requires a restart — applied on Save.
                </div>
              )}
            </div>
          </div>
        );
      })()}

      </FieldGroup>

      <FieldGroup label="Model" hint="what it loads">
      {/* Task 1: live model swap — mirrors the card's ModelPicker but with the
          full type+rocmfp4 compatibility filter (same as InlineSwapPopover).
          Swap is its own POST /slots/{name}/swap (not part of the batched
          Save); container slots cold-restart to load, so we toast like the
          popover does. */}
      {(() => {
        const isContainer = slot.runtime === "container";
        // Derive the backend from the SELECTED profile (reactive), falling back
        // to the slot's persisted backend when the profile carries none or isn't
        // found yet. This makes the rocmfp4 filter re-evaluate immediately when
        // the operator switches profiles — before Save is clicked.
        const selProfileMeta = (profilesQuery.data ?? []).find(p => p.name === selectedProfile);
        const selBackend = selProfileMeta?.backend ?? slot.backend;
        const compatible = compatibleModels(modelsQuery.data, { type: slot.type, backend: selBackend });
        const cur = slot.model_id || slot.model || "";
        const has = compatible.some(m => m.id === cur);
        // A background swap is in flight — the select stays usable, but show a
        // "Swapping…" hint so the operator knows the load is happening.
        const swapping = swapMut.isPending;
        return (
          <div className="form-row">
            <div className="form-lbl">
              <span>Model</span>
              <span className="sub">
                {isContainer ? "swap restarts the container to load" : "applies immediately"}
              </span>
            </div>
            <div className="form-ctl">
              <select
                className="input mono"
                value={cur}
                disabled={saving}
                aria-label={`Model for ${slot.name}`}
                onChange={(e) => {
                  const id = e.target.value;
                  if (!id || id === cur) return;
                  const picked = compatible.find(m => m.id === id);
                  const label = picked?.longName || id;
                  // UI-5: swapping the model on a LIVE container slot cold-restarts
                  // it (~model-load seconds). Confirm through the shared
                  // ConfirmDialog before firing — stashing the pick re-renders the
                  // select back to `cur` (value={cur}), so cancel needs no manual
                  // revert. Mirrors the delete/dirty-close confirm gates.
                  const live = slotButtonPhase(slot) === "running";
                  if (isContainer && live) { setPendingSwap({ id, label }); return; }
                  fireSwap(id, label);
                }}
              >
                {cur && !has && <option value={cur}>{slot.modelLong || slot.model || cur}</option>}
                {!cur && <option value="">—</option>}
                {compatible.map(m => (
                  <option key={m.id} value={m.id}>{m.longName || m.id}</option>
                ))}
              </select>
              {swapping && <div className="hint">Swapping…</div>}
            </div>
          </div>
        );
      })()}

      <div className="form-row">
        <div className="form-lbl">
          <span>ctx_size</span>
          <span className="warn">⟳ restarts the container (~model-load seconds)</span>
        </div>
        <div className="form-ctl">
          <input
            className={"input mono" + (fieldErrs.ctx ? " input-err" : "")}
            value={ctx}
            onChange={e => { setCtx(e.target.value); setFieldErrs(p => ({...p, ctx: undefined})); }}
          />
          {fieldErrs.ctx && <div className="hint" style={{color: "var(--err)"}}>{fieldErrs.ctx}</div>}
        </div>
      </div>

      {/* Task 5: per-slot chat_template override.
          Shows the model-level default template (from model.defaults.chat_template)
          read-only, with an [Override] button to reveal a select for a per-slot
          override. Override is dirty-tracked against slot.chat_template and
          included in the config PUT only when changed. A template change requires
          a cold restart (it changes llama-server --chat-template arg). */}
      {(() => {
        const cur = slot.model_id || slot.model || "";
        const m = (modelsQuery.data ?? []).map(normalizeApiModel).find(x => x.id === cur);
        const modelTemplate = m?.defaults?.chat_template || "auto";
        const templates = Array.isArray(chatTemplatesQuery.data) ? chatTemplatesQuery.data : [];
        return (
          <div className="form-row">
            <div className="form-lbl">
              <span>Template</span>
              <span className="sub warn">⟳ restart required on change</span>
            </div>
            <div className="form-ctl">
              {!overrideOpen ? (
                <div style={{display: "flex", alignItems: "center", gap: 8}}>
                  <span className="input mono" style={{flex: 1, padding: "6px 10px", background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontSize: 12, color: "var(--fg-3)"}}>
                    {modelTemplate} <span style={{color: "var(--fg-5)", fontSize: 11}}>(from model)</span>
                  </span>
                  <button
                    type="button"
                    className="btn ghost sm"
                    onClick={() => { setChatTemplate(chatTemplate || modelTemplate); setOverrideOpen(true); }}
                  >Override</button>
                </div>
              ) : (
                <>
                  <select
                    className="input mono"
                    value={chatTemplate}
                    onChange={e => setChatTemplate(e.target.value)}
                  >
                    <option value="auto">Auto (GGUF embedded)</option>
                    {/* Filter out the backend's own "auto" entry — it's rendered
                        above as a fixed first option. A template the render-lint
                        flagged invalid is disabled so it can't be pinned (it would
                        only crash the slot at cold-start). */}
                    {templates.filter(t => t.id !== "auto").map(t => (
                      <option key={t.id} value={t.id} disabled={t.valid === false}>
                        {(t.label || t.id) + (t.valid === false ? "  ⚠ invalid" : "")}
                      </option>
                    ))}
                  </select>
                  {(() => {
                    const sel = templates.find(t => t.id === chatTemplate);
                    return sel && sel.valid === false ? (
                      <div className="hint" style={{color: "var(--err)", marginTop: 4}}>
                        ⚠ Template failed to render: {sel.error}
                      </div>
                    ) : null;
                  })()}
                  <button
                    type="button"
                    className="btn ghost sm"
                    style={{marginTop: 4}}
                    onClick={() => { setChatTemplate(""); setOverrideOpen(false); }}
                  >Clear override</button>
                </>
              )}
            </div>
          </div>
        );
      })()}
      </FieldGroup>

      <FieldGroup label="Inference" hint="behavior">
      {/* C4: per-slot thinking default — llm slots only. Instant-apply (its
          own PUT /config), no restart: _slot_thinking_default reads it live
          on the next request. */}
      {slot.type === "llm" && (
        <div className="form-row">
          <div className="form-lbl">
            <span>Reasoning</span>
            <span className="sub">Stream reasoning before the answer. Off = faster, direct replies. Applies to the next message.</span>
          </div>
          <div className="form-ctl">
            <PillToggle
              on={thinking}
              disabled={thinkingPending}
              label="Reasoning"
              stateText={thinking ? "On" : "Off"}
              onToggle={async (next) => {
                setThinking(next);
                setThinkingPending(true);
                setSubmitErr(null);
                setThinkingErr(null);
                try {
                  await editMut.mutateAsync({ name: slot.name, body: { enable_thinking: next } });
                  window.__hal0Toast && window.__hal0Toast(`${slot.name} reasoning ${next ? "on" : "off"} — applies to next message`, "ok");
                } catch (err) {
                  setThinking(!next);
                  setThinkingErr(err?.message || "reasoning toggle failed");
                } finally {
                  setThinkingPending(false);
                }
              }}
            />
            {thinkingErr && <div className="hint" style={{ color: "var(--err)" }}>{thinkingErr}</div>}
          </div>
        </div>
      )}
      {/* MTP — tri-state (Auto/On/Off) after the profile↔model separation.
          Renders when the loaded model is MTP-ELIGIBLE (carries the "mtp" tag)
          OR the slot already has an explicit override, so a forced state is
          always adjustable. NOT gated on rocm any more — the draft device now
          tracks the profile backend, so MTP works wherever an MTP profile runs.
          Auto (null) defers to model-eligibility × profile opt-in; the control
          shows whether Auto is currently effective. Instant-apply via PUT
          /config + non-blocking restart. */}
      {(() => {
        const cur = slot.model_id || slot.model || "";
        const m = (modelsQuery.data ?? []).map(normalizeApiModel).find(x => x.id === cur);
        // Same eligibility rule as the server (`model_is_mtp_eligible`): the
        // `mtp` tag OR a delimited MTP name marker — so an untagged local pull
        // named "…-MTP-….gguf" (which the server WILL auto-speculate on an MTP
        // profile) still surfaces the control here.
        const modelEligible = isMtpEligibleModel(m);
        // Show for an eligible model, or when MTP is force-ON on a now-ineligible
        // model so it stays adjustable. A force-OFF on an ineligible model needs
        // no control (it's already off and would be off under Auto too).
        const forcedOn = slot.mtp === true;
        if (!modelEligible && !forcedOn) return null;
        // Auto is effective only when the model is eligible AND the slot's
        // profile opts into MTP — mirror the server's _effective_mtp rule so
        // the hint never disagrees with what actually launches.
        const prof = (profilesQuery.data ?? []).find(p => p.name === (selectedProfile || slot.profile));
        const autoActive = modelEligible && !!prof?.mtp;
        return (
          <div className="form-row">
            <div className="form-lbl">
              <span>MTP</span>
              <span className="sub">Multi-token speculative decoding. Auto follows the model + profile; On/Off force it. Restarts the container.</span>
            </div>
            <div className="form-ctl">
              <MtpControl
                value={mtp}
                autoActive={autoActive}
                disabled={saving}
                onChange={async (next) => {
                  // Optimistic — set local state before the PUT, revert on error.
                  const prev = mtp;
                  setMtp(next);
                  setSubmitErr(null);
                  const word = next == null ? "auto" : (next ? "on" : "off");
                  try {
                    await editMut.mutateAsync({ name: slot.name, body: { mtp: next } });
                    restartMut.mutate(slot.name, {
                      onError: (err) => window.__hal0Toast && window.__hal0Toast(`MTP restart failed — ${err?.message || "see logs"}`, "err"),
                    });
                    window.__hal0Toast && window.__hal0Toast(`${slot.name} MTP ${word} — restarting in the background`, "info");
                  } catch (err) {
                    setMtp(prev);
                    setSubmitErr(err?.message || "MTP change failed");
                  }
                }}
              />
            </div>
          </div>
        );
      })()}
      {/* #901: Vision pill — gated to slots whose bound model carries an mmproj
          sidecar (the registry Model.mmproj presence flag). Toggling drops or
          adds the ~0.9 GB projector; instant-apply via PUT /config {vision}
          plus a non-blocking cold restart (mirrors MTP). Default-ON, so a
          null/absent on-disk value renders as on. */}
      {(() => {
        const cur = slot.model_id || slot.model || "";
        const m = (modelsQuery.data ?? []).map(normalizeApiModel).find(x => x.id === cur);
        // mmproj is a presence flag on the registry row (path or marker string);
        // any truthy value means the model ships a vision projector sidecar.
        if (!m || !m.mmproj) return null;
        return (
          <div className="form-row">
            <div className="form-lbl">
              <span>Vision</span>
              <span className="sub">Load the multimodal projector so the slot accepts images. Off frees ~0.9 GB (text-only). Restarts the container.</span>
            </div>
            <div className="form-ctl">
              <PillToggle
                on={vision}
                disabled={visionPending || saving}
                label="Vision"
                stateText={vision ? "On" : "Off"}
                onToggle={async (next) => {
                  // Optimistic set-before-mutate + revert-on-error (mirrors MTP).
                  setVision(next);
                  setVisionPending(true);
                  setVisionErr(null);
                  setSubmitErr(null);
                  try {
                    await editMut.mutateAsync({ name: slot.name, body: { vision: next } });
                    restartMut.mutate(slot.name, {
                      onError: (err) => window.__hal0Toast && window.__hal0Toast(`Vision restart failed — ${err?.message || "see logs"}`, "err"),
                    });
                    window.__hal0Toast && window.__hal0Toast(`${slot.name} vision ${next ? "on" : "off"} — restarting in the background`, "info");
                  } catch (err) {
                    setVision(!next);
                    setVisionErr(err?.message || "vision toggle failed");
                  } finally {
                    setVisionPending(false);
                  }
                }}
              />
              {visionErr && <div className="hint" style={{ color: "var(--err)" }}>{visionErr}</div>}
            </div>
          </div>
        );
      })()}
      {/* Task 3: NPU modality toggles (asr/embed) — device=npu slots only.
          Seeded from slot.npu; each toggle sends the full {asr,embed} object via
          PUT /config {npu:{...}} (the backend one-level merge replaces the [npu]
          table wholesale) + a non-blocking cold restart. Optimistic with
          revert-on-error. */}
      {slot.device === "npu" && (() => {
        const applyNpu = async (nextAsr, nextEmbed, prevAsr, prevEmbed, which) => {
          setNpuPending(true);
          setNpuErr(null);
          setSubmitErr(null);
          try {
            await editMut.mutateAsync({ name: slot.name, body: { npu: { asr: nextAsr, embed: nextEmbed } } });
            restartMut.mutate(slot.name, {
              onError: (err) => window.__hal0Toast && window.__hal0Toast(`NPU restart failed — ${err?.message || "see logs"}`, "err"),
            });
            window.__hal0Toast && window.__hal0Toast(`${slot.name} NPU ${which} updated — restarting in the background`, "info");
          } catch (err) {
            // Revert both to their pre-toggle values.
            setNpuAsr(prevAsr);
            setNpuEmbed(prevEmbed);
            setNpuErr(err?.message || "NPU toggle failed");
          } finally {
            setNpuPending(false);
          }
        };
        return (
          <>
            <div className="form-row">
              <div className="form-lbl">
                <span>NPU · ASR</span>
                <span className="sub">Serve speech-to-text on the coresident NPU process. Restarts the container.</span>
              </div>
              <div className="form-ctl">
                <PillToggle
                  on={npuAsr}
                  disabled={npuPending || saving}
                  label="NPU ASR"
                  stateText={npuAsr ? "On" : "Off"}
                  onToggle={(next) => { setNpuAsr(next); applyNpu(next, npuEmbed, npuAsr, npuEmbed, "ASR"); }}
                />
              </div>
            </div>
            <div className="form-row">
              <div className="form-lbl">
                <span>NPU · Embed</span>
                <span className="sub">Serve embeddings on the coresident NPU process. Restarts the container.</span>
              </div>
              <div className="form-ctl">
                <PillToggle
                  on={npuEmbed}
                  disabled={npuPending || saving}
                  label="NPU Embed"
                  stateText={npuEmbed ? "On" : "Off"}
                  onToggle={(next) => { setNpuEmbed(next); applyNpu(npuAsr, next, npuAsr, npuEmbed, "Embed"); }}
                />
              </div>
            </div>
            {npuErr && <div className="hint" style={{ color: "var(--err)" }}>{npuErr}</div>}
          </>
        );
      })()}
      </FieldGroup>

      {/* Task 4: Advanced fields (mostly read-only, profile-owned) are
          collapsed by default — minimal native <details> disclosure (no
          disclosure primitive exists in primitives.jsx). */}
      <details className="adv-disclosure">
      <summary className="form-section" style={{cursor: "pointer", listStyle: "revert"}}>Advanced</summary>

      {/* GPU offload tuning — per-slot override persisted via
          PATCH /defaults ([model].n_gpu_layers). -1/empty = unset, i.e. the
          model default / profile value applies. Rides the Save button with
          the same dirty-tracking as ctx_size.
          (rope_freq_base was removed — deprecated; advanced users set it via
          extra_args below.) */}
      <div className="form-row">
        <div className="form-lbl">
          <span>n_gpu_layers</span>
          <span className="sub">-1 / empty = use model default / profile</span>
        </div>
        <div className="form-ctl">
          <input
            className={"input mono" + (fieldErrs.ngl ? " input-err" : "")}
            value={nGpuLayers}
            onChange={e => { setNGpuLayers(e.target.value); setFieldErrs(p => ({...p, ngl: undefined})); }}
            placeholder="-1"
            inputMode="numeric"
            data-testid="n-gpu-layers-input"
          />
          {fieldErrs.ngl && <div className="hint" style={{color: "var(--err)"}}>{fieldErrs.ngl}</div>}
        </div>
      </div>

      {/* Per-slot freeform override. Persisted to [server].extra_args on the
          slot TOML (NOT the profile) and appended AFTER the profile flags in
          the resolved command, so slot flags win on collision. Editable so
          operators can test one-off flags without minting a new profile. */}
      <div className="form-row">
        <div className="form-lbl">
          <span>extra_args</span>
          <span className="sub">per-slot override · wins over profile flags</span>
        </div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={extraArgs}
            onChange={(e) => setExtraArgs(e.target.value)}
            placeholder="--flag value  (one-off, no new profile)"
            spellCheck={false}
            data-testid="extra-args-input"
          />
          {extraArgsErr && (
            <div style={{color: "var(--err)", fontSize: 11, paddingTop: 4, fontFamily: "var(--jbm)"}}>
              {extraArgsErr}
            </div>
          )}
        </div>
      </div>

      {/* Flags preview — backend-provided resolved_command (real podman argv).
          The resolved command is computed SERVER-SIDE (profile + MTP + image
          resolution), so when extra_args is dirty the displayed command is
          stale: dim it and overlay a Regenerate prompt that persists the slot
          override and refetches the freshly-resolved command.
          When the /resolved endpoint returns provenance data, we enhance this
          view with per-flag source badges and a duplicate-collapse note. */}
      <div className="form-section">Resolved command</div>
      <div style={{position: "relative"}}>
        <div style={{
          padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)",
          borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11,
          color: "var(--fg-3)", lineHeight: 1.6, whiteSpace: "pre-wrap",
          opacity: extraArgsDirty ? 0.28 : 1,
          filter: extraArgsDirty ? "grayscale(1)" : "none",
          transition: "opacity .15s ease",
        }}>
          {(() => {
            // Prefer deduped argv from /resolved when available; fall back to
            // slot.resolved_command (list-payload) then a "not yet" sentinel.
            const resolvedData = resolvedQuery.data;
            const argv = resolvedData?.argv ?? null;
            if (Array.isArray(argv) && argv.length > 0) {
              return argv.join(" \\\n  ");
            }
            if (Array.isArray(slot.resolved_command)) {
              return slot.resolved_command.join(" \\\n  ");
            }
            return slot.resolved_command || "— not yet available (slot not loaded)";
          })()}
        </div>
        {extraArgsDirty && (
          <div style={{
            position: "absolute", inset: 0, display: "flex", flexDirection: "column",
            alignItems: "center", justifyContent: "center", gap: 10, textAlign: "center", padding: 12,
          }} data-testid="resolved-stale-overlay">
            <div style={{
              maxWidth: 360, padding: "12px 16px", background: "var(--bg-2)",
              border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)",
              boxShadow: "0 4px 16px rgba(0,0,0,0.25)", display: "flex",
              flexDirection: "column", alignItems: "center", gap: 10,
            }}>
              <div style={{fontSize: 11.5, color: "var(--fg-2)", lineHeight: 1.5}}>
                Flags changed. Slot <code style={{fontFamily: "var(--jbm)"}}>extra_args</code> take
                precedence over the profile — regenerate to fold them into the resolved command.
              </div>
              <button
                className="btn sm"
                disabled={!!extraArgsErr || editMut.isPending}
                onClick={onRegenerateClick}
                data-testid="regenerate-resolved"
              >
                {editMut.isPending ? "Regenerating…" : "Regenerate"}
              </button>
            </div>
          </div>
        )}
      </div>
      {/* Provenance legend + per-flag badges — only when the /resolved endpoint
          returns data with at least one provenance entry. Gracefully absent for
          non-llama slots (argv null) or when the endpoint hasn't loaded yet. */}
      {(() => {
        const resolvedData = resolvedQuery.data;
        if (!resolvedData || !Array.isArray(resolvedData.provenance) || resolvedData.provenance.length === 0) {
          return null;
        }
        // Source → display label + CSS variable colour. The backend command
        // assembler emits MORE segment labels than the original three (new:
        // model_defaults, chat_template, mmproj, slot_overrides) and may grow
        // others — metaFor() falls back to a generic neutral badge carrying
        // the raw label text, so an unknown source renders instead of being
        // mislabelled "base" or breaking the drawer.
        const SOURCE_META = {
          base:       { label: "base",       color: "var(--fg-4)" },
          profile:    { label: "profile",    color: "var(--info)" },
          extra_args: { label: "extra_args", color: "var(--accent)" },
        };
        const metaFor = (source) =>
          SOURCE_META[source] || { label: String(source || "unknown"), color: "var(--fg-3)" };
        // Legend: the known trio plus any additional sources actually present
        // in this slot's provenance (deduped, generic-styled).
        const legendSources = [
          ...Object.keys(SOURCE_META),
          ...[...new Set(resolvedData.provenance.map((e) => e.source))]
            .filter((s) => !SOURCE_META[s]),
        ];
        const badgeStyle = (source) => {
          const meta = metaFor(source);
          return {
            display: "inline-block",
            padding: "1px 5px",
            borderRadius: "var(--rad-sm)",
            border: `1px solid ${meta.color}`,
            color: meta.color,
            fontFamily: "var(--jbm)",
            fontSize: 9,
            lineHeight: 1.5,
            letterSpacing: "0.04em",
            verticalAlign: "middle",
            whiteSpace: "nowrap",
          };
        };
        return (
          <div style={{marginTop: 8}}>
            {/* Legend */}
            <div style={{
              display: "flex", alignItems: "center", gap: 8,
              paddingBottom: 6, flexWrap: "wrap",
            }}>
              <span style={{fontSize: 10, color: "var(--fg-5)", fontFamily: "var(--jbm)"}}>source:</span>
              {legendSources.map((src) => (
                <span key={src} style={badgeStyle(src)}>{metaFor(src).label}</span>
              ))}
            </div>
            {/* Per-flag provenance rows */}
            <div style={{
              display: "flex", flexDirection: "column", gap: 2,
              padding: "8px 10px", background: "var(--bg)",
              border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)",
            }}>
              {resolvedData.provenance.map((entry, i) => (
                <div key={i} style={{
                  display: "flex", alignItems: "center", gap: 6,
                  fontFamily: "var(--jbm)", fontSize: 10.5,
                }}>
                  <span style={{color: "var(--fg-3)", minWidth: 120, flexShrink: 0}}>
                    {entry.flag}
                    {entry.value != null && (
                      <span style={{color: "var(--fg-5)"}}>{" "}{entry.value}</span>
                    )}
                  </span>
                  <span style={badgeStyle(entry.source)}>
                    {metaFor(entry.source).label}
                  </span>
                </div>
              ))}
            </div>
            {/* Duplicate-collapse note */}
            {resolvedData.removed > 0 && (
              <div style={{
                marginTop: 5, fontSize: 10, color: "var(--fg-5)",
                fontFamily: "var(--jbm)",
              }}>
                {resolvedData.removed} duplicate flag{resolvedData.removed !== 1 ? "s" : ""} collapsed
              </div>
            )}
          </div>
        );
      })()}
      <div className="hint" style={{paddingTop: 6, fontSize: 10.5, color: "var(--fg-5)", fontFamily: "var(--jbm)"}}>
        Real podman argv: profile image + flags, then slot extra_args (slot wins). Restart the slot to run with new flags.
      </div>
      </details>
    </Drawer>
    <ConfirmDialog
      open={delOpen}
      onCancel={() => setDelOpen(false)}
      onConfirm={onDeleteConfirm}
      title={`Delete slot ${slot.name}?`}
      message={
        <span>
          This removes the slot <span className="mono" style={{color: "var(--fg)"}}>{slot.name}</span> and
          its <span className="mono">capabilities.toml</span> config. The container is stopped and the
          slot is gone from the host.
        </span>
      }
      confirmLabel={deleting ? "Deleting…" : "Delete slot"}
      destructive
      typeToConfirm={slot.name}
    />
    <ConfirmDialog
      open={discardOpen}
      onCancel={() => setDiscardOpen(false)}
      onConfirm={() => { setDiscardOpen(false); onClose(); }}
      title="Discard unsaved changes?"
      message={
        <span>
          <span className="mono" style={{color: "var(--fg)"}}>{slot.name}</span> has
          unsaved edits — closing the drawer discards them.
        </span>
      }
      confirmLabel="Discard"
    />
    <ConfirmDialog
      open={!!pendingSwap}
      onCancel={() => setPendingSwap(null)}
      onConfirm={() => {
        const p = pendingSwap;
        setPendingSwap(null);
        if (p) fireSwap(p.id, p.label);
      }}
      title={`Swap model on running slot ${slot.name}?`}
      message={
        <span>
          Loading <span className="mono" style={{color: "var(--fg)"}}>{pendingSwap?.label || ""}</span> cold-restarts
          the container (~model-load seconds). The slot is unavailable while it reloads.
        </span>
      }
      confirmLabel="Swap model"
    />
    </>
  );
}

function ReadOnlyStrip({ k, v }) {
  return (
    <div style={{padding: "10px 12px", borderRight: "1px solid var(--line-soft)", background: "var(--bg)"}}>
      <div className="mono" style={{fontSize: 9, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", marginBottom: 3}}>{k}</div>
      <div className="mono" style={{fontSize: 12, color: "var(--fg)"}}>{v}</div>
    </div>
  );
}

// ─── Inline swap popover ────────────────────────────────────────
function InlineSwapPopover({ slot, open, onClose, onPick }) {
  // Hooks first — React rules-of-hooks forbid an early return before
  // them. The popover is mounted unconditionally and toggles via `open`;
  // useQuery's own caching means useModels() costs ~nothing when closed.
  const modelsQuery = useModels();
  const hwQuery = useHardware();
  // UI-5 (state-driven): a live-container pick is stashed here and confirmed
  // through the shared ConfirmDialog before committing. Must be declared
  // before the early return (rules-of-hooks).
  const [pendingPick, setPendingPick] = useStateSM(null);
  if (!open) return null;

  const isContainer = slot.runtime === "container";
  const ramFreeGb = hwQuery.data?.ram?.free ?? 0;
  // ROCmFP4-quantized models only run on the custom rocm fork binary — don't
  // offer them when swapping a non-rocm slot (shared compatibleModels filter).
  const compatible = compatibleModels(modelsQuery.data, { type: slot.type, backend: slot.backend });

  // N2: container swap = cold systemctl restart (not a hot in-place swap).
  // Intercept onPick for container slots: toast the restart and fire the same
  // onPick (which drives restart), so the parent card drives to "starting"
  // state immediately. The parent's onSwapPick calls useSlotSwap which
  // triggers a restart for container slots server-side.
  const commitPick = (m) => {
    if (isContainer) {
      window.__hal0Toast && window.__hal0Toast(
        `Restarting ${slot.name} to load ${m.longName || m.id} — ~model-load seconds`,
        "info"
      );
    }
    onPick(m);
    onClose();
  };
  const handlePick = (m) => {
    // UI-5: confirm before cold-restarting a LIVE container slot. Cancelling
    // the dialog leaves the popover open, same as declining the old
    // window.confirm.
    const live = slotButtonPhase(slot) === "running";
    if (isContainer && live) { setPendingPick(m); return; }
    commitPick(m);
  };

  return (
    <div className="swap-pop" onClick={e => e.stopPropagation()}>
      {/* N2: container cold-restart notice in popover header */}
      <div className="swap-pop-h">
        Swap model · type {slot.type}
        {isContainer && (
          <span
            className="chip"
            style={{marginLeft: 8, fontSize: 9, color: "var(--warn)", borderColor: "var(--warn-line)", background: "var(--warn-soft)"}}
            title="Container runtime — model swap requires a container restart (~model-load seconds)"
          >
            · cold restart
          </span>
        )}
      </div>
      {compatible.map(m => {
        const isCur = slot.model_id === m.id;
        const fits = ramFreeGb > parseSizeGB(m.size);
        return (
          // The whole row is a mouse-click target (convenience) but the
          // nested chevron button is the single keyboard/AT-accessible
          // affordance — making the row also a role=button creates a
          // double-announce for screen readers (a11y review 2026-05-27).
          <div
            key={m.id}
            className={"swap-pop-item" + (isCur ? " cur" : "")}
            onClick={() => handlePick(m)}
          >
            <div className="nm">
              {m.longName}
              <span className="sub">{m.repo}</span>
            </div>
            <div className="sz num">{m.size}</div>
            <div className={"fit" + (fits ? "" : " no")}>{m.installed ? (fits ? "fits ✓" : "tight") : "will pull"}</div>
            <button
              type="button"
              className="swap-arrow"
              aria-label={`Load ${m.longName || m.id}`}
              onClick={e => { e.stopPropagation(); handlePick(m); }}
            >{Icons.chevR}</button>
          </div>
        );
      })}
      <div className="swap-pop-h" style={{cursor: "pointer", color: "var(--accent)"}}
           onClick={() => { onClose(); window.location.hash = "#models"; }}>
        + Browse all models →
      </div>
      <ConfirmDialog
        open={!!pendingPick}
        onCancel={() => setPendingPick(null)}
        onConfirm={() => {
          const m = pendingPick;
          setPendingPick(null);
          if (m) commitPick(m);
        }}
        title={`Swap model on running slot ${slot.name}?`}
        message={
          <span>
            Loading <span className="mono" style={{color: "var(--fg)"}}>{pendingPick?.longName || pendingPick?.id || ""}</span> cold-restarts
            the container (~model-load seconds). The slot is unavailable while it reloads.
          </span>
        }
        confirmLabel="Swap model"
      />
    </div>
  );
}

// ─── Slot logs drawer ────────────────────────────────────────────
// Raw per-slot journald tail, backed by the shared `useSlotLogsStream`
// hook (same transport the Logs page "slot" channel uses). The hook now
// owns backfill (so the one-shot model-loading lines are visible even when
// the drawer opens after the slot is up), idle-spam filtering, capped
// backoff reconnect, and the `degraded` frame — replacing the old inline
// EventSource with a no-op onerror.
function SlotLogsDrawer({ open, slot, onClose }) {
  const { ring, disconnected, degraded } = useSlotLogsStream(
    open && slot ? slot.name : null,
    { follow: open, max: 500 },
  );
  const lines = ring.map((r) => r.msg);

  if (!slot) return null;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      eyebrow={`Slots · /slots/${slot.name}/logs`}
      title={`Logs — ${slot.name}`}
      width={720}
      foot={
        <span style={{display: "inline-flex", gap: 8, marginLeft: "auto"}}>
          <button className="btn ghost sm" onClick={onClose}>Close</button>
        </span>
      }
    >
      {degraded && (
        <div
          className="mono"
          data-testid="slot-logs-degraded"
          style={{
            background: "var(--warn-soft)",
            border: "1px solid var(--warn-line)",
            borderRadius: "var(--rad-sm)",
            padding: "8px 10px",
            fontSize: 11.5,
            color: "var(--warn)",
            lineHeight: 1.5,
            marginBottom: 8,
          }}
        >
          {degraded}
        </div>
      )}
      <div
        className="mono"
        style={{
          background: "var(--bg)",
          border: "1px solid var(--line-soft)",
          borderRadius: "var(--rad-sm)",
          padding: 10,
          fontSize: 11.5,
          color: "var(--fg-2)",
          lineHeight: 1.5,
          height: degraded ? 414 : 460,
          overflow: "auto",
          whiteSpace: "pre-wrap",
        }}
      >
        {lines.length === 0
          ? (
            <span style={{color: "var(--fg-4)", fontStyle: "italic"}}>
              {degraded
                ? "No log lines — see the notice above."
                : disconnected
                ? "Reconnecting to log stream…"
                : "waiting for log lines…"}
            </span>
          )
          : lines.join("\n")}
      </div>
    </Drawer>
  );
}

// ─── Empty SlotCard (no model loaded) ────────────────────────────
function EmptySlotCard({ name, type, device, onConfigure }) {
  return (
    <div className="slot" style={{borderStyle: "dashed", borderColor: "var(--line)"}}>
      <div className="slot-h">
        <span className="dot empty" />
        <div className="slot-name"><span className="nm" style={{color: "var(--fg-3)"}}>{name}</span></div>
      </div>
      <div style={{padding: "8px 10px", background: "var(--bg)", border: "1px dashed var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--fg-4)", fontStyle: "italic"}}>
        no model loaded
      </div>
      <div className="slot-chips">
        <span className="chip">{type}</span>
        <span className={"chip dev-" + (device || "cpu").replace("gpu-", "")}>{device}</span>
      </div>
      <div style={{padding: "10px 12px", background: "var(--accent-soft)", border: "1px solid var(--accent-line)", borderRadius: "var(--rad-sm)", display: "flex", alignItems: "center", gap: 8}}>
        <span className="mono" style={{fontSize: 11, color: "var(--accent)", flex: 1}}>seeded · ready to configure</span>
        <button className="btn sm" onClick={onConfigure}>{Icons.plus} Configure</button>
      </div>
    </div>
  );
}

// ─── Image pull progress bar ─────────────────────────────────────
function ImagePullBar({ pull }) {
  // pull: ImagePullSnapshot from useSlotImagePull()
  const { state, layer, totalLayers, image, error } = pull;
  if (state !== "pulling" && state !== "completed" && state !== "failed") return null;
  const pct = totalLayers > 0 ? Math.round((layer / totalLayers) * 100) : null;
  // Truncate the image tag to the last segment for display.
  const imgShort = image ? image.split("/").pop() : null;
  const label =
    state === "completed" ? `Image ready` :
    state === "failed"    ? `Pull failed${error ? `: ${error}` : ""}` :
    totalLayers > 0       ? `Pulling image${imgShort ? ` ${imgShort}` : ""}… (layer ${layer}/${totalLayers})` :
                            `Pulling image${imgShort ? ` ${imgShort}` : ""}…`;
  const barColor = state === "failed" ? "var(--err)" : state === "completed" ? "var(--ok)" : "var(--accent)";
  return (
    <div style={{marginTop: 6}}>
      <div
        aria-live="polite"
        aria-label={label}
        style={{fontFamily: "var(--jbm)", fontSize: 11, color: state === "failed" ? "var(--err)" : "var(--fg-2)", marginBottom: 4}}
      >
        {label}
      </div>
      <div style={{height: 3, background: "var(--bg-2)", borderRadius: 2, overflow: "hidden"}}>
        {/* Correct ARIA pattern: omit aria-valuenow entirely while the
            layer count is unknown (indeterminate progressbar). */}
        <div
          role="progressbar"
          {...(pct !== null ? { "aria-valuenow": pct } : {})}
          aria-valuemin={0}
          aria-valuemax={100}
          style={{
            height: "100%",
            width: pct !== null ? `${pct}%` : "40%",
            background: barColor,
            borderRadius: 2,
            transition: "width 0.3s ease",
            // Indeterminate animation when layer count unknown.
            animation: pct === null && state === "pulling" ? "hal0-indeterminate 1.4s ease infinite" : "none",
          }}
        />
      </div>
    </div>
  );
}

// ─── Error SlotCard ─────────────────────────────────────────────
function ErrorSlotCardBanner({ slot, message }) {
  const pull = useSlotImagePull();
  const loadMut = useSlotLoad();
  const isPulling = pull.slotName === slot?.name && pull.inFlight;

  // Retry was toast-only. A "load failed" banner means the slot's child never
  // came up, so Retry re-attempts the load (POST /api/slots/{name}/load) —
  // the same mutation the SlotCard's Start uses. Query invalidation refreshes
  // the card on success.
  const handleRetry = async () => {
    if (!slot?.name) return;
    try {
      await loadMut.mutateAsync(slot.name);
      window.__hal0Toast && window.__hal0Toast(`Retrying load for ${slot.name}`, "info");
    } catch (err) {
      window.__hal0Toast && window.__hal0Toast(
        `Retry failed for ${slot.name}: ${err?.message || err}`, "warn"
      );
    }
  };

  const handleRePull = async () => {
    if (!slot?.name) return;
    try {
      await pull.start(slot.name);
    } catch (err) {
      window.__hal0Toast && window.__hal0Toast(
        `Re-pull failed for ${slot.name}: ${err?.message || err}`, "warn"
      );
    }
  };

  return (
    <div style={{padding: "10px 12px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", display: "flex", alignItems: "flex-start", gap: 8}}>
      <span style={{color: "var(--err)", display: "inline-flex"}}>{Icons.warn}</span>
      <div style={{flex: 1, fontFamily: "var(--jbm)", fontSize: 11.5, color: "var(--fg-2)", lineHeight: 1.5}}>
        <div style={{color: "var(--err)", fontWeight: 500, marginBottom: 2}}>load failed</div>
        <div>{message}</div>
        {(isPulling || pull.state === "completed" || pull.state === "failed") && pull.slotName === slot?.name && (
          <ImagePullBar pull={pull} />
        )}
        <div style={{display: "flex", gap: 6, marginTop: 6}}>
          <button
            className="btn ghost sm"
            disabled={loadMut.isPending}
            onClick={handleRetry}
          >{Icons.restart} {loadMut.isPending ? "Retrying…" : "Retry"}</button>
          <button
            className="btn ghost sm"
            disabled={isPulling}
            onClick={handleRePull}
            title="Re-pull the container image from the registry"
          >
            {Icons.download} {isPulling ? "Pulling…" : "Re-pull"}
          </button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { CreateSlotModal, EditSlotDrawer, InlineSwapPopover, EmptySlotCard, ErrorSlotCardBanner, SlotLogsDrawer });
