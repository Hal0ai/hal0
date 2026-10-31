// hal0 dashboard — Profiles view (issue #658; ADR-0029 profile-centric config).
//
// A profile is the whole TUNE for a slot: llama-server flags, context window,
// thinking default, vision, speculation and runtime. The model carries file facts
// only and the slot carries placement, so "what does this slot launch with"
// is answered by one profile. This view is a structured surface: a summary
// strip, seed/custom grouping, cards that show the tune (ctx, thinking,
// vision, speculation, runtime, flags) and the "used by" slot binding, a slide-in form
// drawer, and a styled delete confirm with an in-use guard.
//
// Data comes from GET /api/profiles (useProfiles).
//
// Seeds are immutable: Edit becomes "Edit a copy" (forks <seed>-custom with
// cloned_from set); Delete is disabled. Custom profiles get Clone/Edit/Delete.

import { useState } from 'react'
import {
  useProfiles,
  useProfileCreate,
  useProfileUpdate,
  useProfileDelete,
  useProfileExport,
  useProfileImport,
} from '@/api/hooks/useProfiles'
import { useSystemInfo } from '@/api/hooks/useRuntimes'
import { useRunnerImages, useRunnerImagePullJob } from '@/api/hooks/useRunnerImages'
import { hostHwFlags, selectedRunnerKey, tierRank } from './hw-cascade.js'
import { prettyProfile } from './profile-names'
import {
  findManagedFlags,
  findSlotHardwareFlags,
  findModelFactFlags,
  findHostOwnedFlags,
  findPositionalArgs,
  screenCanon,
  tokenizeFlags,
  MANAGED_FLAG_SOURCE,
  MODEL_FACT_FLAG_SOURCE,
  SPEC_TYPE_REFUSAL,
} from './flags-tune.js'

// `NAME_RE` (name regex) and `toast` are shared globals from primitives.jsx.

// Server-enforced ctx_size bounds (400 profiles.ctx_size_out_of_range).
const PROFILE_CTX_MIN = 128;
const PROFILE_CTX_MAX = 16777216;

// ctx is held as text in the form ('' = defer to the model's native window);
// thinking is 'auto' | 'on' | 'off'; vision is 'auto' | 'off'; speculation is 'auto' | 'off' | 'ngram'.
const BLANK = {
  name: '', intent: '', flags: '', runner: '',
  ctx: '', thinking: 'auto', effort: 'default', budget: '', vision: 'auto', speculation: 'auto',
};

// ── typed tune fields ────────────────────────────────────────────────────────
// enable_thinking on the wire is null (unset: off unless the request asks) | true | false.
// A structured-output request with no preference is exempt from the off
// default and keeps the chat template's own default (normalize/thinking.py).
export function thinkingToForm(v) { return v === true ? 'on' : v === false ? 'off' : 'auto'; }
export function thinkingFromForm(v) { return v === 'on' ? true : v === 'off' ? false : null; }
// Unset (null) reasons only when a request asks — the same as 'off' — so the
// card and preview say so instead of implying the model decides.
export function thinkingLabel(v) { return v === 'on' ? 'on' : v === 'off' ? 'off' : 'default (off)'; }
// Reasoning effort (llama-server --reasoning-effort); 'default' emits nothing.
export const REASONING_EFFORTS = ['default', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];
export const effortOf = (v) => (REASONING_EFFORTS.includes(v) ? v : 'default');
// Thinking-token budget is held as text in the form ('' = unlimited = -1 on the wire).
export function budgetToForm(v) { return v == null || v === -1 ? '' : String(v); }
export function budgetFromForm(v) {
  const t = String(v ?? '').trim();
  return t === '' ? -1 : Number(t);
}
export function budgetError(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  if (!/^(-1|\d+)$/.test(raw)) return 'Must be a whole number, 0 or more (blank = unlimited)';
  return null;
}
export function reasoningLabel(effort, budget) {
  const b = budget == null || budget === -1 ? 'unlimited' : String(budget);
  return `effort ${effortOf(effort)} · budget ${b}`;
}
const switchOf = (v) => (v === 'off' ? 'off' : 'auto');
const specOf = (v) => (v === 'off' || v === 'ngram' ? v : 'auto');

