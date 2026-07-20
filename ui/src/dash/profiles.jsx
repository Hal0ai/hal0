// hal0 dashboard — Profiles view (issue #658; overhaul 2026-06-13).
//
// A profile is a named container image + bench-tuned flag bundle that backs
// one or more inference slots. This view replaces the flat card grid with a
// structured surface: a summary strip, seed/custom grouping, richer cards
// (bench tok/s hero metric, backend-hued accent + chip, quant chip, the
// "used by" slot binding), a slide-in form drawer, and a styled delete
// confirm with an in-use guard.
//
// Data comes from GET /api/profiles (useProfiles). Cards read the explicit
// `backend` field (#751) plus the overhaul's intent/quant/tps/rtf/used_by.
//
// Seeds are immutable: Edit becomes "Edit a copy" (forks <seed>-custom with
// cloned_from set); Delete is disabled. Custom profiles get Clone/Edit/Delete.

import { useState, useEffect, useMemo } from 'react'
import {
  useProfiles,
  useProfileCreate,
  useProfileUpdate,
  useProfileDelete,
  useProfileExport,
  useProfileImport,
} from '@/api/hooks/useProfiles'
import { useMetaEnums } from '@/api/hooks/useMeta'
import { prettyProfile } from './profile-names'

// Backend runtime hue map — built from meta.devices (GET /api/meta/enums via
// useMetaEnums, static fallback when absent) instead of the old hardcoded
// table. Keys stay the display backends shown on the card + drawer (rocm /
// vulkan / npu / cpu / img); colors reference shared dashboard tokens.
// `backendField` is the value persisted to ProfileConfig.backend (the GPU
// devices' legacy_backend; null for non-GPU paths, where device_class carries
// the hardware intent — see #751). PUT /api/profiles accepts device_class ∈
// {gpu,cpu,npu,img}, so the img/ComfyUI entry keeps an existing
// device_class='img' profile from being silently rewritten on edit.
function buildBackendMeta(enums) {
  const out = {};
  for (const d of enums.devices) {
    const key = d.legacy_backend
      || (d.device_class && d.device_class !== 'gpu' ? d.device_class : String(d.id).replace(/^gpu-/, ''));
    if (!key || out[key]) continue;
    out[key] = {
      label: d.label || key,
      color: `var(--dev-${key})`,
      device_class: d.device_class,
      backendField: d.legacy_backend || null,
      recommended: !!d.recommended,
      description: d.description || '',
    };
  }
  // Defensive floor — bk() falls back to `cpu`, so the key must exist even
  // against a degenerate enums payload.
  if (!out.cpu) out.cpu = { label: 'CPU', color: 'var(--dev-cpu)', device_class: 'cpu', backendField: null, recommended: false, description: '' };
  return out;
}

function useBackendMeta() {
  const enums = useMetaEnums();
  return useMemo(() => buildBackendMeta(enums), [enums]);
}

// `NAME_RE` (name regex) and `toast` are shared globals from primitives.jsx.

const BLANK = { name: '', intent: '', backend: 'rocm', quant: '', flags: '', mtp: false };

function bk(name, meta) { return meta[name] || meta.cpu; }

// Display backend for a profile: the explicit GPU backend (rocm|vulkan) when
// set, otherwise mapped from device_class so npu/cpu/img still get a hue.
// (spec-hw-slot-ownership §3: profiles no longer carry an image, so the old
// vulkan-from-image-string inference is gone.)
function backendOf(p, meta) {
  if (p.backend && meta[p.backend]) return p.backend;
  if (p.device_class === 'img') return 'img';
  if (p.device_class === 'npu') return 'npu';
  if (p.device_class === 'cpu') return 'cpu';
  return 'cpu';
}

function runtimeLabel(p) {
  return {
    'llama-server': 'llama-server · slot hardware',
    flm: 'FLM · slot binary',
    kokoro: 'Kokoro · slot binary',
    qwen3tts: 'Qwen3-TTS · slot binary',
    comfyui: 'ComfyUI · slot binary',
  }[p.runtime_family] || 'slot-selected runtime';
}

// Card headline. Prefer the server-authored intent; fall back to a pretty
// profile name (#751 shared map) so un-labelled custom profiles read well.
function intentOf(p) {
  if (p.intent) return p.intent;
  const base = prettyProfile(p.name);
  return p.mtp ? `${base} · MTP` : base;
}

