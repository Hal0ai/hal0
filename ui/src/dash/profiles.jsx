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

import { useState, useEffect } from 'react'
import {
  useProfiles,
  useProfileCreate,
  useProfileUpdate,
  useProfileDelete,
} from '@/api/hooks/useProfiles'
import { prettyProfile } from './profile-names'

// Backend runtime hue. Keys are the display backends shown on the card +
// drawer; colors reference shared dashboard tokens. `backendField` is the
// value persisted to ProfileConfig.backend (null for non-GPU paths, where
// device_class carries the hardware intent — see #751).
const BACKEND_META = {
  rocm:   { label: 'ROCm',      color: 'var(--dev-rocm)',   device_class: 'gpu', backendField: 'rocm' },
  vulkan: { label: 'Vulkan',    color: 'var(--dev-vulkan)', device_class: 'gpu', backendField: 'vulkan' },
  npu:    { label: 'FLM · NPU', color: 'var(--dev-npu)',    device_class: 'npu', backendField: null },
  cpu:    { label: 'CPU',       color: 'var(--dev-cpu)',    device_class: 'cpu', backendField: null },
};

// Mirrors the API name regex ^[a-z0-9][a-z0-9_-]{0,31}$.
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;

const BLANK = { name: '', intent: '', image: '', backend: 'rocm', quant: '', flags: '', mtp: false };

function bk(name) { return BACKEND_META[name] || BACKEND_META.cpu; }

// Display backend for a profile: the explicit GPU backend (rocm|vulkan) when
// set, otherwise mapped from device_class so npu/cpu/img still get a hue.
function backendOf(p) {
  if (p.backend && BACKEND_META[p.backend]) return p.backend;
  if (p.device_class === 'npu') return 'npu';
  if (p.device_class === 'cpu') return 'cpu';
  if ((p.image || '').toLowerCase().includes('vulkan')) return 'vulkan';
  return 'rocm';
}

// Card headline. Prefer the server-authored intent; fall back to a pretty
// profile name (#751 shared map) so un-labelled custom profiles read well.
function intentOf(p) {
  if (p.intent) return p.intent;
  const base = p.image ? p.image.split(':').pop() : prettyProfile(p.name);
  return p.mtp ? `${base} · MTP` : base;
}

function toast(msg, kind = 'info') {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind);
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

