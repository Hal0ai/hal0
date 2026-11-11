// The first card of Memory ▸ Health: the verdict, and under it the repair path.
//
// The path is the route's order (health-pure.ts builds the rows). It is an ordered list, so a
// screen reader says where a step stands, and each row says its state in words:
//
//   current   "Step 1 of 4 · do this now", a raised row, a filled marker and `aria-current`. The
//             one row that may carry a command to review.
//   waiting   "Waiting on step 1: storing memories."
//   clear     one quiet line.
//
// Nothing here fixes anything. A step whose remedy is on another page ends in a go-link, and
// the embedder step shows a command to copy, not a button that runs it. So the page carries no
// primary button at all today; if a step ever gets one, only the current row may hold it.
//
// With nothing open the same rows are listed as what was checked, unnumbered.
import '../memory-area.css'
import { memoryTabHref } from '../memory-route-pure'
import { plural } from '../area-pure'
import { CommandBox } from '../shared/CommandBox.jsx'
import { GoLink } from '../shared/GoLink.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'
import { formatDateTime, formatRelativeTime } from '../shared/format-pure'

/** A sentence with its names (a model, a slot) set in mono and its figures a step brighter. */
export function Segments({ segments }) {
  return segments.map((segment, at) => {
    if (typeof segment === 'string') return segment
    if ('code' in segment) {
      return (
        <span className="ma-mono ma-em" key={at}>
          {segment.code}
        </span>
      )
    }
    return (
      <span className="ma-em" key={at}>
        {segment.em}
      </span>
    )
  })
}

function linkHref(to, route) {
  if (to === 'services') return '#services'
  if (to === 'settings') return '#settings/memory'
  return memoryTabHref(to, route)
}

function StepLinks({ step, route }) {
  if (step.links.length === 0) return null
  return (
    <div className="ma-step-links">
      {step.links.map((link) => (
        <GoLink key={link.to} href={linkHref(link.to, route)} data-testid={`ma-health-step-${step.id}-${link.to}`}>
          {link.label}
        </GoLink>
      ))}
    </div>
  )
}

function Check() {
  return (
    <svg
      aria-hidden="true"
      width="13"
      height="13"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M3.5 8.5l3 3 6-7" />
    </svg>
  )
}

// A clear step: one line. The word "clear" is in the row; the tick only repeats it.
function ClearRow({ step, route, numbered }) {
  return (
    <li className="ma-step ma-step-clear" data-step={step.id} data-state="clear">
      <span className="ma-step-marker ma-step-marker-clear" aria-hidden="true">
        <Check />
      </span>
      <p className="ma-step-line">
        <span className="ma-eyebrow">
          {numbered ? `Step ${step.position} of ${step.of} · clear` : 'Clear'}
        </span>{' '}
        <span className="ma-step-line-title">{step.title}</span>{' '}
        <span className="ma-step-line-text">
          <Segments segments={step.body} />
        </span>
        {step.note && (
          <>
            {' '}
            <span className="ma-step-line-note" data-testid={`ma-health-step-${step.id}-note`}>
              {step.note}
            </span>
          </>
        )}
      </p>
      <StepLinks step={step} route={route} />
    </li>
  )
}

