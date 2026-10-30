// tune-state.js — pure helpers for per-slot tune overrides. A slot's sparse
// `tune` table overrides its profile; `tune_effective` is the resolved result
// the slots snapshot carries. Nothing here touches the DOM or the network.
import { ctxLabel, thinkingLabel, thinkingToForm } from './profiles.jsx'

/** The six override keys, in display order. Mirrors `SlotTuneConfig`. */
export const TUNE_KEYS = [
  'ctx_size',
  'enable_thinking',
  'reasoning_effort',
  'reasoning_budget',
  'vision',
  'speculation',
]

// Keys whose change needs a slot restart; a thinking-only change applies to
// the next request (chat_template_kwargs), so it does not.
const RESTART_KEYS = new Set(['ctx_size', 'reasoning_effort', 'reasoning_budget', 'vision', 'speculation'])

/** Number of keys the slot overrides (absent key = the profile's value). */
export function overrideCount(tune) {
  if (!tune || typeof tune !== 'object') return 0
  return TUNE_KEYS.filter((k) => tune[k] !== undefined && tune[k] !== null).length
}

/** `{title}` when effective thinking is on, else null (the glyph stays dark). */
export function thinkingMark(tuneEffective) {
  if (!tuneEffective || tuneEffective.enable_thinking !== true) return null
  let title = 'thinking on'
  const effort = tuneEffective.reasoning_effort
  if (effort && effort !== 'default') title += ` · effort ${effort}`
  const budget = tuneEffective.reasoning_budget
  if (typeof budget === 'number' && budget !== -1) title += ` · budget ${budget}`
  return { title }
}

/** Eye state: null without a vision-capable model, 'off' when the slot turns it off. */
export function eyeState(model, tuneEffective) {
  const caps = Array.isArray(model?.capabilities) ? model.capabilities : []
  if (!caps.includes('vision')) return null
  return tuneEffective?.vision === 'off' ? 'off' : 'lit'
}

/** Profile value for a tune row, as the pill label next to the override pills. */
export function profileValueLabel(key, profile) {
  const p = profile || {}
  switch (key) {
    case 'ctx_size':
      return ctxLabel(p.ctx_size)
    case 'enable_thinking':
      return thinkingLabel(thinkingToForm(p.enable_thinking))
    case 'reasoning_effort':
      return p.reasoning_effort || 'default'
    case 'reasoning_budget':
      return p.reasoning_budget == null || p.reasoning_budget === -1 ? 'unlimited' : String(p.reasoning_budget)
    case 'vision':
    case 'speculation':
      return p[key] || 'auto'
    default:
      return ''
  }
}

/** True when any changed key needs the slot restarted to take effect. */
export function restartNeeded(changedKeys) {
  return (changedKeys || []).some((k) => RESTART_KEYS.has(k))
}

// ── staged form helpers ─────────────────────────────────────────────────────
// The drawer stages a tune object where the numeric rows may hold text while
// the operator types. These turn it into the wire shape and compare it with
// the persisted table.

const CTX_MIN = 128;
const CTX_MAX = 16777216;
const EFFORTS = ['default', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'];

const isSet = (v) => v !== undefined && v !== null && v !== '';

/** Inline error for one staged key, or null. Mirrors `SlotTuneConfig`. */
export function tuneKeyError(key, v) {
  if (!isSet(v)) return null;
  const raw = String(v).trim();
  if (key === 'ctx_size') {
    if (!/^\d+$/.test(raw)) return 'Must be a whole number of tokens';
    const n = Number(raw);
    if (n < CTX_MIN || n > CTX_MAX) return 'Must be between 128 and 16,777,216';
  }
  if (key === 'reasoning_budget' && !/^-?\d+$/.test(raw)) return 'Must be a whole number (-1 = unlimited, 0 = none)';
  if (key === 'reasoning_budget' && Number(raw) < -1) return 'Must be -1 or more';
  if (key === 'reasoning_effort' && !EFFORTS.includes(raw)) return 'Unknown effort';
  return null;
}

/**
 * `{key: message}` for every staged key that would be refused.
 * @returns {Record<string, string>}
 */
export function tuneErrors(tune) {
  /** @type {Record<string, string>} */
  const out = {};
  for (const k of TUNE_KEYS) {
    const e = tuneKeyError(k, tune?.[k]);
    if (e) out[k] = e;
  }
  return out;
}

/** The staged overrides as wire values (numbers parsed, unset keys dropped). */
export function tuneBody(tune) {
  const out = {};
  for (const k of TUNE_KEYS) {
    const v = tune?.[k];
    if (!isSet(v)) continue;
    out[k] = k === 'ctx_size' || k === 'reasoning_budget' ? Number(v) : v;
  }
  return out;
}

/** Keys whose staged value differs from the persisted table. */
export function changedTuneKeys(baseline, staged) {
  const a = tuneBody(baseline);
  const b = tuneBody(staged);
  return TUNE_KEYS.filter((k) => a[k] !== b[k]);
}

/**
 * Value for `tune` in the PUT body, or undefined when nothing changed. Clearing
 * every override sends null (drops the table); otherwise the full staged table,
 * with an explicit null for each key that was set and no longer is, so the
 * write is right whether the server replaces or merges the table.
 */
export function tuneWire(baseline, staged) {
  if (changedTuneKeys(baseline, staged).length === 0) return undefined;
  const next = tuneBody(staged);
  if (Object.keys(next).length === 0) return null;
  const prev = tuneBody(baseline);
  const out = { ...next };
  for (const k of TUNE_KEYS) if (k in prev && !(k in next)) out[k] = null;
  return out;
}
