// hal0 dashboard — Profiles view (issue #658, Phase C6 CRUD).
//
// Lists container-slot profiles from GET /api/profiles.
// Each profile is a named image + bench-tuned flag bundle that backs
// one or more container slots. The UI labels them by intent (what they're
// for) rather than by slug, so operators see "MoE agents · ROCmFP4 ·
// ~52.8 tok/s" rather than "moe-rocmfp4".
//
// Phase C6: CRUD — New profile button + create/edit form (inline drawer),
// per-card Edit/Delete for custom profiles, Clone for custom (prefills form
// with <name>-copy). Seeds are immutable: badge shown, Delete disabled, and
// Edit becomes "Edit a copy" — opens the create form prefilled from the seed
// (name <seed>-custom) so editing forks a custom profile instead of mutating
// the seed. Clones carry cloned_from provenance, shown as "based on <source>".

import { useState, useEffect, useCallback } from 'react'
import {
  useProfiles,
  useProfileCreate,
  useProfileUpdate,
  useProfileDelete,
} from '@/api/hooks/useProfiles'

// Seed profile intent labels, mapped by slug.
// Tok/s from hal0-container-bench-2026-06-08.md.
const PROFILE_INTENT = {
  'moe-rocmfp4':       'MoE agents · ROCmFP4 · ~52.8 tok/s',
  'dense-mtp-rocmfp4': 'Dense chat + MTP · ~24.4 tok/s',
  'vulkan-std':        'Vulkan std (fallback)',
  'flm-npu':           'FLM NPU inference',
  'kokoro-cpu':        'TTS · Kokoro CPU',
};

// Mirrors the API name regex ^[a-z0-9][a-z0-9_-]{0,31}$ — lowercase alnum
// start, hyphens/underscores allowed after, 32 chars max.
const NAME_RE = /^[a-z0-9][a-z0-9_-]{0,31}$/;

function profileIntent(p) {
  if (PROFILE_INTENT[p.name]) return PROFILE_INTENT[p.name];
  const base = p.image ? p.image.split(':').pop() : p.name;
  return p.mtp ? `${base} · MTP` : base;
}

function imageTag(image) {
  if (!image) return '—';
  const parts = image.split(':');
  return parts.length > 1 ? parts[parts.length - 1] : image;
}

function toast(msg, kind = 'info') {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind);
}

// ── Profile Form (create / edit) ──────────────────────────────────────────────

const BLANK_FORM = { name: '', image: '', flags: '', mtp: false, device_class: '' };

