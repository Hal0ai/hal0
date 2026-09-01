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
import { useSystemInfo } from '@/api/hooks/useRuntimes'
import { useRunnerImages, useRunnerImagePullJob } from '@/api/hooks/useRunnerImages'
import { selectedRunnerKey } from './hw-cascade.js'
import { prettyProfile } from './profile-names'
import {
  findManagedFlags,
  findNewSlotHardwareFlags,
  tokenizeFlags,
  MANAGED_FLAG_SOURCE,
} from './flags-tune.js'

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

const BLANK = { name: '', intent: '', backend: 'rocm', quant: '', flags: '', mtp: false, runner: '' };

function bk(name, meta) { return meta[name] || meta.cpu; }

// (backendOf() retired with the card's device-hued chip: a profile's INERT
// backend/device_class fit hint is not the runtime it carries, and the card
// now shows the runtime's real lanes instead of a hue derived from a
// match-only field.)

// ── runtime vocabulary ───────────────────────────────────────────────────────
//
// `profile.runner` is an OPTIONAL RUNNER_IMAGES key (Task 10 D4) — a profile
// that carries one pins the slot's runtime on apply (the server's profile-wins
// reconcile, Task 5); an empty one is "Auto", a flags-only tune with no
// runtime opinion. Display copy is the registry's own (`title`/`blurb` on
// system-info's `backends` rows), so the profile drawer, the profile card and
// the slot drawer name a runtime the same way.

const LANE_TITLE = { rocm: 'ROCm', vulkan: 'Vulkan', cuda: 'CUDA', cpu: 'CPU' };
// One hue per backend lane (dashboard.css --dev-*), so a lane reads the same
// colour in a chip here as it does on a slot card or a chart legend.
const LANE_HUE = {
  rocm: 'var(--dev-rocm)', vulkan: 'var(--dev-vulkan)',
  cuda: 'var(--dev-cuda)', cpu: 'var(--dev-cpu)',
};
// Lane token → the host capability flag gating it (mirrors hw-cascade.js's
// LANE_HW). A lane with no entry is never hardware-vetoed.
const LANE_HW = { rocm: 'rocm', vulkan: 'vulkan', cuda: 'cuda' };

// The backends a runtime can actually run on: the §4 fit-check list, falling
// back to its single declared `backend` on an older system-info payload.
// Empty = backend-agnostic (nothing to claim).
function runnerLanes(r) {
  const sup = r?.supported_backends;
  if (Array.isArray(sup) && sup.length > 0) return sup;
  return r?.backend ? [r.backend] : [];
}
function laneLabel(lanes) {
  return (lanes || []).map((l) => LANE_TITLE[l] || l).join(' + ');
}

// Host capability flags for the runtime filter below — mirrors the slot
// drawer's hostHwFlags (slot-modals.jsx): read from the RAW /api/system-info
// `hardware` payload (snake_case, nested under gpus[]), NOT the normalized
// computeCapable/vulkanCapable shape useHardware.ts exposes. Absent hardware
// (still loading) never vetoes.
function hostHwFlags(rawHardware) {
  const gpu0 = rawHardware?.gpus?.[0];
  return rawHardware
    ? { rocm: !!gpu0?.compute_capable, vulkan: !!gpu0?.vulkan_capable, cuda: !!gpu0?.compute_capable }
    : {};
}

// Runtime options for the profile drawer, in the same row shape hw-cascade's
// runnerOptions() produces. That function is NOT reused here: its device_class
// and slot-type filters answer "what can THIS slot launch", and a profile has
// neither a device nor a slot type — it is a portable tune template, so the
// list is every runtime the box's registry carries (CPU engines included).
// The hardware veto DOES apply identically: a runtime whose every lane is
// infeasible on this box is hidden rather than offered and then rejected.
function profileRunnerOptions(backends, hw) {
  const out = [];
  for (const [key, r] of Object.entries(backends || {})) {
    if (!r) continue;
    const lanes = runnerLanes(r);
    if (hw && lanes.length > 0 && lanes.every((l) => LANE_HW[l] && hw[LANE_HW[l]] === false)) continue;
    out.push({
      key,
      title: r.title || key,
      blurb: r.blurb || '',
      lanes,
      state: r.state,
      isDefault: !!r.is_default,
    });
  }
  out.sort((a, b) => (b.isDefault ? 1 : 0) - (a.isDefault ? 1 : 0) || a.title.localeCompare(b.title));
  return out;
}

