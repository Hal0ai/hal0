// A link that stands alone and leaves the current view: underlined text and an arrow.
//
// The underline is the cue that it is a link (colour never is), and the arrow says it goes
// somewhere else: after the text to go on, before it and pointing left to go back (`back`).
// A link inside a sentence is a plain `<a className="ma-link">` instead.
import '../memory-area.css'

function Arrow({ back }) {
  return (
    <svg
      aria-hidden="true"
      width="11"
      height="11"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d={back ? 'M13 8H3M7 4L3 8l4 4' : 'M3 8h10M9 4l4 4-4 4'} />
    </svg>
  )
}

/**
 * @param {{
 *   href: string,
 *   children?: import('react').ReactNode,
 *   back?: boolean,
 *   'data-testid'?: string,
 * }} props
 */
export function GoLink({ href, children, back = false, 'data-testid': testId }) {
  return (
    <a className="ma-go" href={href} data-testid={testId}>
      {back && <Arrow back />}
      <span className="ma-go-text">{children}</span>
      {!back && <Arrow />}
    </a>
  )
}
