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
} from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useModels } from '@/api/hooks/useModels'
import { useProfiles } from '@/api/hooks/useProfiles'
import { useChatTemplates } from '@/api/hooks/useChatTemplates'
import { ENDPOINTS } from '@/api/endpoints'
import { stateChipClassForSlot } from './slot-status.js'

const { useState: useStateSM, useEffect: useEffectSM, useRef: useRefSM } = React;

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

// Map /api/models registry rows → the shape this file's swap popover and
// create-slot modal grew up around (HAL0_DATA seed). Done in JSX rather
// than at the API layer so the response stays identical to what the
// Models view (models.jsx) already consumes. NEVER ship HAL0_DATA model
// ids to the backend — they're fictional (`qwen3.6-27b-mtp` etc.) and
// the slot orchestrator correctly rejects them against the real registry.
function normalizeApiModel(m) {
  // Accept both shapes: the registry/API shape (capabilities + backends +
  // size_bytes + name + hf_repo) and the legacy HAL0_DATA seed shape
  // (labels + device + size + longName + repo + type). Local dev without
  // a backend falls back via src/api/mock.ts to HAL0_DATA.models, and the
  // γ-suite hits that fallback when fetch fails before page.route catches
  // (race + connection-refused on the Vite proxy target). Tolerating both
  // shapes keeps the popover non-empty in every mock path.
  const sourceCaps = Array.isArray(m.capabilities)
    ? m.capabilities
    : Array.isArray(m.labels) ? m.labels : [];
  const derivedType =
    sourceCaps.includes('chat') || sourceCaps.includes('coding') ? 'llm'
    : sourceCaps.includes('rerank') || sourceCaps.includes('reranking') ? 'reranking'
    : sourceCaps.includes('embed') || sourceCaps.includes('embeddings') ? 'embedding'
    : sourceCaps.includes('transcription') || sourceCaps.includes('asr') ? 'transcription'
    : sourceCaps.includes('tts') ? 'tts'
    : sourceCaps.includes('image') ? 'image'
    : '';
  const type = typeof m.type === 'string' && m.type ? m.type : derivedType;
  const backends = Array.isArray(m.backends) ? m.backends : [];
  const derivedDevice =
    backends.includes('rocm') ? 'rocm'
    : backends.includes('vulkan') ? 'vulkan'
    : backends.includes('cpu') ? 'cpu'
    : backends[0] || '';
  const device = typeof m.device === 'string' && m.device ? m.device : derivedDevice;
  const b = m.size_bytes || 0;
  const derivedSize = !b
    ? '—'
    : b < 1024 ** 2 ? `${(b / 1024).toFixed(1)} KB`
    : b < 1024 ** 3 ? `${(b / 1024 ** 2).toFixed(1)} MB`
    : `${(b / 1024 ** 3).toFixed(2)} GB`;
  const size = typeof m.size === 'string' && m.size ? m.size : derivedSize;
  return {
    ...m,
    type,
    device,
    longName: m.longName || m.name || m.id,
    size,
    repo: m.repo || m.hf_repo || m.path || '',
  };
}

