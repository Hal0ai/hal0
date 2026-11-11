// Memory ▸ Facts: the scope bar over the fact workspace of one bank.
//
// The workspace is the one the dashboard already had (dash/memory-bank-workspace.jsx, published
// as `window.MemV2Workspace`): the fact list with its filters, sources and inspector, the focus
// and web views, the reflect console, and the bank's description, add, consolidate and delete
// controls. It is mounted here as the old Memory page mounted it, with `embedded` set, which
// drops the two things this area supplies itself: its bank `<select>` (the scope bar chooses the
// bank) and its rules tab (directives and mental models belong to Knowledge).
//
// The chosen bank and the open fact are the address (`?bank=…&fact=…`):
//   - choosing a bank rewrites the address and closes the open fact, which belongs to the bank
//     that was left;
//   - an address with no bank gets the first bank written into it;
//   - an address that names a bank the engine does not list says so, and waits for a choice.
//
// The last bank looked at is also written to localStorage, for the Overview page's Memory card.
// Nothing in this area reads it back.
import { useEffect } from 'react'
import './memory-area.css'
import { EmptyState } from './shared/EmptyState.jsx'
import { GoLink } from './shared/GoLink.jsx'
import { ScopeBar } from './shared/ScopeBar.jsx'
import { memoryTabHref } from './memory-route-pure'

/** The key the Overview page's Memory card reads (dash/overview/memory.jsx). */
export const LAST_BANK_KEY = 'hal0.mem.bank'

function rememberBank(id) {
  try {
    if (id) localStorage.setItem(LAST_BANK_KEY, id)
  } catch {
    /* private mode or a full store: the Overview card falls back to the first bank */
  }
}

function Card({ children, testId }) {
  return (
    <section className="card ma-card" aria-label="Facts" data-testid={testId}>
      {children}
    </section>
  )
}

/** @param {{ area: object }} props What MemoryArea holds: the route, the engine check, the banks. */
export function FactsTab({ area }) {
  const { route, replaceRoute, engine, banks, banksLoading, banksFailed, banksQuery } = area
  const ready = engine === 'reachable' && !banksLoading && !banksFailed
  const first = banks[0]?.bank_id ?? null
  const listed = !!route.bank && banks.some((b) => b.bank_id === route.bank)
  // A long bank list arrives one page at a time: a bank that is not on this page may still exist.
  const paged = (banksQuery.data?.total ?? 0) > banks.length

  // An address with no bank is completed in place, so a reload or a copied link names the bank.
  useEffect(() => {
    if (ready && !route.bank && first) replaceRoute({ bank: first })
  }, [ready, route.bank, first, replaceRoute])

  useEffect(() => {
    if (ready && listed) rememberBank(route.bank)
  }, [ready, listed, route.bank])

  if (engine === 'loading' || (engine === 'reachable' && banksLoading)) {
    return (
      <Card testId="ma-facts-loading">
        <div className="ma-card-body" aria-busy="true">
          <span className="ma-skel ma-skel-line" aria-hidden="true" />
          <span className="ma-skel ma-skel-line" aria-hidden="true" />
          <span className="sr-only">Loading</span>
        </div>
      </Card>
    )
  }

  if (engine === 'unreachable') {
    return (
      <Card testId="ma-facts-unreachable">
        <EmptyState
          title="The memory engine is not answering"
          action={<GoLink href={memoryTabHref('health', route)}>Open Health</GoLink>}
        >
          Facts are read from the engine, so none can be shown until it answers. Health says what is known
          and where to look.
        </EmptyState>
      </Card>
    )
  }

  if (engine === 'fallback') {
    return (
      <Card testId="ma-facts-fallback">
        <EmptyState title="Memory is not running on Hindsight">
          The fact workspace reads a Hindsight bank. Memory is running on a fallback engine, which has none.
        </EmptyState>
      </Card>
    )
  }

  if (banksFailed) {
    return (
      <Card testId="ma-facts-banks-error">
        <EmptyState
          title="The bank list did not load"
          action={
            <button type="button" className="btn ghost sm" onClick={() => banksQuery.refetch()}>
              Check again
            </button>
          }
        >
          The engine answered its health check but not the request for its banks, so there is no bank to
          open.
        </EmptyState>
      </Card>
    )
  }

  if (banks.length === 0) {
    return (
      <Card testId="ma-facts-no-banks">
        <EmptyState title="No banks yet">
          A bank appears here the first time an agent saves a memory to it.
        </EmptyState>
      </Card>
    )
  }

  const selectBank = (id) => {
    // The open fact belongs to the bank being left.
    replaceRoute({ bank: id, fact: null })
  }
  const selectFact = (id) => replaceRoute({ fact: id || null })

  const Workspace = typeof window !== 'undefined' ? window.MemV2Workspace : null
  const scope = (
    <ScopeBar banks={banks.map((b) => ({ id: b.bank_id }))} bank={route.bank} onBankChange={selectBank} />
  )

  // The address names a bank the engine does not list (deleted, or mistyped). Its facts are
  // not shown under another bank's name: say so, and leave the choice to the scope bar.
  if (route.bank && !listed && !paged) {
    return (
      <>
        {scope}
        <Card testId="ma-facts-unknown-bank">
          <EmptyState title="This bank is not in the engine's list">
            The address names <span className="ma-mono ma-em">{route.bank}</span>, which the engine does not
            list. Choose a bank above.
          </EmptyState>
        </Card>
      </>
    )
  }

  return (
    <>
      {scope}
      {route.bank && typeof Workspace === 'function' ? (
        <Workspace
          bank={route.bank}
          setBank={selectBank}
          sel={route.fact}
          setSel={selectFact}
          embedded
        />
      ) : null}
    </>
  )
}
