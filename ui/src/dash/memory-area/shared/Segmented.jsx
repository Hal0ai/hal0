// The one "pick one of N" control in the Memory area: bank scope, a status or type filter, a
// view switch. A radio group: exactly one segment is checked.
//
// Keyboard, as for any radio group: Tab reaches the group once, on the checked segment (roving
// tabindex). Right and Down move to the next segment, Left and Up to the previous one, wrapping
// at the ends; Home and End jump to the first and last. Moving selects, so focus and selection
// never part. A disabled segment is skipped.
//
// The checked segment differs by fill, inner edge and weight (memory-area.css), never by hue.
// More than six options is not a segmented control: use RichSelect (ScopeBar does this).
import { useRef } from 'react'
import '../memory-area.css'
import { formatCount } from './format-pure'

const STEP = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }

// The option an arrow, Home or End key lands on, or null when the key is not one of ours or
// there is nowhere to go.
function targetFor(key, options, currentId) {
  const enabled = options.filter((o) => !o.disabled)
  if (enabled.length === 0) return null
  if (key === 'Home') return enabled[0]
  if (key === 'End') return enabled[enabled.length - 1]
  const step = STEP[key]
  if (!step) return null
  const at = enabled.findIndex((o) => o.id === currentId)
  if (at === -1) return step > 0 ? enabled[0] : enabled[enabled.length - 1]
  return enabled[(at + step + enabled.length) % enabled.length]
}

/**
 * @param {{
 *   options: Array<{
 *     id: string,
 *     label: import('react').ReactNode,
 *     count?: number|null,
 *     ariaLabel?: string,
 *     disabled?: boolean,
 *   }>,
 *   value: string|null,
 *   onChange: (id: string) => void,
 *   label?: string,
 *   labelledBy?: string,
 *   inset?: boolean,
 *   'data-testid'?: string,
 * }} props `label` or `labelledBy` names what is being chosen; pass one. `inset` is for a
 *   control that sits on a card.
 */
export function Segmented({
  options,
  value,
  onChange,
  label,
  labelledBy,
  inset = false,
  'data-testid': testId,
}) {
  const buttons = useRef(new Map())

  // The group's one tab stop: the checked segment, or the first usable one when the value
  // matches none (so the group is still reachable).
  const checked = options.find((o) => o.id === value && !o.disabled)
  const tabStop = checked ?? options.find((o) => !o.disabled)

  const onKeyDown = (event, id) => {
    const target = targetFor(event.key, options, id)
    if (!target) return
    event.preventDefault()
    if (target.id === id) return
    buttons.current.get(target.id)?.focus()
    if (target.id !== value) onChange(target.id)
  }

  return (
    <div
      className={inset ? 'ma-seg ma-seg-inset' : 'ma-seg'}
      role="radiogroup"
      aria-label={labelledBy ? undefined : label}
      aria-labelledby={labelledBy}
      data-testid={testId}
    >
      {options.map((option) => {
        const isChecked = option.id === value
        const count = formatCount(option.count)
        return (
          <button
            key={option.id}
            ref={(el) => {
              if (el) buttons.current.set(option.id, el)
              else buttons.current.delete(option.id)
            }}
            type="button"
            role="radio"
            className="ma-seg-btn"
            aria-checked={isChecked}
            aria-label={option.ariaLabel}
            disabled={!!option.disabled}
            tabIndex={tabStop && option.id === tabStop.id ? 0 : -1}
            data-option-id={option.id}
            onClick={() => {
              if (!isChecked) onChange(option.id)
            }}
            onKeyDown={(event) => onKeyDown(event, option.id)}
          >
            {option.label}
            {count !== null && <span className="ma-seg-count">{count}</span>}
          </button>
        )
      })}
    </div>
  )
}
