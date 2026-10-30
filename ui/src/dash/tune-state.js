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
