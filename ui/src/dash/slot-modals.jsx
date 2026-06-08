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
  useSlotBackend,
} from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useBackends } from '@/api/hooks/useBackends'
import { useModels } from '@/api/hooks/useModels'
import { useProfiles } from '@/api/hooks/useProfiles'
import { ENDPOINTS } from '@/api/endpoints'
import { stateChipClassForSlot } from './slot-status.js'

// Full static device list — shown as fallback when /api/backends hasn't
// loaded yet or returns empty. Never render an empty device dropdown.
const DEVICE_STATIC = ['gpu-rocm', 'gpu-vulkan', 'cpu', 'npu']

// Map a backend id (e.g. "llamacpp:rocm", "llamacpp:vulkan", "flm:npu",
// "llamacpp:cpu") to its slot device token.
function backendToDevice(id) {
  const s = String(id || '').toLowerCase()
  if (s.includes('rocm'))   return 'gpu-rocm'
  if (s.includes('vulkan')) return 'gpu-vulkan'
  if (s.includes('npu') || s.includes('flm')) return 'npu'
  if (s.includes('cpu'))    return 'cpu'
  return null
}

const { useState: useStateSM, useEffect: useEffectSM, useRef: useRefSM } = React;

// Map a slot lifecycle state to a chip color class.
//   online/ready/serving → green (ok); starting → amber (warn);
//   error → red (err); offline/empty/anything else → neutral grey (base chip).
//
// N1: accepts either a state string (lemond path, unchanged) or a full slot
// object. When given a slot object, delegates to stateChipClassForSlot()
// from slot-status.js which handles container runtime correctly via
// slotPhase(). The primitive string overload is kept for call sites that
// only have the state string — its behaviour is unchanged.
function stateChipClass(stateOrSlot) {
  // Duck-type: if it's a string, keep original behaviour (lemond path).
  if (typeof stateOrSlot === "string" || stateOrSlot == null) {
    // STRING path = lemond, byte-identical to origin/main. Do NOT add
    // warming/pulling/crashed here — that recolored lemond state strips
    // (e.g. state="warming" must stay grey at the EditSlotDrawer strip).
    // Container chips route through the slot-OBJECT overload only.
    const s = String(stateOrSlot || "").toLowerCase();
    if (["ready", "online", "loaded", "serving", "running"].includes(s)) return "chip ok";
    if (["starting", "loading", "pending", "stopping"].includes(s)) return "chip warn";
    if (["error", "failed", "broken"].includes(s)) return "chip err";
    return "chip"; // offline / warming / empty / unconfigured → neutral grey
  }
  // Full slot object: delegate to the shared N1 helper.
  // stateChipClassForSlot returns null for lemond slots (sentinel),
  // in which case we fall back to the original string-based path.
  const slot = stateOrSlot;
  const fromPhase = stateChipClassForSlot(slot);
  if (fromPhase !== null) return fromPhase;
  return stateChipClass(slot.state);
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
  const [device, setDevice] = useStateSM(defaults.device || "gpu-rocm");
  const [runtime, setRuntime] = useStateSM(defaults.runtime || "lemonade");
  const [profile, setProfile] = useStateSM(defaults.profile || "");
  const [model, setModel] = useStateSM(defaults.model || "");
  const [group, setGroup] = useStateSM(defaults.group || "chat");
  const [advOpen, setAdvOpen] = useStateSM(false);
  const [makeDefault, setMakeDefault] = useStateSM(false);
  const [ctx, setCtx] = useStateSM(8192);
  const [extraArgs, setExtraArgs] = useStateSM("--flash-attn on");
  const [submitErr, setSubmitErr] = useStateSM(null);

  const createMut = useSlotCreate();
  const hwQuery = useHardware();
  const backendsQuery = useBackends();
  const modelsQuery = useModels();
  const profilesQuery = useProfiles();

  // Device options: derived from installed backends in /api/backends.
  // cpu is always runnable — force-add it whenever we have real backend data.
  // Fallback to DEVICE_STATIC when data is absent/loading/empty so the
  // dropdown is never empty.
  const backendsData = backendsQuery.data;
  const haveBackends = (backendsData?.backends?.length ?? 0) > 0;
  const deviceOptions = (() => {
    if (!haveBackends) return DEVICE_STATIC;
    const installed = (backendsData.backends || []).filter(b => b.state === 'installed');
    const avail = new Set(installed.map(b => backendToDevice(b.id)).filter(Boolean));
    avail.add('cpu'); // always runnable on the host
    return DEVICE_STATIC.filter(d => avail.has(d));
  })();

  useEffectSM(() => {
    if (open) {
      setName(defaults.name || "");
      setType(defaults.type || "llm");
      setDevice(defaults.device || "gpu-rocm");
      setRuntime(defaults.runtime || "lemonade");
      setProfile(defaults.profile || "");
      setGroup(defaults.group || "chat");
      setModel("");
      setAdvOpen(false);
      setMakeDefault(false);
      setSubmitErr(null);
    }
  }, [open, defaults]);

  // Reconcile selected device with the derived option list: if the current
  // selection isn't in the available set (e.g. rocm not installed), snap to
  // the first available device rather than silently POSTing an invalid one.
  useEffectSM(() => {
    if (deviceOptions.length && !deviceOptions.includes(device)) {
      setDevice(deviceOptions[0]);
    }
  }, [deviceOptions.join(','), device]);

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
  const isContainerSlot = runtime === "container";
  const compatible = allModels.filter(m => {
    if (m.type !== type) return false;
    // Container slots: any model of the right type works (the profile's
    // image determines the backend, not the device selector).
    if (isContainerSlot) return true;
    // ROCmFP4-quantized models only run on the custom rocm fork binary
    // (lemonade rocm_bin) — never offer them for vulkan / npu / cpu slots.
    if (Array.isArray(m.tags) && m.tags.includes("rocmfp4") && device !== "gpu-rocm") return false;
    return device === "cpu"
      || (Array.isArray(m.backends) && m.backends.includes((device || "cpu").replace("gpu-", "")))
      || (device === "npu" && m.device === "npu");
  });

  const npuAvailable = !!hwQuery.data?.npu?.present;
  const canSave = !!name && !nameError && !createMut.isPending &&
    (!isContainerSlot || !!profile);

  // Next available port after the highest currently-allocated
  const nextPort = Math.max(8090, ...((existingSlots || []).map(s => s.port || 8090))) + 1;

  async function onCreateClick() {
    setSubmitErr(null);
    const body = {
      name,
      type,
      ...(isContainerSlot
        ? { runtime: "container", profile, device: "gpu-rocm" }
        : { device }),
      group,
      ...(model ? { model } : {}),
      ...(makeDefault ? { default: true } : {}),
      ...(advOpen && !isContainerSlot
        ? {
            model: {
              ...(model ? { default: model } : {}),
              ctx_size: Number(ctx) || ctx,
            },
            llamacpp_args: extraArgs,
          }
        : {}),
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
          <span>Runtime <span className="req">*</span></span>
          <span className="sub">container = podman-managed iGPU image · lemonade = Lemonade-managed</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={runtime} onChange={e => { setRuntime(e.target.value); setModel(""); }}>
            <option value="lemonade">lemonade</option>
            <option value="container">container</option>
          </select>
        </div>
      </div>

      {isContainerSlot ? (
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
      ) : (
        <div className="form-row">
          <div className="form-lbl">
            <span>Device <span className="req">*</span></span>
            <span className="sub">{!npuAvailable && device === "npu" ? <span style={{color: "var(--warn)"}}>NPU disabled — FLM not installed</span> : "hardware preference for this slot"}</span>
          </div>
          <div className="form-ctl">
            <select className="input mono" value={device} onChange={e => setDevice(e.target.value)}>
              {deviceOptions.map(d => (
                <option key={d} value={d} disabled={d === 'npu' && !npuAvailable}>
                  {d === 'npu' && !npuAvailable ? 'npu — install FLM first' : d}
                </option>
              ))}
            </select>
          </div>
        </div>
      )}

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
          <span className="mono" style={{padding: "6px 10px", background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", display: "inline-block", color: "var(--fg-3)", fontSize: 12}}>:{nextPort}</span>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Group</span>
          <span className="sub">pure UI rollup label</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={group} onChange={e => setGroup(e.target.value)}>
            <option value="chat">chat</option>
            <option value="embed">embed</option>
            <option value="voice">voice</option>
            <option value="img">img</option>
            <option value="custom">custom</option>
          </select>
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

      <div className="form-section" onClick={() => setAdvOpen(v => !v)} style={{cursor: "pointer", display: "flex", alignItems: "center", gap: 8}}>
        <span style={{transform: advOpen ? "rotate(90deg)" : "rotate(0deg)", transition: "transform 0.15s ease"}}>{Icons.chevR}</span>
        <span>Recipe options</span>
        <span style={{marginLeft: "auto", color: "var(--fg-5)", letterSpacing: 0, textTransform: "none"}}>{advOpen ? "collapse" : "expand"}</span>
      </div>
      {advOpen && (
        <>
          <div className="form-row">
            <div className="form-lbl">
              <span>ctx_size</span>
              <span className="warn">⟳ restart required</span>
            </div>
            <div className="form-ctl">
              <input className="input mono" value={ctx} onChange={e => setCtx(Number(e.target.value))} />
            </div>
          </div>
          <div className="form-row">
            <div className="form-lbl">
              <span>llamacpp_args</span>
              <span className="sub">merged with --parallel 1 --threads 8 baseline</span>
            </div>
            <div className="form-ctl">
              <input className="input mono" value={extraArgs} onChange={e => setExtraArgs(e.target.value)} />
              <div className="hint">Denied flags: <span className="mono">-m / --port / --ctx-size / -ngl / --jinja / --mmproj / --embeddings / --reranking</span></div>
            </div>
          </div>
        </>
      )}
    </Modal>
  );
}

// ─── Edit-slot drawer ───────────────────────────────────────────
function EditSlotDrawer({ open, slot, onClose }) {
  // Hooks must execute every render — early `return null` would skip
  // them; render the drawer shell with a sentinel slot instead.
  const editMut = useSlotEdit();
  const defaultsMut = useSlotDefaults();
  const deleteMut = useSlotDelete();
  const backendMut = useSlotBackend();

  // Seed from the slot list payload when available (PR #587 — same fix
  // class as #584). idle_timeout_s / workers / llamacpp_args are all
  // surfaced on the list payload now, so the drawer mirrors them
  // verbatim and only sends what actually changed. When the payload
  // is missing the field (older backend, or synthetic slot), the
  // schema defaults act as a fallback for first-time edits only.
  const initialIdle = slot?.idle_timeout_s != null ? slot.idle_timeout_s : 900;
  const initialWorkers = slot?.workers != null ? slot.workers : 1;
  const initialExtraArgs = slot?.llamacpp_args != null ? slot.llamacpp_args : "";
  const initialRope = slot?.rope_freq_base != null ? slot.rope_freq_base : 0;

  const [ctx, setCtx] = useStateSM(slot?.metrics?.ctx || 4096);
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
  const [idleTimeout, setIdleTimeout] = useStateSM(initialIdle);
  const [workers, setWorkers] = useStateSM(initialWorkers);
  const [extraArgs, setExtraArgs] = useStateSM(initialExtraArgs);
  const [device, setDevice] = useStateSM(slot?.device || "gpu-rocm");
  const [makeDefault, setMakeDefault] = useStateSM(!!slot?.isDefault);
  const [submitErr, setSubmitErr] = useStateSM(null);
  // Per-field validation errors for numeric inputs (#548).
  const [fieldErrs, setFieldErrs] = useStateSM({});
  // Runtime Backend selector (ADR-0022). Seeded from the DECLARED backend
  // token (bare rocm|vulkan|cpu|flm), falling back to the gpu-stripped
  // device. The selector itself only offers the selectable backends.
  const [selectedBackend, setSelectedBackend] = useStateSM(
    slot?.declared_backend || (slot?.device || "gpu-rocm").replace("gpu-", "") || "rocm"
  );
  const [backendSwitchPending, setBackendSwitchPending] = useStateSM(false);

  useEffectSM(() => {
    if (slot) {
      setCtx(slot.metrics?.ctx || 4096);
      setThinking(slot.enable_thinking === true);
      setThinkingPending(false);
      setNGpuLayers(slot.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1");
      setRopeFreqBase(slot.rope_freq_base != null ? String(slot.rope_freq_base) : "0");
      setDevice(slot.device || "gpu-rocm");
      setMakeDefault(!!slot.isDefault);
      // #587: re-seed from the slot prop so the drawer tracks the real
      // on-disk values (was hardcoded constants before — that was the
      // bug). The dirty-tracking in onSaveClick below only ships fields
      // that actually changed, so a no-op edit no longer rewrites
      // anything.
      setIdleTimeout(slot.idle_timeout_s != null ? slot.idle_timeout_s : 900);
      setWorkers(slot.workers != null ? slot.workers : 1);
      setExtraArgs(slot.llamacpp_args != null ? slot.llamacpp_args : "");
      setSubmitErr(null);
      setFieldErrs({});
      setSelectedBackend(
        slot.declared_backend || (slot.device || "gpu-rocm").replace("gpu-", "") || "rocm"
      );
      setBackendSwitchPending(false);
    }
  }, [slot?.name]);

  if (!slot) return null;

  async function onSaveClick() {
    setSubmitErr(null);
    // Issue #548: validate numeric fields before any network call.
    // Invalid values surface inline and block Save.
    const ctxNum = Number(ctx);
    const nglNum = Number(nGpuLayers);
    const ropeNum = Number(ropeFreqBase);
    const errs = {};
    if (!Number.isFinite(ctxNum) || !Number.isInteger(ctxNum) || ctxNum < 128) {
      errs.ctx = "Must be an integer ≥ 128";
    }
    if (!Number.isFinite(nglNum) || !Number.isInteger(nglNum) || nglNum < -1) {
      errs.ngl = "Must be an integer ≥ -1 (use -1 to offload all layers)";
    }
    if (!Number.isFinite(ropeNum) || ropeNum < 0) {
      errs.rope = "Must be a number ≥ 0 (0 = use model default)";
    }
    if (Object.keys(errs).length > 0) {
      setFieldErrs(errs);
      return;
    }
    setFieldErrs({});
    try {
      // Two-step: defaults (ctx_size / n_gpu_layers / rope_freq_base live
      // under [model]) + slot config for the top-level keys (device,
      // llamacpp_args, idle_timeout_s, workers, default).
      const idleNum = Number(idleTimeout);
      const workersNum = Number(workers);
      // #587 dirty-tracking: only include a field in the body if the
      // user actually changed it from the seeded (on-disk) value.
      // Sending every field unconditionally is what clobbered values
      // before — same fix class as #584. ctx_size / n_gpu_layers stay
      // unconditional because the drawer's seed is best-effort
      // (metrics?.ctx / -1 sentinel) and NOT the truth source.
      const isContainerSave = slot.runtime === "container";
      const ctxBody = {
        ctx_size: ctxNum,
        // n_gpu_layers is defined by the profile for container slots — don't overwrite
        ...(isContainerSave ? {} : { n_gpu_layers: nglNum }),
      };
      // rope_freq_base is dirty-tracked (seed = real on-disk value).
      // Container slots: profile owns rope_freq_base — skip.
      if (!isContainerSave && Number(ropeFreqBase) !== Number(initialRope)) {
        ctxBody.rope_freq_base = ropeNum;
      }
      const slotBody = {
        // device selector is hidden for container slots — don't overwrite (profile picks GPU config)
        ...(isContainerSave ? {} : { device }),
        default: makeDefault,
      };
      const idleSeeded = initialIdle;
      const workersSeeded = initialWorkers;
      const extraArgsSeeded = initialExtraArgs;
      // Container slots: idle_timeout_s / workers / llamacpp_args are hidden in the UI
      // and owned by the profile — never include them in a container save.
      if (!isContainerSave && Number(idleTimeout) !== Number(idleSeeded)) {
        slotBody.idle_timeout_s = Number.isFinite(idleNum) ? idleNum : idleTimeout;
      }
      if (!isContainerSave && Number(workers) !== Number(workersSeeded)) {
        slotBody.workers = Number.isFinite(workersNum) ? workersNum : workers;
      }
      if (!isContainerSave && extraArgs !== extraArgsSeeded) {
        slotBody.llamacpp_args = extraArgs;
      }
      await defaultsMut.mutateAsync({
        name: slot.name,
        body: ctxBody,
      });
      await editMut.mutateAsync({
        name: slot.name,
        body: slotBody,
      });
      window.__hal0Toast && window.__hal0Toast(
        `Slot "${slot.name}" saved — restart required for ctx_size`,
        "warn",
      );
      onClose();
    } catch (err) {
      setSubmitErr(err?.message || "save failed");
    }
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

  const saving = editMut.isPending || defaultsMut.isPending;
  const deleting = deleteMut.isPending;

  return (
    <Drawer
      open={open}
      onClose={onClose}
      eyebrow={`Slots · /slots/${slot.name}`}
      title={`Edit ${slot.name}`}
      width={560}
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
      {/* Provider + port strip — read-only.
          Container slots show image tag instead of "lemonade". */}
      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 0, border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", overflow: "hidden", marginBottom: 16}}>
        {slot.runtime === "container"
          ? <ReadOnlyStrip k="image" v={slot.image ? slot.image.split(':').pop() : slot.profile || "—"} />
          : <ReadOnlyStrip k="provider" v="lemonade" />
        }
        <ReadOnlyStrip k="port" v={`:${slot.port || "—"}`} />
        <ReadOnlyStrip k="state" v={<span className={stateChipClass(slot.state)}>{slot.state}</span>} />
      </div>

      {/* Declared vs actual backend (ADR-0022). Container slots show
          profile + image_status instead; lemonade slots keep the declared/actual
          backend pair. */}
      {slot.runtime === "container" ? (
        <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0, border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", overflow: "hidden", marginBottom: 16}}>
          <ReadOnlyStrip k="profile" v={slot.profile || "—"} />
          <ReadOnlyStrip k="image status" v={slot.image_status || "present"} />
        </div>
      ) : (
        <>
          <div style={{display: "grid", gridTemplateColumns: "1fr 1fr", gap: 0, border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", overflow: "hidden", marginBottom: 16}}>
            <ReadOnlyStrip k="declared backend" v={slot.declared_backend || device.replace("gpu-", "") || "—"} />
            <ReadOnlyStrip k="actual backend" v={slot.actual_backend || "—"} />
          </div>

          {/* Mismatch banner — rendered ONLY on the backend-computed flag. */}
          {slot.backend_mismatch && (
            <div style={{padding: 10, background: "var(--warn-soft)", border: "1px solid var(--warn-line)", borderRadius: "var(--rad-sm)", marginBottom: 12, fontSize: 11, color: "var(--fg-2)"}}>
              ⚠ Backend mismatch: declared <b>{slot.declared_backend || device.replace("gpu-", "")}</b> but running <b>{slot.actual_backend}</b>. Pick a backend below and Apply to reload under the declared backend.
            </div>
          )}
        </>
      )}

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

      {slot.runtime === "container" ? (
        /* Container slots: profile is the configuration surface.
           Device + Runtime Backend selectors are replaced with a read-only
           profile display — flags are baked into the profile image. */
        <div className="form-row">
          <div className="form-lbl">
            <span>Profile</span>
            <span className="sub">image + bench-tuned flags for this slot — set in profiles.toml</span>
          </div>
          <div className="form-ctl">
            <input className="input mono" value={slot.profile || "—"} readOnly />
            {slot.image && (
              <div className="hint mono">{slot.image}</div>
            )}
          </div>
        </div>
      ) : (
        <>
          <div className="form-row">
            <div className="form-lbl"><span>Device</span><span className="warn">⟳ restart required</span></div>
            <div className="form-ctl">
              <select
                className="input mono"
                value={device}
                onChange={e => setDevice(e.target.value)}
              >
                <option value="gpu-rocm">gpu-rocm</option>
                <option value="gpu-vulkan">gpu-vulkan</option>
                <option value="cpu">cpu</option>
                <option value="npu">npu</option>
              </select>
            </div>
          </div>

          {/* Runtime Backend (ADR-0022) — its own mutation (POST
              /api/slots/{name}/backend). Disabled for cpu/npu devices, where
              the backend is not selectable. Apply writes `device` to the TOML
              and reloads the slot when loaded. Distinct from the Save button,
              which never touches backend. */}
          {(() => {
            const dev = device.replace("gpu-", "");
            const selectable = dev === "rocm" || dev === "vulkan";
            const declaredToken = slot.declared_backend || dev || "rocm";
            const unchanged = selectedBackend === declaredToken;
            return (
              <div className="form-row">
                <div className="form-lbl">
                  <span>Runtime Backend</span>
                  <span className="sub">{selectable ? "select + apply to reload under a different llama.cpp build" : "not selectable for this device"}</span>
                </div>
                <div className="form-ctl">
                  <div style={{display: "flex", gap: 8, alignItems: "center"}}>
                    <select
                      className="input mono"
                      value={selectedBackend}
                      onChange={e => setSelectedBackend(e.target.value)}
                      disabled={!selectable || backendSwitchPending}
                    >
                      <option value="rocm">rocm</option>
                      <option value="vulkan">vulkan</option>
                      <option value="auto">auto (global default)</option>
                    </select>
                    <button
                      className="btn ghost sm"
                      disabled={!selectable || backendSwitchPending || unchanged}
                      onClick={async () => {
                        setBackendSwitchPending(true);
                        setSubmitErr(null);
                        try {
                          await backendMut.mutateAsync({
                            name: slot.name,
                            backend: selectedBackend,
                          });
                          window.__hal0Toast && window.__hal0Toast(
                            `${slot.name} backend → ${selectedBackend}${slot.actual_backend ? " — reloading" : ""}`,
                            "ok",
                          );
                        } catch (err) {
                          setSubmitErr(err?.message || "backend switch failed");
                        } finally {
                          setBackendSwitchPending(false);
                        }
                      }}
                    >{backendSwitchPending ? "Applying…" : "Apply"}</button>
                  </div>
                  <div className="hint">If the slot is loaded, applying reloads it under the new backend. The current backend stays in VRAM until the reload completes.</div>
                </div>
              </div>
            );
          })()}
        </>
      )}

      <div className="form-row">
        <div className="form-lbl"><span>Model</span><span className="sub">use inline swap from the card for live changes</span></div>
        <div className="form-ctl">
          <input className="input mono" value={slot.modelLong || slot.model} readOnly />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Default for type {slot.type}?</span>
          <span className="sub">{slot.isDefault ? "currently default" : "another slot is default"}</span>
        </div>
        <div className="form-ctl">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={makeDefault}
              onChange={e => setMakeDefault(e.target.checked)}
            />
            <span>Set as default</span>
          </label>
        </div>
      </div>

      {/* C4: per-slot thinking default — llm slots only. Instant-apply (its
          own PUT /config), no restart: _slot_thinking_default reads it live
          on the next request. */}
      {slot.type === "llm" && (
        <div className="form-row">
          <div className="form-lbl">
            <span>Thinking</span>
            <span className="sub">Stream reasoning before the answer. Off = faster, direct replies.</span>
          </div>
          <div className="form-ctl">
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={thinking}
                disabled={thinkingPending}
                onChange={async (e) => {
                  const next = e.target.checked;
                  setThinking(next);
                  setThinkingPending(true);
                  setSubmitErr(null);
                  try {
                    await editMut.mutateAsync({
                      name: slot.name,
                      body: { enable_thinking: next },
                    });
                    window.__hal0Toast && window.__hal0Toast(
                      `${slot.name} thinking ${next ? "on" : "off"} — applies to next message`,
                      "ok",
                    );
                  } catch (err) {
                    setThinking(!next); // revert on failure
                    setSubmitErr(err?.message || "thinking toggle failed");
                  } finally {
                    setThinkingPending(false);
                  }
                }}
              />
              <span>{thinking ? "Reasoning on" : "Reasoning off"}</span>
            </label>
          </div>
        </div>
      )}

      <div className="form-section">Advanced</div>

      <div className="form-row">
        <div className="form-lbl">
          <span>ctx_size</span>
          {slot.runtime === "container"
            ? <span className="warn">⟳ restarts the container (~model-load seconds)</span>
            : <span className="warn">⟳ restart required</span>
          }
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

      {/* C5: GPU offload tuning. Container slots: read-only ("defined by profile").
          Lemonade slots: editable, saved via the Save button (PATCH /defaults). */}
      <div className="form-row">
        <div className="form-lbl">
          <span>n_gpu_layers</span>
          {slot.runtime === "container"
            ? <span className="sub">defined by profile {slot.profile}</span>
            : <span className="warn">⟳ restart required</span>
          }
        </div>
        <div className="form-ctl">
          <input
            className={"input mono" + (fieldErrs.ngl ? " input-err" : "")}
            value={nGpuLayers}
            onChange={e => { setNGpuLayers(e.target.value); setFieldErrs(p => ({...p, ngl: undefined})); }}
            readOnly={slot.runtime === "container"}
          />
          {fieldErrs.ngl && <div className="hint" style={{color: "var(--err)"}}>{fieldErrs.ngl}</div>}
          {!fieldErrs.ngl && slot.runtime !== "container" && <div className="hint">-1 offloads all layers to the GPU.</div>}
        </div>
      </div>

      {/* Issue #548: rope_freq_base. Container: read-only. */}
      <div className="form-row">
        <div className="form-lbl">
          <span>rope_freq_base</span>
          {slot.runtime === "container"
            ? <span className="sub">defined by profile {slot.profile}</span>
            : <span className="warn">⟳ restart required</span>
          }
        </div>
        <div className="form-ctl">
          <input
            className={"input mono" + (fieldErrs.rope ? " input-err" : "")}
            value={ropeFreqBase}
            onChange={e => { setRopeFreqBase(e.target.value); setFieldErrs(p => ({...p, rope: undefined})); }}
            readOnly={slot.runtime === "container"}
          />
          {fieldErrs.rope && <div className="hint" style={{color: "var(--err)"}}>{fieldErrs.rope}</div>}
          {!fieldErrs.rope && slot.runtime !== "container" && <div className="hint">0 uses the model default. Override for long-context models.</div>}
        </div>
      </div>

      {/* idle_timeout_s + workers — hidden for container slots (no lemond idle-unload). */}
      {slot.runtime !== "container" && (
        <>
          <div className="form-row">
            <div className="form-lbl"><span>idle_timeout_s</span><span className="sub">unload after N seconds idle</span></div>
            <div className="form-ctl">
              <input
                className="input mono"
                value={idleTimeout}
                onChange={e => setIdleTimeout(e.target.value)}
              />
            </div>
          </div>

          <div className="form-row">
            <div className="form-lbl"><span>workers</span><span className="sub">concurrent inflight per slot · 1 = serial</span></div>
            <div className="form-ctl">
              <input
                className="input mono"
                value={workers}
                onChange={e => setWorkers(e.target.value)}
              />
            </div>
          </div>
        </>
      )}

      <div className="form-row">
        <div className="form-lbl">
          <span>extra_args</span>
          {slot.runtime === "container"
            ? <span className="sub">defined by profile {slot.profile}</span>
            : <span className="sub">slot-level llamacpp_args overlay</span>
          }
        </div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={extraArgs}
            onChange={e => setExtraArgs(e.target.value)}
            readOnly={slot.runtime === "container"}
          />
          {slot.runtime !== "container" && (
            <div className="hint">Merged with model recipe defaults + the global baseline.</div>
          )}
        </div>
      </div>

      {/* Flags preview.
          Container slots: show backend-provided resolved_command (real podman argv).
          Lemonade slots: show effectiveFlagsFor() preview (approximate; client-side). */}
      {slot.runtime === "container" ? (
        <>
          <div className="form-section">Resolved command</div>
          <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-3)", lineHeight: 1.6, whiteSpace: "pre-wrap"}}>
            {Array.isArray(slot.resolved_command)
              ? slot.resolved_command.join(" \\\n  ")
              : slot.resolved_command || "— not yet available (slot not loaded)"}
          </div>
          <div className="hint" style={{paddingTop: 6, fontSize: 10.5, color: "var(--fg-5)", fontFamily: "var(--jbm)"}}>
            Real podman argv from profile image + flags. Read-only.
          </div>
        </>
      ) : (
        <>
          <div className="form-section">Effective flags preview</div>
          <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-3)", lineHeight: 1.6, whiteSpace: "pre-wrap"}}>
            {effectiveFlagsFor(slot)}
          </div>
          <div className="hint" style={{paddingTop: 6, fontSize: 10.5, color: "var(--fg-5)", fontFamily: "var(--jbm)"}}>
            Merge order: lemond baseline → backend default → model recipe → slot extra_args. Read-only.
          </div>
        </>
      )}
    </Drawer>
  );
}

// Per-slot-type effective flags preview
function effectiveFlagsFor(slot) {
  const port = `--port ${slot.port || "auto"}`;
  const baseline = "--parallel 1 --threads 8";
  if (slot.type === "llm") {
    return `${baseline} --flash-attn on --no-mmap\n--ctx-size ${slot.metrics.ctx || 4096}\n-m ${slot.modelLong || slot.model}\n${port}\n-ngl ${slot.device === "cpu" ? 0 : 999}`;
  }
  if (slot.type === "embedding" || slot.type === "reranking") {
    return `${baseline} --embeddings${slot.type === "reranking" ? " --reranking" : ""}\n--ctx-size ${slot.metrics.dim || 8192}\n-m ${slot.modelLong || slot.model}\n${port}\n-ngl ${slot.device === "cpu" ? 0 : 999}`;
  }
  if (slot.type === "transcription") {
    return `--backend ${slot.device === "npu" ? "flm" : "whispercpp:vulkan"}\n--model ${slot.modelLong || slot.model}\n--precision ${slot.metrics.precision || "Q4_K_M"}\n${port}`;
  }
  if (slot.type === "tts") {
    return `--backend kokoro:cpu\n--voice ${slot.metrics.voice || "af_heart"}\n--model ${slot.modelLong || slot.model}\n${port}`;
  }
  if (slot.type === "image") {
    return `--backend sdcpp:${slot.device === "cpu" ? "cpu" : "rocm"}\n--model ${slot.modelLong || slot.model}\n--steps 20 --cfg 7.0\n--w 512 --h 512\n${port}`;
  }
  return `${baseline}\n${port}`;
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

  // N2: container swap = cold systemctl restart (NOT lemond hot /v1/load).
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
  const esRef = useRefSM(null);

  useEffectSM(() => {
    if (!open || !slot) {
      if (esRef.current) {
        esRef.current.close();
        esRef.current = null;
      }
      setLines([]);
      return;
    }
    setLines([]);
    try {
      const es = new EventSource(ENDPOINTS.slotLogsStream(slot.name));
      esRef.current = es;
      es.onmessage = (ev) => {
        setLines(prev => {
          const next = prev.concat(ev.data);
          return next.length > 500 ? next.slice(next.length - 500) : next;
        });
      };
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
          height: 460,
          overflow: "auto",
          whiteSpace: "pre-wrap",
        }}
      >
        {lines.length === 0
          ? <span style={{color: "var(--fg-4)", fontStyle: "italic"}}>waiting for log lines…</span>
          : lines.join("\n")}
      </div>
    </Drawer>
  );
}

// ─── Empty SlotCard (no model loaded) ────────────────────────────
function EmptySlotCard({ name, type, group, device, onConfigure }) {
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
        <span className="chip">{group}</span>
      </div>
      <div style={{padding: "10px 12px", background: "var(--accent-soft)", border: "1px solid var(--accent-line)", borderRadius: "var(--rad-sm)", display: "flex", alignItems: "center", gap: 8}}>
        <span className="mono" style={{fontSize: 11, color: "var(--accent)", flex: 1}}>seeded · ready to configure</span>
        <button className="btn sm" onClick={onConfigure}>{Icons.plus} Configure</button>
      </div>
    </div>
  );
}

// ─── Error SlotCard ─────────────────────────────────────────────
function ErrorSlotCardBanner({ slot, message }) {
  return (
    <div style={{padding: "10px 12px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", display: "flex", alignItems: "flex-start", gap: 8}}>
      <span style={{color: "var(--err)", display: "inline-flex"}}>{Icons.warn}</span>
      <div style={{flex: 1, fontFamily: "var(--jbm)", fontSize: 11.5, color: "var(--fg-2)", lineHeight: 1.5}}>
        <div style={{color: "var(--err)", fontWeight: 500, marginBottom: 2}}>load failed</div>
        <div>{message}</div>
        <div style={{display: "flex", gap: 6, marginTop: 6}}>
          <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Retrying load for ${slot.name}`, "info")}>{Icons.restart} Retry</button>
          <button className="btn ghost sm" onClick={() => window.__hal0Toast && window.__hal0Toast(`Re-pulling model for ${slot.name}`, "info")}>{Icons.download} Re-pull</button>
        </div>
      </div>
    </div>
  );
}

Object.assign(window, { CreateSlotModal, EditSlotDrawer, InlineSwapPopover, EmptySlotCard, ErrorSlotCardBanner, SlotLogsDrawer });