function OpenRow({ step, route }) {
  const current = step.state === 'current'
  return (
    <li
      className={current ? 'ma-step ma-step-current' : 'ma-step'}
      data-step={step.id}
      data-state={step.state}
      aria-current={current ? 'step' : undefined}
    >
      <span className="ma-step-marker" aria-hidden="true">
        {step.position}
      </span>
      <div className="ma-step-main">
        <div className="ma-step-head">
          <span className="ma-eyebrow">
            Step {step.position} of {step.of}
            {current ? ' · do this now' : ''}
          </span>
          {step.chip && <StatusChip tone={step.chip.tone}>{step.chip.word}</StatusChip>}
        </div>
        <h3 className="ma-item-title">{step.title}</h3>
        <p className="ma-body" data-testid={`ma-health-step-${step.id}-text`}>
          <Segments segments={step.body} />
        </p>
        {step.waitsOn && (
          <p className="ma-helper ma-step-extra" data-testid={`ma-health-step-${step.id}-waits`}>
            {step.waitsOn}.
          </p>
        )}
        {step.figures.length > 0 && (
          <dl className="ma-step-figures" data-testid={`ma-health-step-${step.id}-figures`}>
            {step.figures.map((figure) => (
              <div className="ma-step-figure" key={figure.label}>
                <dt>{figure.label}</dt>
                <dd>{figure.value}</dd>
              </div>
            ))}
          </dl>
        )}
        {current && step.errors.length > 0 && (
          <div className="ma-step-extra" data-testid={`ma-health-step-${step.id}-errors`}>
            <span className="ma-eyebrow ma-eyebrow-block">What the engine reported</span>
            <ul className="ma-step-errors">
              {step.errors.map((group) => (
                <li key={group.message}>
                  <span className="ma-mono ma-em">{group.message}</span>{' '}
                  <span className="ma-step-error-count">seen {plural(group.count, 'time', 'times')}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {step.command && (
          <div className="ma-step-extra ma-step-command">
            <span className="ma-eyebrow ma-eyebrow-block">Command to review</span>
            <CommandBox command={step.command.command} data-testid={`ma-health-step-${step.id}-command`} />
            <p className="ma-helper ma-step-command-note">{step.command.note}</p>
          </div>
        )}
        {step.note && (
          <p className="ma-helper ma-step-extra" data-testid={`ma-health-step-${step.id}-note`}>
            {step.note}
          </p>
        )}
      </div>
      <StepLinks step={step} route={route} />
    </li>
  )
}

/**
 * @param {{
 *   verdict: object,
 *   path: object[],
 *   route: object,
 *   checkedAt?: string|null,
 * }} props `verdict` and `path` are health-pure's `healthVerdict` and `buildPath`.
 */
export function VerdictCard({ verdict, path, route, checkedAt = null }) {
  const attention = verdict.kind === 'attention'
  const checked = formatRelativeTime(checkedAt)
  const List = attention ? 'ol' : 'ul'
  return (
    <section
      className="card ma-card"
      aria-labelledby="ma-health-lead"
      data-testid="ma-health-summary"
      data-state={verdict.kind}
      data-incomplete={verdict.qualifier ? 'true' : undefined}
    >
      <div className="ma-lead-row">
        <div className="ma-lead-main">
          <span className="ma-eyebrow ma-eyebrow-block">Does anything need you?</span>
          <h2 className="ma-lead" id="ma-health-lead">
            {verdict.lead}
          </h2>
          <p className="ma-body" data-testid="ma-health-detail">
            {verdict.detail}
          </p>
          {verdict.qualifier && (
            <p className="ma-body ma-health-qualifier" data-testid="ma-health-qualifier">
              {verdict.qualifier}
            </p>
          )}
        </div>
        <div className="ma-lead-side">
          {checked && (
            <span className="ma-card-meta">
              checked{' '}
              <time dateTime={checkedAt} title={formatDateTime(checkedAt) ?? undefined}>
                {checked}
              </time>
            </span>
          )}
          {verdict.chip && <StatusChip tone={verdict.chip.tone}>{verdict.chip.word}</StatusChip>}
        </div>
      </div>
      {path.length > 0 && (
        <List
          className="ma-path"
          // The list has no markers of its own, and some screen readers then stop calling it a
          // list. The role keeps "list, 4 items" and each row's position.
          role="list"
          aria-label={attention ? 'Repair path, in order' : 'What was checked'}
          data-testid="ma-health-path"
        >
          {path.map((step) =>
            step.state === 'clear' ? (
              <ClearRow key={step.id} step={step} route={route} numbered={attention} />
            ) : (
              <OpenRow key={step.id} step={step} route={route} />
            ),
          )}
        </List>
      )}
    </section>
  )
}
