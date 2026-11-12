// The confirm step of a destructive action, drawn in place of the button row that opened it.
// It is not a dialog: nothing floats, nothing is trapped, and the page behind stays readable.
//
// It says what will be destroyed and how many, and the confirming button says it again:
//
//   Discard 105 failed operations?
//   What agents sent in these 105 operations will never become facts. This cannot be undone.
//   [ Discard 105 failed operations ]  [ Keep them ]
//
// The title and the confirm label are built from the same `verb`, `count` and `noun`, so they
// cannot drift apart and the button can never read "OK" or "Confirm". The other button is named
// for what is kept ("Keep them"), never "Cancel", and it takes focus when the block opens, so a
// stray Enter or Space keeps the data. Escape also keeps it.
//
// The caller renders this while its own "confirming" state is set and removes it from
// `onDismiss` and after `onConfirm` settles.
import { useEffect, useId, useRef } from 'react'
import '../memory-area.css'
import { formatCount } from './format-pure'

/**
 * "Discard 105 failed operations" / "Delete 1 fact" / "Delete the bank" (no count).
 *
 * @param {{verb: string, noun: string, nounSingular?: string, count?: number|null}} parts
 */
export function confirmPhrase({ verb, count, noun, nounSingular }) {
  if (count === null || count === undefined) return `${verb} ${noun}`
  const name = count === 1 && nounSingular ? nounSingular : noun
  return `${verb} ${formatCount(count)} ${name}`
}

/**
 * @param {{
 *   verb: string,
 *   noun: string,
 *   nounSingular?: string,
 *   count?: number|null,
 *   children?: import('react').ReactNode,
 *   onConfirm: () => void,
 *   onDismiss: () => void,
 *   dismissLabel?: string,
 *   busy?: boolean,
 *   'data-testid'?: string,
 * }} props `noun` is the plural ("failed operations"). `children` is the sentence that says
 *   what is lost and that it cannot be undone. `dismissLabel` is "Keep it" for a single thing.
 */
export function InlineConfirm({
  verb,
  noun,
  nounSingular,
  count,
  children,
  onConfirm,
  onDismiss,
  dismissLabel = 'Keep them',
  busy = false,
  'data-testid': testId,
}) {
  const titleId = useId()
  const bodyId = useId()
  const keep = useRef(null)
  const phrase = confirmPhrase({ verb, count, noun, nounSingular })

  // Focus starts on the safe choice.
  useEffect(() => {
    keep.current?.focus()
  }, [])

  return (
    <div
      className="ma-confirm"
      role="group"
      aria-labelledby={titleId}
      data-testid={testId}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.stopPropagation()
          onDismiss()
        }
      }}
    >
      <h3 className="ma-confirm-title" id={titleId}>
        {phrase}?
      </h3>
      <p className="ma-confirm-body" id={bodyId}>
        {children}
      </p>
      <div className="ma-confirm-actions">
        <button
          type="button"
          className="btn danger"
          aria-describedby={bodyId}
          disabled={busy}
          onClick={onConfirm}
        >
          {phrase}
        </button>
        <button type="button" className="btn ghost" ref={keep} onClick={onDismiss}>
          {dismissLabel}
        </button>
      </div>
    </div>
  )
}