// Runner badge title: the runner's operator-facing name from system-info's
// backends catalog, falling back to the raw key when the catalog hasn't
// loaded yet or no longer carries it (deleted/renamed runner).
function runnerTitleFor(key, backends) {
  return backends?.[key]?.title || key;
}

// Backend chips for a profile's title row. ONE HUE PER BACKEND, ONE CHIP PER
// BACKEND: a dual-backend runtime renders two chips side by side, never a
// blended "ROCm + Vulkan" pill — the chip row is literally the list of lanes
// the runtime can run on. A profile that pins nothing makes no backend claim,
// so it gets the muted AUTO chip, joined by its runtime family when that
// family is a singleton engine rather than the generic llama-server (the
// engine is then a structural fact of the profile, not a runtime it chose).
// Replaces the old runtimeLabel() "family · slot binary" chip, which named a
// vocabulary ("binary") the operator surface no longer speaks.
export function runtimeChips(p, backends) {
  const key = p?.runner;
  if (!key) {
    const chips = [{ key: 'auto', label: 'AUTO', hue: null }];
    if (p?.runtime_family && p.runtime_family !== 'llama-server')
      chips.push({ key: 'family', label: p.runtime_family, hue: null });
    return chips;
  }
  const row = backends?.[key];
  if (!row) return [{ key: 'unknown', label: 'unknown', hue: null }];
  const lanes = runnerLanes(row);
  if (lanes.length === 0) return [{ key: 'auto', label: 'AUTO', hue: null }];
  return lanes.map((l) => ({ key: l, label: LANE_TITLE[l] || l, hue: LANE_HUE[l] || null }));
}

// Install state of the runtime a profile pins, in the dropdown's vocabulary.
export function runtimeState(p, backends) {
  if (!p?.runner) return { label: 'not pinned', tone: '' };
  const row = backends?.[p.runner];
  if (!row) return { label: 'unknown', tone: '' };
  if (row.state === 'installed') return { label: '● installed', tone: 'ok' };
  if (row.state === 'installable') return { label: '○ not pulled', tone: 'warn', pullable: true };
  return { label: 'unavailable', tone: 'err' };
}

// The one-liner under the intent: the registry's own title + blurb, the same
// sentence the slot drawer shows for that runtime. An unpinned profile says
// so in words rather than by omission.
function runtimeLine(p, backends) {
  if (!p?.runner) return 'Auto — runs on whatever runtime the slot already uses.';
  const title = runnerTitleFor(p.runner, backends);
  const blurb = backends?.[p.runner]?.blurb;
  return blurb ? `${title} — ${blurb}` : title;
}

// ── inline pull affordance ───────────────────────────────────────────────────
//
// "Not pulled" is fixable where it is read (drawer consequence box, card state
// chip) instead of failing at spawn, through the SAME seam the Runner Images
// page uses: useRunnerImagePullJob → POST /api/runner-images/{id}/pull.
// system-info names a runtime's resolved image REF; that route addresses a
// catalogue ROW id, which images.json may supply independently of the repo
// path (registry/runner_image_sync.py:475), so the row is matched by repo
// rather than derived from the ref.
function splitImageRef(ref) {
  const s = String(ref || '');
  const at = s.indexOf('@');
  const body = at >= 0 ? s.slice(0, at) : s;
  const lastSlash = body.lastIndexOf('/');
  const tail = lastSlash >= 0 ? body.slice(lastSlash + 1) : body;
  const colon = tail.indexOf(':');
  if (colon < 0) return { repo: body, tag: null };
  return { repo: body.slice(0, lastSlash + 1) + tail.slice(0, colon), tag: tail.slice(colon + 1) };
}

export function pullTargetFor(runnerRow, images) {
  const { repo, tag } = splitImageRef(runnerRow?.image);
  if (!repo) return null;
  const row = (images || []).find((i) => i?.image === repo);
  if (!row) return null;
  // Only name a tag the catalogue actually knows — the route 404s on any
  // other, and the row's headline tag is the honest fallback.
  const known = tag && (row.tag === tag || (row.available_tags || []).includes(tag));
  return { id: row.id, tag: known ? tag : undefined };
}

