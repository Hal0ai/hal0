// engine-settings.jsx — the profile drawer's engine block for a halogen-family
// runner. The halogen engine is configured through HALOGEN_NAME=value
// variables, which a profile keeps in its `flags` text (ADR-0029: the profile
// owns the tune); this block renders one typed control per curated engine knob
// (`backends[<runner>].knobs` on /api/system-info) and writes the choice back
// into that text with setEnvToken, so the controls and the free-text "Advanced"
// field edit the same string. The block is controlled: `flags` is the text and
// `onChange` receives the next one. The parent owns validation and Save.
import React from 'react'
import { envPairs, setEnvToken } from './flags-tune.js'

/** The value `flags` sets for `env`, or null: the last token wins, as on the server. */
function valueOf(flags, env) {
  let v = null
  for (const p of envPairs(flags)) if (p.name === env) v = p.value
  return v === '' ? null : v
}

function NumberControl({ knob, value, disabled, onSet, describedBy }) {
  return (
    <input
      type="number"
      inputMode="numeric"
      step={1}
      className="pf-input mono eng-num"
      min={knob.minimum ?? undefined}
      max={knob.maximum ?? undefined}
      value={value ?? ''}
      placeholder={knob.default ?? 'engine default'}
      disabled={disabled}
      aria-label={knob.label}
      aria-describedby={describedBy}
      data-testid={`engine-input-${knob.env}`}
      onChange={(e) => onSet(e.target.value.trim() === '' ? null : e.target.value)}
    />
  )
}

function EnumControl({ knob, value, disabled, onSet, describedBy }) {
  const effective = value ?? knob.default
  return (
    <div className="tune-segs" role="group" aria-label={knob.label} aria-describedby={describedBy}>
      {knob.choices.map((choice) => (
        <button
          key={choice}
          type="button"
          className={'tune-seg' + (effective === choice ? ' on' : '')}
          aria-pressed={effective === choice}
          disabled={disabled}
          title={choice === knob.default ? 'engine default' : undefined}
          data-testid={`engine-opt-${knob.env}-${choice}`}
          onClick={() => onSet(choice)}
        >
          {choice}
        </button>
      ))}
    </div>
  )
}

function BoolControl({ knob, value, disabled, onSet, describedBy }) {
  const on = (value ?? knob.default) === '1'
  return (
    <button
      type="button"
      role="switch"
      aria-checked={on}
      aria-label={knob.label}
      aria-describedby={describedBy}
      className={'pf-switch' + (on ? ' on' : '')}
      disabled={disabled}
      data-testid={`engine-switch-${knob.env}`}
      onClick={() => onSet(on ? '0' : '1')}
    >
      <span className="pf-switch-knob" />
      <span className="pf-switch-lbl mono">{on ? 'On' : 'Off'}</span>
    </button>
  )
}

const CONTROLS = { int: NumberControl, enum: EnumControl, bool: BoolControl }

function KnobRow({ knob, flags, readOnly, onSet }) {
  const Control = CONTROLS[knob.kind]
  if (!Control) return null
  const value = valueOf(flags, knob.env)
  // The help line names the control it explains: one id per knob, so a screen
  // reader announces it with the control rather than as stray text.
  const helpId = knob.help ? `engine-help-${knob.env}` : undefined
  return (
    <div className={'eng-row' + (value !== null ? ' set' : '')} data-testid={`engine-knob-${knob.env}`}>
      <div className="eng-head">
        <span className="eng-lbl">{knob.label}</span>
        {knob.memory && (
          <span
            className="pf-state mono eng-mem"
            title="Counted in the slot's memory estimate: changing it changes how much memory the slot needs."
            data-testid={`engine-memory-${knob.env}`}
          >
            memory
          </span>
        )}
        {value !== null && !readOnly && (
          <button
            type="button"
            className="eng-reset mono"
            aria-label={`Reset ${knob.label}`}
            data-testid={`engine-reset-${knob.env}`}
            onClick={() => onSet(knob.env, null)}
          >
            reset
          </button>
        )}
      </div>
      <div className="eng-ctl">
        <Control knob={knob} value={value} disabled={readOnly} describedBy={helpId} onSet={(v) => onSet(knob.env, v)} />
      </div>
      {knob.help && (
        <div className="eng-help" id={helpId}>
          {knob.help}
        </div>
      )}
    </div>
  )
}

/**
 * @param {{ knobs?: Array<object>, flags?: string, onChange: (flags: string) => void,
 *           readOnly?: boolean, error?: string | null }} props
 *   `error` is the screen's verdict on `flags`, shown under the controls so a
 *   bad value is named where the operator is looking (the free-text field is
 *   collapsed).
 */
export function EngineSettings({ knobs, flags = '', onChange, readOnly = false, error = null }) {
  if (!Array.isArray(knobs) || knobs.length === 0) return null
  const set = (env, value) => {
    if (!readOnly) onChange(setEnvToken(flags, env, value))
  }
  return (
    <div className="eng" data-testid="pf-engine-settings">
      {knobs.map((knob) => (
        <KnobRow key={knob.env} knob={knob} flags={flags} readOnly={readOnly} onSet={set} />
      ))}
      {error && (
        <div className="pf-msg err mono eng-err" role="alert" data-testid="pf-engine-error">
          {error}
        </div>
      )}
    </div>
  )
}
