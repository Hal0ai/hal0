// The scope bar of Facts, Knowledge and Operations: which bank, and optionally which project,
// everything below it shows.
//
// The bank control is a Segmented control while there are six banks or fewer and a RichSelect
// past that, so the bar holds one row whether a box has two banks or twenty. The project control
// is drawn only when projects are passed: a view that has no project data gets no dead control.
//
// The bar holds no state. The chosen bank and project live in the route (the caller owns them).
import { useId } from 'react'
import '../memory-area.css'
import { RichSelect } from '../../rich-select'
import { Segmented } from './Segmented'

/** Past this many banks the control is a select, not segments. */
export const SEGMENT_LIMIT = 6

// RichSelect's value for "no project chosen". The caller sees `null`.
const ALL_PROJECTS = ''

/**
 * @param {{
 *   banks: Array<{id: string, label?: import('react').ReactNode, count?: number|null, ariaLabel?: string}>,
 *   bank: string|null,
 *   onBankChange: (id: string) => void,
 *   projects?: Array<{id: string, label?: string}>,
 *   project?: string|null,
 *   onProjectChange?: (id: string|null) => void,
 * }} props A bank's `label` defaults to its id, shown verbatim. `project` is `null` for all
 *   projects.
 */
export function ScopeBar({ banks, bank, onBankChange, projects, project = null, onProjectChange }) {
  const bankLabelId = useId()
  const hasProjects = Array.isArray(projects) && projects.length > 0

  return (
    <div className="ma-scope" role="group" aria-label="Scope" data-testid="ma-scope">
      <div className="ma-scope-field">
        <span className="ma-eyebrow" id={bankLabelId}>
          Bank
        </span>
        {banks.length <= SEGMENT_LIMIT ? (
          <Segmented
            labelledBy={bankLabelId}
            options={banks.map((b) => ({
              id: b.id,
              label: b.label ?? b.id,
              count: b.count,
              ariaLabel: b.ariaLabel,
            }))}
            value={bank}
            onChange={onBankChange}
            data-testid="ma-scope-bank"
          />
        ) : (
          <div className="ma-scope-select">
            <RichSelect
              aria-label="Bank"
              value={bank}
              options={banks.map((b) => ({
                id: b.id,
                row: <span className="ma-mono">{b.label ?? b.id}</span>,
              }))}
              onChange={onBankChange}
              data-testid="ma-scope-bank"
            />
          </div>
        )}
      </div>
      {hasProjects && (
        <div className="ma-scope-field">
          <span className="ma-eyebrow">Project</span>
          <div className="ma-scope-select">
            <RichSelect
              aria-label="Project"
              value={project ?? ALL_PROJECTS}
              options={[
                { id: ALL_PROJECTS, row: 'All projects' },
                ...projects.map((p) => ({
                  id: p.id,
                  row: <span className="ma-mono">{p.label ?? p.id}</span>,
                })),
              ]}
              onChange={(id) => {
                if (onProjectChange) onProjectChange(id === ALL_PROJECTS ? null : id)
              }}
              data-testid="ma-scope-project"
            />
          </div>
        </div>
      )}
    </div>
  )
}
