// What a card shows in place of a list or table that has nothing in it: one sentence of fact,
// one of what happens next, and at most one action.
//
// Left-aligned, like the list it stands in for. It carries no `role="status"`: it is on the page
// when the page loads, and a live region is for content that appears after an action.
import '../memory-area.css'

/**
 * @param {{
 *   title: import('react').ReactNode,
 *   children?: import('react').ReactNode,
 *   action?: import('react').ReactNode,
 *   'data-testid'?: string,
 * }} props `children` is the body sentence. `action` is one link or one secondary button.
 */
export function EmptyState({ title, children, action, 'data-testid': testId }) {
  return (
    <div className="ma-empty" data-testid={testId}>
      <svg
        className="ma-empty-icon"
        aria-hidden="true"
        width="22"
        height="22"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <ellipse cx="8" cy="4" rx="5" ry="2" />
        <path d="M3 4v8c0 1.1 2.2 2 5 2s5-.9 5-2V4" />
      </svg>
      <h3 className="ma-empty-title">{title}</h3>
      {children && <p className="ma-empty-body">{children}</p>}
      {action && <div className="ma-empty-action">{action}</div>}
    </div>
  )
}