// 32768 → "32K"; non-round values keep the exact count.
export function ctxLabel(n) {
  if (n == null || n === '') return 'native';
  const v = Number(n);
  if (!Number.isFinite(v)) return String(n);
  if (v >= 1024 && v % 1024 === 0) return `${v / 1024}K`;
  return v.toLocaleString('en-US');
}

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

// Host capability flags come from hw-cascade.js's shared `hostHwFlags` — the
// same translator the slot drawer uses, so the two drawers cannot answer the
// feasibility question differently for one box (this file used to keep a
// private copy, which drifted: it missed the kfd_present ROCm rule and turned
// a gpus[]-less payload into an explicit all-false veto).

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
      tier: r.tier || null,
    });
  }
  out.sort((a, b) => tierRank(a.tier) - tierRank(b.tier) || a.title.localeCompare(b.title));
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
// catalogue ROW id, and catalogue rows are one per runner (the row id IS the
// runner key), so the row is looked up by runner key. The ref only
// contributes the tag.
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

export function pullTargetFor(runnerKey, runnerRow, images) {
  if (!runnerKey) return null;
  const row = (images || []).find((i) => i?.id === runnerKey);
  if (!row) return null;
  const { tag } = splitImageRef(runnerRow?.image);
  // Only name a tag the catalogue actually knows — the route 404s on any
  // other, and the row's headline tag is the honest fallback.
  const known = tag && (row.tag === tag || (row.available_tags || []).includes(tag));
  return { id: row.id, tag: known ? tag : undefined };
}

// Exported (Task 11c, issue #2185): the slot drawer's Runtime RichSelect
// reuses this SAME pull mutation for its own not-pulled rows, instead of
// wiring a second pull hook that could drift from this one's target
// resolution (splitImageRef/pullTargetFor above).
export function useRunnerPull(runnerKey, backends) {
  const imagesQuery = useRunnerImages();
  const job = useRunnerImagePullJob();
  const target = pullTargetFor(runnerKey, backends?.[runnerKey], imagesQuery.data?.images);
  return {
    target,
    inFlight: !!job.inFlight,
    // A failed pull is reported inline and nothing else — it never gates the
    // profile save (the profile is a template; the image is a separate fact).
    error: job.error?.message || (target ? null : 'no catalogue entry for this runtime'),
    start: () => { if (target) job.start(target.id, target.tag).catch(() => {}); },
  };
}

// Card headline. Prefer the server-authored intent; fall back to a pretty
// profile name (#751 shared map) so un-labelled custom profiles read well.
function intentOf(p) {
  return p.intent || prettyProfile(p.name);
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
  if (p.quarantined) return <QuarantinedCard p={p} index={index} onDelete={onDelete} />;
  return <TunedProfileCard p={p} index={index} onEdit={onEdit} onClone={onClone} onDelete={onDelete} onExport={onExport} />;
}

// A profiles.toml entry that failed validation (#2653): it carries no tune,
// only the reason it was set aside. Read-only apart from delete; recreating
// the name through "New profile" replaces it.
function QuarantinedCard({ p, index, onDelete }) {
  const usedBy = p.used_by || [];
  return (
    <div className="stk-lib-card" style={{ animationDelay: (index * 34) + 'ms', opacity: 0.75 }}
      data-testid={`pf-quarantined-${p.name}`}>
      <div className="stk-lib-h">
        <div className="stk-lib-id">
          <div className="pf-title-row">
            <span className="stk-lib-name">{p.name}</span>
          </div>
          <div className="stk-lib-intent" style={{ color: 'var(--err)' }}>invalid as stored</div>
          <div className="pf-runtime-line mono" data-testid={`pf-quarantined-error-${p.name}`}>{p.error}</div>
        </div>
      </div>
      <div className="stk-lib-f">
        {usedBy.length
          ? <span className="stk-tag" title={'used by ' + usedBy.join(', ')}>used by {usedBy.length}</span>
          : <span className="mono pf-card-unused">unused</span>}
        <span className="stk-tag">quarantined</span>
        <span className="stk-spacer" />
        <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onDelete(p)}
          title={usedBy.length ? 'In use — detach slots first' : 'Delete this entry (or recreate the name to replace it)'}
          data-testid={`pf-btn-delete-${p.name}`}>{Icons.trash}</button>
      </div>
    </div>
  );
}

