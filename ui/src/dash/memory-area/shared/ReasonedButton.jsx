// A button that, when it cannot be used, says why.
//
// An action that is known to fail is shown disabled, never hidden and never offered and left to
// fail. `reason` is the sentence that explains it ("Waiting on the extraction connection."): when
// it is set the button is disabled and points at the sentence with `aria-describedby`, so the
// reason is read out with the button and is on screen under it. With no reason it is a plain
// button.
//
// The disabled fill comes from the `.ma .btn[disabled]` rule in memory-area.css.
import { useId } from 'react'
import '../memory-area.css'

/**
 * @param {{
 *   reason?: string|null,
 *   className?: string,
 *   children?: import('react').ReactNode,
 * } & import('react').ButtonHTMLAttributes<HTMLButtonElement>} props `className` is the button's
 *   kind (`btn`, `btn ghost sm`, `btn danger`).
 */
export function ReasonedButton({ reason, className = 'btn', children, disabled, ...rest }) {
  const reasonId = useId()
  const blocked = !!reason
  return (
    <span className="ma-reasoned">
      <button
        type="button"
        {...rest}
        className={className}
        disabled={blocked || !!disabled}
        aria-describedby={blocked ? reasonId : rest['aria-describedby']}
      >
        {children}
      </button>
      {blocked && (
        <p className="ma-reason" id={reasonId}>
          {reason}
        </p>
      )}
    </span>
  )
}