// ── slot binding pill ─────────────────────────────────────────────────────────

function SlotPill({ name }) {
  return (
    <span className="pf-slot">
      <span className="pf-slot-dot" />
      <span className="mono">{name}</span>
    </span>
  );
}

// ── Profile card ──────────────────────────────────────────────────────────────

// Info row in the stacks-card "slotlist" idiom: hued dot · label · value.
function PfRow({ label, value, hue }) {
  return (
    <div className="stk-lib-slotrow">
      <span className="pf-row-dot" style={hue ? { background: hue, boxShadow: `0 0 6px ${hue}` } : null} />
      <span className="stk-csr-name mono">{label}</span>
      <span className="stk-csr-model mono">{value}</span>
    </div>
  );
}

// Profile card — adopts the Stacks library-card shell (.stk-lib-*) so the
// Profiles and Stacks grids read as one family. Same data + actions as before.
function ProfileCard({ p, index, onEdit, onClone, onDelete, onExport }) {
  const BACKEND_META = useBackendMeta();
  const meta = bk(backendOf(p, BACKEND_META), BACKEND_META);
  const isSeed = !!p.seed;
  const usedBy = p.used_by || [];
  const inUse = usedBy.length;
  const metric = p.tps != null ? `${p.tps.toFixed(1)} tok/s`
    : p.rtf != null ? `${p.rtf.toFixed(2)}× rtf` : null;

  return (
    <div className="stk-lib-card" style={{ animationDelay: (index * 34) + 'ms' }}>
      <div className="stk-lib-h">
        <div className="stk-lib-id">
          <div className="stk-lib-name">{p.name}</div>
          <div className="stk-lib-intent">
            {intentOf(p)}{p.cloned_from && <span className="pf-based mono"> · ↳ {p.cloned_from}</span>}
          </div>
        </div>
        <div className="pf-card-meta">
          <span className="stk-tag pf-bk" style={{ '--bk': meta.color, color: meta.color, borderColor: 'color-mix(in srgb, ' + meta.color + ' 34%, transparent)', background: 'color-mix(in srgb, ' + meta.color + ' 10%, transparent)' }}>
            {runtimeLabel(p)}
          </span>
          {metric && <span className="mono pf-card-metric">{metric}</span>}
        </div>
      </div>

      <div className="stk-lib-slotlist">
        {p.quant && <PfRow label="quant" value={p.quant} />}
        {p.mtp && <PfRow label="mtp" value="speculative" hue="var(--accent)" />}
        <PfRow label="flags" value={p.resolved_flags || p.flags || '—'} />
      </div>

      <div className="stk-lib-f">
        {inUse
          ? <span className="stk-tag" title={'used by ' + usedBy.join(', ')}>used by {inUse}</span>
          : <span className="mono pf-card-unused">unused</span>}
        {isSeed
          ? <span className="stk-tag pf-seed" title="Seed profiles are read-only">{Icons.lock} seed</span>
          : <span className="stk-tag shared pf-custom">custom</span>}
        <span className="stk-spacer" />
        {isSeed ? (
          <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onClone(p)}
            title="Seeds are immutable — fork a custom copy" data-testid={`pf-btn-editcopy-${p.name}`}>
            {Icons.copy}
          </button>
        ) : (
          <>
            <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onClone(p)}
              title="Clone this profile" data-testid={`pf-btn-clone-${p.name}`}>{Icons.copy}</button>
            <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onEdit(p)}
              title="Edit" data-testid={`pf-btn-edit-${p.name}`}>{Icons.edit}</button>
          </>
        )}
        <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onDelete(p)} disabled={isSeed}
          title={isSeed ? 'Seed profiles cannot be deleted' : inUse ? 'In use — detach slots first' : 'Delete'}
          data-testid={`pf-btn-delete-${p.name}`}>{Icons.trash}</button>
        <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onExport(p)}
          title="Export this profile as a .hal0profile.json" data-testid={`pf-btn-export-${p.name}`}>{Icons.download}</button>
      </div>
    </div>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────

function Section({ title, count, hint, children }) {
  return (
    <div className="pf-section">
      <div className="sec">
        <h2>{title}<span className="ct num">{count}</span></h2>
        {hint && <span className="pf-sec-hint mono">{hint}</span>}
        <span className="rule" />
      </div>
      <div className="pf-grid">{children}</div>
    </div>
  );
}

