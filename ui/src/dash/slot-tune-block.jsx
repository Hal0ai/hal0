// slot-tune-block.jsx — the slot drawer's Tune block: six per-slot overrides of
// the profile (context, thinking, effort, budget, vision, speculation). Each row
// is a segmented control whose first pill is `Profile · <value>` (what the
// profile gives, live) followed by the explicit choices; an absent key means
// "the profile's value". The block is controlled: `value` is the staged tune
// object (`{}` = no overrides) and `onChange` receives the next one. The parent
// owns Save, validation of the numbers (tuneErrors) and the profile drawer.
import React from 'react'
import { TUNE_KEYS, effectiveProfileName, overrideCount, profileValueLabel, tuneErrors } from './tune-state.js'

const ROWS = {
  ctx_size: { label: 'Context', numeric: true, placeholder: 'tokens' },
  enable_thinking: {
    label: 'Thinking',
    options: [
      ['on', true, 'On'],
      ['off', false, 'Off'],
    ],
  },
  reasoning_effort: {
    label: 'Effort',
    options: ['minimal', 'low', 'medium', 'high', 'xhigh', 'max'].map((v) => [v, v, v]),
  },
  reasoning_budget: { label: 'Budget', numeric: true, placeholder: '0 = none' },
  vision: {
    label: 'Vision',
    options: [
      ['auto', 'auto', 'Auto'],
      ['off', 'off', 'Off'],
    ],
  },
  speculation: {
    label: 'Speculation',
    options: [
      ['auto', 'auto', 'Auto'],
      ['off', 'off', 'Off'],
      ['ngram', 'ngram', 'N-gram'],
    ],
  },
}

// Restart note per row, shown in the footer.
const RESTART_NOTE =
  'Context, effort, budget, vision and speculation restart the slot on save; thinking applies to new requests.'

/** Reason a row cannot take an override, or null. */
function disabledReason(key, { model, tuneEffective }) {
  if (key === 'vision' && !model?.mmproj) return 'This model has no vision projector (mmproj).'
  if (key === 'reasoning_effort' && tuneEffective?.reasoning_effort_unavailable) {
    // The server's own reason text (runner build too old, runner unknown, ...).
    return String(tuneEffective.reasoning_effort_unavailable)
  }
  if (key === 'enable_thinking' && tuneEffective?.sources?.enable_thinking === 'profile-flags') {
    return "The profile's flags pin thinking with --reasoning; edit the profile to change it."
  }
  return null
}

function TuneRow({ k, tune, profile, tuneEffective, reason, error, disabled, onChange }) {
  const row = ROWS[k]
  const has = tune[k] !== undefined && tune[k] !== null && tune[k] !== ''
  const set = (v) => onChange({ ...tune, [k]: v })
  const clear = () => {
    const next = { ...tune }
    delete next[k]
    onChange(next)
  }
  const off = disabled || !!reason
  return (
    <div className={'tune-row' + (has ? ' overridden' : '')} data-testid={`tune-row-${k}`}>
      <div className="tune-lbl">
        {has && <span className="tune-dot" data-testid={`tune-dot-${k}`} title="overrides the profile" />}
        {row.label}
      </div>
      <div className="tune-ctl">
        <div className="tune-segs" role="group" aria-label={row.label}>
          <button
            type="button"
            className={'tune-seg profile' + (has ? '' : ' on')}
            aria-pressed={!has}
            // Always reachable while the row holds an override, so a stale one
            // (model lost its mmproj, profile now pins thinking) can be removed
            // without Reset dropping the other rows.
            disabled={disabled || (!!reason && !has)}
            data-testid={`tune-${k}-profile`}
            onClick={clear}
          >
            Profile · {profileValueLabel(k, profile, tuneEffective)}
          </button>
          {row.options?.map(([id, val, text]) => (
            <button
              key={id}
              type="button"
              className={'tune-seg' + (has && tune[k] === val ? ' on' : '')}
              aria-pressed={has && tune[k] === val}
              disabled={off}
              data-testid={`tune-${k}-${id}`}
              onClick={() => set(val)}
            >
              {text}
            </button>
          ))}
          {row.numeric && (
            <input
              className={'pf-input mono tune-num' + (error ? ' err' : '')}
              inputMode="numeric"
              value={has ? String(tune[k]) : ''}
              placeholder={row.placeholder}
              disabled={off}
              aria-invalid={!!error}
              data-testid={`tune-${k}-input`}
              onChange={(e) => (e.target.value.trim() === '' ? clear() : set(e.target.value))}
            />
          )}
        </div>
        {reason && (
          <div className="hint" data-testid={`tune-${k}-reason`}>
            {reason}
          </div>
        )}
        {error && !reason && (
          <div className="hint err" data-testid={`tune-${k}-error`}>
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

/** Whether the Tune block applies to this slot at all. */
export function tuneApplies(slot) {
  return !!slot && slot.type === 'llm' && slot.device !== 'npu'
}

export function TuneBlock({
  slot,
  profile,
  model,
  value,
  onChange,
  onSaveAsProfile,
  canSaveAsProfile = true,
  onReset,
  disabled,
}) {
  if (!tuneApplies(slot)) return null
  const tune = value || {}
  const errors = tuneErrors(tune)
  const n = overrideCount(tune)
  const tuneEffective = slot.tune_effective
  return (
    <div className="form-row tune-block" data-testid="slot-tune-block">
      <div className="form-lbl">
        <span>Tune</span>
      </div>
      <div className="form-ctl">
        {TUNE_KEYS.map((k) => (
          <TuneRow
            key={k}
            k={k}
            tune={tune}
            profile={profile}
            tuneEffective={tuneEffective}
            reason={disabledReason(k, { model, tuneEffective })}
            error={errors[k]}
            disabled={disabled}
            onChange={onChange}
          />
        ))}
        <div className="tune-foot">
          <span className="hint" data-testid="tune-summary">
            {n > 0
              ? `${n} override${n === 1 ? '' : 's'} on profile ${effectiveProfileName(slot)}`
              : 'no overrides · running the profile as-is'}
          </span>
          <span className="pf-grow" />
          <button
            type="button"
            className="btn ghost sm"
            data-testid="tune-save-as-profile"
            disabled={disabled || n === 0 || !canSaveAsProfile}
            title={canSaveAsProfile ? undefined : 'The profile list has not loaded yet'}
            onClick={() => onSaveAsProfile()}
          >
            Save as profile…
          </button>
          <button
            type="button"
            className="btn ghost sm"
            data-testid="tune-reset"
            disabled={disabled || n === 0}
            onClick={() => onReset()}
          >
            Reset to profile
          </button>
        </div>
        <div className="hint">{RESTART_NOTE}</div>
      </div>
    </div>
  )
}