function TunedProfileCard({ p, index, onEdit, onClone, onDelete, onExport }) {
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

      <div className="stk-lib-slotlist" data-testid={`pf-tune-${p.name}`}>
        <PfRow label="ctx" value={p.ctx_size != null ? ctxLabel(p.ctx_size) : 'model native'} />
        <PfRow label="thinking" value={thinkingLabel(thinkingToForm(p.enable_thinking))} />
        <PfRow label="reasoning" value={reasoningLabel(p.reasoning_effort, p.reasoning_budget)} />
        <PfRow label="vision" value={p.vision === 'off' ? 'off' : 'auto'} />
        <PfRow label="spec" value={specOf(p.speculation)} />
        <PfRow label="runner" value={p.runner ? runnerTitleFor(p.runner, backends) : 'auto'} />
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

// Managed / slot-hardware / model-fact rejection copy — the inline mirror of
// the server's 400s (slot.managed_arg_denied, slot.hardware_flag_denied,
// profiles.model_fact_flag_denied).
function managedFlagError(offenders) {
  const first = offenders[0];
  const canon = screenCanon(first);
  const where = MANAGED_FLAG_SOURCE[first] || MANAGED_FLAG_SOURCE[canon] || 'the slot/model configuration';
  const rest = offenders.length > 1 ? ` (also managed: ${offenders.slice(1).join(', ')})` : '';
  return `${first} is computed by hal0 and can't be set here — it comes from ${where}. Remove it.${rest}`;
}
function hardwareFlagError(offenders) {
  const first = offenders[0];
  const rest = offenders.length > 1 ? ` (also: ${offenders.slice(1).join(', ')})` : '';
  return `${first} is hardware — it belongs on the slot (device · NGL · THREADS grid), not the profile. Remove it.${rest}`;
}

function modelFactFlagError(offenders) {
  const first = offenders[0];
  const name = screenCanon(first.split(/[ =]/)[0]);
  const where = MODEL_FACT_FLAG_SOURCE[name];
  const why = name === '--spec-type'
    ? SPEC_TYPE_REFUSAL
    : where
    ? `it is a fact about the model file, set on ${where}, not on the profile`
    : name === '--spec-draft-device' || name === '--mmproj-device'
      ? 'hal0 derives it from the slot\'s device'
      : 'it breaks the reasoning split hal0 depends on';
  const rest = offenders.length > 1 ? ` (also: ${offenders.slice(1).join(', ')})` : '';
  return `${first} can't be set on a profile — ${why}. Remove it.${rest}`;
}

function hostOwnedFlagError(offenders) {
  const first = offenders[0];
  const rest = offenders.length > 1 ? ` (also: ${offenders.slice(1).join(', ')})` : '';
  return `${first} can't be set on a profile — it fetches a model, reads or writes a host file, opens or shapes a listener, or sets auth/TLS, and hal0 manages all of that for the slot. Remove it.${rest}`;
}

function positionalArgError(args) {
  const rest = args.length > 1 ? ` (also: ${args.slice(1).join(', ')})` : '';
  return `“${args[0]}” has no flag in front of it — every value must follow its flag. Remove it or add the flag.${rest}`;
}

// Context window text → error message or null. Empty = defer to the model's
// native window. Mirrors 400 profiles.ctx_size_out_of_range (128..16777216).
export function ctxError(text) {
  const raw = String(text ?? '').trim();
  if (!raw) return null;
  if (!/^\d+$/.test(raw)) return 'Must be a whole number of tokens';
  const n = Number(raw);
  if (n < PROFILE_CTX_MIN || n > PROFILE_CTX_MAX) {
    return `Must be between ${PROFILE_CTX_MIN.toLocaleString('en-US')} and ${PROFILE_CTX_MAX.toLocaleString('en-US')}`;
  }
  return null;
}

function validateForm(form, existing) {
  const errs = {};
  const name = (form.name || '').trim();
  if (!name) errs.name = 'Name is required';
  else if (!NAME_RE.test(name)) errs.name = 'lowercase · digits · - · _ · must start alphanumeric';
  else if (existing.includes(name)) errs.name = `“${name}” already exists`;
  const ctxErr = ctxError(form.ctx);
  if (ctxErr) errs.ctx = ctxErr;
  const budgetErr = budgetError(form.budget);
  if (budgetErr) errs.budget = budgetErr;
  // Flag-ownership screens (§21.7 managed + §5 slot-hardware + ADR-0029
  // model-fact flags) — mirror the server hard-reject inline so the save never
  // surfaces the 400. Hardware first so -ngl (in both sets) gets the "belongs
  // on the slot" message. The server screens the WHOLE flag text on every save
  // (hal0.profiles.screen_profile_flags), with no grandfathering of what a
  // stored profile already carries, so the client does the same.
  const flagsText = form.flags || '';
  const quoteErr = tokenizeFlags(flagsText).error;
  const hw = quoteErr ? [] : findSlotHardwareFlags(flagsText);
  const managed = quoteErr ? [] : findManagedFlags(flagsText);
  // Model-fact flags are refused outright (profiles.model_fact_flag_denied).
  const facts = quoteErr ? [] : findModelFactFlags(flagsText);
  // Host-owned flags and bare arguments (slot.host_owned_flag_denied /
  // slot.positional_arg_denied), the same screens the server runs.
  const host = quoteErr ? [] : findHostOwnedFlags(flagsText);
  const positional = quoteErr ? [] : findPositionalArgs(flagsText);
  if (quoteErr) errs.flags = quoteErr;
  else if (hw.length) errs.flags = hardwareFlagError(hw);
  else if (managed.length) errs.flags = managedFlagError(managed);
  else if (facts.length) errs.flags = modelFactFlagError(facts);
  else if (host.length) errs.flags = hostOwnedFlagError(host);
  else if (positional.length) errs.flags = positionalArgError(positional);
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

// `saveAs` (slot drawer's "Save as profile…"): a clone whose source already
// carries the suggested name and the staged overrides, so the name is used
// verbatim and `cloned_from` is the source's own.
export function ProfileDrawer({ mode, source, existing = [], onClose, onSaved, saveAs = false }) {
  const isEdit = mode === 'edit';
  const systemInfoQuery = useSystemInfo();
  const backends = systemInfoQuery.data?.backends ?? {};
  const runtimeOptions = profileRunnerOptions(backends, hostHwFlags(systemInfoQuery.data?.hardware));
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
      flags: source.flags || '',
      ctx: source.ctx_size != null ? String(source.ctx_size) : '',
      thinking: thinkingToForm(source.enable_thinking),
      effort: effortOf(source.reasoning_effort),
      budget: budgetToForm(source.reasoning_budget),
      vision: switchOf(source.vision),
      speculation: specOf(source.speculation),
      runner: source.runner || '',
    };
    if (mode === 'clone' && saveAs) {
      return { ...base, name: String(source.name || '').slice(0, 32), ...(source.cloned_from ? { cloned_from: source.cloned_from } : {}) };
    }
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
    const ctxText = String(form.ctx ?? '').trim();
    const body = {
      name: form.name.trim(),
      flags: form.flags ?? '',
      // Empty ctx = "defer to the model's native window": null clears it on a
      // PUT; on a create the key is simply omitted.
      ...(ctxText ? { ctx_size: Number(ctxText) } : isEdit ? { ctx_size: null } : {}),
      enable_thinking: thinkingFromForm(form.thinking),
      reasoning_effort: effortOf(form.effort),
      reasoning_budget: budgetFromForm(form.budget),
      vision: switchOf(form.vision),
      speculation: specOf(form.speculation),
      intent: form.intent ?? '',
      // '' is the API's CLEAR sentinel for runner (#2186), NOT null: on a PUT
      // null means "leave the stored runtime alone", so sending it for the
      // Auto option made picking Auto a silent no-op. On a create '' reads as
      // "no runtime pinned" — the same Auto, nothing to clear.
      runner: form.runner || '',
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
      onSaved(isEdit ? source.name : body.name);
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
    : mode === 'clone' && saveAs ? 'Save as profile'
    : mode === 'clone' ? (source.seed ? `Edit a copy · ${source.name}` : `Clone · ${source.name}`)
    : 'New profile';
  const eyebrow = mode === 'create' ? 'CREATE' : mode === 'clone' ? (saveAs ? 'SAVE AS' : source.seed ? 'EDIT A COPY' : 'CLONE') : 'EDIT';

  return (
    <FormDrawer
      eyebrow={eyebrow}
      title={title}
      submitting={f.submitting}
      dirty={f.isDirty}
      onClose={onClose}
      foot={({ requestClose }) => (
        <>
          <div className="pf-drawer-preview mono" data-testid="pf-drawer-preview">
            <span className="pf-chip-dot" />
            ctx {ctxLabel(String(form.ctx ?? '').trim() || null)} · thinking {thinkingLabel(form.thinking)} · {reasoningLabel(form.effort, budgetFromForm(form.budget))} · vision {form.vision} · speculation {form.speculation}
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

        <div className="mono pf-hint">A profile is the whole tune. Hardware and image are selected on the slot; versions are managed on the Runner Images page.</div>

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

        <FormRow label="Context" sub="tokens · 128–16,777,216 · empty = the model's native window"
          error={show('ctx') ? errs.ctx : null}>
          <input className={'pf-input mono' + (show('ctx') && errs.ctx ? ' err' : '')}
            inputMode="numeric" value={form.ctx || ''}
            onChange={e => set('ctx', e.target.value)} onBlur={() => touch('ctx')}
            placeholder="native" aria-invalid={!!(show('ctx') && errs.ctx)}
            data-testid="pf-input-ctx" />
        </FormRow>

        <FormRow label="Thinking" sub="reasoning default for requests this profile serves; structured-output (JSON) requests keep the model's template default unless they ask">
          <select className="pf-input mono" value={form.thinking}
            onChange={e => set('thinking', e.target.value)} data-testid="pf-select-thinking">
            <option value="auto">Default — off unless the request asks</option>
            <option value="on">On — reason unless the request opts out</option>
            <option value="off">Off — reason only when the request asks</option>
          </select>
        </FormRow>

        <FormRow label="Effort" sub="how hard the model reasons when thinking is on (--reasoning-effort); needs a recent runner build">
          <select className="pf-input mono" value={form.effort}
            onChange={e => set('effort', e.target.value)} data-testid="pf-select-effort">
            {REASONING_EFFORTS.map(v => (
              <option key={v} value={v}>{v === 'default' ? 'Default — the model decides' : v}</option>
            ))}
          </select>
        </FormRow>

        <FormRow label="Budget" sub="thinking-token cap; blank = unlimited, 0 = end thinking immediately"
          error={show('budget') ? errs.budget : null}>
          <input className={'pf-input mono' + (show('budget') && errs.budget ? ' err' : '')}
            inputMode="numeric" value={form.budget || ''}
            onChange={e => set('budget', e.target.value)} onBlur={() => touch('budget')}
            placeholder="unlimited" aria-invalid={!!(show('budget') && errs.budget)}
            data-testid="pf-input-budget" />
        </FormRow>

        <FormRow label="Vision" sub="load the model's mmproj projector">
          <select className="pf-input mono" value={form.vision}
            onChange={e => set('vision', e.target.value)} data-testid="pf-select-vision">
            <option value="auto">Auto — load it when the model has one</option>
            <option value="off">Off — never load it (saves VRAM)</option>
          </select>
        </FormRow>

        <FormRow label="Speculation" sub="speculative decoding (drafter, MTP heads or n-gram)">
          <select className="pf-input mono" value={form.speculation}
            onChange={e => set('speculation', e.target.value)} data-testid="pf-select-speculation">
            <option value="auto">Auto — use the model's drafter or MTP heads when it has them</option>
            <option value="off">Off — never draft</option>
            <option value="ngram">N-gram — model-free speculation, no drafter needed</option>
          </select>
        </FormRow>

        <FormRow label="Flags" sub="appended to the run command"
          error={show('flags') ? errs.flags : null}>
          <textarea className={'pf-input mono pf-textarea' + (show('flags') && errs.flags ? ' err' : '')}
            value={form.flags || ''}
            onChange={e => set('flags', e.target.value)} onBlur={() => touch('flags')}
            rows={3} placeholder="-fa on -b 2048 -ub 512"
            aria-invalid={!!(show('flags') && errs.flags)} data-testid="pf-input-flags" />
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
export function ImportModal({ existing, onClose, onImported }) {
  const imp = useProfileImport();
  const systemInfoQuery = useSystemInfo();
  const backends = systemInfoQuery.data?.backends ?? {};
  // The dry run reports the runtime the envelope AUTHORS (`runner`), the
  // family the profile resolves to, and whether importing has to drop that
  // runtime because this box has no such entry (`runner_stripped`). A dropped
  // runtime WARNS and proceeds: import stays portable, so the flags and tune
  // still land — the profile just arrives with no runtime pinned, and the
  // commit button says exactly that.
  const renderPreview = (report) => {
    const stripped = !!report.runner_stripped;
    const key = report.runner || '';
    const chips = (stripped || !key)
      ? [{ key: 'auto', label: 'AUTO', hue: null }]
      : runtimeChips({ runner: key }, backends);
    return (
      <>
        <div className="stk-dlg-hint">
          {report.name || 'profile'} · schema v{report.schema_version} · checksum {report.checksum_ok ? '✓ ok' : '✗ mismatch'}
        </div>
        <div className="stk-dlg-hint pf-import-runtime" data-testid="pf-import-runtime">
          Runtime:{' '}
          {stripped ? (
            <>
              <span className="mono pf-import-gone">{key}</span>
              <span className="pf-state mono">not available</span>
              {' → Auto'}
            </>
          ) : key ? runnerTitleFor(key, backends) : 'Auto'}
          <span className="pf-be-row">
            {chips.map(c => (
              <span key={c.key} className="pf-be mono" style={c.hue ? { '--bk': c.hue } : null}>{c.label}</span>
            ))}
          </span>
          {report.runtime_family && (
            <span className="pf-state mono">family: {report.runtime_family}</span>
          )}
        </div>
        {stripped && (
          <div className="stk-dlg-warn" data-testid="pf-import-runtime-warn">
            {Icons.alert}<b>{key}</b> is not available on this box — the profile
            imports with runtime Auto. Its flags and tune come across unchanged;
            slots applying it keep their own runtime. Install it from the Runner
            Images page, then set it on the profile.
          </div>
        )}
        {report.collides && (
          <div className="stk-dlg-warn">{Icons.alert}A profile named “{report.name}” already exists — choose a different name to import.</div>
        )}
      </>
    );
  };
  return (
    <ImportDialog
      title="Import profile"
      ariaLabel="Import profile"
      fileAccept=".hal0profile.json,.json,application/json"
      fileHint="Choose a .hal0profile.json file"
      fileTestid="pf-import-file"
      nameTestid="pf-import-name"
      confirmTestid="pf-import-confirm"
      confirmLabel={(report) => (report?.runner_stripped ? 'Import as Auto' : 'Import')}
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
        <span className="meta">the whole tune: flags, context, thinking, vision, speculation, runtime</span>
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
  const allProfiles = query.data ?? [];
  const exportMut = useProfileExport();

  const [drawer, setDrawer] = useState(null);   // {mode, source}
  const [confirm, setConfirm] = useState(null);
  const [importing, setImporting] = useState(false);

  const quarantined = allProfiles.filter(p => p.quarantined);
  const profiles = allProfiles.filter(p => !p.quarantined);
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
        </div>
      </div>

      {profiles.length === 0 && quarantined.length === 0 ? (
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

          {quarantined.length > 0 && (
            <Section title="Needs attention" count={quarantined.length}
              hint="invalid as stored · delete, or recreate the name to replace">
              {quarantined.map((p, i) => (
                <ProfileCard key={p.name} p={p} index={i} onDelete={pp => setConfirm(pp)} />
              ))}
            </Section>
          )}
        </>
      )}

      {drawer && (
        <ProfileDrawer
          mode={drawer.mode}
          source={drawer.source}
          existing={allProfiles.filter(p => !p.quarantined).map(p => p.name)}
          onClose={() => setDrawer(null)}
          onSaved={() => setDrawer(null)}
        />
      )}
      {confirm && (
        <DeleteConfirm p={confirm} onCancel={() => setConfirm(null)} onConfirmed={() => setConfirm(null)} />
      )}
      {importing && (
        <ImportModal
          existing={allProfiles.filter(p => !p.quarantined).map(p => p.name)}
          onClose={() => setImporting(false)}
          onImported={() => setImporting(false)}
        />
      )}
    </div>
  );
}

Object.assign(window, { ProfilesView });