// ── Form drawer (create / edit / clone) ─────────────────────────────────────────
// FormRow is a shared primitive (primitives.jsx) — the superset variant this
// view seeded (error/warn/ok/counter + real <label htmlFor>).

function validateForm(form, existing) {
  const errs = {};
  const name = (form.name || '').trim();
  if (!name) errs.name = 'Name is required';
  else if (!NAME_RE.test(name)) errs.name = 'lowercase · digits · - · _ · must start alphanumeric';
  else if (existing.includes(name)) errs.name = `“${name}” already exists`;
  // spec-hw-slot-ownership §3: profiles no longer carry an image — only a name
  // + device-agnostic tune. Image lives on the runner (RUNNER_IMAGES[binary]).
  return errs;
}

// No warnings today (the image-tag warning was removed with the image field).
function warnForm(_form) {
  return {};
}

// The editor drawer. Consumes the shared FormDrawer shell + useForm hook +
// shared FormRow. Named ProfileDrawer (not Drawer) to avoid shadowing the
// primitives.jsx Drawer global. validateForm/warnForm are passed into useForm
// verbatim so the validation rules stay in this view.
function ProfileDrawer({ mode, source, existing = [], onClose, onSaved }) {
  const isEdit = mode === 'edit';
  const BACKEND_META = useBackendMeta();

  const deriveInitial = () => {
    if (mode === 'create') return { ...BLANK };
    const base = {
      name: source.name,
      intent: source.intent || '',
      quant: source.quant || '',
      flags: source.flags || '',
      mtp: !!source.mtp,
    };
    if (mode === 'clone') {
      const suffix = source.seed ? '-custom' : '-copy';
      return { ...base, name: (source.name + suffix).slice(0, 32), cloned_from: source.name };
    }
    return base;
  };

  const create = useProfileCreate();
  const update = useProfileUpdate();

  const taken = existing.filter(n => !(isEdit && n === source.name));
  const f = useForm({
    deriveInitial,
    resetKey: `${mode}:${source?.name ?? ''}`,
    validate: (v) => validateForm(v, taken),
    warn: (v) => warnForm(v),
  });
  const form = f.values;
  const errs = f.errors;
  const blocking = f.blocking;
  const show = f.show;
  const set = f.set;
  const touch = f.touch;
  const meta = bk('cpu', BACKEND_META);
  const nameValid = !errs.name && (form.name || '').trim().length > 0;
  const nameLen = (form.name || '').length;

  async function submit(e) {
    e.preventDefault();
    f.setSubmitted(true);
    if (blocking) {
      const first = document.querySelector('.pf-field.err input');
      if (first) first.focus();
      return;
    }
    f.setSubmitting(true);
    const body = {
      name: form.name.trim(),
      flags: form.flags ?? '',
      mtp: !!form.mtp,
      intent: form.intent ?? '',
      quant: form.quant ?? '',
      ...(form.cloned_from ? { cloned_from: form.cloned_from } : {}),
    };
    try {
      if (isEdit) {
        const { name, ...rest } = body;
        await update.mutateAsync({ name: source.name, body: rest });
        toast(`Profile ${source.name} updated`, 'ok');
      } else {
        await create.mutateAsync(body);
        toast(`Profile ${body.name} created`, 'ok');
      }
      onSaved();
    } catch (err) {
      const code = err?.code || '';
      if (code === 'profiles.exists') {
        touch('name');
        toast(`A profile named ${body.name} already exists`, 'err');
      } else if (code === 'profiles.seed_immutable') {
        toast('Seed profiles cannot be modified', 'err');
      } else {
        toast(err?.message || 'Save failed', 'err');
      }
    } finally {
      f.setSubmitting(false);
    }
  }

  const title = isEdit ? `Edit · ${source.name}`
    : mode === 'clone' ? (source.seed ? `Edit a copy · ${source.name}` : `Clone · ${source.name}`)
    : 'New profile';
  const eyebrow = mode === 'create' ? 'CREATE' : mode === 'clone' ? (source.seed ? 'EDIT A COPY' : 'CLONE') : 'EDIT';

  return (
    <FormDrawer
      eyebrow={eyebrow}
      title={title}
      submitting={f.submitting}
      dirty={f.isDirty}
      onClose={onClose}
      foot={({ requestClose }) => (
        <>
          <div className="pf-drawer-preview mono" style={{ '--bk': meta.color }}>
            <span className="pf-chip-dot" />{meta.label}{form.mtp ? ' · MTP' : ''}
          </div>
          <span className="pf-grow" />
          {f.submitted && blocking && (
            <span className="pf-foot-err mono">{Icons.alert}Fix {Object.keys(errs).length} field{Object.keys(errs).length > 1 ? 's' : ''}</span>
          )}
          <button className="pf-btn" onClick={requestClose} type="button" disabled={f.submitting}>Cancel</button>
          <button className={'pf-btn primary' + (f.submitted && blocking ? ' is-blocked' : '')}
            onClick={submit} disabled={f.submitting} data-testid="pf-btn-submit">
            {f.submitting ? 'Saving…' : isEdit ? 'Save changes' : 'Create profile'}
          </button>
        </>
      )}
    >
      <form className="pf-drawer-body" onSubmit={submit} noValidate>
        <FormRow label="Name" req sub="lowercase · - _ · ≤32"
          error={show('name') ? errs.name : null}
          ok={!isEdit && nameValid}
          counter={!isEdit ? { text: nameLen + '/32', warn: nameLen >= 28 } : null}>
          <input className={'pf-input mono' + (show('name') && errs.name ? ' err' : '')} value={form.name}
            onChange={e => set('name', e.target.value)} onBlur={() => touch('name')}
            placeholder="my-profile" maxLength={32} disabled={isEdit}
            aria-invalid={!!(show('name') && errs.name)} data-testid="pf-input-name" />
        </FormRow>

        <FormRow label="Intent" sub="what it's for">
          <input className="pf-input" value={form.intent} onChange={e => set('intent', e.target.value)}
            placeholder="MoE agents · long-ctx" data-testid="pf-input-intent" />
        </FormRow>

        {/* Image field removed (spec-hw-slot-ownership §3): a profile is a
            device-agnostic tune template only. The container image lives on the
            runner (RUNNER_IMAGES[slot.binary]); the per-slot escape hatch is
            slot.image_pin in the slot editor. */}

        <div className="mono pf-hint">Hardware, runner binary, and image are selected on the slot.</div>

        <FormRow label="Quant" sub="weight format">
          <input className="pf-input mono" value={form.quant || ''} onChange={e => set('quant', e.target.value)}
            placeholder="FP4 · Q4_K_M …" data-testid="pf-input-quant" />
        </FormRow>

        <FormRow label="Flags" sub="appended to the run command">
          <textarea className="pf-input mono pf-textarea" value={form.flags || ''}
            onChange={e => set('flags', e.target.value)} rows={3} placeholder="--flash-attn on -ngl 999"
            data-testid="pf-input-flags" />
        </FormRow>

        <FormRow label="MTP" sub="Multi-Token Prediction speculative decode">
          <button type="button" className={'pf-switch' + (form.mtp ? ' on' : '')} onClick={() => set('mtp', !form.mtp)}
            role="switch" aria-checked={form.mtp} data-testid="pf-check-mtp">
            <span className="pf-switch-knob" />
            <span className="pf-switch-lbl mono">{form.mtp ? 'enabled' : 'disabled'}</span>
          </button>
        </FormRow>
      </form>
    </FormDrawer>
  );
}

