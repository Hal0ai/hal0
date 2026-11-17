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

/** Words and hover text for a bank's role (area-pure's `bankRoles`). */
const ROLE_TITLE = {
  shared: 'The shared bank: memory every agent can read',
  private: 'The private bank: memory kept private to an agent',
  retired: 'Retired: folded into the two-bank layout and kept for reference',
}

// A bank's name, then its role as a word (never colour alone) when it has one.
function BankName({ bank }) {
  const name = bank.label ?? bank.id
  if (!bank.role) return name
  return (
    <>
      {name}
      <span className="ma-bank-role" data-role={bank.role} title={ROLE_TITLE[bank.role]}>
        {bank.role}
      </span>
    </>
  )
}

/** Past this many banks the control is a select, not segments. */
export const SEGMENT_LIMIT = 6

// RichSelect's value for "no project chosen". The caller sees `null`.
const ALL_PROJECTS = ''

/**
 * Move focus to the scope bar's bank control (the checked segment, or the select's button), so an
 * action that needs a bank can hand the reader straight to the place that chooses one.
 */
export function focusBankControl() {
  const control = document.querySelector('[data-testid="ma-scope"] [data-testid="ma-scope-bank"]')
  const target =
    control?.querySelector('[tabindex="0"]') ?? control?.querySelector('button, [role="combobox"]') ?? control
  if (target instanceof HTMLElement) target.focus()
}

/**
 * @param {{
 *   banks: Array<{id: string, label?: import('react').ReactNode, count?: number|null, ariaLabel?: string,
 *     role?: 'shared'|'private'|'retired'}>,
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
              label: <BankName bank={b} />,
              count: b.count,
              ariaLabel: b.ariaLabel ?? (b.role ? `${b.label ?? b.id}, ${b.role}` : undefined),
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
                row: (
                  <span className="ma-mono">
                    <BankName bank={b} />
                  </span>
                ),
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
