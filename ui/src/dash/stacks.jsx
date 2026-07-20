// hal0 dashboard — Stacks view (Focus layout).
//
// A Stack is a named, portable bundle of slots + their profiles + model
// assignments. The Focus layout (design handoff: stacks_overhaul) surfaces the
// currently-applied stack as a full-width hero with per-slot live state, then
// lists the rest as a compact library grid below — keeping attention on what's
// running while giving fast access to swap.
//
// Real data: GET /api/stacks (useStacks) + live slot state (useSlots) + the
// local registry (useModels, for "model not available → pull"). Load goes
// through the real apply (commit + converge, create-on-apply); Pull starts a
// real model pull job; Export downloads the portable envelope; Import / New /
// Snapshot reuse the existing flows. Styles: .stk-* in stacks.css.

import { useState, useEffect, useMemo } from 'react'
import {
  useStacks,
  useStackCreate,
  useStackUpdate,
  useStackDelete,
  useStackApply,
  useStackExport,
  useStackImport,
  useStackSnapshot,
} from '@/api/hooks/useStacks'
import { useModels } from '@/api/hooks/useModels'
import { useProfiles } from '@/api/hooks/useProfiles'
import { useSlots } from '@/api/hooks/useSlots'
import { useSystemInfo } from '@/api/hooks/useRuntimes'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { deviceClassForToken, profileDeviceClass } from '@/lib/deviceMeta'
import { isMtpEligibleModel } from '@/lib/normalizeApiModel'
import { api } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { slotIndicatorFromPhase } from './slot-status.js'

// Device hue + label map for the editor selectors — built from meta.devices
// (GET /api/meta/enums via useMetaEnums, static fallback when the endpoint is
// absent). Includes the img/ComfyUI device, previously missing here, so image
// slots can be expressed in stacks. Hue token: the GPU devices' legacy backend
// (rocm/vulkan), else the device_class (npu/cpu/img) — matches the shared
// --dev-* palette.
function buildDeviceMeta(enums) {
  const out = {};
  for (const d of enums.devices) {
    const hue = d.legacy_backend || d.device_class || 'cpu';
    out[d.id] = {
      label: d.label || d.id,
      color: `var(--dev-${hue})`,
      device_class: d.device_class,
      recommended: !!d.recommended,
      description: d.description || '',
      default_profile: d.default_profile || null,
    };
  }
  return out;
}

// `NAME_RE` (slug regex) and `toast` are shared globals from primitives.jsx.

// mtp defaults to null = AUTO: MTP is derived from model-eligibility × profile
// opt-in on apply, so a row carries an explicit boolean only when the user
// forces it — no stale `mtp:false` frozen onto every row.
// spec-hw-slot-ownership §2: a slot owns its hardware — device + BINARY (runner
// image ref) live on the slot. `profile` stays as a device-agnostic tune
// template (§1). `binary` empty = HW-gated default derived from `device`.
const BLANK_SLOT = { slot: '', model: '', device: 'gpu-rocm', binary: '', profile: '', mtp: null, capabilities: [] };
const BLANK = { name: '', description: '', icon: '', tags: '', slots: [{ ...BLANK_SLOT }] };

// Live-slot → dot class, derived from the SHARED slot-status classifier
// (slot-status.js) instead of the old local DOT_STATES re-derivation, so the
// hero dots agree with every other slot surface (recency + health gates
// included). `stale` renders as the `ready` dot, same mapping the Inference
// pane uses; a missing live slot is offline.
function liveDotCls(liveSlot) {
  if (!liveSlot) return 'offline';
  const cls = slotIndicatorFromPhase(liveSlot).cls;
  if (cls === 'stale') return 'ready';
  return cls; // serving | warming | error | offline
}

// ── status dot ────────────────────────────────────────────────────────────────

function D({ state, sz = 6 }) {
  return <span className={'dot ' + (state || 'offline')} style={{ width: sz, height: sz, flexShrink: 0 }} />;
}

// ── view-model: project a stack into the Focus shape ──────────────────────────
// slots: [{ name, model, profile, device, state, available }]. `profile` and
// `device` are kept as SEPARATE fields (the old VM conflated them into one
// `profile: sl.profile || sl.device` string); displays that want a single
// line join them via profileLine(). `state` is the live slot status for the
// active stack (shared slot-status classifier), "offline" otherwise.
// `available` is false only when a referenced model isn't in the local
// registry (→ pull affordance).

function profileLine(s) {
  return [s.profile, s.device].filter(Boolean).join(' · ');
}

