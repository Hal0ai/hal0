// hal0 dashboard — Model interactive surface
//
// Add by HF coords modal · Recipe editor · Delete confirm ·
// "Used by" slot panel · "On disk" panel · DownloadRow (live SSE).
//
// Phase B2 wireup (#220 brief): every modal now drives off real
// hooks — `useModelInspect` populates the variant list, `usePullJob`
// owns the pull lifecycle, `useModelUpdate` saves recipe edits, and
// `useModelDelete` returns the cascade affected_slots straight from
// the backend. The old HAL0_DATA fallbacks live on only as cosmetic
// fixtures (host RAM, /var disk hint).

import { useModels, useModelInspect, useModelUpdate, useModelDelete, usePullJob, useScanPreview, useAddModelFromPath, fmtBytes, fmtSpeed, fmtEta } from '@/api/hooks/useModels'
import { useSlots } from '@/api/hooks/useSlots'
import { useSettings } from '@/api/hooks/useSettings'
import { useChatTemplates } from '@/api/hooks/useChatTemplates'
import { useProfiles } from '@/api/hooks/useProfiles'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { canonicalCapabilities, modelDeviceClasses, profileDeviceClass } from '@/lib/deviceMeta'
import { MODEL_TYPE_TAGS, splitModelTags, mergeModelTags } from '@/dash/model-types.js'

const { useState: useStateMM, useEffect: useEffectMM, useMemo: useMemoMM } = React;

