// spec-state.js — what the dashboard shows about speculative decoding
// (ADR-0031 §5): the Slots-row bolt, the slot drawer's Speculation line and
// the catalog drafter chip. Pure and dependency-light so vitest covers it.
import { isMtpModel } from './model-sort.js'

const VARIANT_LABELS = {
  lilicorr: 'LiLiCorr',
  dflash2: 'DFlash2',
  dspark: 'DSpark',
  dflash: 'DFlash',
  eagle3: 'EAGLE3',
  mtp: 'MTP sidecar',
  simple: 'draft model',
}

/** Copy for a drafter variant; unknown variants pass through. */
export function draftVariantLabel(variant) {
  if (!variant) return 'drafter'
  return VARIANT_LABELS[variant] || String(variant)
}

/** True when the model row has a paired drafter file. */
export function isDrafterModel(m) {
  return typeof m?.draft === 'string' && m.draft !== ''
}

/** Value for `<ModalityTag draft=…>`: the variant (or 'draft'), null without a drafter. */
export function draftTagValue(m) {
  return isDrafterModel(m) ? m.draftVariant || 'draft' : null
}

// What is speculating, in words: a drafter variant, MTP heads, or n-gram.
function specLabel(s) {
  if (s?.mode === 'ngram') return 'n-gram'
  const variant = s?.variant || s?.kind
  if (variant) return draftVariantLabel(variant)
  if (s?.mode === 'mtp') return 'MTP heads'
  return 'drafter'
}

function pct(n) {
  return typeof n === 'number' ? `${Math.round(n * 100)}%` : null
}

/**
 * Bolt for a Slots-list row: `{state: 'lit'|'off'|'warn', title}` or null.
 * @param spec  the slot's `metrics.spec`
 * @param model the slot's model row (to know whether it carries a drafter/MTP heads)
 */
export function slotBolt(spec, model) {
  if (!spec) return null
  if (spec.unavailable_reason) {
    return {
      state: 'warn',
      title: `drafter unavailable on this runner: ${spec.unavailable_reason}`,
    }
  }
  if (spec.mode === 'ngram') return { state: 'lit', title: 'drafting · n-gram' }
  if (spec.active) {
    const rate = pct(spec.acceptance)
    return {
      state: 'lit',
      title: `drafting · ${specLabel(spec)}${rate ? ` · ${rate} accepted` : ''}`,
    }
  }
  if (isDrafterModel(model) || (model && isMtpModel(model))) {
    return { state: 'off', title: 'drafter paired · speculation off' }
  }
  return null
}

/**
 * Slot drawer line from `/resolved` `speculation` plus the live counters.
 * Null until /resolved has answered.
 */
export function slotSpecLine(resolved, live) {
  if (!resolved) return null
  if (resolved.unavailable_reason) {
    return `Speculation: drafter unavailable — ${resolved.unavailable_reason}`
  }
  if (!resolved.mode || resolved.mode === 'none') return 'Speculation: off'
  let line = `Speculation: ${specLabel(resolved)}`
  const rate = pct(live?.acceptance)
  if (rate) line += ` · ${rate} accepted`
  if (typeof live?.mean_accepted_len === 'number') {
    line += ` · ${live.mean_accepted_len.toFixed(1)} tok/step`
  }
  return line
}
