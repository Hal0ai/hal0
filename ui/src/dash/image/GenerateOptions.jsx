// "More options" on the Generate tab: negative prompt, Steps (1-100), CFG (0-20, step 0.5), Seed
// (Random, or locked to a number, with a dice) and Images (1-4). The panel owns the values (the
// numeric ones as the text typed, so a half-typed "7." survives) and clamps them on blur and when
// it builds the request; this draws them.
import { useId } from 'react'
import './image-generate.css'
import { CFG_RANGE, N_RANGE, STEPS_RANGE } from './generate-pure'

const DiceIcon = () => (
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.4" aria-hidden="true">
    <rect x="2" y="2" width="12" height="12" rx="2.5" />
    <circle cx="5.5" cy="5.5" r="0.9" fill="currentColor" stroke="none" />
    <circle cx="10.5" cy="5.5" r="0.9" fill="currentColor" stroke="none" />
    <circle cx="8" cy="8" r="0.9" fill="currentColor" stroke="none" />
    <circle cx="5.5" cy="10.5" r="0.9" fill="currentColor" stroke="none" />
    <circle cx="10.5" cy="10.5" r="0.9" fill="currentColor" stroke="none" />
  </svg>
)

/**
 * @param {object} props
 * @param {{ negative: string, steps: string, cfg: string, n: string, seed: string, seedLocked: boolean }} props.values
 * @param {{
 *   onNegative: (v: string) => void,
 *   onSteps: (v: string) => void, onStepsBlur: () => void,
 *   onCfg: (v: string) => void, onCfgBlur: () => void,
 *   onN: (v: string) => void, onNBlur: () => void,
 *   onSeed: (v: string) => void, onSeedBlur: () => void,
 *   onSeedMode: (locked: boolean) => void, onRoll: () => void,
 * }} props.on
 */
export function GenerateOptions({ values, on }) {
  const id = useId()
  return (
    <div className="gen-options" data-testid="gen-options">
      <div className="gen-field wide">
        <label className="gen-label" htmlFor={`${id}-neg`}>
          Negative prompt
        </label>
        <textarea
          id={`${id}-neg`}
          className="input gen-neg"
          data-testid="gen-negative"
          rows={2}
          placeholder="What to keep out of the image"
          value={values.negative}
          onChange={(e) => on.onNegative(e.target.value)}
        />
      </div>

      <div className="gen-field">
        <label className="gen-label" htmlFor={`${id}-steps`}>
          Steps <span className="hint">{STEPS_RANGE.min}-{STEPS_RANGE.max}</span>
        </label>
        <input
          id={`${id}-steps`}
          className="input mono"
          data-testid="gen-steps"
          type="number"
          inputMode="numeric"
          min={STEPS_RANGE.min}
          max={STEPS_RANGE.max}
          step={1}
          placeholder="model default"
          value={values.steps}
          onChange={(e) => on.onSteps(e.target.value)}
          onBlur={on.onStepsBlur}
        />
      </div>

      <div className="gen-field">
        <label className="gen-label" htmlFor={`${id}-cfg`}>
          CFG <span className="hint">{CFG_RANGE.min}-{CFG_RANGE.max}</span>
        </label>
        <input
          id={`${id}-cfg`}
          className="input mono"
          data-testid="gen-cfg"
          type="number"
          inputMode="decimal"
          min={CFG_RANGE.min}
          max={CFG_RANGE.max}
          step={CFG_RANGE.step}
          placeholder="model default"
          value={values.cfg}
          onChange={(e) => on.onCfg(e.target.value)}
          onBlur={on.onCfgBlur}
        />
      </div>

      <div className="gen-field">
        <label className="gen-label" htmlFor={`${id}-n`}>
          Images <span className="hint">{N_RANGE.min}-{N_RANGE.max}</span>
        </label>
        <input
          id={`${id}-n`}
          className="input mono"
          data-testid="gen-n"
          type="number"
          inputMode="numeric"
          min={N_RANGE.min}
          max={N_RANGE.max}
          step={1}
          value={values.n}
          onChange={(e) => on.onN(e.target.value)}
          onBlur={on.onNBlur}
        />
      </div>

      <div className="gen-field">
        <label className="gen-label" htmlFor={`${id}-seed`}>
          Seed
        </label>
        <div className="gen-seed" data-testid="gen-seed-mode" data-locked={values.seedLocked ? 'true' : 'false'}>
          <div className="gen-seg" role="group" aria-label="Seed mode">
            <button
              type="button"
              className="gen-chip"
              data-testid="gen-seed-random"
              aria-pressed={!values.seedLocked}
              onClick={() => on.onSeedMode(false)}
            >
              Random
            </button>
            <button
              type="button"
              className="gen-chip"
              data-testid="gen-seed-locked"
              aria-pressed={values.seedLocked}
              onClick={() => on.onSeedMode(true)}
            >
              Locked
            </button>
          </div>
          <input
            id={`${id}-seed`}
            className="input mono"
            data-testid="gen-seed"
            type="number"
            inputMode="numeric"
            min={0}
            step={1}
            disabled={!values.seedLocked}
            placeholder="Random"
            value={values.seed}
            onChange={(e) => on.onSeed(e.target.value)}
            onBlur={on.onSeedBlur}
          />
          <button
            type="button"
            className="btn ghost sm gen-dice"
            data-testid="gen-seed-dice"
            title="Roll a new seed and lock it"
            aria-label="Roll a new seed"
            onClick={on.onRoll}
          >
            <DiceIcon />
          </button>
        </div>
      </div>
    </div>
  )
}
