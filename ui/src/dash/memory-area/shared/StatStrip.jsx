// A row of figures with one anatomy: the label above, the value, then its unit, and at most one
// helper line under it.
//
//   - A number is never tinted. Its status goes in a chip after it (`chip`).
//   - Every number has a unit. A number is formatted here (thousands with a comma), so a caller
//     passes the number itself, not a string.
//   - A value the engine did not report (`null` or `undefined`) is an en dash read out as
//     "not reported", with no unit. It is never `0`.
//   - While loading, every value is a skeleton block. No value is printed, not even one the
//     caller already holds, so a stale or default `0` cannot flash.
//
// A string value is a name (an engine, a model) and is set smaller than a number.
import '../memory-area.css'
import { formatCount } from './format-pure'

function Value({ cell, loading }) {
  if (loading) return <span className="ma-skel" aria-hidden="true" />
  const missing =
    cell.value === null ||
    cell.value === undefined ||
    cell.value === '' ||
    (typeof cell.value === 'number' && !Number.isFinite(cell.value))
  if (missing) {
    return (
      <span className="ma-missing" role="img" aria-label="not reported">
        –
      </span>
    )
  }
  return (
    <>
      <span>{typeof cell.value === 'number' ? formatCount(cell.value) : cell.value}</span>
      {cell.unit && <span className="ma-stat-unit">{cell.unit}</span>}
      {cell.chip}
    </>
  )
}

/**
 * @param {{
 *   label: string,
 *   cells: Array<{
 *     id?: string,
 *     label: string,
 *     value: number|string|null|undefined,
 *     unit?: string,
 *     helper?: import('react').ReactNode,
 *     chip?: import('react').ReactNode,
 *   }>,
 *   loading?: boolean,
 *   inCard?: boolean,
 *   'data-testid'?: string,
 * }} props `label` names the strip for assistive tech. `inCard` drops the strip's own frame.
 */
export function StatStrip({ label, cells, loading = false, inCard = false, 'data-testid': testId }) {
  return (
    <section aria-label={label} aria-busy={loading || undefined} data-testid={testId}>
      <dl className={inCard ? 'ma-stats ma-stats-incard' : 'ma-stats'}>
        {cells.map((cell) => {
          const isName = !loading && typeof cell.value === 'string' && cell.value !== ''
          return (
            <div className="ma-stat" key={cell.id ?? cell.label} data-stat={cell.id ?? cell.label}>
              <dt className="ma-eyebrow ma-stat-label">{cell.label}</dt>
              <dd className={isName ? 'ma-stat-value ma-stat-value-name' : 'ma-stat-value'}>
                <Value cell={cell} loading={loading} />
              </dd>
              {cell.helper && !loading && <dd className="ma-stat-helper">{cell.helper}</dd>}
            </div>
          )
        })}
      </dl>
      {loading && <span className="sr-only">Loading</span>}
    </section>
  )
}