function ProfileForm({ initial, isEdit, title, onClose, onSaved }) {
  const [form, setForm] = useState(initial ?? BLANK_FORM);
  const [errors, setErrors] = useState({});
  const [submitting, setSubmitting] = useState(false);

  const create = useProfileCreate();
  const update = useProfileUpdate();

  // Sync form when initial changes (e.g. switching from clone to edit).
  useEffect(() => {
    setForm(initial ?? BLANK_FORM);
    setErrors({});
  }, [initial]);

  const set = useCallback((field, val) => {
    setForm(f => ({ ...f, [field]: val }));
    setErrors(e => ({ ...e, [field]: undefined }));
  }, []);

  function validate() {
    const errs = {};
    if (!form.name.trim()) {
      errs.name = 'Name is required';
    } else if (!NAME_RE.test(form.name.trim())) {
      errs.name = 'Lowercase letters, digits, hyphens, underscores · max 32 chars';
    }
    if (!form.image.trim()) {
      errs.image = 'Image is required';
    }
    return errs;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    const errs = validate();
    if (Object.keys(errs).length) { setErrors(errs); return; }
    setSubmitting(true);
    const body = {
      name: form.name.trim(),
      image: form.image.trim(),
      flags: form.flags ?? '',
      mtp: !!form.mtp,
      ...(form.device_class ? { device_class: form.device_class } : {}),
      ...(form.cloned_from ? { cloned_from: form.cloned_from } : {}),
    };
    try {
      if (isEdit) {
        const { name, ...rest } = body;
        await update.mutateAsync({ name: initial.name, body: rest });
        toast(`Profile ${initial.name} updated`, 'ok');
      } else {
        await create.mutateAsync(body);
        toast(`Profile ${body.name} created`, 'ok');
      }
      onSaved?.();
    } catch (err) {
      const code = err?.code || '';
      if (code === 'profiles.exists') {
        setErrors({ name: 'A profile with this name already exists' });
      } else if (code === 'profiles.seed_immutable') {
        setErrors({ name: 'Seed profiles cannot be modified' });
      } else {
        toast(err?.message || 'Save failed', 'err');
      }
    } finally {
      setSubmitting(false);
    }
  }

  const fallbackTitle = isEdit ? `Edit · ${initial?.name}` : 'New profile';
  const panelTitle = title || fallbackTitle;

  return (
    <div className="pf-form-panel" role="dialog" aria-label={panelTitle}>
      <div className="pf-form-head">
        <span className="pf-form-title mono">{panelTitle}</span>
        <button className="btn ghost sm pf-form-close" onClick={onClose} aria-label="Close">×</button>
      </div>
      <form onSubmit={handleSubmit} className="pf-form-body">
        {/* Name */}
        <div className="form-row">
          <div className="form-lbl">
            <span>Name <span className="req">*</span></span>
            <span className="sub">lowercase · hyphens/underscores · ≤32 chars</span>
          </div>
          <div className="form-ctl">
            <input
              className={`input mono${errors.name ? ' err' : ''}`}
              value={form.name}
              onChange={e => set('name', e.target.value)}
              placeholder="my-profile"
              maxLength={32}
              disabled={isEdit}
              data-testid="pf-input-name"
            />
            {errors.name && <div className="hint err">{errors.name}</div>}
          </div>
        </div>

        {/* Image */}
        <div className="form-row">
          <div className="form-lbl">
            <span>Image <span className="req">*</span></span>
            <span className="sub">container image URI</span>
          </div>
          <div className="form-ctl">
            <input
              className={`input mono${errors.image ? ' err' : ''}`}
              value={form.image}
              onChange={e => set('image', e.target.value)}
              placeholder="ghcr.io/hal0ai/…:tag"
              data-testid="pf-input-image"
            />
            {errors.image && <div className="hint err">{errors.image}</div>}
          </div>
        </div>

        {/* Flags */}
        <div className="form-row">
          <div className="form-lbl">
            <span>Flags</span>
            <span className="sub">extra CLI flags appended to the run command</span>
          </div>
          <div className="form-ctl">
            <input
              className="input mono"
              value={form.flags}
              onChange={e => set('flags', e.target.value)}
              placeholder="--flash-attn on -ngl 999"
              data-testid="pf-input-flags"
            />
          </div>
        </div>

        {/* Device class */}
        <div className="form-row">
          <div className="form-lbl">
            <span>Device class</span>
            <span className="sub">gpu · npu · cpu · auto</span>
          </div>
          <div className="form-ctl">
            <select
              className="input mono"
              value={form.device_class || ''}
              onChange={e => set('device_class', e.target.value)}
              data-testid="pf-select-device"
            >
              <option value="">auto</option>
              <option value="gpu">gpu</option>
              <option value="npu">npu</option>
              <option value="cpu">cpu</option>
            </select>
          </div>
        </div>

        {/* MTP */}
        <div className="form-row">
          <div className="form-lbl">
            <span>MTP</span>
            <span className="sub">Multi-Token Prediction speculative decoding</span>
          </div>
          <div className="form-ctl">
            <label className="pf-toggle-label">
              <input
                type="checkbox"
                checked={!!form.mtp}
                onChange={e => set('mtp', e.target.checked)}
                data-testid="pf-check-mtp"
              />
              <span className="mono" style={{ fontSize: 11, marginLeft: 6 }}>
                {form.mtp ? 'enabled' : 'disabled'}
              </span>
            </label>
          </div>
        </div>

        <div className="pf-form-foot">
          <button type="button" className="btn ghost sm" onClick={onClose}>Cancel</button>
          <button
            type="submit"
            className="btn sm"
            disabled={submitting}
            data-testid="pf-btn-submit"
          >
            {submitting ? 'Saving…' : isEdit ? 'Save' : 'Create'}
          </button>
        </div>
      </form>
    </div>
  );
}

