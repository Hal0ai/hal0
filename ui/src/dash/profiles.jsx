// hal0 dashboard — Profiles view (issue #658; ADR-0029 profile-centric config).
//
// A profile is the whole TUNE for a slot: llama-server flags, context window,
// thinking default, vision, speculation and runtime. The model carries file facts
// only and the slot carries placement, so "what does this slot launch with"
// is answered by one profile.
//
// The page is laid out like the public gallery at hal0.dev/profiles: one grid,
// every card labelled with its kind (built in, custom, import), filter pills
// for kind and runtime, cards that show the settings a profile makes as chips,
// and a detail drawer that prints the flags, the typed settings and the
// `[profile.<name>]` table. The pure half lives in profile-gallery.js.
//
// Data: GET /api/profiles (useProfiles) for the profiles on this box, and
// GET /api/profile-addons (useProfileAddons) for the ones the catalog offers.
// Importing a catalog row is the ordinary import with the row's envelope.
//
// Seeds are immutable: Edit becomes "Edit a copy" (forks <seed>-custom with
// cloned_from set); Delete is disabled. Custom profiles get Clone/Edit/Delete.

import { Fragment, useMemo, useState } from 'react'
import {
  useProfiles,
  useProfileCreate,
  useProfileUpdate,
  useProfileDelete,
  useProfileExport,
  useProfileImport,
  useProfileAddons,
  useProfileAddonsRefresh,
} from '@/api/hooks/useProfiles'
import {
  KIND_CUSTOM,
  KIND_IMPORT,
  KIND_SEED,
  emptyFactsLabel,
  facetOptions,
  galleryCards,
  matchesFilters,
  profileToml,
  toggleFilter,
  tuneFacts,
  tuneOf,
} from './profile-gallery.js'
import { useSystemInfo } from '@/api/hooks/useRuntimes'
import { useRunnerImages, useRunnerImagePullJob } from '@/api/hooks/useRunnerImages'
import { effectiveRuntimeFamily, hostHwFlags, selectedRunnerKey, tierRank } from './hw-cascade.js'
import { EngineSettings } from './engine-settings.jsx'
import { prettyProfile } from './profile-names'
import {
  findManagedFlags,
  findSlotHardwareFlags,
  findModelFactFlags,
  findHostOwnedFlags,
  findPositionalArgs,
  findHalogenDenied,
  findHalogenMalformed,
  findHalogenOutOfRange,
  halogenValueProblem,
  echo,
  HALOGEN_MAX_VALUE_BYTES,
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
export function ProfileCard({ p, index, onEdit, onClone, onDelete, onExport, onOpen = null }) {
  if (p.quarantined) return <QuarantinedCard p={p} index={index} onDelete={onDelete} />;
  return <TunedProfileCard p={p} index={index} onEdit={onEdit} onClone={onClone} onDelete={onDelete} onExport={onExport} onOpen={onOpen} />;
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

// How many settings chips a card shows before it folds the rest into "+N".
const CARD_FACTS = 4;

// The settings a profile makes, as chips. A profile that sets no typed field
// says which kind of nothing that is: flags only, or the engine's defaults.
function FactChips({ p, backends, testid }) {
  const facts = tuneFacts(p, (key) => runnerTitleFor(key, backends));
  return (
    <div className="pf-gc-sets" data-testid={testid}>
      <span className="pf-gc-label mono">sets</span>
      <div className="pf-gc-chips">
        {facts.slice(0, CARD_FACTS).map(f => (
          <span key={f.key} className="pf-mchip mono" title={f.key}>
            <span className="fam">{f.label}</span>{f.value}
          </span>
        ))}
        {facts.length > CARD_FACTS && (
          <span className="pf-mchip more mono"
            title={facts.slice(CARD_FACTS).map(f => `${f.label} ${f.value}`).join(' · ')}>
            +{facts.length - CARD_FACTS}
          </span>
        )}
        {facts.length === 0 && <span className="pf-mchip mono">{emptyFactsLabel(p)}</span>}
      </div>
    </div>
  );
}

// The line every card opens with: its kind, then the runtime family.
function KindEyebrow({ kind, family, version }) {
  const tone = kind === KIND_SEED ? ' pf-seed' : kind === KIND_CUSTOM ? ' pf-custom' : ' pf-import';
  return (
    <span className={'pf-gc-eyebrow mono' + tone}
      title={kind === KIND_SEED ? 'Ships with hal0. Read-only: clone it to change it.'
        : kind === KIND_CUSTOM ? 'Made on this box.'
        : 'Not on this box yet. Import it from the catalog.'}>
      {kind === KIND_SEED && Icons.lock}{kind}{version ? ` · v${version}` : ''}{family ? ` · ${family}` : ''}
    </span>
  );
}

function TunedProfileCard({ p, index, onEdit, onClone, onDelete, onExport, onOpen = null }) {
  const systemInfoQuery = useSystemInfo();
  const backends = systemInfoQuery.data?.backends ?? {};
  const chips = runtimeChips(p, backends);
  const isSeed = !!p.seed;
  const usedBy = p.used_by || [];
  const inUse = usedBy.length;
  const metric = p.tps != null ? `${p.tps.toFixed(1)} tok/s`
    : p.rtf != null ? `${p.rtf.toFixed(2)}× rtf` : null;

  return (
    <div className="stk-lib-card pf-gcard" style={{ animationDelay: (index * 34) + 'ms' }}
      data-kind={isSeed ? KIND_SEED : KIND_CUSTOM} data-testid={`pf-card-${p.name}`}>
      <div className="pf-gc-top">
        <KindEyebrow kind={isSeed ? KIND_SEED : KIND_CUSTOM} family={p.runtime_family} />
        <div className="pf-card-meta">
          <RuntimeStateChip p={p} backends={backends} />
          {metric && <span className="mono pf-card-metric">{metric}</span>}
        </div>
      </div>
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
      </div>

      <FactChips p={p} backends={backends} testid={`pf-tune-${p.name}`} />

      <div className="stk-lib-f">
        {inUse
          ? <span className="stk-tag" title={'used by ' + usedBy.join(', ')}>used by {inUse}</span>
          : <span className="mono pf-card-unused">unused</span>}
        <span className="stk-spacer" />
        {isSeed ? (
          <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onClone(p)}
            title="Built-in profiles are read-only: clone a custom copy" data-testid={`pf-btn-editcopy-${p.name}`}>
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
          title={isSeed ? 'Built-in profiles cannot be deleted' : inUse ? 'In use — detach slots first' : 'Delete'}
          data-testid={`pf-btn-delete-${p.name}`}>{Icons.trash}</button>
        <button className="stk-icon-btn" style={{ width: 26, height: 26 }} onClick={() => onExport(p)}
          title="Export this profile as a .hal0profile.json" data-testid={`pf-btn-export-${p.name}`}>{Icons.download}</button>
        {onOpen && (
          <button type="button" className="pf-gc-open mono" onClick={() => onOpen(p)}
            aria-haspopup="dialog" aria-label={`settings for ${p.name}`}
            data-testid={`pf-btn-open-${p.name}`}>settings →</button>
        )}
      </div>
    </div>
  );
}

// A catalog row: a profile this box does not have yet. The action is Import,
// which opens the same dry-run dialog a file import does. A row the server
// would refuse (bad checksum, a flag a profile may not carry, a newer release
// needed) is still listed, with the reason where the button would be.
export function AddonCard({ a, index, onImport, onOpen = null }) {
  const systemInfoQuery = useSystemInfo();
  const backends = systemInfoQuery.data?.backends ?? {};
  const body = a.profile || {};
  // A runner this box lacks is dropped on import, so the card shows what
  // will land (Auto), not what the envelope asks for.
  const shown = a.runner_stripped ? { ...body, runner: null, runtime_family: a.runtime_family } : { ...body, runtime_family: a.runtime_family };
  const chips = runtimeChips(shown, backends);
  return (
    <div className="stk-lib-card pf-gcard" style={{ animationDelay: (index * 34) + 'ms' }}
      data-kind={KIND_IMPORT} data-testid={`pf-addon-${a.name}`}>
      <div className="pf-gc-top">
        <KindEyebrow kind={KIND_IMPORT} family={a.runtime_family} version={a.version} />
        <div className="pf-card-meta">
          {a.first_party && <span className="pf-state mono" title="Published by the hal0 project">first-party</span>}
          {a.installed && <span className="pf-state mono" title={`A profile named ${a.name} is already on this box`}
            data-testid={`pf-addon-installed-${a.name}`}>name in use</span>}
        </div>
      </div>
      <div className="stk-lib-h">
        <div className="stk-lib-id">
          <div className="pf-title-row">
            <span className="stk-lib-name">{a.name}</span>
            {a.profile && (
              <span className="pf-be-row">
                {chips.map(c => (
                  <span key={c.key} className="pf-be mono" style={c.hue ? { '--bk': c.hue } : null}>{c.label}</span>
                ))}
              </span>
            )}
          </div>
          <div className="stk-lib-intent">{a.title || a.description || prettyProfile(a.name)}</div>
          {a.summary && <div className="pf-runtime-line">{a.summary}</div>}
        </div>
      </div>

      {a.profile && <FactChips p={body} backends={backends} testid={`pf-tune-addon-${a.name}`} />}

      {a.blocked && (
        <div className="pf-gc-blocked mono" data-testid={`pf-addon-blocked-${a.name}`} title={a.blocked.code}>
          {Icons.alert}<span>Not offered: {a.blocked.message}</span>
        </div>
      )}

      <div className="stk-lib-f">
        {a.min_hal0_version
          ? <span className="mono pf-card-unused">hal0 {a.min_hal0_version}+</span>
          : <span className="mono pf-card-unused">catalog</span>}
        <span className="stk-spacer" />
        {!a.blocked && (
          <button className="pf-btn" onClick={() => onImport(a)} data-testid={`pf-btn-import-${a.name}`}
            title={a.installed ? 'Import under another name' : 'Import this profile'}>
            {Icons.download} {a.installed ? 'Import a copy' : 'Import'}
          </button>
        )}
        {onOpen && a.profile && (
          <button type="button" className="pf-gc-open mono" onClick={() => onOpen(a)}
            aria-haspopup="dialog" aria-label={`settings for ${a.name}`}
            data-testid={`pf-btn-open-addon-${a.name}`}>settings →</button>
        )}
      </div>
    </div>
  );
}

// ── Detail drawer ─────────────────────────────────────────────────────────────
//
// Read-only, and in the gallery's order: what the profile is for, what you can
// do with it, the flag string, the typed settings, then the profile as the
// table hal0 reads from profiles.toml.

function copyText(text, what) {
  const done = () => toast(`Copied ${what}`, 'ok');
  const failed = () => toast('Could not copy', 'err');
  try {
    navigator.clipboard.writeText(text).then(done, failed);
  } catch { failed(); }
}

export function ProfileDetail({ card, onClose, onEdit, onClone, onExport, onImport }) {
  const systemInfoQuery = useSystemInfo();
  const backends = systemInfoQuery.data?.backends ?? {};
  const p = card.profile;
  const a = card.addon;
  const facts = tuneFacts(p, (key) => runnerTitleFor(key, backends));
  const flags = tuneOf(p).flags || '';
  const toml = profileToml(card.name, p);
  const summary = a ? (a.summary || a.description || a.title) : intentOf(p);
  const usedBy = p.used_by || [];

  return (
    <Drawer open onClose={onClose} title={card.name} width={520}
      eyebrow={card.kind + (card.family ? ` · ${card.family}` : '')}>
      <div className="pf-detail" data-testid="pf-detail">
        <p className="pf-detail-summary">{summary}</p>

        <div className="pf-detail-actions">
          {card.kind === KIND_SEED && (
            <>
              <span className="pf-detail-note">Already on this box and read-only. Clone it to change it.</span>
              <button className="pf-btn primary" onClick={() => onClone(p)} data-testid="pf-detail-clone">{Icons.copy} Clone</button>
            </>
          )}
          {card.kind === KIND_CUSTOM && (
            <>
              <span className="pf-detail-note">
                {usedBy.length ? `Used by ${usedBy.join(', ')}.` : 'No slot uses it.'}
              </span>
              <button className="pf-btn" onClick={() => onExport(p)}>{Icons.download} Export</button>
              <button className="pf-btn" onClick={() => onClone(p)}>{Icons.copy} Clone</button>
              <button className="pf-btn primary" onClick={() => onEdit(p)} data-testid="pf-detail-edit">{Icons.edit} Edit</button>
            </>
          )}
          {card.kind === KIND_IMPORT && (a.blocked ? (
            <span className="pf-gc-blocked mono">{Icons.alert}<span>Not offered: {a.blocked.message}</span></span>
          ) : (
            <>
              <span className="pf-detail-note">
                {a.installed
                  ? `A profile named ${a.name} is already on this box. Import this one under another name.`
                  : 'Not on this box yet.'}
                {a.runner_stripped && ` Its runtime (${a.profile?.runner}) is not available here, so it imports with none pinned.`}
              </span>
              <button className="pf-btn primary" onClick={() => onImport(a)} data-testid="pf-detail-import">
                {Icons.download} {a.installed ? 'Import a copy' : 'Import'}
              </button>
            </>
          ))}
        </div>

        <div className="pf-detail-sec">
          <div className="pf-detail-h mono">
            <span>flags</span>
            {flags && <button className="pf-gc-open mono" onClick={() => copyText(flags, 'flags')}>copy</button>}
          </div>
          {flags
            ? <pre className="pf-detail-pre mono" data-testid="pf-detail-flags">{flags}</pre>
            : <div className="pf-detail-note">None. The engine's own defaults apply.</div>}
        </div>

        <div className="pf-detail-sec">
          <div className="pf-detail-h mono"><span>settings</span></div>
          {facts.length ? (
            <dl className="pf-kv" data-testid="pf-detail-facts">
              {facts.map(f => (
                <Fragment key={f.key}><dt>{f.label}</dt><dd>{f.value}</dd></Fragment>
              ))}
            </dl>
          ) : (
            <div className="pf-detail-note">No typed setting: {emptyFactsLabel(p)}.</div>
          )}
        </div>

        <div className="pf-detail-sec">
          <div className="pf-detail-h mono">
            <span>profiles.toml</span>
            <button className="pf-gc-open mono" onClick={() => copyText(toml, 'table')}>copy</button>
          </div>
          <pre className="pf-detail-pre mono" data-testid="pf-detail-toml">{toml}</pre>
        </div>

        {a && (
          <div className="pf-detail-sec">
            <div className="pf-detail-h mono"><span>catalog entry</span></div>
            <dl className="pf-kv">
              {a.version != null && <><dt>version</dt><dd>v{a.version}</dd></>}
              {a.min_hal0_version && <><dt>needs</dt><dd>hal0 {a.min_hal0_version} or newer</dd></>}
              {a.author && <><dt>author</dt><dd>{a.author}</dd></>}
              {a.checksum && <><dt>checksum</dt><dd className="trunc" title={a.checksum}>{a.checksum}</dd></>}
            </dl>
          </div>
        )}
      </div>
    </Drawer>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────

function Section({ title, count, hint, children }) {
  return (
    <div className="pf-section pf-section-below">
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

// A halogen-family runner's flags are HALOGEN_NAME=value tokens, not llama
// arguments. The engine block's rows come from `backends[runner].knobs` and the
// names a profile may not set from `.knob_denied` (hal0.runners.knobs, served on
// system-info), so the screen below reads both rather than carrying the
// server's sets. Null for any other runner.
export function halogenEngine(runner, backends) {
  if (!runner) return null;
  if (effectiveRuntimeFamily({ binary: runner, backends }) !== 'halogen') return null;
  const row = backends[runner];
  return { knobs: row.knobs || [], denied: row.knob_denied || [] };
}

// The inline mirror of the server's profiles.halogen_flag_malformed /
// _denied / _out_of_range (hal0.runners.knobs.screen_halogen_flags), in the
// server's order: the text is read before any name or value is judged.
export function halogenFlagsError(text, { knobs, denied }) {
  const quoteErr = tokenizeFlags(text).error;
  if (quoteErr) return quoteErr;
  const malformed = findHalogenMalformed(text);
  if (malformed.length) {
    const first = malformed[0];
    const rest = malformed.length > 1 ? ` (also: ${malformed.slice(1).map(echo).join(', ')})` : '';
    if (/^HALOGEN_[A-Z0-9_]+=$/.test(first)) {
      return `${first} needs a value — remove the token to leave it at the engine default.${rest}`;
    }
    const held = /^(HALOGEN_[A-Z0-9_]+)=([\s\S]+)$/.exec(first);
    const problem = held ? halogenValueProblem(held[2]) : null;
    if (problem === 'control') {
      return `${held[1]} holds a control character (a tab or a line break, say), which the slot's unit file cannot carry. Rewrite the value without it.${rest}`;
    }
    if (problem === 'long') {
      return `${held[1]} is longer than ${HALOGEN_MAX_VALUE_BYTES} bytes, which is all the slot's unit file can carry. Shorten the value.${rest}`;
    }
    return `“${first}” is not a HALOGEN_NAME=value setting — this runtime reads its tuning from HALOGEN_* variables, one NAME=value per token (quote a value that has spaces). Remove it or rewrite it.${rest}`;
  }
  const deniedNames = findHalogenDenied(text, knobs, denied);
  if (deniedNames.length) {
    const rest = deniedNames.length > 1 ? ` (also: ${deniedNames.slice(1).join(', ')})` : '';
    return `${deniedNames[0]} can't be set on a profile — hal0 sets it from the slot, the model, or this profile's own context, thinking, vision and speculation fields. Remove it.${rest}`;
  }
  const range = findHalogenOutOfRange(text, knobs);
  if (range.length) {
    const rest = range.length > 1 ? ` (also: ${range.slice(1).map((r) => r.name).join(', ')})` : '';
    return `${range[0].message}.${rest}`;
  }
  return null;
}

function validateForm(form, existing, halogen) {
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
  if (halogen) {
    // None of the llama screens below apply: HALOGEN_* tokens are not flags.
    const flagsErr = halogenFlagsError(flagsText, halogen);
    if (flagsErr) errs.flags = flagsErr;
    return errs;
  }
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
  // The "Advanced: flags" disclosure (halogen-family forms only); the engine
  // block above it carries the editing, so the text starts collapsed.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const f = useForm({
    deriveInitial,
    resetKey: `${mode}:${source?.name ?? ''}`,
    validate: (v) => validateForm(v, taken, halogenEngine(v.runner, backends)),
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
  // A halogen runner with engine knobs swaps the flags field for the engine
  // block + a collapsed "Advanced: flags" text; any other runner is unchanged.
  const engine = halogenEngine(form.runner, backends);
  const engineKnobs = engine && engine.knobs.length > 0 ? engine.knobs : null;
  const flagsErr = show('flags') ? errs.flags : null;
  // A rejected flags text opens the field that holds it.
  const advancedShown = advancedOpen || !!flagsErr;

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

        <FormRow label="Speculation" sub={engine ? 'speculative decoding (drafter or MTP heads)' : 'speculative decoding (drafter, MTP heads or n-gram)'}>
          <select className="pf-input mono" value={form.speculation}
            onChange={e => set('speculation', e.target.value)} data-testid="pf-select-speculation">
            <option value="auto">Auto — use the model's drafter or MTP heads when it has them</option>
            <option value="off">Off — never draft</option>
            {/* The halogen engine has no n-gram drafter, and the server refuses
                the value on a halogen profile. A stored choice stays listed,
                marked, so the select never shows a value it does not hold. */}
            {(!engine || form.speculation === 'ngram') && (
              <option value="ngram">
                {engine
                  ? 'N-gram — not available on this runtime, pick Auto or Off'
                  : 'N-gram — model-free speculation, no drafter needed'}
              </option>
            )}
          </select>
        </FormRow>

        {engineKnobs && (
          <FormRow label="Engine" sub="halogen settings · written as HALOGEN_* flags">
            <EngineSettings knobs={engineKnobs} flags={form.flags || ''}
              onChange={(next) => set('flags', next)} error={flagsErr} />
          </FormRow>
        )}

        {engineKnobs && (
          <button type="button" className="pf-disc" aria-expanded={advancedShown}
            onClick={() => setAdvancedOpen(o => !o)} data-testid="pf-advanced-toggle">
            <span className="pf-disc-caret">{advancedShown ? '▾' : '▸'}</span>
            Advanced: flags
          </button>
        )}
        {(!engineKnobs || advancedShown) && (
          <FormRow label="Flags"
            sub={engine ? 'HALOGEN_NAME=value, one per token — the controls above write here' : 'appended to the run command'}
            error={engineKnobs ? null : flagsErr}>
            <textarea className={'pf-input mono pf-textarea' + (flagsErr ? ' err' : '')}
              value={form.flags || ''}
              onChange={e => set('flags', e.target.value)} onBlur={() => touch('flags')}
              rows={3} placeholder={engine ? 'HALOGEN_KV_SLOTS=4' : '-fa on -b 2048 -ub 512'}
              aria-invalid={!!flagsErr} data-testid="pf-input-flags" />
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
export function ImportModal({ existing, onClose, onImported, envelope = null }) {
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
      initialEnvelope={envelope}
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

// Filter pills: one row per facet, AND across rows, OR within a row. Counts
// are over every card, so a pill always says how many it would show alone.
function FilterBar({ facets, selected, total, shown, onToggle, onClear, children }) {
  const active = facets.some(f => (selected[f.key] || []).length > 0);
  return (
    <div className="pf-filterbar" data-testid="pf-filters">
      {facets.map(facet => {
        const picked = selected[facet.key] || [];
        return (
          <div className="pf-frow" key={facet.key}>
            <span className="pf-gc-label mono">{facet.label}</span>
            <div className="pf-fbar">
              <button type="button" className={'pf-fpill mono' + (picked.length === 0 ? ' on' : '')}
                aria-pressed={picked.length === 0} onClick={() => onClear(facet.key)}
                data-testid={`pf-filter-${facet.key}-all`}>
                all<span className="n num">{total}</span>
              </button>
              {facet.options.map(value => (
                <button type="button" key={value} className={'pf-fpill mono' + (picked.includes(value) ? ' on' : '')}
                  aria-pressed={picked.includes(value)} onClick={() => onToggle(facet.key, value)}
                  data-testid={`pf-filter-${facet.key}-${value.replace(/\s+/g, '-')}`}>
                  {value}<span className="n num">{facet.counts.get(value)}</span>
                </button>
              ))}
            </div>
          </div>
        );
      })}
      <div className="pf-filter-foot">
        <span className="mono pf-filter-readout" data-testid="pf-filter-readout">{shown} of {total} profiles</span>
        {active && (
          <button type="button" className="pf-gc-open mono" onClick={() => onClear(null)} data-testid="pf-filter-clear">clear</button>
        )}
        <span className="pf-grow" />
        {children}
      </div>
    </div>
  );
}

function hostOf(url) {
  try { return new URL(url).host; } catch { return url || ''; }
}

// Where the import rows came from, in a sentence, with the way to re-check.
// The catalog failing is never the page failing: the profiles on the box are
// already on screen.
export function catalogNoteText(query) {
  if (query.isLoading) return 'Checking the import catalog…';
  if (query.isError) return `Import catalog unavailable: ${query.error?.message || 'the request failed'}.`;
  const c = query.data;
  if (!c || !Array.isArray(c.addons)) return 'Import catalog unavailable.';
  const n = c.addons.length;
  const rows = `${n} profile${n === 1 ? '' : 's'} to import`;
  if (c.source === 'remote') return `${rows}, from ${hostOf(c.url)}.`;
  if (c.error) return `${rows}, from the set this release ships. ${hostOf(c.url)} could not be read: ${c.error}.`;
  return `${rows}, from the set this release ships. The online catalog is switched off.`;
}

function CatalogNote({ query, refresh }) {
  const canRefresh = !query.isLoading && (query.isError || query.data?.url);
  return (
    <span className="pf-catalog-note mono" data-testid="pf-catalog-note">
      {catalogNoteText(query)}
      {canRefresh && (
        <button type="button" className="pf-gc-open mono" disabled={refresh.isPending}
          onClick={() => (query.isError ? query.refetch() : refresh.mutate(undefined, {
            onError: (e) => toast(e?.message || 'Could not check the catalog', 'err'),
          }))}
          data-testid="pf-catalog-refresh">
          {refresh.isPending ? 'checking…' : 'check again'}
        </button>
      )}
    </span>
  );
}

// Stable identity for "no catalog rows", so the card memo holds.
const EMPTY = [];

function ProfilesView() {
  const query = useProfiles();
  const addonsQuery = useProfileAddons();
  const addonsRefresh = useProfileAddonsRefresh();
  const allProfiles = query.data ?? [];
  const addons = Array.isArray(addonsQuery.data?.addons) ? addonsQuery.data.addons : EMPTY;
  const exportMut = useProfileExport();

  const [drawer, setDrawer] = useState(null);   // {mode, source}
  const [confirm, setConfirm] = useState(null);
  const [importing, setImporting] = useState(null);   // {envelope} | null
  const [selected, setSelected] = useState({});
  const [detail, setDetail] = useState(null);   // card id

  const quarantined = allProfiles.filter(p => p.quarantined);
  const profiles = allProfiles.filter(p => !p.quarantined);
  const seeds = profiles.filter(p => p.seed);
  const custom = profiles.filter(p => !p.seed);
  const inUseCount = profiles.filter(p => (p.used_by || []).length).length;

  const cards = useMemo(() => galleryCards(allProfiles, addons), [allProfiles, addons]);
  const facets = useMemo(() => facetOptions(cards), [cards]);
  const shown = cards.filter(c => matchesFilters(c, selected));
  // Looked up by id on every render so the drawer follows a refetch, and
  // closes by itself when its profile goes away.
  const detailCard = detail ? cards.find(c => c.id === detail) : null;
  const existingNames = profiles.map(p => p.name);

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

  // Every action that opens a form closes the detail drawer first: two
  // drawers on one edge would stack.
  const act = (fn) => (arg) => { setDetail(null); fn(arg); };
  const onEdit = act(pp => setDrawer({ mode: 'edit', source: pp }));
  const onClone = act(pp => setDrawer({ mode: 'clone', source: pp }));
  const onImportAddon = act(a => setImporting({ envelope: a.envelope }));

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
        <ProfilesHeader count={profiles.length} onNew={() => setDrawer({ mode: 'create' })} onImport={() => setImporting({ envelope: null })} />
        <div className="pf-summary">
          <Stat value={profiles.length} label="on this box" />
          <span className="pf-stat-div" />
          <Stat value={seeds.length} label="built in" />
          <Stat value={custom.length} label="custom" accent="var(--accent)" />
          <span className="pf-stat-div" />
          <Stat value={`${inUseCount}/${profiles.length}`} label="bound to slots" accent="var(--ok)" />
          <span className="pf-stat-div" />
          <Stat value={Array.isArray(addonsQuery.data?.addons) ? addons.length : '–'} label="to import" />
        </div>
      </div>

      <FilterBar facets={facets} selected={selected} total={cards.length} shown={shown.length}
        onToggle={(key, value) => setSelected(sel => toggleFilter(sel, key, value))}
        onClear={(key) => setSelected(sel => (key ? { ...sel, [key]: [] } : {}))}>
        <CatalogNote query={addonsQuery} refresh={addonsRefresh} />
      </FilterBar>

      {cards.length === 0 && quarantined.length === 0 ? (
        <div className="empty mono">No profiles configured.</div>
      ) : shown.length === 0 ? (
        <div className="empty mono" data-testid="pf-filter-empty">No profile matches these filters.</div>
      ) : (
        <div className="pf-grid pf-gallery" data-testid="pf-gallery">
          {shown.map((c, i) => (c.addon ? (
            <AddonCard key={c.id} a={c.addon} index={i} onImport={onImportAddon} onOpen={() => setDetail(c.id)} />
          ) : (
            <ProfileCard key={c.id} p={c.profile} index={i}
              onEdit={onEdit} onClone={onClone}
              onDelete={pp => setConfirm(pp)}
              onExport={onExport}
              onOpen={() => setDetail(c.id)} />
          )))}
        </div>
      )}

      {quarantined.length > 0 && (
        <Section title="Needs attention" count={quarantined.length}
          hint="invalid as stored · delete, or recreate the name to replace">
          {quarantined.map((p, i) => (
            <ProfileCard key={p.name} p={p} index={i} onDelete={pp => setConfirm(pp)} />
          ))}
        </Section>
      )}

      {detailCard && (
        <ProfileDetail card={detailCard} onClose={() => setDetail(null)}
          onEdit={onEdit} onClone={onClone} onExport={onExport} onImport={onImportAddon} />
      )}
      {drawer && (
        <ProfileDrawer
          mode={drawer.mode}
          source={drawer.source}
          existing={existingNames}
          onClose={() => setDrawer(null)}
          onSaved={() => setDrawer(null)}
        />
      )}
      {confirm && (
        <DeleteConfirm p={confirm} onCancel={() => setConfirm(null)} onConfirmed={() => setConfirm(null)} />
      )}
      {importing && (
        <ImportModal
          existing={existingNames}
          envelope={importing.envelope}
          onClose={() => setImporting(null)}
          onImported={() => setImporting(null)}
        />
      )}
    </div>
  );
}

Object.assign(window, { ProfilesView });
export { ProfilesView };