function useRunnerPull(runnerKey, backends) {
  const imagesQuery = useRunnerImages();
  const job = useRunnerImagePullJob();
  const target = pullTargetFor(backends?.[runnerKey], imagesQuery.data?.images);
  return {
    target,
    inFlight: !!job.inFlight,
    // A failed pull is reported inline and nothing else — it never gates the
    // profile save (the profile is a template; the image is a separate fact).
    error: job.error?.message || (target ? null : 'no catalogue entry for this runtime image'),
    start: () => { if (target) job.start(target.id, target.tag).catch(() => {}); },
  };
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

// Install-state chip. A not-pulled runtime is fixable where the state is
// read: the chip itself becomes the Pull button (its own component so the
// pull hooks mount only on the cards that offer it). A failed pull reverts
// the chip and carries the reason in its tooltip — it changes nothing about
// the profile.
function RuntimePullChip({ name, runnerKey, backends }) {
  const pull = useRunnerPull(runnerKey, backends);
  return (
    <button type="button" className="pf-state mono warn" onClick={pull.start}
      disabled={pull.inFlight || !pull.target}
      title={pull.error ? `Pull failed — ${pull.error}` : 'Pull this runtime image now'}
      data-testid={`pf-runtime-state-${name}`}>
      {pull.inFlight ? '◌ pulling…' : '○ not pulled'}
    </button>
  );
}

function RuntimeStateChip({ p, backends }) {
  const st = runtimeState(p, backends);
  if (st.pullable) return <RuntimePullChip name={p.name} runnerKey={p.runner} backends={backends} />;
  return (
    <span className={'pf-state mono' + (st.tone ? ' ' + st.tone : '')}
      data-testid={`pf-runtime-state-${p.name}`}>{st.label}</span>
  );
}

// Profile card — adopts the Stacks library-card shell (.stk-lib-*) so the
// Profiles and Stacks grids read as one family. Same data + actions as before,
// plus the runtime the profile carries: backend chips in the title row,
// install state where the metric sits, title + blurb under the intent.
export function ProfileCard({ p, index, onEdit, onClone, onDelete, onExport }) {
  const systemInfoQuery = useSystemInfo();
  const backends = systemInfoQuery.data?.backends ?? {};
  const chips = runtimeChips(p, backends);
  const isSeed = !!p.seed;
  const usedBy = p.used_by || [];
  const inUse = usedBy.length;
  const metric = p.tps != null ? `${p.tps.toFixed(1)} tok/s`
    : p.rtf != null ? `${p.rtf.toFixed(2)}× rtf` : null;

  return (
    <div className="stk-lib-card" style={{ animationDelay: (index * 34) + 'ms' }}>
      <div className="stk-lib-h">
        <div className="stk-lib-id">
          <div className="pf-title-row">
            <span className="stk-lib-name">{p.name}</span>
            <span className="pf-be-row" data-testid={`pf-runner-badge-${p.name}`}
              title={p.runner ? `runtime → ${p.runner}` : 'no runtime pinned'}>
              {chips.map(c => (
                <span key={c.key} className="pf-be mono" style={c.hue ? { '--bk': c.hue } : null}>{c.label}</span>
              ))}
            </span>
          </div>
          <div className="stk-lib-intent">
            {intentOf(p)}{p.cloned_from && <span className="pf-based mono"> · ↳ {p.cloned_from}</span>}
          </div>
          <div className="pf-runtime-line" data-testid={`pf-runtime-line-${p.name}`}>
            {runtimeLine(p, backends)}
          </div>
        </div>
        <div className="pf-card-meta">
          <RuntimeStateChip p={p} backends={backends} />
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

// Managed / slot-hardware rejection copy — mirrors the model drawer's inline
// messages (model-drawer.jsx) so both flag editors speak with one voice.
function managedFlagError(offenders) {
  const first = offenders[0];
  const canon = first === '-ngl' ? '--n-gpu-layers' : first === '-c' ? '--ctx-size' : first;
  const where = MANAGED_FLAG_SOURCE[first] || MANAGED_FLAG_SOURCE[canon] || 'the slot/model configuration';
  const rest = offenders.length > 1 ? ` (also managed: ${offenders.slice(1).join(', ')})` : '';
  return `${first} is computed by hal0 and can't be set here — it comes from ${where}. Remove it.${rest}`;
}
function hardwareFlagError(offenders) {
  const first = offenders[0];
  const rest = offenders.length > 1 ? ` (also: ${offenders.slice(1).join(', ')})` : '';
  return `${first} is hardware — it belongs on the slot (device · NGL · THREADS grid), not the profile. Remove it.${rest}`;
}

function validateForm(form, existing, storedFlags = '') {
  const errs = {};
  const name = (form.name || '').trim();
  if (!name) errs.name = 'Name is required';
  else if (!NAME_RE.test(name)) errs.name = 'lowercase · digits · - · _ · must start alphanumeric';
  else if (existing.includes(name)) errs.name = `“${name}” already exists`;
  // spec-hw-slot-ownership §3: profiles no longer carry an image — only a name
  // + device-agnostic tune. Image lives on the runner (RUNNER_IMAGES[binary]).
  // Flag-ownership screens (§21.7 managed + §5 slot-hardware) — mirror the
  // server hard-reject inline so the save never surfaces the 400. Hardware
  // first so -ngl (in both sets) gets the "belongs on the slot" message.
  //
  // `storedFlags` is the profile's own persisted flag text when EDITING (empty
  // for create/clone). Only newly-introduced hardware flags are rejected, which
  // is exactly the server's rule since #1411 — a profile authored before §5
  // shipped carries -dev/--threads and must stay editable.
  const flagsText = form.flags || '';
  const quoteErr = tokenizeFlags(flagsText).error;
  const hw = quoteErr ? [] : findNewSlotHardwareFlags(flagsText, storedFlags);
  const managed = quoteErr ? [] : findManagedFlags(flagsText);
  if (quoteErr) errs.flags = quoteErr;
  else if (hw.length) errs.flags = hardwareFlagError(hw);
  else if (managed.length) errs.flags = managedFlagError(managed);
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
// "Pull now" — the inline fix for a not-pulled runtime (panel 11). Its own
// component so the pull hooks mount only where the affordance renders.
function RunnerPullButton({ runnerKey, backends }) {
  const pull = useRunnerPull(runnerKey, backends);
  return (
    <>
      <button type="button" className="pf-btn" onClick={pull.start}
        disabled={pull.inFlight || !pull.target} data-testid="pf-runtime-pull">
        {pull.inFlight ? 'Pulling…' : pull.error ? 'Retry pull' : 'Pull now'}
      </button>
      {pull.error && !pull.inFlight && (
        <span className="mono pf-pull-err" data-testid="pf-runtime-pull-err">{pull.error}</span>
      )}
    </>
  );
}

export function ProfileDrawer({ mode, source, existing = [], onClose, onSaved }) {
  const isEdit = mode === 'edit';
  const BACKEND_META = useBackendMeta();
  const systemInfoQuery = useSystemInfo();
  const backends = systemInfoQuery.data?.backends ?? {};
  const runtimeOptions = profileRunnerOptions(backends, hostHwFlags(systemInfoQuery.data?.hardware));
  const [advOpen, setAdvOpen] = useState(false);
  // Seed profiles render the Runtime select disabled (edit-a-copy forks the
  // seed's runner binding verbatim; a subsequent Edit of that custom copy —
  // which is never itself a seed — unlocks it). `isEdit` never lands on a
  // seed (ProfileCard only offers Edit-a-copy for seeds — see onClone), so
  // this only actually gates the 'clone' mode.
  const runtimeLocked = !!(source && source.seed);

  const deriveInitial = () => {
    if (mode === 'create') return { ...BLANK };
    const base = {
      name: source.name,
      intent: source.intent || '',
      quant: source.quant || '',
      flags: source.flags || '',
      mtp: !!source.mtp,
      runner: source.runner || '',
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
  // Grandfather baseline (#1411): only an EDIT inherits the profile's stored
  // flags. A clone is a create — its hardware flags are newly introduced under
  // the new name, so they stay a hard reject, matching ProfileCatalog.create.
  const storedFlags = isEdit ? (source.flags || '') : '';
  const f = useForm({
    deriveInitial,
    resetKey: `${mode}:${source?.name ?? ''}`,
    validate: (v) => validateForm(v, taken, storedFlags),
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
  // '' = Auto; a key = that option; null = a stored key this box's registry
  // doesn't offer (out-of-vocab — rendered as its own option below).
  const runtimeSel = selectedRunnerKey({ binary: form.runner || '', options: runtimeOptions });
  const runtimeUnknown = runtimeSel === null;
  const runtimeValue = runtimeUnknown ? form.runner : runtimeSel;
  const selectedRuntime = runtimeSel ? runtimeOptions.find(o => o.key === runtimeSel) : null;

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
      runner: form.runner || null,
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

        <div className="mono pf-hint">Hardware and image are selected on the slot.</div>

        {/* Runtime — same dropdown anatomy as the slot drawer's Runtime
            control (title · lane(s) · install state, blurb underneath), so
            one vocabulary covers both surfaces. '' = Auto. */}
        <FormRow label="Runtime" sub="optional — pins the slot's engine build on apply">
          <select className="pf-input mono" value={runtimeValue} disabled={runtimeLocked}
            onChange={e => set('runner', e.target.value)} data-testid="profile-runner">
            <option value="">— Auto · no runtime pinned —</option>
            {/* A persisted key this box's registry no longer carries keeps its
                own option: the drawer never silently rewrites stored state. */}
            {runtimeUnknown && (
              <option value={form.runner}>{form.runner} · not in this box's registry</option>
            )}
            {runtimeOptions.map(o => (
              <option key={o.key} value={o.key}>
                {o.title}{laneLabel(o.lanes) ? ` · ${laneLabel(o.lanes)}` : ''}
                {o.state === 'installable' ? ' · not pulled' : ''}
              </option>
            ))}
          </select>
        </FormRow>
        {selectedRuntime?.blurb && <div className="mono pf-hint">{selectedRuntime.blurb}</div>}
        {runtimeUnknown && (
          <div className="mono pf-hint pf-hint-warn" data-testid="pf-runtime-unknown">
            hal0 has no entry for this runtime — kept because the profile already carries it.
            Picking anything else drops it for good.
          </div>
        )}
        {selectedRuntime && (
          <div className="pf-consequence" data-testid="pf-runtime-consequence">
            <span>
              {laneLabel(selectedRuntime.lanes)
                ? `Slots applying this profile move to the ${laneLabel(selectedRuntime.lanes)} lane.`
                : `Slots applying this profile launch on ${selectedRuntime.title}.`}
            </span>
            {selectedRuntime.state === 'installable' && (
              <RunnerPullButton runnerKey={selectedRuntime.key} backends={backends} />
            )}
          </div>
        )}
        {runtimeLocked && (
          <div className="mono pf-hint">Forked from a seed — its runtime carries over; edit the copy to change it.</div>
        )}

        <FormRow label="Flags" sub="appended to the run command"
          error={show('flags') ? errs.flags : null}>
          <textarea className={'pf-input mono pf-textarea' + (show('flags') && errs.flags ? ' err' : '')}
            value={form.flags || ''}
            onChange={e => set('flags', e.target.value)} onBlur={() => touch('flags')}
            rows={3} placeholder="-fa on --jinja -b 2048 -ub 512"
            aria-invalid={!!(show('flags') && errs.flags)} data-testid="pf-input-flags" />
        </FormRow>

        <FormRow label="MTP" sub="Multi-Token Prediction speculative decode">
          <button type="button" className={'pf-switch' + (form.mtp ? ' on' : '')} onClick={() => set('mtp', !form.mtp)}
            role="switch" aria-checked={form.mtp} data-testid="pf-check-mtp">
            <span className="pf-switch-knob" />
            <span className="pf-switch-lbl mono">{form.mtp ? 'enabled' : 'disabled'}</span>
          </button>
        </FormRow>

        {/* Advanced (operator ruling): the main form is name · intent ·
            runtime · flags · MTP. Quant is a match-only display fact, not
            something authoring a tune starts from, so it demotes here — same
            progressive-disclosure idiom as the slot drawer's Advanced
            section. Still editable, still prefilled on edit/clone/import. */}
        <button type="button" className="pf-disc" aria-expanded={advOpen}
          onClick={() => setAdvOpen(o => !o)} data-testid="pf-advanced-toggle">
          <span className="mono pf-disc-caret">{advOpen ? '▾' : '▸'}</span>Advanced
        </button>
        {advOpen && (
          <FormRow label="Quant" sub="weight format">
            <input className="pf-input mono" value={form.quant || ''} onChange={e => set('quant', e.target.value)}
              placeholder="FP4 · Q4_K_M …" data-testid="pf-input-quant" />
          </FormRow>
        )}
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