// ─── Add model by HF coords ─────────────────────────────────────
function AddByHfModal({ open, onClose, initialRepo = "" }) {
  // ``initialRepo`` lets the dashboard's HF-search panel prefill the
  // coord when the user clicks "Add" on a search result (issue #311).
  // The reset effect below still wins on close, so the modal opens
  // clean on the next invocation.
  const [repo, setRepo] = useStateMM("");
  const [variant, setVariant] = useStateMM(null);
  const [name, setName] = useStateMM("");
  const [labels, setLabels] = useStateMM({ chat: true });
  const [mmproj, setMmproj] = useStateMM("");
  // Optional chat-template pin, applied at pull time (WS-6). "auto" = use the
  // GGUF-embedded template — the common case, so it's the default.
  const [chatTemplate, setChatTemplate] = useStateMM("auto");

  const inspect = useModelInspect();
  const pullJob = usePullJob();
  // Chat-template catalogue — gated on `open` like the Recipe editor so the
  // request only fires while the modal is visible.
  const templates = useChatTemplates(open);
  // One canonical capability vocabulary for every add/edit surface —
  // meta.model_capabilities (this modal used to carry its own ad-hoc list
  // with legacy spellings: embeddings/reranking/transcription).
  const enums = useMetaEnums();

  useEffectMM(() => {
    if (open) {
      setRepo(initialRepo || "");
      setVariant(null);
      setName("");
      setLabels({ chat: true });
      setMmproj("");
      setChatTemplate("auto");
      inspect.reset();
      pullJob.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, initialRepo]);

  const variants = inspect.data?.variants ?? [];
  // mmproj affordance — HF repos often ship an mmproj-Q8.gguf next to
  // the main quants. Pick those out of the same variant list so the
  // dropdown reflects what's actually in the repo.
  const mmprojChoices = useMemoMM(
    () => variants.filter(v => /mmproj/i.test(v.id)).map(v => v.id),
    [variants],
  );

  const onInspect = async () => {
    if (!repo) return;
    try {
      const result = await inspect.mutateAsync({ hf_repo: repo });
      // Auto-suggest a name from the repo's tail segment.
      const tail = (result?.repo || repo).split("/")[1] || "model";
      setName(prev => prev || tail.replace(/-GGUF$/i, ""));
    } catch (e) {
      // The toast surface already renders the Hal0Error envelope;
      // surface a hint for offline mock scenarios so the operator
      // doesn't think the button is wedged.
      window.__hal0Toast && window.__hal0Toast(
        `Inspect failed — ${e?.message || "unreachable"}`,
        "err",
      );
    }
  };

  const inspected = inspect.isSuccess && !!inspect.data;
  const sel = variants.find(v => v.id === variant);
  // The vision↔mmproj invariant on the CREATE path (#1394) — the exact twin of
  // the edit drawer's #1380 gate. The message used to render as decoration next
  // to a live Pull button; it is now folded into the single `canPull` gate so
  // the modal can't seed a projector-less vision row. Escape hatch: a repo that
  // ships no mmproj at all can't satisfy "pick one below", so the wording points
  // at the label instead — the pull itself is fine, only the label is
  // unsupportable. (The server refuses this shape too, via
  // `model.vision_requires_mmproj` on POST /pull, so a stale client can't
  // route around the gate.)
  const mmprojError = !(labels.vision && !mmproj)
    ? null
    : inspected && mmprojChoices.length === 0
      ? "vision label requires an mmproj file — this repo ships none, so untick vision to pull"
      : "vision label requires an mmproj file — pick one below";
  const canPull = inspected && variant && name && !pullJob.inFlight && !mmprojError;

  const onPull = async () => {
    if (!canPull) return;
    try {
      // The pull endpoint keys on the registry model id. We use the
      // operator-chosen ``name`` so the row lands under their preferred
      // namespace, and pass the HF variant + optional mmproj as the
      // pull job body — the backend resolves them against the registry
      // entry's hf_repo/hf_filename it writes during the curated
      // ``pick-default`` flow.
      const labelList = Object.entries(labels).filter(([, v]) => v).map(([k]) => k);
      await pullJob.start(name, {
        hf_repo: inspect.data?.repo ?? repo,
        hf_filename: variant,
        mmproj_filename: mmproj || undefined,
        labels: labelList,
        chat_template: chatTemplate && chatTemplate !== "auto" ? chatTemplate : undefined,
      });
      window.__hal0Toast && window.__hal0Toast(
        `Pulling ${name} · ${sel?.size ?? ""}`, "info",
      );
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Pull failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Catalog · add model"
      title="Add model from Hugging Face"
      width={680}
      foot={
        <>
          <span>Files land under <span className="mono">/var/lib/hal0/models/&lt;id&gt;</span></span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" disabled={!canPull} onClick={onPull}>
              {Icons.download} Pull{sel ? ` (${sel.size})` : ""}
            </button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>Repo <span className="req">*</span></span>
          <span className="sub">org / repo · GGUF preferred</span>
        </div>
        <div className="form-ctl" style={{display: "flex", gap: 8}}>
          <input
            className="input mono"
            value={repo}
            onChange={e => { setRepo(e.target.value); inspect.reset(); }}
            placeholder="unsloth/Qwen3-8B-GGUF"
            style={{flex: 1}}
            autoFocus
          />
          <button className="btn ghost sm" disabled={!repo || inspect.isPending} onClick={onInspect}>
            {inspect.isPending ? "Inspecting…" : "Inspect"}
          </button>
        </div>
      </div>

      {inspect.isError && (
        <div className="err" style={{marginBottom: 10}}>
          {inspect.error?.code === "hf.repo_not_found"
            ? `HF repo not found: ${repo}`
            : `Inspect failed: ${inspect.error?.message || "unreachable"}`}
        </div>
      )}

      {inspected && (
        <>
          <div className="form-row">
            <div className="form-lbl">
              <span>Variants <span className="req">*</span></span>
              <span className="sub">
                {variants.length} available · pick a quant
              </span>
            </div>
            <div className="form-ctl" style={{display: "flex", flexDirection: "column", gap: 6}}>
              {variants.length === 0 ? (
                <div className="mono" style={{fontSize: 12, color: "var(--fg-4)", fontStyle: "italic"}}>
                  No .gguf files found in this repo.
                </div>
              ) : variants.filter(v => !/mmproj/i.test(v.id)).map(v => (
                <div
                  key={v.id}
                  className={"variant-row" + (variant === v.id ? " sel" : "")}
                  onClick={() => setVariant(v.id)}
                >
                  <span className="rad" />
                  <span className="nm">
                    {v.id}
                    <span className="sub">{v.info}</span>
                  </span>
                  <span className="sz num">{v.size}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="form-row">
            <div className="form-lbl">
              <span>Model name (in hal0)</span>
              <span className="sub">derived from the HF repo name — edit to taste</span>
            </div>
            <div className="form-ctl">
              <input className="input mono" value={name} onChange={e => setName(e.target.value)} />
            </div>
          </div>

          <div className="form-row">
            <div className="form-lbl">
              <span>Labels</span>
              <span className="sub">drives OmniRouter eligibility</span>
            </div>
            <div className="form-ctl" style={{display: "flex", flexWrap: "wrap", gap: 8}}>
              {enums.model_capabilities.map(l => (
                <label key={l} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={!!labels[l]}
                    onChange={e => setLabels({ ...labels, [l]: e.target.checked })}
                  />
                  <span className="mono">{l}</span>
                </label>
              ))}
              {mmprojError && (
                <div
                  className="err"
                  data-testid="hf-add-mmproj-error"
                  style={{flexBasis: "100%"}}
                >
                  {mmprojError}
                </div>
              )}
            </div>
          </div>

          {labels.vision && (
            <div className="form-row">
              <div className="form-lbl">
                <span>mmproj file</span>
                <span className="warn">required for vision-labeled models</span>
              </div>
              <div className="form-ctl">
                <select
                  className="input mono"
                  data-testid="hf-add-mmproj-select"
                  value={mmproj}
                  onChange={e => setMmproj(e.target.value)}
                >
                  <option value="">
                    {mmprojChoices.length === 0
                      ? "— no mmproj files in repo —"
                      : "— pick from repo files… —"}
                  </option>
                  {mmprojChoices.map(id => <option key={id} value={id}>{id}</option>)}
                </select>
              </div>
            </div>
          )}

          <div className="form-row">
            <div className="form-lbl">
              <span>chat template</span>
              <span className="sub">auto = use template embedded in the GGUF · applied on pull</span>
            </div>
            <div className="form-ctl">
              <select
                className="input mono chat-template-select"
                value={chatTemplate}
                onChange={e => setChatTemplate(e.target.value)}
              >
                <option value="auto">Auto (GGUF embedded)</option>
                {(Array.isArray(templates.data) ? templates.data : []).filter(t => t.id !== "auto").map(t => (
                  <option key={t.id} value={t.id}>{t.label}</option>
                ))}
              </select>
            </div>
          </div>

          {inspect.data?.metadata?.license && (
            <div className="form-section">License · <span className="mono" style={{color: "var(--fg)"}}>{inspect.data.metadata.license}</span></div>
          )}

          <div className="form-section">Pre-flight</div>
          <div style={{padding: 12, background: "var(--bg)", border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11.5, lineHeight: 1.7}}>
            <div>repo · <span style={{color: "var(--fg)"}}>{inspect.data?.repo || repo}</span></div>
            <div>variant · <span style={{color: "var(--fg)"}}>{variant || "—"}</span></div>
            <div>size · <span style={{color: "var(--fg)"}}>{sel ? sel.size : "—"}</span></div>
            {pullJob.inFlight && (
              <div>pull · <span style={{color: "var(--accent)"}}>{pullJob.state} {pullJob.pct != null ? `${pullJob.pct}%` : ""}</span></div>
            )}
          </div>
        </>
      )}
    </Modal>
  );
}

// Model capability + backend vocabularies now come from GET /api/meta/enums
// (useMetaEnums → meta.model_capabilities / meta.model_backends, with the
// typed static fallback) — the hardcoded MODEL_CAPABILITIES / MODEL_BACKENDS
// constants that used to live here are gone.

// Order-insensitive set equality for the array fields (capabilities/backends),
// so a defaults-only save doesn't spuriously report those as changed.
function sameStringSet(a, b) {
  const sa = [...a].sort();
  const sb = [...b].sort();
  return sa.length === sb.length && sa.join(" ") === sb.join(" ");
}

// ─── Recipe editor (per-model defaults) ────────────────────────
function RecipeEditorModal({ open, onClose, model }) {
  const update = useModelUpdate();
  // Gate on `open` — the modal is mounted while a model is merely selected,
  // so an ungated fetch would fire on the models list and detach row controls.
  const templates = useChatTemplates(open);
  const profilesQuery = useProfiles();
  const enums = useMetaEnums();
  const init = model?.defaults || {};
  const [ctx, setCtx] = useStateMM("");
  const [ngl, setNgl] = useStateMM("");
  const [extra, setExtra] = useStateMM("");
  const [chatTemplate, setChatTemplate] = useStateMM("auto");
  // Preferred runtime profile that loads with this model (ModelDefaults.profile).
  const [profile, setProfile] = useStateMM("");
  // Identity + types. `name` is the editable display name; `types` is the
  // selected subset of MODEL_TYPE_TAGS; `otherTags` snapshots the model's
  // non-curated tags so we can re-emit them verbatim on save (never clobber).
  const [name, setName] = useStateMM("");
  const [types, setTypes] = useStateMM([]);
  const [otherTags, setOtherTags] = useStateMM([]);
  // Routing-critical top-level fields (registry/model.py): capabilities gate
  // dispatch/omni eligibility, backends gate slot-compat filtering, mmproj is
  // the vision projector sidecar path, and hf_repo/hf_filename are the re-pull
  // coords (POST /pull 422s without them).
  const [caps, setCaps] = useStateMM([]);
  const [backends, setBackends] = useStateMM([]);
  const [mmproj, setMmproj] = useStateMM("");
  const [hfRepo, setHfRepo] = useStateMM("");
  const [hfFilename, setHfFilename] = useStateMM("");

  useEffectMM(() => {
    if (open && model) {
      setCtx(init.context_size != null ? String(init.context_size) : "");
      setNgl(init.n_gpu_layers != null ? String(init.n_gpu_layers) : "");
      setExtra(init.extra_args || "");
      setChatTemplate(init.chat_template ?? "auto");
      setProfile(init.profile || "");
      setName(model.name || "");
      const split = splitModelTags(model.tags);
      setTypes(split.selected);
      setOtherTags(split.other);
      // Normalize legacy capability spellings (embeddings/reranking/…)
      // through meta.capability_aliases so the toggles light up correctly.
      setCaps(canonicalCapabilities(model.capabilities, enums));
      setBackends(Array.isArray(model.backends) ? model.backends : []);
      setMmproj(model.mmproj || "");
      setHfRepo(model.hf_repo || "");
      setHfFilename(model.hf_filename || "");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, model?.id]);

  const toggleType = (tag) =>
    setTypes(prev => prev.includes(tag) ? prev.filter(t => t !== tag) : [...prev, tag]);
  const toggleCap = (cap) =>
    setCaps(prev => prev.includes(cap) ? prev.filter(c => c !== cap) : [...prev, cap]);
  const toggleBackend = (b) =>
    setBackends(prev => prev.includes(b) ? prev.filter(x => x !== b) : [...prev, b]);

  if (!model) return null;

  const onSave = async () => {
    // Preserve any ModelDefaults field the editor doesn't surface (e.g. a legacy
    // hand-set rope_freq_base, which no longer has an input here — deprecated
    // backend-side, use extra_args) by starting from the stored defaults. Every
    // key we DO render is then sent explicitly: a value, or `null` to clear.
    // Since #1413 the registry merges `defaults` one level deep, so absent means
    // "keep" and only `null` deletes — an emptied input must say `null` to keep
    // the "empty = launcher default" affordance intact.
    const defaults = { ...init };
    const ctxNum = ctx.trim() ? parseInt(ctx, 10) : NaN;
    defaults.context_size = Number.isFinite(ctxNum) ? ctxNum : null;
    const nglNum = ngl.trim() ? parseInt(ngl, 10) : NaN;
    defaults.n_gpu_layers = Number.isFinite(nglNum) ? nglNum : null;
    defaults.extra_args = extra.trim() ? extra : null;
    // Only persist a real template choice — 'auto' means GGUF-embedded, which
    // is the absence of an override, so don't pollute defaults with it.
    defaults.chat_template = chatTemplate && chatTemplate !== "auto" ? chatTemplate : null;
    // Preferred profile: "" means no preference (slot keeps its device default).
    defaults.profile = profile.trim() || null;
    const body = { defaults };
    // Display name: only persist a real, changed value — never blank it out.
    const trimmedName = name.trim();
    if (trimmedName && trimmedName !== (model.name || "")) body.name = trimmedName;
    // Tags: union of preserved (non-curated) tags + selected types. Only
    // emit when the set actually changed (order-insensitive) so a
    // defaults-only save doesn't spuriously report a tag edit.
    const nextTags = mergeModelTags(otherTags, types);
    const prevTags = Array.isArray(model.tags) ? model.tags : [];
    const sameTags =
      nextTags.length === prevTags.length &&
      [...nextTags].sort().join(" ") === [...prevTags].sort().join(" ");
    if (!sameTags) body.tags = nextTags;
    // Capabilities + backends: routing-critical top-level sets. Emit only when
    // the set actually changed (order-insensitive, compared through the
    // canonical alias map so a legacy "embeddings" row isn't spuriously dirty).
    const prevCaps = canonicalCapabilities(model.capabilities, enums);
    if (!sameStringSet(caps, prevCaps)) body.capabilities = caps;
    const prevBackends = Array.isArray(model.backends) ? model.backends : [];
    if (!sameStringSet(backends, prevBackends)) body.backends = backends;
    // Vision projector sidecar path; "" clears it (null on the wire).
    const trimmedMmproj = mmproj.trim();
    if (trimmedMmproj !== (model.mmproj || "")) body.mmproj = trimmedMmproj || null;
    // Re-pull source coords — without these a subsequent Pull 422s.
    const trimmedRepo = hfRepo.trim();
    if (trimmedRepo !== (model.hf_repo || "")) body.hf_repo = trimmedRepo;
    const trimmedFile = hfFilename.trim();
    if (trimmedFile !== (model.hf_filename || "")) body.hf_filename = trimmedFile;
    try {
      await update.mutateAsync({ id: model.id, body });
      window.__hal0Toast && window.__hal0Toast(`Updated ${model.longName || model.id}`, "ok");
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Save failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Edit model · name, types & defaults"
      title={`Edit options · ${model.longName || model.name || model.id}`}
      width={560}
      foot={
        <>
          <span style={{color: "var(--warn)"}}>⟳ ctx_size + extra_args require slot restart</span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" onClick={onSave} disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save options"}
            </button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>display name</span>
          <span className="sub">what the dashboard shows · empty keeps the model id</span>
        </div>
        <div className="form-ctl">
          <input
            className="input"
            data-testid="model-name-input"
            placeholder={model.id}
            value={name}
            onChange={e => setName(e.target.value)}
          />
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>types</span>
          <span className="sub">capability tags · drive routing &amp; slot features (e.g. mtp shows the slot MTP toggle)</span>
        </div>
        <div className="form-ctl" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {MODEL_TYPE_TAGS.map(tag => {
            const on = types.includes(tag);
            return (
              <button
                key={tag}
                type="button"
                role="switch"
                aria-checked={on}
                data-testid={`type-toggle-${tag}`}
                className={"mdl-chip" + (on ? " on" : "")}
                onClick={() => toggleType(tag)}
              >
                {tag}
              </button>
            );
          })}
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>capabilities</span>
          <span className="sub">dispatch / omni eligibility · canonical vocab from /api/meta/enums</span>
        </div>
        <div className="form-ctl" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {enums.model_capabilities.map(cap => {
            const on = caps.includes(cap);
            return (
              <button
                key={cap}
                type="button"
                role="switch"
                aria-checked={on}
                data-testid={`cap-toggle-${cap}`}
                className={"mdl-chip" + (on ? " on" : "")}
                onClick={() => toggleCap(cap)}
              >
                {cap}
              </button>
            );
          })}
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>backends</span>
          <span className="sub">runners this model can bind · drives slot-compat filtering</span>
        </div>
        <div className="form-ctl" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {enums.model_backends.map(b => {
            const on = backends.includes(b);
            return (
              <button
                key={b}
                type="button"
                role="switch"
                aria-checked={on}
                data-testid={`backend-toggle-${b}`}
                className={"mdl-chip" + (on ? " on" : "")}
                onClick={() => toggleBackend(b)}
              >
                {b}
              </button>
            );
          })}
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>context_size</span>
          <span className="sub">tokens · empty = launcher default</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" inputMode="numeric" placeholder="e.g. 8192" value={ctx} onChange={e => setCtx(e.target.value)} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>n_gpu_layers</span>
          <span className="sub">-1 = all on GPU · 0 = CPU only</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" inputMode="numeric" placeholder="e.g. -1" value={ngl} onChange={e => setNgl(e.target.value)} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>extra_args</span>
          <span className="sub">freeform · appended after slot extra_args</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" placeholder="--rope-freq-base 10000" value={extra} onChange={e => setExtra(e.target.value)} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>chat_template</span>
          <span className="sub">auto = use template embedded in the GGUF</span>
        </div>
        <div className="form-ctl">
          <select
            className="input mono chat-template-select"
            value={chatTemplate}
            onChange={e => setChatTemplate(e.target.value)}
          >
            <option value="auto">Auto (GGUF embedded)</option>
            {(Array.isArray(templates.data) ? templates.data : []).filter(t => t.id !== "auto").map(t => (
              <option key={t.id} value={t.id}>{t.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>preferred profile</span>
          <span className="sub">runtime profile loaded with this model on create + every swap · empty = slot default</span>
        </div>
        <div className="form-ctl">
          <select
            className="input mono"
            value={profile}
            onChange={e => setProfile(e.target.value)}
          >
            <option value="">None (use slot default)</option>
            {(() => {
              // Filter to profiles that fit this model, keyed on the profile's
              // device_class/backend vs the model's backends set (the old code
              // compared the brittle derived `model.device` string). A model
              // with unknown backends matches every class; a profile with no
              // class signal is likewise never filtered out.
              const all = Array.isArray(profilesQuery.data) ? profilesQuery.data : [];
              const mClasses = modelDeviceClasses(model.backends, model.device, enums);
              const mBackends = Array.isArray(model.backends) ? model.backends : [];
              const fit = all.filter(p => {
                const pc = profileDeviceClass(p);
                const classOk = !pc || mClasses.size === 0 || mClasses.has(pc);
                // GPU profiles pin a concrete runtime (rocm|vulkan) — require
                // the model to actually list that backend when it lists any.
                const backendOk = !p.backend || mBackends.length === 0 || mBackends.includes(p.backend);
                const typeOk = !model.type || !Array.isArray(p.supported_slot_types) || p.supported_slot_types.includes(model.type);
                return classOk && backendOk && typeOk;
              });
              // Always include the current selection even if it doesn't pass the
              // filter, so an existing preference never silently disappears.
              const names = new Set(fit.map(p => p.name));
              const rows = [...fit];
              if (profile && !names.has(profile)) rows.unshift({ name: profile });
              return rows.map(p => (
                <option key={p.name} value={p.name}>
                  {p.name}{p.intent ? ` · ${p.intent}` : ""}
                </option>
              ));
            })()}
          </select>
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>mmproj</span>
          <span className="sub">vision projector sidecar path · required when a vision capability is set</span>
        </div>
        <div className="form-ctl">
          <input
            className="input mono"
            placeholder="/var/lib/hal0/models/…/mmproj-Q8.gguf"
            value={mmproj}
            onChange={e => setMmproj(e.target.value)}
          />
          {caps.includes("vision") && !mmproj.trim() && (
            <div className="err" style={{marginTop: 6}}>vision capability requires an mmproj sidecar path</div>
          )}
        </div>
      </div>
      <div className="form-section">Source · re-pull coords</div>
      <div className="form-row">
        <div className="form-lbl">
          <span>hf_repo</span>
          <span className="sub">HuggingFace repo · needed to re-pull</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" placeholder="unsloth/Qwen3-8B-GGUF" value={hfRepo} onChange={e => setHfRepo(e.target.value)} />
        </div>
      </div>
      <div className="form-row">
        <div className="form-lbl">
          <span>hf_filename</span>
          <span className="sub">variant filename within the repo</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" placeholder="qwen3-8b-q4_k_m.gguf" value={hfFilename} onChange={e => setHfFilename(e.target.value)} />
        </div>
      </div>
      {update.isError && (
        <div className="err">{update.error?.message || "Save failed"}</div>
      )}
    </Modal>
  );
}

// ─── Delete model confirm ───────────────────────────────────────
function DeleteModelDialog({ open, onClose, model }) {
  const del = useModelDelete();
  // Live affected_slots — prefer the cascade response when the user
  // has already attempted a force_cascade=false dry-run (not yet
  // wired in B2); for the default flow we fall back to the live
  // slots query so the warning matches what the registry sees.
  const slotsQuery = useSlots();
  if (!model) return null;
  const slots = slotsQuery.data ?? [];
  const slotsUsing = slots.filter(s => s.model_id === model.id || s.model === model.id);
  const hasUsers = slotsUsing.length > 0;

  const onConfirm = async () => {
    try {
      const res = await del.mutateAsync(model.id);
      const cascaded = res?.affected_slots?.length || 0;
      window.__hal0Toast && window.__hal0Toast(
        cascaded
          ? `Deleted ${model.longName || model.id} (cascaded ${cascaded} slot${cascaded > 1 ? "s" : ""})`
          : `Deleted ${model.longName || model.id}`,
        "ok",
      );
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Delete failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  return (
    <ConfirmDialog
      open={open}
      onCancel={onClose}
      onConfirm={onConfirm}
      title={`Delete ${model.longName || model.name || model.id}?`}
      message={
        <span>
          This removes <span className="mono" style={{color: "var(--fg)"}}>{model.size || fmtBytes(model.size_bytes || 0)}</span> from <span className="mono">/var/lib/hal0/models</span>.{" "}
          {hasUsers && (
            <span style={{display: "block", marginTop: 10, padding: "10px 12px", background: "var(--warn-soft)", border: "1px solid var(--warn-line)", borderRadius: "var(--rad-sm)", color: "var(--warn)", fontFamily: "var(--jbm)", fontSize: 12}}>
              ⚠ {slotsUsing.length} slot{slotsUsing.length > 1 ? "s" : ""} reference this model: <b>{slotsUsing.map(s => s.name).join(", ")}</b>. They'll move to <span className="mono">empty</span> state. Re-configure with a different model first if you need them live.
            </span>
          )}
        </span>
      }
      confirmLabel={del.isPending ? "Deleting…" : "Delete model"}
      destructive
      typeToConfirm={hasUsers ? model.id : null}
    />
  );
}

// ─── Used-by panel (Model detail) ───────────────────────────────
function UsedByPanel({ model }) {
  const slotsQuery = useSlots();
  if (!model) return null;
  const slots = slotsQuery.data ?? [];
  const using = slots.filter(s => s.model_id === model.id || s.model === model.id);
  return (
    <div className="mdl-detail-recipe">
      <div className="lbl">Used by</div>
      {using.length === 0 ? (
        <div className="mono" style={{fontSize: 12, color: "var(--fg-4)", fontStyle: "italic"}}>
          No slot references this model.
        </div>
      ) : (
        using.map(s => (
          <div key={s.name} className="ro-row" style={{cursor: "pointer", padding: "7px 0"}}
               onClick={() => { window.location.hash = "#slots/" + s.name; }}>
            <span className="k" style={{display: "flex", alignItems: "center", gap: 6}}>
              <span className={"dot " + (s.state || "empty")} />
              {s.name}
            </span>
            <span className="v" style={{display: "flex", alignItems: "center", gap: 6}}>
              <span style={{color: "var(--fg-3)"}}>{s.type || ""} · {s.device || s.backend || ""}</span>
              {s.isDefault && <span className="chip outlined amber">default</span>}
              <span style={{marginLeft: "auto", color: "var(--accent)"}}>→</span>
            </span>
          </div>
        ))
      )}
    </div>
  );
}

// ─── On-disk panel (Model detail) ───────────────────────────────
function OnDiskPanel({ model }) {
  if (!model || !model.installed) return null;
  const path = model.path || `/var/lib/hal0/models/${model.id}/`;
  return (
    <div className="mdl-detail-recipe">
      <div className="lbl">On disk</div>
      <div className="ro-row">
        <span className="k">path</span>
        <span className="v" style={{wordBreak: "break-all", fontSize: 11}}>{path}</span>
      </div>
      <div className="ro-row">
        <span className="k">size</span>
        <span className="v">{model.size || fmtBytes(model.size_bytes || 0)}</span>
      </div>
      <div className="ro-row">
        <span className="k">ns</span>
        <span className="v">{model.ns || "—"}</span>
      </div>
      <div style={{display: "flex", gap: 6, marginTop: 8}}>
        <button className="btn ghost sm" onClick={() => {
          navigator.clipboard?.writeText(path);
          window.__hal0Toast && window.__hal0Toast("Path copied to clipboard", "ok");
        }}>Copy path</button>
      </div>
    </div>
  );
}

// ─── Download row backed by usePullJob ──────────────────────────
function DownloadRow({ modelId, onRemove }) {
  const job = usePullJob();
  // Reattach on mount so a refresh keeps showing in-flight pulls.
  useEffectMM(() => {
    if (modelId) job.reattach(modelId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelId]);

  const state = job.state;
  const pct = job.pct ?? 0;
  const [cancelling, setCancelling] = useStateMM(false);
  const doCancel = async () => {
    // The hook re-throws Hal0Error on a failed cancel; surface it as a
    // toast instead of letting the rejection float as unhandled.
    setCancelling(true);
    try {
      await job.cancel();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Cancel failed — ${e?.message || "see logs"}`, "err",
      );
    } finally {
      setCancelling(false);
    }
  };
  // UI-9: no "Pause" button — the pull engine has no pause (job.cancel() sets
  // the terminal `cancelled` state and destroys the download). Only an honest
  // Cancel is offered; a re-pull resumes from the .part file backend-side.
  const onCancel = doCancel;
  const onRetry = () => { job.start(modelId); };

  return (
    <div style={{padding: "12px 16px", borderBottom: "1px solid var(--line-soft)", position: "relative"}}>
      <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 11.5, marginBottom: 6, alignItems: "center", gap: 8}}>
        <span style={{
          color: state === "completed" ? "var(--ok)" : state === "cancelled" ? "var(--fg-4)" : state === "failed" ? "var(--err)" : "var(--fg)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          flex: 1,
          textDecoration: state === "cancelled" ? "line-through" : "none",
        }}>{modelId}</span>
        <span style={{
          color: state === "completed" ? "var(--ok)" : state === "queued" ? "var(--fg-4)" : state === "failed" ? "var(--err)" : "var(--fg)",
          fontSize: 11,
        }}>
          {state === "completed" && "✓ done"}
          {state === "queued"    && "queued"}
          {state === "running"   && `${pct}%`}
          {state === "cancelled" && "cancelled"}
          {state === "failed"    && "failed"}
          {state === "idle"      && "—"}
        </span>
      </div>
      <div className="dl-bar" style={{height: 4}}>
        <i style={{
          width: `${pct}%`,
          background: state === "completed" ? "var(--ok)" : state === "failed" ? "var(--err)" : state === "cancelled" ? "var(--fg-4)" : "var(--accent)",
        }} />
      </div>
      {state === "running" && (
        <div style={{display: "flex", justifyContent: "space-between", fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", marginTop: 4}}>
          <span>{fmtBytes(job.downloaded)} / {fmtBytes(job.total)}</span>
          <span>{fmtSpeed(job.speedBps)} · {fmtEta(job.etaS)}</span>
        </div>
      )}
      {state === "failed" && job.error && (
        <div style={{marginTop: 6, padding: "8px 10px", background: "var(--err-soft)", border: "1px solid var(--err-line)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 11, color: "var(--err)"}}>
          {job.error.message || "pull failed"}
        </div>
      )}
      <div style={{display: "flex", gap: 4, marginTop: 6}}>
        {state === "running" && (
          <button className="btn ghost sm" onClick={onCancel} disabled={cancelling}>{cancelling ? "Cancelling…" : "Cancel"}</button>
        )}
        {state === "queued" && (
          <button className="btn ghost sm" onClick={onCancel} disabled={cancelling}>{cancelling ? "Cancelling…" : "Cancel"}</button>
        )}
        {state === "failed" && (
          <>
            <button className="btn ghost sm" onClick={onRetry}>{Icons.restart} Retry</button>
            <button className="btn ghost sm" onClick={() => onRemove && onRemove(modelId)}>Remove</button>
          </>
        )}
        {state === "cancelled" && (
          <button className="btn ghost sm" onClick={() => onRemove && onRemove(modelId)}>Remove</button>
        )}
        {state === "completed" && (
          <button className="btn ghost sm" onClick={() => onRemove && onRemove(modelId)}>Dismiss</button>
        )}
      </div>
    </div>
  );
}

// ─── Add model by absolute path ────────────────────────────────
//
// PR feat/models-scan-and-add-by-path: minimal modal around
// /api/models/add-from-path. The operator types (or pastes) an
// absolute file path; we POST and let the backend's detect() pass
// derive capabilities/backends/size. The id + name + labels overrides
// are surfaced for the cases where the auto-derived ones aren't what
// the operator wants.
function AddByPathModal({ open, onClose }) {
  const [path, setPath] = useStateMM("");
  const [id, setId] = useStateMM("");
  const [name, setName] = useStateMM("");
  const [labelSel, setLabelSel] = useStateMM({ chat: true });
  const add = useAddModelFromPath();
  const settings = useSettings();
  // Canonical capability vocabulary — same meta-driven list as the HF modal
  // and the recipe editor (this surface used to hand-roll a third variant).
  const enums = useMetaEnums();

  useEffectMM(() => {
    if (open) {
      setPath("");
      setId("");
      setName("");
      setLabelSel({ chat: true });
      add.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const scanRoot = settings.data?.models?.roots?.[0] || "";
  const labels = Object.entries(labelSel).filter(([, v]) => v).map(([k]) => k);

  const onSubmit = async () => {
    if (!path.trim()) return;
    try {
      const body = { path: path.trim() };
      if (id.trim()) body.id = id.trim();
      if (name.trim()) body.name = name.trim();
      if (labels.length) body.labels = labels;
      const res = await add.mutateAsync(body);
      window.__hal0Toast && window.__hal0Toast(
        `Registered ${res?.name || res?.id || "model"}`, "ok",
      );
      onClose();
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Add failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  const canSubmit = !!path.trim() && !add.isPending;

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Catalog · add model"
      title="Add model by path"
      width={640}
      foot={
        <>
          <span>File must be readable by <span className="mono">hal0-api</span></span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Cancel</button>
            <button className="btn sm" disabled={!canSubmit} onClick={onSubmit}>
              {add.isPending ? "Registering…" : "Register"}
            </button>
          </span>
        </>
      }
    >
      <div className="form-row">
        <div className="form-lbl">
          <span>Absolute path <span className="req">*</span></span>
          <span className="sub">
            .gguf · .safetensors · etc{scanRoot ? <> · scan dir is <span className="mono" style={{color: "var(--fg-3)"}}>{scanRoot}</span></> : null}
          </span>
        </div>
        <div className="form-ctl">
          <input
            className="input mono"
            value={path}
            onChange={e => setPath(e.target.value)}
            placeholder={scanRoot ? `${scanRoot}/local/qwen3-4b-q4_k_m.gguf` : "/mnt/ai-models/local/qwen3-4b-q4_k_m.gguf"}
            autoFocus
          />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Registry id</span>
          <span className="sub">empty → derived from filename</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" value={id} onChange={e => setId(e.target.value)} placeholder="my-model" />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Display name</span>
          <span className="sub">empty → derived from filename</span>
        </div>
        <div className="form-ctl">
          <input className="input mono" value={name} onChange={e => setName(e.target.value)} />
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Labels</span>
          <span className="sub">empty → auto-detect from header / filename</span>
        </div>
        <div className="form-ctl" style={{display: "flex", flexWrap: "wrap", gap: 8}}>
          {enums.model_capabilities.map(l => (
            <label key={l} className="checkbox-row">
              <input
                type="checkbox"
                checked={!!labelSel[l]}
                onChange={e => setLabelSel({ ...labelSel, [l]: e.target.checked })}
              />
              <span className="mono">{l}</span>
            </label>
          ))}
        </div>
      </div>

      {add.isError && (
        <div className="err">
          {add.error?.code === "model.path_missing"
            ? `Path not found or unreadable: ${path}`
            : add.error?.code === "model.unsupported_format"
              ? `Unsupported file extension`
              : add.error?.code === "model.already_exists"
                ? `Already registered — set a different id or use overwrite`
                : `Add failed: ${add.error?.message || "unreachable"}`}
        </div>
      )}
    </Modal>
  );
}

// ─── Scan directory + bulk register ────────────────────────────
//
// PR feat/models-scan-and-add-by-path: walks an absolute path
// (defaulting to [models].roots[0]) recursively via
// /api/models/scan/preview, surfaces every candidate with an
// already-registered badge, and registers the picked rows one at a
// time through /api/models/add-from-path. No batch endpoint by design
// — the per-row loop keeps the failure mode obvious (one bad path
// doesn't poison the rest).
function ScanDirectoryModal({ open, onClose }) {
  const settings = useSettings();
  const preview = useScanPreview();
  const add = useAddModelFromPath();
  const modelsHook = useModels();

  const [scanPath, setScanPath] = useStateMM("");
  const [recursive, setRecursive] = useStateMM(true);
  const [picked, setPicked] = useStateMM(() => new Set());
  const [progress, setProgress] = useStateMM(null); // { done, total } | null

  useEffectMM(() => {
    if (open) {
      preview.reset();
      add.reset();
      setPicked(new Set());
      setProgress(null);
      setRecursive(true);
      // Seed the input with the configured scan root so the operator
      // can hit Scan immediately after pinning /mnt/ai-models in Settings.
      const root = settings.data?.models?.roots?.[0] || "";
      setScanPath(root);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, settings.data]);

  const knownPaths = useMemoMM(
    () => new Set((modelsHook.data || []).map(m => m.path).filter(Boolean)),
    [modelsHook.data],
  );

  const onScan = async () => {
    if (!scanPath.trim()) return;
    setPicked(new Set());
    setProgress(null);
    try {
      await preview.mutateAsync({ paths: [scanPath.trim()], recursive });
    } catch (e) {
      window.__hal0Toast && window.__hal0Toast(
        `Scan failed — ${e?.message || "see logs"}`, "err",
      );
    }
  };

  const rows = preview.data?.preview ?? [];

  const togglePick = (path) => {
    setPicked(prev => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path); else next.add(path);
      return next;
    });
  };

  const togglePickAll = () => {
    const allPickable = rows.filter(r => !knownPaths.has(r.resolved_path) && !knownPaths.has(r.path));
    if (picked.size === allPickable.length) {
      setPicked(new Set());
    } else {
      setPicked(new Set(allPickable.map(r => r.path)));
    }
  };

  const onRegisterPicked = async () => {
    const toReg = rows.filter(r => picked.has(r.path));
    if (!toReg.length) return;
    setProgress({ done: 0, total: toReg.length, failed: 0 });
    for (let i = 0; i < toReg.length; i++) {
      const r = toReg[i];
      try {
        await add.mutateAsync({
          path: r.path,
          labels: r.suggested_capabilities && r.suggested_capabilities.length
            ? r.suggested_capabilities
            : undefined,
        });
        setProgress(p => ({ ...p, done: p.done + 1 }));
      } catch (e) {
        // Keep going — one bad row shouldn't stall the rest.
        setProgress(p => ({ ...p, done: p.done + 1, failed: (p.failed || 0) + 1 }));
      }
    }
    window.__hal0Toast && window.__hal0Toast(
      `Registered ${toReg.length} model${toReg.length > 1 ? "s" : ""}`, "ok",
    );
    // Re-scan so the already-registered badges update without closing.
    await onScan();
  };

  const scanRootMissing = !settings.data?.models?.roots?.[0];

  return (
    <Modal
      open={open}
      onClose={onClose}
      eyebrow="Catalog · discover"
      title="Scan directory for models"
      width={780}
      foot={
        <>
          <span className="mono" style={{fontSize: 11, color: "var(--fg-4)"}}>
            {rows.length > 0 ? `${rows.length} found · ${picked.size} picked` : "—"}
          </span>
          <span style={{display: "inline-flex", gap: 8}}>
            <button className="btn ghost sm" onClick={onClose}>Close</button>
            <button
              className="btn sm"
              disabled={!picked.size || !!progress && progress.done < progress.total}
              onClick={onRegisterPicked}
            >
              {progress && progress.done < progress.total
                ? `Registering ${progress.done}/${progress.total}…`
                : `Register ${picked.size || ""} picked`}
            </button>
          </span>
        </>
      }
    >
      {scanRootMissing && (
        <div style={{padding: "10px 12px", marginBottom: 12, background: "var(--warn-soft)", border: "1px solid var(--warn-line)", borderRadius: "var(--rad-sm)", fontFamily: "var(--jbm)", fontSize: 12, color: "var(--warn)"}}>
          No scan dir set. Type an absolute path below, or set <span className="mono">[models].roots</span> in <span className="mono">Settings → Storage</span> to default it.
        </div>
      )}

      <div className="form-row">
        <div className="form-lbl">
          <span>Scan path <span className="req">*</span></span>
          <span className="sub">absolute path</span>
        </div>
        <div className="form-ctl" style={{display: "flex", gap: 8}}>
          <input
            className="input mono"
            value={scanPath}
            onChange={e => setScanPath(e.target.value)}
            placeholder="/mnt/ai-models"
            style={{flex: 1}}
          />
          <button className="btn ghost sm" disabled={!scanPath.trim() || preview.isPending} onClick={onScan}>
            {preview.isPending ? "Scanning…" : "Scan"}
          </button>
        </div>
      </div>

      <div className="form-row">
        <div className="form-lbl">
          <span>Walk</span>
          <span className="sub">off → top-level files only</span>
        </div>
        <div className="form-ctl">
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={recursive}
              onChange={e => setRecursive(e.target.checked)}
            />
            <span className="mono">Recurse into subdirectories</span>
          </label>
        </div>
      </div>

      {preview.isError && (
        <div className="err" style={{marginBottom: 10}}>
          {preview.error?.message || "Scan failed"}
        </div>
      )}

      {rows.length > 0 && (
        <div style={{border: "1px solid var(--line-soft)", borderRadius: "var(--rad-sm)", marginTop: 10}}>
          <div style={{display: "grid", gridTemplateColumns: "32px 1fr 90px 80px 110px", gap: 12, padding: "8px 12px", background: "var(--bg)", borderBottom: "1px solid var(--line-soft)", fontFamily: "var(--jbm)", fontSize: 10, color: "var(--fg-4)", textTransform: "uppercase", letterSpacing: "0.08em", alignItems: "center"}}>
            <span>
              <input
                type="checkbox"
                onChange={togglePickAll}
                checked={
                  rows.length > 0 &&
                  picked.size === rows.filter(r => !knownPaths.has(r.resolved_path) && !knownPaths.has(r.path)).length
                }
              />
            </span>
            <span>path</span>
            <span style={{textAlign: "right"}}>size</span>
            <span>kind</span>
            <span>status</span>
          </div>
          <div style={{maxHeight: 320, overflow: "auto"}}>
            {rows.map(r => {
              const registered = knownPaths.has(r.resolved_path) || knownPaths.has(r.path);
              return (
                <div
                  key={r.path}
                  style={{
                    display: "grid",
                    gridTemplateColumns: "32px 1fr 90px 80px 110px",
                    gap: 12,
                    padding: "8px 12px",
                    borderBottom: "1px solid var(--line-soft)",
                    fontFamily: "var(--jbm)",
                    fontSize: 11.5,
                    color: registered ? "var(--fg-4)" : "var(--fg-2)",
                    alignItems: "center",
                  }}
                >
                  <span>
                    <input
                      type="checkbox"
                      disabled={registered}
                      checked={picked.has(r.path)}
                      onChange={() => togglePick(r.path)}
                    />
                  </span>
                  <span style={{wordBreak: "break-all", paddingRight: 8}}>
                    {r.path}
                    <div style={{fontSize: 10, color: "var(--fg-5)", marginTop: 2}}>
                      {r.suggested_name || ""} · {(r.suggested_capabilities || []).join(", ") || "—"}
                    </div>
                  </span>
                  <span style={{textAlign: "right", color: "var(--fg-3)"}}>{fmtBytes(r.size_bytes)}</span>
                  <span style={{color: "var(--fg-3)"}}>{r.kind}</span>
                  <span>
                    {registered
                      ? <span className="chip ok">registered</span>
                      : <span className="chip" style={{color: "var(--accent)", borderColor: "var(--accent-line)", background: "var(--accent-soft)"}}>new</span>}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {preview.isSuccess && rows.length === 0 && (
        <div style={{padding: "20px 12px", textAlign: "center", color: "var(--fg-4)", fontFamily: "var(--jbm)", fontSize: 12}}>
          No candidate files under <span className="mono">{scanPath}</span>.
        </div>
      )}

      {progress && progress.failed > 0 && (
        <div className="err" style={{marginTop: 10}}>
          {progress.failed} of {progress.total} failed to register — check the toast log.
        </div>
      )}
    </Modal>
  );
}

Object.assign(window, { AddByHfModal, AddByPathModal, ScanDirectoryModal, RecipeEditorModal, DeleteModelDialog, UsedByPanel, OnDiskPanel, DownloadRow });