function buildVM(stack, modelSet, liveByName, activeSlug) {
  const active = stack.slug === activeSlug;
  const slots = [];
  for (const sl of stack.slots || []) {
    if (sl.model) {
      slots.push({
        name: sl.slot,
        model: sl.model,
        profile: sl.profile || '',
        device: sl.device || '',
        state: active ? liveDotCls(liveByName[sl.slot]) : 'offline',
        available: modelSet.has(sl.model),
      });
    }
    for (const row of sl.capabilities || []) {
      if (!row.model) continue;
      slots.push({
        name: row.child,
        model: row.model,
        profile: '',
        device: row.device || '',
        state: 'offline',
        available: modelSet.has(row.model),
      });
    }
  }
  return {
    id: stack.slug,
    slug: stack.slug,
    name: stack.name || stack.slug,
    intent: stack.description || '',
    seed: !!stack.seed,
    active,
    drift: active ? (stack.drift || 'clean') : null,
    tags: stack.tags || [],
    slots,
  };
}

function missingCount(vm) { return vm.slots.filter(s => !s.available).length; }

// ── Pull-missing-models dialog ────────────────────────────────────────────────

function PullDialog({ vm, onPull, pulled, onClose }) {
  const missing = vm.slots.filter(s => !s.available);
  return (
    <div className="stk-scrim" onMouseDown={onClose}>
      <div className="stk-dialog" onMouseDown={e => e.stopPropagation()} role="dialog" aria-label="Pull missing models">
        <div className="stk-dlg-h">
          <span className="stk-dlg-eye">Pull missing models · {vm.name}</span>
          <button className="stk-dlg-x" onClick={onClose} aria-label="Close">{Icons.close}</button>
        </div>
        <div className="stk-dlg-b">
          <div className="stk-dlg-hint">
            These models aren't available locally. Pull them before loading this stack.
          </div>
          {missing.map(s => {
            const done = pulled.includes(s.model);
            return (
              <div key={s.name + s.model} className="stk-pull-item">
                <span className="stk-pull-slot">{s.name}</span>
                <span className="stk-pull-model">{s.model}</span>
                {done
                  ? <span className="stk-pull-done">{Icons.check} queued</span>
                  : <button className="btn sm" onClick={() => onPull(s.model)}>{Icons.download} Pull</button>}
              </div>
            );
          })}
        </div>
        <div className="stk-dlg-f">
          <button className="btn ghost sm" onClick={onClose}>Close</button>
          {missing.some(s => !pulled.includes(s.model)) && (
            <button className="btn sm" onClick={() => missing.forEach(s => onPull(s.model))}>
              Queue all {missing.length}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Load-stack confirm dialog ─────────────────────────────────────────────────

function LoadDialog({ vm, onLoad, onPull, busy, onClose }) {
  const missing = vm.slots.filter(s => !s.available);
  const hasMissing = missing.length > 0;
  return (
    <div className="stk-scrim" onMouseDown={() => { if (!busy) onClose(); }}>
      <div className="stk-dialog" onMouseDown={e => e.stopPropagation()} role="dialog" aria-label="Load stack" aria-busy={busy}>
        <div className="stk-dlg-h">
          <span className="stk-dlg-eye">Load stack</span>
          <button className="stk-dlg-x" onClick={onClose} aria-label="Close" disabled={busy}>{Icons.close}</button>
        </div>
        <div className="stk-dlg-b">
          <div>
            <div className="stk-dlg-stack">{vm.name}</div>
            <div className="stk-dlg-hint" style={{ marginTop: 4 }}>{vm.intent}</div>
          </div>
          {hasMissing && (
            <div className="stk-dlg-warn">
              {Icons.alert}
              {missing.length} model{missing.length > 1 ? 's' : ''} not found locally — those slots are skipped unless pulled first.
            </div>
          )}
          <div className="stk-slot-list">
            {vm.slots.map(s => (
              <div key={s.name + s.model} className={'stk-slot-row' + (!s.available ? ' miss' : '')}>
                <D state={s.available ? 'ready' : 'offline'} sz={6} />
                <span className="sname">{s.name}</span>
                <span className="smodel">{s.model}</span>
                {!s.available && <span className="smiss">not found</span>}
              </div>
            ))}
          </div>
        </div>
        <div className="stk-dlg-f">
          <button className="btn ghost sm" onClick={onClose} disabled={busy}>Cancel</button>
          {hasMissing && (
            <button className="btn sm" style={{ background: 'transparent', color: 'var(--warn)', borderColor: 'var(--warn-line)' }}
              onClick={() => { onClose(); onPull(vm); }} disabled={busy}>
              Pull missing first
            </button>
          )}
          <button className="btn sm" onClick={() => onLoad(vm)} disabled={busy} data-testid="st-load-confirm">
            {busy ? 'Loading…' : hasMissing ? 'Load anyway' : 'Load stack'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Focus hero (active stack) ─────────────────────────────────────────────────

function HeroPanel({ vm, isCustom, onPull, onExport, onReapply, onEdit }) {
  const miss = missingCount(vm);
  return (
    <div className="stk-hero">
      <div className="stk-hero-h">
        <div>
          <div className="stk-hero-eye">Active stack</div>
          <div className="stk-hero-name">{vm.name}</div>
          <div className="stk-hero-intent">{vm.intent || 'no description'}</div>
        </div>
        <div className="stk-hero-meta">
          <span className="stk-badge live"><D state="serving" sz={6} />running</span>
          <span className="stk-hero-ver">{vm.drift === 'modified' ? 'modified since apply' : 'clean'}</span>
        </div>
      </div>
      <div className="stk-hero-slots">
        {vm.slots.map(s => (
          <div key={s.name + s.model} className="stk-hero-slot">
            <div className="stk-hs-row">
              <D state={s.state} sz={7} />
              <span className="stk-hs-name">{s.name}</span>
              <span className={'stk-hs-state ' + s.state}>{s.available ? s.state : 'no model'}</span>
            </div>
            <div className="stk-hs-profile">{profileLine(s)}</div>
            <div className="stk-hs-model">{s.model}</div>
          </div>
        ))}
      </div>
      <div className="stk-hero-f">
        <div className="stk-hero-tags">
          {vm.tags.map(t => <span key={t} className="stk-tag">{t}</span>)}
          <span className="stk-tag shared">{vm.seed ? 'seed' : 'custom'}</span>
        </div>
        <span className="stk-spacer" />
        {miss > 0 && (
          <button className="stk-missing-btn" onClick={() => onPull(vm)}>{Icons.alert} {miss} missing</button>
        )}
        {isCustom && <button className="btn ghost sm" onClick={() => onEdit(vm)}>{Icons.edit} Edit</button>}
        <button className="btn ghost sm" onClick={() => onExport(vm)}>{Icons.download} Export</button>
        <button className="btn sm" onClick={() => onReapply(vm)} data-testid={`st-reapply-${vm.slug}`}>Re-apply</button>
      </div>
    </div>
  );
}

// ── Library card (inactive stacks) ────────────────────────────────────────────

function LibCard({ vm, idx, isCustom, onLoad, onPull, onExport, onClone, onEdit, onDelete }) {
  const miss = missingCount(vm);
  return (
    <div className="stk-lib-card" style={{ animationDelay: idx * 35 + 'ms' }}>
      <div className="stk-lib-h">
        <div className="stk-lib-id">
          <div className="stk-lib-name">{vm.name}</div>
          <div className="stk-lib-intent">{vm.intent || 'no description'}</div>
        </div>
        <span className="mono" style={{ fontSize: 9.5, color: 'var(--fg-5)', paddingTop: 2, flexShrink: 0 }}>
          {vm.seed ? 'seed' : 'custom'}
        </span>
      </div>
      <div className="stk-lib-slotlist">
        {vm.slots.map(s => (
          <div key={s.name + s.model} className={'stk-lib-slotrow' + (!s.available ? ' miss' : '')}>
            <D state="offline" sz={5} />
            <span className="stk-csr-name mono">{s.name}</span>
            <span className="stk-csr-model mono">{s.model}</span>
            {!s.available && Icons.alert}
          </div>
        ))}
        {vm.slots.length === 0 && <div className="stk-lib-slotrow"><span className="stk-csr-model mono" style={{ color: 'var(--fg-5)' }}>no slots</span></div>}
      </div>
      <div className="stk-lib-f">
        {miss > 0 && (
          <button className="stk-missing-btn sm" onClick={() => onPull(vm)}>{Icons.alert} {miss} missing</button>
        )}
        <span className="stk-spacer" />
        <button className="stk-icon-btn" style={{ width: 26, height: 26 }} title="Clone" onClick={() => onClone(vm)}>{Icons.copy}</button>
        {isCustom && (
          <>
            <button className="stk-icon-btn" style={{ width: 26, height: 26 }} title="Edit" onClick={() => onEdit(vm)}>{Icons.edit}</button>
            <button className="stk-icon-btn" style={{ width: 26, height: 26 }} title="Delete" onClick={() => onDelete(vm)}>{Icons.trash}</button>
          </>
        )}
        <button className="stk-icon-btn" style={{ width: 26, height: 26 }} title="Export" onClick={() => onExport(vm)}>{Icons.download}</button>
        <button className="btn sm" onClick={() => onLoad(vm)} data-testid={`st-load-${vm.slug}`}>Load</button>
      </div>
    </div>
  );
}

// ── Import modal (file → dry-run resolve → commit) ──────────────────────────

// Thin wrapper over the shared <ImportDialog>: owns the useStackImport hook and
// the stacks-specific preview (model resolutions + unresolvable warning) and
// filename→slug derivation. The dialog shell + name input + commit button live
// in primitives.jsx.
function ImportModal({ existing, onClose, onImported }) {
  const imp = useStackImport();
  const deriveSlug = (file) => {
    const base = (file.name || '').replace(/\.hal0stack\.json$|\.json$/i, '')
      .toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 32);
    return base || 'imported-stack';
  };
  const renderPreview = (report) => (
    <>
      <div className="stk-dlg-hint">
        {report.name || 'stack'} · schema v{report.schema_version} · checksum {report.checksum_ok ? 'ok' : '⚠ mismatch'}
      </div>
      <div className="stk-slot-list">
        {(report.resolutions || []).map(r => (
          <div key={r.model_id} className={'stk-slot-row' + (r.status === 'unresolvable' ? ' miss' : '')}>
            <span className="smodel">{r.model_id}</span>
            <span className="smiss" style={{ color: r.status === 'present' ? 'var(--ok)' : r.status === 'pullable' ? 'var(--info)' : 'var(--err)' }}>{r.status}</span>
          </div>
        ))}
        {(!report.resolutions || report.resolutions.length === 0) && (
          <div className="stk-dlg-hint" style={{ color: 'var(--fg-5)' }}>no model references</div>
        )}
      </div>
      {report.unresolvable?.length > 0 && (
        <div className="stk-dlg-warn">{Icons.alert}{report.unresolvable.length} model(s) unresolvable — those slots import disabled.</div>
      )}
    </>
  );
  return (
    <ImportDialog
      title="Import stack"
      ariaLabel="Import stack"
      fileHint="Choose a .hal0stack.json file"
      fileTestid="st-import-file"
      nameTestid="st-import-slug"
      confirmTestid="st-import-confirm"
      namePlaceholder="my-stack"
      invalidCopy="Not a valid .hal0stack.json envelope"
      existing={existing}
      deriveName={deriveSlug}
      renderPreview={renderPreview}
      dryRun={(env) => imp.mutateAsync({ envelope: env, dry_run: true })}
      commit={async ({ envelope, name }) => {
        await imp.mutateAsync({ envelope, slug: name });
        toast(`Imported as ${name}`, 'ok');
      }}
      onClose={onClose}
      onImported={onImported}
    />
  );
}

// ── Editor drawer (create / edit / clone) ───────────────────────────────────

function fromStack(s) {
  return {
    name: s.name || '',
    description: s.description || s.intent || '',
    icon: s.icon || '',
    tags: (s.tags || []).join(', '),
    slots: (s.slots || []).map(e => ({
      slot: e.slot || e.name || '',
      model: e.model || '',
      device: e.device || 'gpu-rocm',
      binary: e.binary || '',
      profile: e.profile || '',
      mtp: e.mtp ?? null, // preserve Auto (null) vs explicit on/off
      capabilities: e.capabilities || [],
    })),
  };
}

// The editor drawer. Consumes the shared FormDrawer shell + useForm hook +
// shared FormRow from primitives.jsx. Named StackDrawer (not Drawer) to avoid
// shadowing the primitives.jsx Drawer global. Validation rules (slug/slot) and
// the submit body are preserved verbatim.
function StackDrawer({ mode, source, existing = [], onClose, onSaved }) {
  const isEdit = mode === 'edit';
  const models = useModels().data || [];
  const profiles = useProfiles().data || [];
  const liveSlots = (useSlots().data || []).filter(s => (s.kind ?? 'local') === 'local');
  const enums = useMetaEnums();
  const deviceMeta = useMemo(() => buildDeviceMeta(enums), [enums]);
  const deviceIds = Object.keys(deviceMeta);
  // BINARY (runner image ref) options — RUNNER_IMAGES keys via system-info.
  const runnerBackends = useSystemInfo().data?.backends ?? {};
  const binaryKeys = Object.keys(runnerBackends);

  const create = useStackCreate();
  const update = useStackUpdate();

  const deriveInitial = () => {
    if (mode === 'create') {
      const base = source ? fromStack(source) : { ...BLANK, slots: [{ ...BLANK_SLOT }] };
      return { ...base, _slug: '' };
    }
    const base = fromStack(source);
    if (mode === 'clone') {
      const suffix = source.seed ? '-custom' : '-copy';
      return { ...base, _slug: (source.slug + suffix).slice(0, 32) };
    }
    return { ...base, _slug: source.slug };
  };

  const f = useForm({ deriveInitial, resetKey: `${mode}:${source?.slug ?? ''}` });
  const v = f.values;
  const slug = v._slug || '';

  const setSlug = (val) => f.set('_slug', val);
  const setSlot = (i, k, val) => f.setValues(ff => ({ ...ff, slots: ff.slots.map((s, j) => j === i ? { ...s, [k]: val } : s) }));
  const addSlot = () => f.setValues(ff => ({ ...ff, slots: [...ff.slots, { ...BLANK_SLOT }] }));
  const rmSlot = (i) => f.setValues(ff => ({ ...ff, slots: ff.slots.filter((_, j) => j !== i) }));

  const taken = existing.filter(n => !(isEdit && n === source?.slug));
  const slugErr = !slug.trim() ? 'Slug is required'
    : !NAME_RE.test(slug) ? 'lowercase · digits · - · _ · ≤32'
    : taken.includes(slug) ? `“${slug}” already exists` : '';
  const slotErr = !v.slots.length ? 'add at least one slot'
    : v.slots.some(s => !s.slot.trim()) ? 'every slot needs a name' : '';
  const blocking = (!isEdit && !!slugErr) || !!slotErr;

  async function submit(e) {
    e.preventDefault();
    if (blocking) { toast('Fix the highlighted fields', 'warn'); return; }
    f.setSubmitting(true);
    const body = {
      name: v.name.trim(),
      description: v.description.trim(),
      icon: v.icon.trim(),
      tags: v.tags.split(',').map(t => t.trim()).filter(Boolean),
      slots: v.slots.map(s => ({
        slot: s.slot.trim(),
        model: s.model || null,
        device: s.device || null,
        binary: s.binary || null,
        profile: s.profile || null,
        mtp: s.mtp,
        capabilities: s.capabilities || [],
      })),
    };
    try {
      if (isEdit) {
        await update.mutateAsync({ slug: source.slug, stack: body });
        toast(`Stack ${source.slug} updated`, 'ok');
      } else {
        await create.mutateAsync({ slug: slug.trim(), stack: body });
        toast(`Stack ${slug.trim()} created`, 'ok');
      }
      onSaved();
    } catch (err) {
      const code = err?.code || '';
      if (code === 'stacks.exists') toast(`A stack named ${slug} already exists`, 'err');
      else if (code === 'stacks.seed_immutable') toast('Seed stacks cannot be modified', 'err');
      else toast(err?.message || 'Save failed', 'err');
    } finally { f.setSubmitting(false); }
  }

  const title = isEdit ? `Edit · ${source.slug}`
    : mode === 'clone' ? (source.seed ? `Edit a copy · ${source.slug}` : `Clone · ${source.slug}`)
    : 'New stack';
  const eyebrow = isEdit ? 'EDIT' : mode === 'clone' ? (source?.seed ? 'EDIT A COPY' : 'CLONE') : 'CREATE';

  return (
    <FormDrawer
      eyebrow={eyebrow}
      title={title}
      panelClassName="st-drawer"
      submitting={f.submitting}
      dirty={f.isDirty}
      onClose={onClose}
      foot={({ requestClose }) => (
        <>
          <span className="pf-grow" />
          <button className="pf-btn" onClick={requestClose} type="button" disabled={f.submitting}>Cancel</button>
          <button className="pf-btn primary" onClick={submit} disabled={f.submitting} data-testid="st-btn-submit">
            {f.submitting ? 'Saving…' : isEdit ? 'Save changes' : 'Create stack'}
          </button>
        </>
      )}
    >
      <form className="pf-drawer-body" onSubmit={submit} noValidate>
        <FormRow label="Slug" req sub="lowercase · - _ · ≤32" error={!isEdit ? slugErr : null}>
          <input className={'pf-input mono' + (!isEdit && slugErr ? ' err' : '')} value={slug}
            onChange={e => setSlug(e.target.value)} placeholder="my-stack" maxLength={32}
            disabled={isEdit} data-testid="st-input-slug" />
        </FormRow>
        <FormRow label="Name" sub="display label">
          <input className="pf-input" value={v.name}
            onChange={e => f.set('name', e.target.value)} placeholder="Coding" data-testid="st-input-name" />
        </FormRow>
        <FormRow label="Description" sub="what it's for">
          <textarea className="pf-input pf-textarea" rows={2} value={v.description}
            onChange={e => f.set('description', e.target.value)}
            placeholder="Fast coder + agentic muscle + repo retrieval" />
        </FormRow>
        <FormRow label="Tags" sub="comma-separated">
          <input className="pf-input mono" value={v.tags}
            onChange={e => f.set('tags', e.target.value)} placeholder="coding, fast" />
        </FormRow>

        <div className="st-slots-edit">
          <div className="pf-row-lbl" style={{ marginBottom: 6 }}>
            <span>Slots{slotErr && <span className="pf-msg err mono hint err" style={{ marginLeft: 8 }}>{Icons.alert}{slotErr}</span>}</span>
          </div>
          <datalist id="st-existing-slots">
            {liveSlots.map(s => <option key={s.name} value={s.name} />)}
          </datalist>
          {v.slots.map((s, i) => {
            const isNew = !!s.slot && !liveSlots.some(ls => ls.name === s.slot);
            const dm = deviceMeta[s.device];
            // Profile compatibility for this row's device: match the
            // profile's device_class (explicit, or gpu inferred from its
            // rocm/vulkan backend — same rule as profiles.jsx backendOf)
            // against the picked device's class. Unknown class on either
            // side never filters. Incompatible profiles stay listed as a
            // disabled escape hatch (and the current pick is never trapped).
            const rowClass = dm?.device_class ?? deviceClassForToken(s.device, enums);
            const compatProfiles = [];
            const incompatProfiles = [];
            for (const p of profiles) {
              const pc = profileDeviceClass(p);
              (!pc || !rowClass || pc === rowClass ? compatProfiles : incompatProfiles).push(p);
            }
            // MTP is DERIVED (model-eligibility × profile opt-in) and defaults
            // to Auto — no stale boolean per row. Surface the tri-state control
            // whenever EITHER side is MTP-relevant (eligible model OR an
            // MTP-opting profile) or an explicit override is persisted — the
            // drawer's always-show rationale, bounded so rows where MTP is
            // meaningless stay compact.
            const rowModel = models.find(m => m.id === s.model);
            // Same eligibility rule as the server: `mtp` tag OR MTP name marker.
            const modelEligible = isMtpEligibleModel(rowModel);
            const profileOptsIn = !!profiles.find(p => p.name === s.profile)?.mtp;
            const showMtp = modelEligible || profileOptsIn || s.mtp != null;
            return (
              <div className="st-slot-card" key={i}>
                <div className="st-slot-head">
                  <input className="pf-input mono st-slot-name" value={s.slot} list="st-existing-slots"
                    onChange={e => setSlot(i, 'slot', e.target.value)} placeholder="slot name — pick or type…" maxLength={32}
                    title={isNew ? 'New slot — created on apply' : 'Existing slot'} data-testid={`st-slot-name-${i}`} />
                  {isNew && <span className="st-slot-new mono" title="Created on apply">new</span>}
                  <span className="pf-grow" />
                  <button type="button" className="pf-btn danger st-slot-rm" onClick={() => rmSlot(i)} title="Remove slot" data-testid={`st-slot-rm-${i}`}>{Icons.trash}</button>
                </div>
                <div className="st-slot-fields">
                  <label className="st-fld st-fld-model">
                    <span className="st-fld-lbl">Model</span>
                    <select className="pf-input mono" value={s.model}
                      onChange={e => setSlot(i, 'model', e.target.value)} data-testid={`st-slot-model-${i}`}>
                      <option value="">— model —</option>
                      {models.map(m => <option key={m.id} value={m.id}>{m.id}</option>)}
                    </select>
                  </label>
                  <label className="st-fld">
                    <span className="st-fld-lbl">Device</span>
                    <select className="pf-input mono" value={s.device}
                      title={dm?.description || undefined}
                      onChange={e => setSlot(i, 'device', e.target.value)} data-testid={`st-slot-device-${i}`}>
                      {/* keep an out-of-vocab persisted device selectable */}
                      {s.device && !deviceIds.includes(s.device) && (
                        <option value={s.device}>{s.device}</option>
                      )}
                      {deviceIds.map(d => (
                        <option key={d} value={d} title={deviceMeta[d].description}>
                          {deviceMeta[d].label}{deviceMeta[d].recommended ? ' ★' : ''}
                        </option>
                      ))}
                    </select>
                    {dm && (dm.recommended || dm.description) && (
                      <span className="mono" style={{ fontSize: 9.5, lineHeight: 1.4, marginTop: 2, color: dm.recommended ? 'var(--ok)' : 'var(--fg-5)' }}>
                        {dm.recommended ? '★ recommended' : dm.description}
                      </span>
                    )}
                  </label>
                  {/* BINARY — the runner image ref (spec-hw-slot-ownership §2).
                      Empty = HW-gated default derived from device. */}
                  <label className="st-fld">
                    <span className="st-fld-lbl">Binary</span>
                    <select className="pf-input mono" value={s.binary || ''}
                      onChange={e => setSlot(i, 'binary', e.target.value)} data-testid={`st-slot-binary-${i}`}>
                      <option value="">— default (from device) —</option>
                      {s.binary && !binaryKeys.includes(s.binary) && (
                        <option value={s.binary}>{s.binary}</option>
                      )}
                      {binaryKeys.map(k => (
                        <option key={k} value={k}>
                          {k}{runnerBackends[k]?.backend ? ` · ${runnerBackends[k].backend}` : ''}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="st-fld">
                    <span className="st-fld-lbl">Profile</span>
                    <select className="pf-input mono" value={s.profile} onChange={e => setSlot(i, 'profile', e.target.value)} data-testid={`st-slot-profile-${i}`}>
                      <option value="">— profile —</option>
                      {compatProfiles.map(p => <option key={p.name} value={p.name}>{p.name}</option>)}
                      {incompatProfiles.length > 0 && (
                        <optgroup label={`— other device class${rowClass ? ` (not ${rowClass})` : ''} —`}>
                          {incompatProfiles.map(p => (
                            /* escape hatch: incompatible profiles are listed but
                               disabled; the row's CURRENT pick stays enabled so a
                               pre-existing binding is never trapped. */
                            <option key={p.name} value={p.name} disabled={p.name !== s.profile}>
                              {p.name}{profileDeviceClass(p) ? ` · ${profileDeviceClass(p)}` : ''}
                            </option>
                          ))}
                        </optgroup>
                      )}
                    </select>
                  </label>
                  {showMtp && (
                    <div className="st-fld st-fld-mtp" data-testid={`st-slot-mtp-${i}`}>
                      <span className="st-fld-lbl">Speculative decode (MTP)</span>
                      <MtpControl
                        value={s.mtp ?? null}
                        autoActive={modelEligible && profileOptsIn}
                        inactiveReason={!modelEligible && !profileOptsIn
                          ? 'model has no MTP heads and profile doesn\'t enable MTP'
                          : !modelEligible ? 'model has no MTP heads' : 'profile doesn\'t enable MTP'}
                        forceOnRisky={!modelEligible}
                        onChange={(next) => setSlot(i, 'mtp', next)}
                      />
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          <button type="button" className="pf-btn" onClick={addSlot} data-testid="st-slot-add">{Icons.plus} Add slot</button>
        </div>
      </form>
    </FormDrawer>
  );
}

// ── Delete confirm ──────────────────────────────────────────────────────────

function DeleteConfirm({ vm, onCancel, onConfirmed }) {
  const del = useStackDelete();
  const [busy, setBusy] = useState(false);
  async function handle() {
    setBusy(true);
    try {
      await del.mutateAsync(vm.slug);
      toast(`Stack ${vm.slug} deleted`, 'ok');
      onConfirmed();
    } catch (err) {
      toast(err?.code === 'stacks.seed_immutable' ? 'Seed stacks cannot be deleted' : (err?.message || 'Delete failed'), 'err');
      onCancel();
    } finally { setBusy(false); }
  }
  return (
    <div className="stk-scrim" onMouseDown={() => { if (!busy) onCancel(); }}>
      <div className="stk-dialog" style={{ maxWidth: 420 }} onMouseDown={e => e.stopPropagation()} role="dialog" aria-label="Confirm delete" aria-busy={busy}>
        <div className="stk-dlg-h"><span className="stk-dlg-eye">Delete · {vm.slug}?</span>
          <button className="stk-dlg-x" onClick={onCancel} aria-label="Close" disabled={busy}>{Icons.close}</button>
        </div>
        <div className="stk-dlg-b">
          <div className="stk-dlg-hint">This removes the stack permanently. Loaded slots are unaffected — only the saved bundle is deleted.</div>
        </div>
        <div className="stk-dlg-f">
          <button className="btn ghost sm" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="btn sm" style={{ background: 'transparent', color: 'var(--err)', borderColor: 'var(--err-line)' }}
            onClick={handle} disabled={busy} data-testid="st-btn-delete-confirm">{busy ? 'Deleting…' : 'Delete stack'}</button>
        </div>
      </div>
    </div>
  );
}

// ── Main view ───────────────────────────────────────────────────────────────

function StacksView() {
  const query = useStacks();
  const slotsQuery = useSlots();
  const modelsQuery = useModels();
  const snapshot = useStackSnapshot();
  const apply = useStackApply();
  const exportMut = useStackExport();

  const data = query.data;
  const rawStacks = data?.stacks ?? [];

  const [drawer, setDrawer] = useState(null);
  const [confirm, setConfirm] = useState(null);
  const [loadTgt, setLoadTgt] = useState(null);
  const [pullTgt, setPullTgt] = useState(null);
  const [pulledQ, setPulledQ] = useState([]);
  const [importing, setImporting] = useState(false);
  const [loadBusy, setLoadBusy] = useState(false);

  const modelSet = new Set((modelsQuery.data ?? []).map(m => m.id));
  const liveByName = {};
  for (const s of slotsQuery.data ?? []) liveByName[s.name] = s;

  const vms = rawStacks.map(s => buildVM(s, modelSet, liveByName, data?.active));
  const activeVM = vms.find(v => v.active) || null;
  const library = vms.filter(v => !v.active);
  const totalMiss = vms.reduce((n, v) => n + missingCount(v), 0);
  const existing = rawStacks.map(s => s.slug);
  const isCustom = (vm) => !vm.seed;

  async function onExport(vm) {
    try {
      const env = await exportMut.mutateAsync(vm.slug);
      const blob = new Blob([JSON.stringify(env, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${vm.slug}.hal0stack.json`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast(`Exported ${vm.slug}`, 'ok');
    } catch (err) { toast(err?.message || 'Export failed', 'err'); }
  }

  async function onSnapshot() {
    try {
      const r = await snapshot.mutateAsync({ name: 'Snapshot' });
      setDrawer({ mode: 'create', source: r.stack });
      toast('Captured live config — name it and save', 'info');
    } catch (err) { toast(err?.message || 'Snapshot failed', 'err'); }
  }

  function openPull(vm) { setPulledQ([]); setPullTgt(vm); }

  async function queuePull(model) {
    setPulledQ(q => q.includes(model) ? q : [...q, model]);
    try {
      await api(ENDPOINTS.modelPull(model), { method: 'POST', raw: true });
      toast(`Pulling ${model}…`, 'info');
    } catch (err) {
      toast(err?.message || `Pull failed: ${model}`, 'err');
    }
  }

  async function confirmLoad(vm) {
    setLoadBusy(true);
    try {
      const r = await apply.mutateAsync({ slug: vm.slug, dryRun: false });
      const errs = r?.converged?.errors?.length || 0;
      toast(errs ? `Loaded ${vm.name} with ${errs} slot error(s)` : `Stack “${vm.name}” loaded`, errs ? 'warn' : 'ok');
      setLoadTgt(null);
    } catch (err) {
      toast(err?.message || 'Load failed', 'err');
    } finally { setLoadBusy(false); }
  }

  if (query.isLoading) {
    return <div className="view"><Header /><div className="empty mono" style={{ marginTop: 20 }}>Loading stacks…</div></div>;
  }
  if (query.isError) {
    return (
      <div className="view"><Header />
        <div className="empty mono" style={{ marginTop: 20, color: 'var(--err)' }}>Failed to load stacks: {query.error?.message || 'unknown error'}</div>
      </div>
    );
  }

  function Header() {
    return (
      <div className="vh">
        <span className="sec-label">
          <b>Stacks</b>
          <span className="dim">·</span>
          <span className="meta">runtime</span>
          <span className="dim">·</span>
          <span className="meta">slot + profile + model bundles</span>
        </span>
        <div className="vh-spacer" />
        <button className="btn ghost sm" onClick={() => setImporting(true)} data-testid="st-btn-import">{Icons.attach} Import</button>
        <button className="btn ghost sm" onClick={onSnapshot} data-testid="st-btn-snapshot">{Icons.copy} Snapshot</button>
        <button className="btn sm" onClick={() => setDrawer({ mode: 'create' })} data-testid="st-btn-new">{Icons.plus} New stack</button>
      </div>
    );
  }

  const libProps = {
    isCustomFn: isCustom,
    onLoad: setLoadTgt, onPull: openPull, onExport,
    onClone: vm => setDrawer({ mode: 'clone', source: rawStacks.find(s => s.slug === vm.slug) }),
    onEdit: vm => setDrawer({ mode: 'edit', source: rawStacks.find(s => s.slug === vm.slug) }),
    onDelete: setConfirm,
  };

  return (
    <div className="view">
      <Header />

      <div className="stk-toolbar">
        <div className="stk-summary">
          <span className="stk-sum-item"><b className="num">{vms.length}</b><span className="mono"> stacks</span></span>
          <span className="stk-sum-sep">·</span>
          <span className="stk-sum-item"><b className="num" style={{ color: activeVM ? 'var(--ok)' : 'var(--fg-4)' }}>{activeVM ? 1 : 0}</b><span className="mono"> active</span></span>
          {totalMiss > 0 && <>
            <span className="stk-sum-sep">·</span>
            <span className="stk-sum-item"><b className="num" style={{ color: 'var(--warn)' }}>{totalMiss}</b><span className="mono"> missing models</span></span>
          </>}
        </div>
      </div>

      {vms.length === 0 ? (
        <div className="empty mono">No stacks yet — create one, snapshot the live config, or import a .hal0stack.json.</div>
      ) : (
        <div className="stk-focus">
          {activeVM
            ? <HeroPanel vm={activeVM} isCustom={isCustom(activeVM)} onPull={openPull} onExport={onExport}
                onReapply={setLoadTgt} onEdit={libProps.onEdit} />
            : <div className="stk-hero" style={{ borderColor: 'var(--line)' }}>
                <div className="stk-hero-h">
                  <div>
                    <div className="stk-hero-eye">No active stack</div>
                    <div className="stk-hero-name" style={{ color: 'var(--fg-3)' }}>Nothing applied</div>
                    <div className="stk-hero-intent">Load a stack below to set the platform's models, embed, and voice in one action.</div>
                  </div>
                </div>
              </div>}

          <div className="sec" style={{ marginTop: 28, marginBottom: 14 }}>
            <h2>Library <span className="ct num">{library.length}</span></h2>
            <span className="rule" />
          </div>

          {library.length === 0
            ? <div className="empty mono">Every stack is the active one. Clone a seed or snapshot the live config to add more.</div>
            : <div className="stk-lib-grid">
                {library.map((vm, i) => (
                  <LibCard key={vm.slug} vm={vm} idx={i} isCustom={isCustom(vm)}
                    onLoad={libProps.onLoad} onPull={libProps.onPull} onExport={libProps.onExport}
                    onClone={libProps.onClone} onEdit={libProps.onEdit} onDelete={libProps.onDelete} />
                ))}
              </div>}
        </div>
      )}

      {drawer && <StackDrawer mode={drawer.mode} source={drawer.source} existing={existing}
        onClose={() => setDrawer(null)} onSaved={() => setDrawer(null)} />}
      {confirm && <DeleteConfirm vm={confirm} onCancel={() => setConfirm(null)} onConfirmed={() => setConfirm(null)} />}
      {loadTgt && <LoadDialog vm={loadTgt} busy={loadBusy} onLoad={confirmLoad} onPull={openPull} onClose={() => setLoadTgt(null)} />}
      {pullTgt && <PullDialog vm={pullTgt} pulled={pulledQ} onPull={queuePull} onClose={() => setPullTgt(null)} />}
      {importing && <ImportModal existing={existing} onClose={() => setImporting(false)} onImported={() => setImporting(false)} />}
    </div>
  );
}

Object.assign(window, { StacksView });