function ProfileCard({ p, index, onEdit, onClone, onDelete }) {
  const meta = bk(backendOf(p));
  const isSeed = !!p.seed;
  const usedBy = p.used_by || [];
  const inUse = usedBy.length;

  return (
    <div className="pf-card" style={{ '--bk': meta.color, animationDelay: (index * 34) + 'ms' }}>
      <span className="pf-accent" />
      <div className="pf-card-top">
        <div className="pf-headline">
          <div className="pf-intent">{intentOf(p)}</div>
          <div className="pf-slug-row mono">
            <span className="pf-slug">{p.name}</span>
            {p.cloned_from && <span className="pf-based">↳ {p.cloned_from}</span>}
          </div>
        </div>
        <div className="pf-metric">
          {p.tps != null ? (
            <>
              <span className="pf-tps num">{p.tps.toFixed(1)}</span>
              <span className="pf-tps-u mono">tok/s · bench</span>
            </>
          ) : p.rtf != null ? (
            <>
              <span className="pf-tps num">{p.rtf.toFixed(2)}×</span>
              <span className="pf-tps-u mono">RTF · synth</span>
            </>
          ) : (
            <span className="pf-tps-u mono">—</span>
          )}
        </div>
      </div>

      <div className="pf-chips">
        <span className="pf-chip backend" style={{ '--bk': meta.color }}>
          <span className="pf-chip-dot" />{meta.label}
        </span>
        {p.quant && <span className="pf-chip">{p.quant}</span>}
        {p.mtp && <span className="pf-chip mtp">{Icons.zap} MTP</span>}
        <span className="pf-grow" />
        {isSeed
          ? <span className="pf-chip seed immutable" title="Seed profiles are read-only">{Icons.lock} seed</span>
          : <span className="pf-chip custom">custom</span>}
      </div>

      <div className="pf-image mono">
        <span className="pf-image-lbl">image</span>
        <span className="pf-image-uri">{p.image}</span>
      </div>

      {p.resolved_flags && (
        <div className="pf-flags">
          <span className="pf-flags-lbl mono">flags</span>
          <code className="pf-flags-code">{p.resolved_flags}</code>
        </div>
      )}

      <div className="pf-foot">
        <div className="pf-usage">
          {inUse ? (
            <>
              <span className="pf-usage-lbl mono">used by</span>
              <span className="pf-slots">{usedBy.map(s => <SlotPill key={s} name={s} />)}</span>
            </>
          ) : (
            <span className="pf-unused mono">unused · safe to delete</span>
          )}
        </div>
        <div className="pf-actions">
          {isSeed ? (
            <button
              className="pf-btn"
              onClick={() => onClone(p)}
              title="Seeds are immutable — fork a custom copy"
              data-testid={`pf-btn-editcopy-${p.name}`}
            >
              {Icons.copy} Edit a copy
            </button>
          ) : (
            <>
              <button
                className="pf-btn"
                onClick={() => onClone(p)}
                title="Clone this profile"
                data-testid={`pf-btn-clone-${p.name}`}
              >
                {Icons.copy}
              </button>
              <button
                className="pf-btn"
                onClick={() => onEdit(p)}
                title="Edit"
                data-testid={`pf-btn-edit-${p.name}`}
              >
                {Icons.edit} Edit
              </button>
            </>
          )}
          <button
            className="pf-btn danger"
            onClick={() => onDelete(p)}
            disabled={isSeed}
            title={isSeed ? 'Seed profiles cannot be deleted' : inUse ? 'In use — detach slots first' : 'Delete'}
            data-testid={`pf-btn-delete-${p.name}`}
          >
            {Icons.trash}
          </button>
        </div>
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

function FormRow({ label, sub, req, children, error, warn, ok, counter }) {
  return (
    <div className={'pf-row' + (error ? ' has-err' : '')}>
      <div className="pf-row-lbl">
        <span>{label}{req && <span className="pf-req" title="required">*</span>}</span>
        {sub && <span className="pf-row-sub mono">{sub}</span>}
      </div>
      <div className="pf-row-ctl">
        <div className={'pf-field' + (ok ? ' ok' : '') + (error ? ' err' : '')}>
          {children}
          {ok && <span className="pf-field-ok" aria-hidden="true">{Icons.check}</span>}
        </div>
        {(error || warn || counter) && (
          <div className="pf-row-foot">
            {error
              ? <span className="pf-msg err mono hint err">{Icons.alert}{error}</span>
              : warn
              ? <span className="pf-msg warn mono">{Icons.alert}{warn}</span>
              : <span />}
            {counter && <span className={'pf-counter mono' + (counter.warn ? ' warn' : '')}>{counter.text}</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function validateForm(form, existing) {
  const errs = {};
  const name = (form.name || '').trim();
  if (!name) errs.name = 'Name is required';
  else if (!NAME_RE.test(name)) errs.name = 'lowercase · digits · - · _ · must start alphanumeric';
  else if (existing.includes(name)) errs.name = `“${name}” already exists`;
  if (!(form.image || '').trim()) errs.image = 'Image is required';
  return errs;
}

function warnForm(form) {
  const warns = {};
  const img = (form.image || '').trim();
  // A tag is the part after the last ':' that isn't part of a host:port.
  if (img && !/:[\w][\w.-]*$/.test(img)) warns.image = 'no tag — will resolve to :latest';
  return warns;
}

function Drawer({ mode, source, existing = [], onClose, onSaved }) {
  const isEdit = mode === 'edit';
  const initial = (() => {
    if (mode === 'create') return { ...BLANK };
    const base = {
      name: source.name,
      intent: source.intent || '',
      image: source.image || '',
      backend: backendOf(source),
      quant: source.quant || '',
      flags: source.flags || '',
      mtp: !!source.mtp,
    };
    if (mode === 'clone') {
      const suffix = source.seed ? '-custom' : '-copy';
      return { ...base, name: (source.name + suffix).slice(0, 32), cloned_from: source.name };
    }
    return base;
  })();

  const [form, setForm] = useState(initial);
  const [touched, setTouched] = useState({});
  const [submitted, setSubmitted] = useState(false);
  const [closing, setClosing] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  const create = useProfileCreate();
  const update = useProfileUpdate();

  useEffect(() => { setForm(initial); setTouched({}); setSubmitted(false); /* eslint-disable-next-line */ }, [mode, source]);

  const meta = bk(form.backend);

  const taken = existing.filter(n => !(isEdit && n === source.name));
  const errs = validateForm(form, taken);
  const warns = warnForm(form);
  const blocking = Object.keys(errs).length > 0;
  const show = (f) => submitted || touched[f];
  const nameValid = !errs.name && (form.name || '').trim().length > 0;

  const set = (k, v) => { setForm(f => ({ ...f, [k]: v })); setTouched(t => ({ ...t, [k]: true })); };
  const touch = (k) => setTouched(t => ({ ...t, [k]: true }));
  const close = () => { if (submitting) return; setClosing(true); setTimeout(onClose, 200); };

  async function submit(e) {
    e.preventDefault();
    setSubmitted(true);
    if (blocking) {
      const first = document.querySelector('.pf-field.err input');
      if (first) first.focus();
      return;
    }
    setSubmitting(true);
    const choice = bk(form.backend);
    const body = {
      name: form.name.trim(),
      image: form.image.trim(),
      flags: form.flags ?? '',
      mtp: !!form.mtp,
      device_class: choice.device_class,
      backend: choice.backendField,
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
        setTouched(t => ({ ...t, name: true }));
        toast(`A profile named ${body.name} already exists`, 'err');
      } else if (code === 'profiles.seed_immutable') {
        toast('Seed profiles cannot be modified', 'err');
      } else {
        toast(err?.message || 'Save failed', 'err');
      }
    } finally {
      setSubmitting(false);
    }
  }

  const title = isEdit ? `Edit · ${source.name}`
    : mode === 'clone' ? (source.seed ? `Edit a copy · ${source.name}` : `Clone · ${source.name}`)
    : 'New profile';
  const eyebrow = mode === 'create' ? 'CREATE' : mode === 'clone' ? (source.seed ? 'EDIT A COPY' : 'CLONE') : 'EDIT';
  const nameLen = (form.name || '').length;

  return (
    <div className={'pf-scrim' + (closing ? ' out' : '')} onMouseDown={close}>
      <div
        className={'pf-drawer pf-form-panel' + (closing ? ' out' : '')}
        onMouseDown={e => e.stopPropagation()}
        role="dialog"
        aria-label={title}
        aria-busy={submitting}
      >
        <div className="pf-drawer-head">
          <div>
            <div className="pf-drawer-eye mono">{eyebrow}</div>
            <div className="pf-drawer-title pf-form-title mono">{title}</div>
          </div>
          <button className="pf-x" onClick={close} aria-label="Close" disabled={submitting}>{Icons.close}</button>
        </div>

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

          <FormRow label="Image" req sub="container image URI"
            error={show('image') ? errs.image : null}
            warn={!errs.image ? warns.image : null}
            ok={!!(form.image || '').trim() && !errs.image && !warns.image}>
            <input className={'pf-input mono' + (show('image') && errs.image ? ' err' : '')} value={form.image}
              onChange={e => set('image', e.target.value)} onBlur={() => touch('image')}
              placeholder="ghcr.io/hal0ai/…:tag" aria-invalid={!!(show('image') && errs.image)}
              data-testid="pf-input-image" />
          </FormRow>

          <FormRow label="Backend" sub="runtime path">
            <div className="pf-seg" data-testid="pf-seg-backend">
              {Object.keys(BACKEND_META).map(k => (
                <button type="button" key={k} className={'pf-seg-btn' + (form.backend === k ? ' on' : '')}
                  style={{ '--bk': BACKEND_META[k].color }} onClick={() => set('backend', k)}>
                  <span className="pf-chip-dot" />{BACKEND_META[k].label}
                </button>
              ))}
            </div>
          </FormRow>

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

        <div className="pf-drawer-foot">
          <div className="pf-drawer-preview mono" style={{ '--bk': meta.color }}>
            <span className="pf-chip-dot" />{meta.label}{form.mtp ? ' · MTP' : ''}
          </div>
          <span className="pf-grow" />
          {submitted && blocking && (
            <span className="pf-foot-err mono">{Icons.alert}Fix {Object.keys(errs).length} field{Object.keys(errs).length > 1 ? 's' : ''}</span>
          )}
          <button className="pf-btn" onClick={close} type="button" disabled={submitting}>Cancel</button>
          <button className={'pf-btn primary' + (submitted && blocking ? ' is-blocked' : '')}
            onClick={submit} disabled={submitting} data-testid="pf-btn-submit">
            {submitting ? 'Saving…' : isEdit ? 'Save changes' : 'Create profile'}
          </button>
        </div>
      </div>
    </div>
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
function ProfilesHeader({ count, onNew }) {
  return (
    <div className="engine-h pf-engine-h">
      <span className="engine-glyph">{Icons.slots}</span>
      <span className="cpane-titles">
        <span className="engine-title">Profiles</span>
        <span className="engine-sub">launch profiles · image + bench-tuned flags per workload</span>
      </span>
      {count != null && (
        <span className="cpill">
          <span className="dot" />
          {count} profile{count === 1 ? '' : 's'}
        </span>
      )}
      <span className="grow" />
      {onNew && (
        <span className="eh-right">
          <button className="pf-btn primary" onClick={onNew} data-testid="pf-btn-new">
            {Icons.plus} New profile
          </button>
        </span>
      )}
    </div>
  );
}

function ProfilesView() {
  const query = useProfiles();
  const profiles = query.data ?? [];

  const [drawer, setDrawer] = useState(null);   // {mode, source}
  const [confirm, setConfirm] = useState(null);

  const seeds = profiles.filter(p => p.seed);
  const custom = profiles.filter(p => !p.seed);
  const inUseCount = profiles.filter(p => (p.used_by || []).length).length;

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
        <ProfilesHeader count={profiles.length} onNew={() => setDrawer({ mode: 'create' })} />
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
                onDelete={pp => setConfirm(pp)} />
            ))}
          </Section>

          <Section title="Custom profiles" count={custom.length} hint="forked or authored on this box">
            {custom.map((p, i) => (
              <ProfileCard key={p.name} p={p} index={i}
                onEdit={pp => setDrawer({ mode: 'edit', source: pp })}
                onClone={pp => setDrawer({ mode: 'clone', source: pp })}
                onDelete={pp => setConfirm(pp)} />
            ))}
          </Section>
        </>
      )}

      {drawer && (
        <Drawer
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
    </div>
  );
}

Object.assign(window, { ProfilesView });