// ── Delete confirm ────────────────────────────────────────────────────────────

function DeleteConfirm({ profile, onCancel, onConfirmed }) {
  const del = useProfileDelete();
  const [busy, setBusy] = useState(false);

  async function handleDelete() {
    setBusy(true);
    try {
      await del.mutateAsync(profile.name);
      toast(`Profile ${profile.name} deleted`, 'ok');
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
    <div className="pf-confirm-scrim" onClick={onCancel} role="dialog" aria-label="Confirm delete">
      <div className="pf-confirm" onClick={e => e.stopPropagation()}>
        <div className="pf-confirm-title mono">Delete profile · {profile.name}?</div>
        <div className="pf-confirm-body mono">
          This removes the profile permanently. Slots using it will revert to defaults.
        </div>
        <div className="pf-confirm-foot">
          <button className="btn ghost sm" onClick={onCancel}>Cancel</button>
          <button
            className="btn danger sm"
            onClick={handleDelete}
            disabled={busy}
            data-testid="pf-btn-delete-confirm"
          >
            {busy ? 'Deleting…' : 'Delete'}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Profile card ──────────────────────────────────────────────────────────────

function ProfileCard({ profile, onEdit, onClone, onDelete }) {
  const intent = profileIntent(profile);
  const tag = imageTag(profile.image);
  const isSeed = !!profile.seed;

  return (
    <div className="pf-card">
      <div className="pf-card-head">
        <div className="pf-intent">{intent}</div>
        <div className="pf-card-badges">
          {isSeed && <span className="pf-badge immutable" title="Seed profiles are read-only">seed</span>}
          {profile.mtp && <span className="pf-badge">MTP</span>}
          {profile.device_class && profile.device_class !== 'gpu' && (
            <span className="pf-badge device">{profile.device_class}</span>
          )}
        </div>
      </div>
      <div className="pf-meta mono">
        <span className="pf-slug">{profile.name}</span>
        <span className="pf-sep">·</span>
        <span className="pf-tag">{tag}</span>
      </div>
      {profile.cloned_from && (
        <div className="pf-based mono">based on {profile.cloned_from}</div>
      )}
      {profile.resolved_flags && (
        <div className="pf-flags mono">{profile.resolved_flags}</div>
      )}
      <div className="pf-card-actions">
        {isSeed ? (
          <button
            className="btn ghost xs"
            onClick={() => onClone(profile)}
            title="Seed profiles are immutable — edit a custom copy instead"
            data-testid={`pf-btn-editcopy-${profile.name}`}
          >
            Edit a copy
          </button>
        ) : (
          <>
            <button
              className="btn ghost xs"
              onClick={() => onClone(profile)}
              title="Clone this profile"
              data-testid={`pf-btn-clone-${profile.name}`}
            >
              Clone
            </button>
            <button
              className="btn ghost xs"
              onClick={() => onEdit(profile)}
              title="Edit"
              data-testid={`pf-btn-edit-${profile.name}`}
            >
              Edit
            </button>
          </>
        )}
        <button
          className="btn ghost xs danger"
          onClick={() => onDelete(profile)}
          disabled={isSeed}
          title={isSeed ? 'Seed profiles cannot be deleted' : 'Delete'}
          data-testid={`pf-btn-delete-${profile.name}`}
        >
          Delete
        </button>
      </div>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

function ProfilesView() {
  const query = useProfiles();
  const profiles = query.data ?? [];

  // Drawer state: null = closed, {mode:'create'} or {mode:'edit',profile} or {mode:'clone',profile}
  const [drawer, setDrawer] = useState(null);
  const [confirmDelete, setConfirmDelete] = useState(null);

  function openCreate() {
    setDrawer({ mode: 'create' });
  }

  function openEdit(profile) {
    setDrawer({ mode: 'edit', profile });
  }

  function openClone(profile) {
    setDrawer({ mode: 'clone', profile });
  }

  function openDelete(profile) {
    setConfirmDelete(profile);
  }

  function closeDrawer() {
    setDrawer(null);
  }

  function onSaved() {
    setDrawer(null);
  }

  function onDeleted() {
    setConfirmDelete(null);
  }

  // Build initial form values for the drawer
  let formInitial = null;
  let isEdit = false;
  let formTitle = null;
  if (drawer) {
    if (drawer.mode === 'create') {
      formInitial = { ...BLANK_FORM };
      isEdit = false;
    } else if (drawer.mode === 'edit' && drawer.profile) {
      formInitial = {
        name: drawer.profile.name,
        image: drawer.profile.image || '',
        flags: drawer.profile.flags || '',
        mtp: !!drawer.profile.mtp,
        device_class: drawer.profile.device_class || '',
      };
      isEdit = true;
    } else if (drawer.mode === 'clone' && drawer.profile) {
      // Seeds fork via "Edit a copy" (<seed>-custom); custom profiles clone
      // as <name>-copy. Both record cloned_from provenance on the new profile.
      const isSeed = !!drawer.profile.seed;
      const suffix = isSeed ? '-custom' : '-copy';
      formInitial = {
        name: `${drawer.profile.name}${suffix}`.slice(0, 32),
        image: drawer.profile.image || '',
        flags: drawer.profile.flags || '',
        mtp: !!drawer.profile.mtp,
        device_class: drawer.profile.device_class || '',
        cloned_from: drawer.profile.name,
      };
      isEdit = false;
      if (isSeed) formTitle = `Edit a copy · ${drawer.profile.name}`;
    }
  }

  if (query.isLoading) {
    return (
      <div className="view">
        <div className="view-head">
          <h2>Profiles</h2>
        </div>
        <div className="empty mono">Loading profiles…</div>
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="view">
        <div className="view-head">
          <h2>Profiles</h2>
        </div>
        <div className="empty mono" style={{color: 'var(--err)'}}>
          Failed to load profiles: {query.error?.message || 'unknown error'}
        </div>
      </div>
    );
  }

  return (
    <div className="view">
      <div className="view-head">
        <h2>Profiles</h2>
        <div className="view-sub mono">
          Container-slot templates — image + bench-tuned flags per inference workload.
        </div>
      </div>

      <div className="pf-toolbar">
        <button
          className="btn sm"
          onClick={openCreate}
          data-testid="pf-btn-new"
        >
          + New profile
        </button>
      </div>

      {profiles.length === 0 ? (
        <div className="empty mono">No profiles configured.</div>
      ) : (
        <div className="pf-list">
          {profiles.map(p => (
            <ProfileCard
              key={p.name}
              profile={p}
              onEdit={openEdit}
              onClone={openClone}
              onDelete={openDelete}
            />
          ))}
        </div>
      )}

      {drawer && (
        <ProfileForm
          initial={formInitial}
          isEdit={isEdit}
          title={formTitle}
          onClose={closeDrawer}
          onSaved={onSaved}
        />
      )}

      {confirmDelete && (
        <DeleteConfirm
          profile={confirmDelete}
          onCancel={() => setConfirmDelete(null)}
          onConfirmed={onDeleted}
        />
      )}
    </div>
  );
}

Object.assign(window, { ProfilesView });