// ── Delete confirm ──────────────────────────────────────────────────────────────

function DeleteConfirm({ p, onCancel, onConfirmed }) {
  const del = useProfileDelete();
  const [busy, setBusy] = useState(false);
  const usedBy = p.used_by || [];
  const inUse = usedBy.length;

  async function handleDelete() {
    setBusy(true);
    try {
      await del.mutateAsync(p.name);
      toast(`Profile ${p.name} deleted`, 'ok');
      onConfirmed();
    } catch (err) {
      const code = err?.code || '';
      if (code === 'profiles.in_use') {
        const slots = err?.details?.slots;
        const slotList = Array.isArray(slots) ? slots.join(', ') : String(slots || '');
        toast(`Cannot delete — in use by: ${slotList}`, 'err');
      } else if (code === 'profiles.seed_immutable') {
        toast('Seed profiles cannot be deleted', 'err');
      } else {
        toast(err?.message || 'Delete failed', 'err');
      }
      onCancel();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pf-scrim center pf-confirm-scrim" onMouseDown={() => { if (!busy) onCancel(); }}>
      <div className="pf-confirm" onMouseDown={e => e.stopPropagation()} role="dialog" aria-label="Confirm delete" aria-busy={busy}>
        <div className="pf-confirm-h pf-confirm-title mono">{Icons.trash} Delete · {p.name}?</div>
        {inUse ? (
          <div className="pf-confirm-b">
            <div className="pf-warn mono">In use by {inUse} slot{inUse > 1 ? 's' : ''}.</div>
            <div className="pf-slots" style={{ margin: '8px 0 2px' }}>{usedBy.map(s => <SlotPill key={s} name={s} />)}</div>
            <div className="pf-confirm-sub">Detach these slots before deleting — they'd revert to defaults.</div>
          </div>
        ) : (
          <div className="pf-confirm-b">
            <div className="pf-confirm-sub">This removes the profile permanently. This cannot be undone.</div>
          </div>
        )}
        <div className="pf-confirm-foot">
          <button className="pf-btn" onClick={onCancel} disabled={busy}>Cancel</button>
          <button className="pf-btn danger solid" onClick={handleDelete} disabled={!!inUse || busy}
            data-testid="pf-btn-delete-confirm">
            {inUse ? 'In use' : busy ? 'Deleting…' : 'Delete profile'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Import modal (file → dry-run → commit) ────────────────────────────────────
// Reuses the Stacks dialog shell (.stk-scrim / .stk-dialog / .stk-dlg-*). A
// profile references no models, so the preview is just identity + integrity +
// collision — no model resolve/pull affordance.

// Thin wrapper over the shared <ImportDialog>: owns the useProfileImport hook
// and the profile-specific preview (identity + integrity + collision — no
// model resolve/pull). The dialog shell + name input + commit live in
// primitives.jsx.
function ImportModal({ existing, onClose, onImported }) {
  const imp = useProfileImport();
  const renderPreview = (report) => (
    <>
      <div className="stk-dlg-hint">
        {report.name || 'profile'} · schema v{report.schema_version} · checksum {report.checksum_ok ? '✓ ok' : '✗ mismatch'}
      </div>
      {report.collides && (
        <div className="stk-dlg-warn">{Icons.alert}A profile named “{report.name}” already exists — choose a different name to import.</div>
      )}
    </>
  );
  return (
    <ImportDialog
      title="Import profile"
      ariaLabel="Import profile"
      fileAccept=".hal0profile.json,.json,application/json"
      fileHint="Choose a .hal0profile.json file"
      fileTestid="pf-import-file"
      nameTestid="pf-import-name"
      confirmTestid="pf-import-confirm"
      namePlaceholder="my-profile"
      invalidCopy="Not a valid .hal0profile.json envelope"
      existing={existing}
      renderPreview={renderPreview}
      dryRun={(env) => imp.mutateAsync({ envelope: env, dry_run: true })}
      commit={async ({ envelope, name }) => {
        try {
          await imp.mutateAsync({ envelope, name, dry_run: false });
          toast(`Imported ${name}`, 'ok');
        } catch (e) {
          if (e?.code === 'profiles.exists' || e?.status === 409) {
            throw { inline: `“${name}” already exists — pick another name` };
          }
          throw e;
        }
      }}
      onClose={onClose}
      onImported={onImported}
    />
  );
}

// ── Summary cell ─────────────────────────────────────────────────────────────────

function Stat({ value, label, accent }) {
  return (
    <div className="pf-stat">
      <span className="pf-stat-v num" style={accent ? { color: accent } : null}>{value}</span>
      <span className="pf-stat-l mono">{label}</span>
    </div>
  );
}

// ── Main view ────────────────────────────────────────────────────────────────────

// Section header — mirrors the engine-h header the Inference / Image Gen /
// Endpoints tabs use (glyph · title · sub · status pill · actions), so the
// Profiles tab reads as part of the same family instead of the old big-h1 view
// header. The base .engine-h styling is global (connections.css); .pf-engine-h
// only de-emphasises the pointer cursor and accent the panel owns.
function ProfilesHeader({ count, onNew, onImport }) {
  return (
    <div className="engine-h pf-engine-h">
      <span className="engine-glyph">{Icons.slots}</span>
      <span className="sec-label">
        <b>Profiles</b>
        <span className="dim">·</span>
        <span className="meta">launch profiles</span>
        <span className="dim">·</span>
        <span className="meta">bench-tuned flags per workload</span>
      </span>
      {count != null && (
        <span className="cpill">
          <span className="dot" />
          {count} profile{count === 1 ? '' : 's'}
        </span>
      )}
      <span className="grow" />
      {(onNew || onImport) && (
        <span className="eh-right">
          {onImport && (
            <button className="pf-btn" onClick={onImport} data-testid="pf-btn-import">
              {Icons.attach} Import
            </button>
          )}
          {onNew && (
            <button className="pf-btn primary" onClick={onNew} data-testid="pf-btn-new">
              {Icons.plus} New profile
            </button>
          )}
        </span>
      )}
    </div>
  );
}

function ProfilesView() {
  const query = useProfiles();
  const profiles = query.data ?? [];
  const exportMut = useProfileExport();
  const BACKEND_META = useBackendMeta();

  const [drawer, setDrawer] = useState(null);   // {mode, source}
  const [confirm, setConfirm] = useState(null);
  const [importing, setImporting] = useState(false);

  const seeds = profiles.filter(p => p.seed);
  const custom = profiles.filter(p => !p.seed);
  const inUseCount = profiles.filter(p => (p.used_by || []).length).length;

  async function onExport(p) {
    const name = p.name;
    try {
      const env = await exportMut.mutateAsync(name);
      const blob = new Blob([JSON.stringify(env, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = `${name}.hal0profile.json`;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
      toast(`Exported ${name}`, 'ok');
    } catch (err) { toast(err?.message || 'Export failed', 'err'); }
  }

  if (query.isLoading) {
    return (
      <div className="view">
        <div className="pf-engine"><ProfilesHeader /></div>
        <div className="empty mono">Loading profiles…</div>
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="view">
        <div className="pf-engine"><ProfilesHeader /></div>
        <div className="empty mono" style={{ color: 'var(--err)' }}>
          Failed to load profiles: {query.error?.message || 'unknown error'}
        </div>
      </div>
    );
  }

  return (
    <div className="view">
      <div className="pf-engine">
        <ProfilesHeader count={profiles.length} onNew={() => setDrawer({ mode: 'create' })} onImport={() => setImporting(true)} />
        <div className="pf-summary">
          <Stat value={profiles.length} label="profiles" />
          <span className="pf-stat-div" />
          <Stat value={seeds.length} label="seed templates" />
          <Stat value={custom.length} label="custom" accent="var(--accent)" />
          <span className="pf-stat-div" />
          <Stat value={`${inUseCount}/${profiles.length}`} label="bound to slots" accent="var(--ok)" />
          <span className="pf-grow" />
          <div className="pf-legend mono">
            {Object.entries(BACKEND_META).map(([k, m]) => (
              <span className="pf-legend-i" key={k} style={{ '--bk': m.color }}><span className="pf-chip-dot" />{m.label}</span>
            ))}
          </div>
        </div>
      </div>

      {profiles.length === 0 ? (
        <div className="empty mono">No profiles configured.</div>
      ) : (
        <>
          <Section title="Seed templates" count={seeds.length} hint="immutable · ship with hal0">
            {seeds.map((p, i) => (
              <ProfileCard key={p.name} p={p} index={i}
                onEdit={pp => setDrawer({ mode: 'edit', source: pp })}
                onClone={pp => setDrawer({ mode: 'clone', source: pp })}
                onDelete={pp => setConfirm(pp)}
                onExport={onExport} />
            ))}
          </Section>

          <Section title="Custom profiles" count={custom.length} hint="forked or authored on this box">
            {custom.map((p, i) => (
              <ProfileCard key={p.name} p={p} index={i}
                onEdit={pp => setDrawer({ mode: 'edit', source: pp })}
                onClone={pp => setDrawer({ mode: 'clone', source: pp })}
                onDelete={pp => setConfirm(pp)}
                onExport={onExport} />
            ))}
          </Section>
        </>
      )}

      {drawer && (
        <ProfileDrawer
          mode={drawer.mode}
          source={drawer.source}
          existing={profiles.map(p => p.name)}
          onClose={() => setDrawer(null)}
          onSaved={() => setDrawer(null)}
        />
      )}
      {confirm && (
        <DeleteConfirm p={confirm} onCancel={() => setConfirm(null)} onConfirmed={() => setConfirm(null)} />
      )}
      {importing && (
        <ImportModal
          existing={profiles.map(p => p.name)}
          onClose={() => setImporting(false)}
          onImported={() => setImporting(false)}
        />
      )}
    </div>
  );
}

Object.assign(window, { ProfilesView });
