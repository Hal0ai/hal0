// The card a tab shows while its real view is still being moved into the Memory area: the tab's
// heading, one sentence saying so, and where the capability lives today.
//
// Used only by the tabs that are not built yet. It goes when the last of them is.
import { useId } from 'react'
import './memory-area.css'
import { EmptyState } from './shared/EmptyState.jsx'
import { GoLink } from './shared/GoLink.jsx'

/**
 * @param {{
 *   heading: string,
 *   title: string,
 *   children: import('react').ReactNode,
 *   action?: import('react').ReactNode,
 *   'data-testid'?: string,
 * }} props `children` is the one sentence. `action` is the link to where it lives today.
 */
export function StubCard({ heading, title, children, action, 'data-testid': testId }) {
  const headingId = useId()
  return (
    <section className="card ma-card" aria-labelledby={headingId} data-testid={testId}>
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id={headingId}>
          {heading}
        </h2>
      </div>
      <EmptyState title={title} action={action}>
        {children}
      </EmptyState>
    </section>
  )
}

/** A link that leaves the Memory area for a section of the legacy tools page. */
export function LegacyLink({ section, children, 'data-testid': testId }) {
  return (
    <GoLink href={`#memworkspace/${section}`} data-testid={testId}>
      {children}
    </GoLink>
  )
}
