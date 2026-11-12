// The banks table of Memory ▸ Health: one row per bank, as the engine has them.
//
// Every figure is the health answer's (area-pure's `healthBankRows` joins its two per-bank
// lists), so nothing here asks a bank for its own stats. A figure that was not reported is the
// "not reported" mark, never a zero, and a read that some banks did not answer says so under
// the table.
import '../memory-area.css'
import { plural } from '../area-pure'
import { serializeMemoryRoute } from '../memory-route-pure'
import { EmptyState } from '../shared/EmptyState.jsx'
import { formatCount } from '../shared/format-pure'

const COLUMNS = [
  { key: 'facts', label: 'Facts' },
  { key: 'documents', label: 'Documents' },
  { key: 'waiting', label: 'Waiting' },
  { key: 'failed', label: 'Failed' },
]

// A count in a table cell: the number, or the "not reported" mark.
function Figure({ value }) {
  const text = formatCount(value)
  if (text === null) {
    return (
      <span className="ma-missing" role="img" aria-label="not reported">
        –
      </span>
    )
  }
  return text
}

/**
 * @param {{
 *   rows: object[],
 *   totals: object,
 *   listMissing?: boolean,
 *   note?: string|null,
 *   onRecheck?: () => void,
 *   rechecking?: boolean,
 * }} props `listMissing`: the health answer could not list the banks. `note`: what was cut
 *   short in the read behind these rows, in words.
 */
export function BanksCard({ rows, totals, listMissing = false, note = null, onRecheck, rechecking = false }) {
  const anyMissing = rows.some((row) => COLUMNS.some((column) => row[column.key] === null))
  let body
  if (listMissing) {
    body = (
      <EmptyState
        title="The bank list did not load"
        action={
          <button type="button" className="btn ghost sm" disabled={rechecking} onClick={onRecheck}>
            {rechecking ? 'Checking' : 'Check again'}
          </button>
        }
        data-testid="ma-health-banks-error"
      >
        The engine answered its health check but not the request for its banks, so no bank is listed.
      </EmptyState>
    )
  } else if (rows.length === 0) {
    body = (
      <EmptyState title="No banks yet" data-testid="ma-health-banks-empty">
        A bank appears here the first time an agent saves a memory to it.
      </EmptyState>
    )
  } else {
    body = (
      <>
        <div className="ma-table-region" role="region" aria-label="Banks table" tabIndex={0}>
          <table className="ma-table" data-testid="ma-health-banks">
            <thead>
              <tr>
                <th scope="col">Bank</th>
                {COLUMNS.map((column) => (
                  <th scope="col" className="ma-num" key={column.key}>
                    {column.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} data-bank={row.id}>
                  <th scope="row">
                    <a className="ma-link" href={serializeMemoryRoute({ tab: 'facts', bank: row.id })}>
                      {row.id}
                    </a>
                  </th>
                  {COLUMNS.map((column) => (
                    <td className="ma-num" key={column.key} data-col={column.key}>
                      <Figure value={row[column.key]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {note && (
          <div className="ma-card-foot" data-testid="ma-health-banks-partial">
            {note}
          </div>
        )}
        {anyMissing && (
          <div className="ma-card-foot" data-testid="ma-health-banks-foot">
            – means the engine did not report a figure
          </div>
        )}
      </>
    )
  }
  return (
    <section className="card ma-card" aria-labelledby="ma-health-banks-h">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id="ma-health-banks-h">
          Banks
        </h2>
        <span className="ma-card-gap" />
        {!listMissing && rows.length > 0 && (
          <span className="ma-card-meta">
            {plural(totals.banks, 'bank', 'banks')} · waiting and failed are operations · a row opens its facts
          </span>
        )}
      </div>
      {body}
    </section>
  )
}