// ─── Create-slot modal ──────────────────────────────────────────
function CreateSlotModal({ open, onClose, defaults = {}, existingSlots = [] }) {
  const [name, setName] = useStateSM(defaults.name || "");
  const [type, setType] = useStateSM(defaults.type || "llm");
  const [profile, setProfile] = useStateSM(defaults.profile || "");
  const [model, setModel] = useStateSM(defaults.model || "");
  const [makeDefault, setMakeDefault] = useStateSM(false);
  const [submitErr, setSubmitErr] = useStateSM(null);

  const createMut = useSlotCreate();
  const hwQuery = useHardware();
  const modelsQuery = useModels();
  const profilesQuery = useProfiles();

  useEffectSM(() => {
    if (open) {
      setName(defaults.name || "");
      setType(defaults.type || "llm");
      setProfile(defaults.profile || "");
      setModel("");
      setMakeDefault(false);
      setSubmitErr(null);
    }
  }, [open, defaults]);

  // validation — slot collision uses the live slot list passed in from
  // the SlotsView (useSlots data), not HAL0_DATA.
  const existing = (existingSlots || []).map(s => s.name);
  const nameCollision = existing.includes(name);
  const nameInvalid = name && !/^[a-z][a-z0-9-]{0,30}$/.test(name);
  const nameError = nameCollision ? "name already in use" : nameInvalid ? "lowercase + dashes only" : null;

  // Live catalogue from /api/models (normalized to the legacy HAL0_DATA
  // shape so the existing filter + render code keeps working). Sending a
  // mock id like `qwen3.6-27b-mtp` here would tunnel into POST
  // /api/slots/{name}/swap and the slot orchestrator would reject it
  // against the real registry (slot.not_found).
  const allModels = (modelsQuery.data ?? []).map(normalizeApiModel);
  const allProfiles = profilesQuery.data ?? [];
  // Any model of the right type works — the profile's image determines
  // the backend, not a device selector.
  const compatible = allModels.filter(m => m.type === type);

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
    <Modal
      open={open}
      onClose={onClose}
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
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
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
            <option value="llm">llm</option>
            <option value="embedding">embedding</option>
            <option value="reranking">reranking</option>
            <option value="transcription">transcription</option>
            <option value="tts">tts</option>
            <option value="image">image</option>
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
          {model && compatible.find(m => m.id === model) && (
            <div className="ok">✓ fits in available memory ({hwQuery.data?.ram?.free ?? "?"} GB free)</div>
          )}
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
  // class as #584). llamacpp_args / n_gpu_layers / rope_freq_base are
  // surfaced on the list payload and rendered read-only (the profile
  // owns them for container slots).
  const initialExtraArgs = slot?.llamacpp_args != null ? slot.llamacpp_args : "";

  const [ctx, setCtx] = useStateSM(slot?.metrics?.ctx || 16384);
  // C4/C5: thinking is instant-apply (its own PUT); n_gpu_layers rides the Save
  // button through PATCH /defaults. Both seed from the slot list payload.
  const [thinking, setThinking] = useStateSM(slot?.enable_thinking === true);
  const [thinkingPending, setThinkingPending] = useStateSM(false);
  const [nGpuLayers, setNGpuLayers] = useStateSM(
    slot?.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1"
  );
  // Issue #548: rope_freq_base — seeded from list payload (null → "0" default).
  const [ropeFreqBase, setRopeFreqBase] = useStateSM(
    slot?.rope_freq_base != null ? String(slot.rope_freq_base) : "0"
  );
  const [extraArgs, setExtraArgs] = useStateSM(initialExtraArgs);
  const [submitErr, setSubmitErr] = useStateSM(null);
  // Enable/disable is instant-apply via its own PUT (mirrors the slot card's
  // pill toggle, which the redesigned cards dropped). `enableBusy` gates the
  // header toggle against a double-trigger while the mutation is in flight.
  const [enableBusy, setEnableBusy] = useStateSM(false);
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

  useEffectSM(() => {
    if (slot) {
      setCtx(slot.metrics?.ctx || 16384);
      setThinking(slot.enable_thinking === true);
      setThinkingPending(false);
      setNGpuLayers(slot.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1");
      setRopeFreqBase(slot.rope_freq_base != null ? String(slot.rope_freq_base) : "0");
      // #587: re-seed from the slot prop so the drawer tracks the real
      // on-disk values.
      setExtraArgs(slot.llamacpp_args != null ? slot.llamacpp_args : "");
      setSubmitErr(null);
      setThinkingErr(null);
      setFieldErrs({});
      // C7: re-seed profile from the (possibly-updated) slot prop.
      setSelectedProfile(slot.profile || "");
      // Task 5: re-seed chat_template override from the slot prop.
      setChatTemplate(slot.chat_template || "");
      setOverrideOpen(!!(slot.chat_template));
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
    try {
      // Two-step: defaults (ctx_size lives under [model]) + slot config
      // for the top-level keys (default, profile). n_gpu_layers /
      // rope_freq_base / llamacpp_args are owned by the profile — never
      // include them in a save. These are fast on-disk writes, so we await
      // them and keep the drawer open to surface any write error.
      const ctxBody = {
        ctx_size: ctxNum,
      };
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
      await defaultsMut.mutateAsync({
        name: slot.name,
        body: ctxBody,
      });
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

  async function onDeleteClick() {
    if (!window.confirm(`Delete slot "${slot.name}"?`)) return;
    setSubmitErr(null);
    try {
      await deleteMut.mutateAsync(slot.name);
      window.__hal0Toast && window.__hal0Toast(`Slot "${slot.name}" deleted`, "ok");
      onClose();
    } catch (err) {
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

  return (
    <Drawer
      open={open}
      onClose={onClose}
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
            onClick={onDeleteClick}
          >{Icons.unload} {deleting ? "Deleting…" : "Delete slot"}</button>
          <span style={{display: "inline-flex", gap: 8, alignItems: "center"}}>
            {submitErr && <span style={{color: "var(--err)", fontSize: 11}}>{submitErr}</span>}
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
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
        const compatible = (modelsQuery.data ?? [])
          .map(normalizeApiModel)
          .filter(m =>
            m.type === slot.type &&
            // ROCmFP4-quantized models only run on the rocm fork binary — hide
            // them when the selected profile isn't on the rocm backend.
            !(Array.isArray(m.tags) && m.tags.includes("rocmfp4") && selBackend !== "rocm")
          );
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
                  setSubmitErr(null);
                  const picked = compatible.find(m => m.id === id);
                  const label = picked?.longName || id;
                  // Non-blocking: a swap cold-restarts container slots to load
                  // the model (slow). Fire it and let the slots poll reflect the
                  // transitional phase — never freeze the drawer on the load.
                  swapMut.mutate({ name: slot.name, model_id: id }, {
                    onError: (err) => setSubmitErr(err?.message || "model swap failed"),
                  });
                  window.__hal0Toast && window.__hal0Toast(
                    isContainer
                      ? `Restarting ${slot.name} to load ${label} — loading in the background`
                      : `${slot.name} → ${label}`,
                    "info",
                  );
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
                    {templates.map(t => (
                      <option key={t.id} value={t.id}>{t.label || t.id}</option>
                    ))}
                  </select>
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
      {/* Task 2: MTP pill — capability-gated, rocm-only.
          Renders ONLY when the slot's loaded model has the "mtp" tag AND
          the slot's backend is "rocm". Toggle is instant-apply
          via PUT /config (editMut) + non-blocking restart (mirrors the
          profile-change pattern above). */}
      {(() => {
        const cur = slot.model_id || slot.model || "";
        const m = (modelsQuery.data ?? []).map(normalizeApiModel).find(x => x.id === cur);
        const mtpCapable = Array.isArray(m?.tags) && m.tags.includes("mtp");
        // Gate on `backend` — the authoritative slot field the API emits.
        // `device` ("gpu-rocm") is a client-side convenience synthesized by
        // normalizeSlot from backend and is ABSENT on the raw slot shape, so
        // keying off it alone is fragile; the device check stays only as a
        // defensive fallback for any path that bypasses the normalizer.
        const isRocm = slot.backend === "rocm" || String(slot.device || "").startsWith("gpu-rocm");
        if (!mtpCapable || !isRocm) return null;
        const mtpOn = slot.mtp === true;
        return (
          <div className="form-row">
            <div className="form-lbl">
              <span>MTP</span>
              <span className="sub">Multi-token speculative decoding — dense models only (MoE runs slower). Restarts the container.</span>
            </div>
            <div className="form-ctl">
              <PillToggle
                on={mtpOn}
                disabled={saving}
                label="MTP"
                stateText={mtpOn ? "On" : "Off"}
                onToggle={async (next) => {
                  setSubmitErr(null);
                  try {
                    await editMut.mutateAsync({ name: slot.name, body: { mtp: next } });
                    restartMut.mutate(slot.name, {
                      onError: (err) => window.__hal0Toast && window.__hal0Toast(`MTP restart failed — ${err?.message || "see logs"}`, "err"),
                    });
                    window.__hal0Toast && window.__hal0Toast(`${slot.name} MTP ${next ? "on" : "off"} — restarting in the background`, "info");
                  } catch (err) {
                    setSubmitErr(err?.message || "MTP toggle failed");
                  }
                }}
              />
            </div>
          </div>
        );
      })()}
      </FieldGroup>

      {/* Task 4: Advanced fields (mostly read-only, profile-owned) are
          collapsed by default — minimal native <details> disclosure (no
          disclosure primitive exists in primitives.jsx). */}
      <details className="adv-disclosure">
      <summary className="form-section" style={{cursor: "pointer", listStyle: "revert"}}>Advanced</summary>

      {/* C5: GPU offload tuning — read-only, defined by the profile. */}
      <div className="form-row">
        <div className="form-lbl">
          <span>n_gpu_layers</span>
          <span className="sub">defined by profile {slot.profile}</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" value={nGpuLayers} readOnly />
        </div>
      </div>

      {/* Issue #548: rope_freq_base — read-only, defined by the profile. */}
      <div className="form-row">
        <div className="form-lbl">
          <span>rope_freq_base</span>
          <span className="sub">defined by profile {slot.profile}</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" value={ropeFreqBase} readOnly />
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
          override and refetches the freshly-resolved command. */}
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
          {Array.isArray(slot.resolved_command)
            ? slot.resolved_command.join(" \\\n  ")
            : slot.resolved_command || "— not yet available (slot not loaded)"}
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
      <div className="hint" style={{paddingTop: 6, fontSize: 10.5, color: "var(--fg-5)", fontFamily: "var(--jbm)"}}>
        Real podman argv: profile image + flags, then slot extra_args (slot wins). Restart the slot to run with new flags.
      </div>
      </details>
    </Drawer>
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
  if (!open) return null;

  const isContainer = slot.runtime === "container";
  const ramFreeGb = hwQuery.data?.ram?.free ?? 0;
  const compatible = (modelsQuery.data ?? [])
    .map(normalizeApiModel)
    .filter(m =>
      m.type === slot.type &&
      // ROCmFP4-quantized models only run on the custom rocm fork binary —
      // don't offer them when swapping a non-rocm slot.
      !(Array.isArray(m.tags) && m.tags.includes("rocmfp4") && slot.backend !== "rocm")
    );

  // N2: container swap = cold systemctl restart (not a hot in-place swap).
  // Intercept onPick for container slots: show a confirm toast and fire
  // the same onPick (which drives restart), so the parent card drives to
  // "starting" state immediately. The parent's onSwapPick calls useSlotSwap
  // which triggers a restart for container slots server-side.
  const handlePick = (m) => {
    if (isContainer) {
      const name = slot.name;
      const label = m.longName || m.id;
      window.__hal0Toast && window.__hal0Toast(
        `Restarting ${name} to load ${label} — ~model-load seconds`,
        "info"
      );
    }
    onPick(m);
    onClose();
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
    </div>
  );
}

// ─── Slot logs drawer ────────────────────────────────────────────
// Minimal SSE-backed log tail. The slot-logs stream endpoint
// (ENDPOINTS.slotLogsStream) emits one JSON-lines event per log line;
// we render the last N in a fixed-height pre. EventSource closes
// automatically when the drawer unmounts.
function SlotLogsDrawer({ open, slot, onClose }) {
  const [lines, setLines] = useStateSM([]);
  // B13: when journalctl is unavailable the backend emits a NAMED
  // `event: degraded` SSE frame instead of streaming lines. Surfacing it
  // tells the user *why* there are no lines, instead of spinning forever
  // on "waiting for log lines…".
  const [degraded, setDegraded] = useStateSM(null);
  const esRef = useRefSM(null);

  useEffectSM(() => {
    if (!open || !slot) {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      setLines([]);
      setDegraded(null);
      return;
    }
    setLines([]);
    setDegraded(null);
    try {
      const es = new EventSource(ENDPOINTS.slotLogsStream(slot.name));
      esRef.current = es;
      es.onmessage = (ev) => {
        setLines(prev => {
          const next = prev.concat(ev.data);
          return next.length > 500 ? next.slice(next.length - 500) : next;
        });
      };
      // Named "degraded" frame — journalctl unavailable for this slot.
      // Parse the payload for a human reason; fall back to a generic note.
      es.addEventListener("degraded", (ev) => {
        let reason = "Log streaming unavailable (journalctl not reachable for this slot).";
        try {
          const data = JSON.parse(ev.data);
          if (data && (data.message || data.reason || data.detail)) {
            reason = data.message || data.reason || data.detail;
          }
        } catch {
          if (typeof ev.data === "string" && ev.data.trim()) reason = ev.data;
        }
        setDegraded(reason);
      });
      es.onerror = () => {
        // Leave the stream open — server can resume; drawer close cleans up.
      };
    } catch {
      // EventSource missing or blocked — log drawer renders empty.
    }
    return () => {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
    };
  }, [open, slot?.name]);

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
              {degraded ? "No log lines — see the notice above." : "waiting for log lines…"}
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
        <div
          role="progressbar"
          aria-valuenow={pct ?? 0}
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
