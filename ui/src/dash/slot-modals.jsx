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
} from '@/api/hooks/useSlots'
import { useHardware } from '@/api/hooks/useHardware'
import { useModels } from '@/api/hooks/useModels'
import { ENDPOINTS } from '@/api/endpoints'

const { useState: useStateSM, useEffect: useEffectSM, useRef: useRefSM } = React;

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
  const [model, setModel] = useStateSM(defaults.model || "");
  const [group, setGroup] = useStateSM(defaults.group || "chat");
  const [advOpen, setAdvOpen] = useStateSM(false);
  const [makeDefault, setMakeDefault] = useStateSM(false);
  const [ctx, setCtx] = useStateSM(8192);
  const [extraArgs, setExtraArgs] = useStateSM("--flash-attn on");
  const [submitErr, setSubmitErr] = useStateSM(null);

  const createMut = useSlotCreate();
  const hwQuery = useHardware();
  const modelsQuery = useModels();

  useEffectSM(() => {
    if (open) {
      setName(defaults.name || "");
      setType(defaults.type || "llm");
      setDevice(defaults.device || "gpu-rocm");
      setGroup(defaults.group || "chat");
      setModel("");
      setAdvOpen(false);
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
  const compatible = allModels.filter(m =>
    m.type === type &&
    (device === "cpu"
      || (Array.isArray(m.backends) && m.backends.includes((device || "cpu").replace("gpu-", "")))
      || (device === "npu" && m.device === "npu"))
  );

  const npuAvailable = !!hwQuery.data?.npu?.present;
  const canSave = !!name && !nameError && !createMut.isPending;

  // Next available port after the highest currently-allocated
  const nextPort = Math.max(8090, ...((existingSlots || []).map(s => s.port || 8090))) + 1;

  async function onCreateClick() {
    setSubmitErr(null);
    const body = {
      name,
      type,
      device,
      group,
      ...(model ? { model } : {}),
      ...(makeDefault ? { default: true } : {}),
      ...(advOpen
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
          <span>Device <span className="req">*</span></span>
          <span className="sub">{!npuAvailable && device === "npu" ? <span style={{color: "var(--warn)"}}>NPU disabled — FLM not installed</span> : "hardware preference for this slot"}</span>
        </div>
        <div className="form-ctl">
          <select className="input mono" value={device} onChange={e => setDevice(e.target.value)}>
            <option value="gpu-rocm">gpu-rocm</option>
            <option value="gpu-vulkan">gpu-vulkan</option>
            <option value="cpu">cpu</option>
            <option value="npu" disabled={!npuAvailable}>npu{!npuAvailable ? " — install FLM first" : ""}</option>
          </select>
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

  const [ctx, setCtx] = useStateSM(slot?.metrics?.ctx || 4096);
  const [idleTimeout, setIdleTimeout] = useStateSM(900);
  const [workers, setWorkers] = useStateSM(1);
  const [extraArgs, setExtraArgs] = useStateSM("--flash-attn on --no-mmap");
  const [device, setDevice] = useStateSM(slot?.device || "gpu-rocm");
  const [makeDefault, setMakeDefault] = useStateSM(!!slot?.isDefault);
  const [submitErr, setSubmitErr] = useStateSM(null);

  useEffectSM(() => {
    if (slot) {
      setCtx(slot.metrics?.ctx || 4096);
      setDevice(slot.device || "gpu-rocm");
      setMakeDefault(!!slot.isDefault);
      setIdleTimeout(900);
      setWorkers(1);
      setExtraArgs("--flash-attn on --no-mmap");
      setSubmitErr(null);
    }
  }, [slot?.name]);

  if (!slot) return null;

  async function onSaveClick() {
    setSubmitErr(null);
    try {
      // Two-step: defaults (ctx_size lives under [model]) + slot config
      // for the top-level keys (device, llamacpp_args, idle_timeout_s,
      // workers, default).
      const ctxNum = Number(ctx);
      const idleNum = Number(idleTimeout);
      const workersNum = Number(workers);
      await defaultsMut.mutateAsync({
        name: slot.name,
        body: {
          ctx_size: Number.isFinite(ctxNum) ? ctxNum : ctx,
        },
      });
      await editMut.mutateAsync({
        name: slot.name,
        body: {
          device,
          default: makeDefault,
          llamacpp_args: extraArgs,
          idle_timeout_s: Number.isFinite(idleNum) ? idleNum : idleTimeout,
          workers: Number.isFinite(workersNum) ? workersNum : workers,
        },
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
      {/* Provider + port strip — read-only */}
      <div style={{display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 0, border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", overflow: "hidden", marginBottom: 16}}>
        <ReadOnlyStrip k="provider" v="lemonade" />
        <ReadOnlyStrip k="port" v={`:${slot.port || "—"}`} />
        <ReadOnlyStrip k="state" v={<span className="chip ok">{slot.state}</span>} />
      </div>

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

      <div className="form-section">Advanced</div>

      <div className="form-row">
        <div className="form-lbl"><span>ctx_size</span><span className="warn">⟳ restart required</span></div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={ctx}
            onChange={e => setCtx(e.target.value)}
          />
        </div>
      </div>

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

      <div className="form-row">
        <div className="form-lbl"><span>extra_args</span><span className="sub">slot-level llamacpp_args overlay</span></div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={extraArgs}
            onChange={e => setExtraArgs(e.target.value)}
          />
          <div className="hint">Merged with model recipe defaults + the global baseline.</div>
        </div>
      </div>

      <div className="form-section">Effective flags preview</div>
      <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--fg-3)", lineHeight: 1.6, whiteSpace: "pre-wrap"}}>
        {effectiveFlagsFor(slot)}
      </div>
      <div className="hint" style={{paddingTop: 6, fontSize: 10.5, color: "var(--fg-5)", fontFamily: "var(--jbm)"}}>
        Merge order: lemond baseline → backend default → model recipe → slot extra_args. Read-only.
      </div>
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
  const ramFreeGb = hwQuery.data?.ram?.free ?? 0;
  const compatible = (modelsQuery.data ?? [])
    .map(normalizeApiModel)
    .filter(m => m.type === slot.type);
  return (
    <div className="swap-pop" onClick={e => e.stopPropagation()}>
      <div className="swap-pop-h">Swap model · type {slot.type}</div>
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
            onClick={() => { onPick(m); onClose(); }}
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
              onClick={e => { e.stopPropagation(); onPick(m); onClose(); }}
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

// ─── Overflow menu (⋯) ──────────────────────────────────────────
function SlotOverflowMenu({ slot, onClose, onViewLogs, onDelete }) {
  return (
    <Menu
      anchor="right"
      onClose={onClose}
      items={[
        {
          icon: Icons.logs,
          label: "View slot logs",
          onClick: () => onViewLogs && onViewLogs(),
        },
        {
          icon: Icons.flame,
          label: slot.isDefault ? "Already default" : "Set as default",
          onClick: () => window.__hal0Toast && window.__hal0Toast(`${slot.name} set as default for ${slot.type}`, "ok"),
        },
        {
          icon: Icons.ext,
          label: "Copy curl example",
          onClick: () => window.__hal0Toast && window.__hal0Toast("curl example copied to clipboard", "ok"),
        },
        { divider: true },
        {
          icon: Icons.unload,
          label: "Delete slot",
          danger: true,
          onClick: () => onDelete && onDelete(),
        },
      ]}
    />
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

Object.assign(window, { CreateSlotModal, EditSlotDrawer, InlineSwapPopover, SlotOverflowMenu, EmptySlotCard, ErrorSlotCardBanner, SlotLogsDrawer });
