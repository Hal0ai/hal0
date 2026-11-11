// The Memory area (`#memory/<tab>`): the one place in the dashboard for what agents remember.
//
// This file is the page: the header with its one-line summary, the tab row, and the dispatch to
// the tab that is open. Each tab is its own file beside this one.
//
// Where state lives:
//   - The open tab, the chosen bank and what is open in a tab are the address
//     (memory-route-pure.ts), and nothing else holds them. This component reads the address,
//     hands it to the tabs as `area.route`, and gives them `area.replaceRoute` to change it.
//   - A tab is a link, so opening one is a new history entry and Back returns to the last tab.
//     Choosing a bank or a fact rewrites the current entry (`replaceState`): Back never has to
//     walk through every bank that was looked at.
//   - There is no bank control here. The tabs that are scoped to a bank carry a scope bar.
//
// The engine and bank reads live here because the summary line and every tab need them; a tab
// receives them as `area` and asks for nothing twice.
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import './memory-area.css'
import { useMemoryBanks, useMemoryEngine } from '@/api/hooks/useHindsight'
import { AgentsTab } from './AgentsTab.jsx'
import { FactsTab } from './FactsTab.jsx'
import { HealthTab } from './HealthTab.jsx'
import { KnowledgeTab } from './KnowledgeTab.jsx'
import { OperationsTab } from './OperationsTab.jsx'
import { useAllBankStats } from './area-data'
import { bankRows, bankTotals, engineState, metaLine } from './area-pure'
import { memoryTabHref, parseMemoryRoute, serializeMemoryRoute } from './memory-route-pure'

const TABS = [
  { id: 'health', label: 'Health' },
  { id: 'facts', label: 'Facts' },
  { id: 'knowledge', label: 'Knowledge' },
  { id: 'operations', label: 'Operations' },
  { id: 'agents', label: 'Agents' },
]

const inArea = (hash) => /^#memory(\/|\?|$)/.test(hash || '')

// The address, kept in step with the browser's. `replaceRoute` merges a change into the address
// that is current now (not the one a caller rendered with), so two changes made in one handler
// cannot undo each other.
function useMemoryRoute() {
  const [route, setRoute] = useState(() => parseMemoryRoute(window.location.hash))
  const latest = useRef(route)
  latest.current = route

  useEffect(() => {
    const sync = () => {
      // Leaving the area unmounts this page; there is nothing to follow.
      if (!inArea(window.location.hash)) return
      setRoute(parseMemoryRoute(window.location.hash))
    }
    window.addEventListener('hashchange', sync)
    window.addEventListener('popstate', sync)
    return () => {
      window.removeEventListener('hashchange', sync)
      window.removeEventListener('popstate', sync)
    }
  }, [])

  const replaceRoute = useCallback((patch) => {
    const next = { ...latest.current, ...patch }
    latest.current = next
    try {
      window.history.replaceState(window.history.state, '', serializeMemoryRoute(next))
    } catch {
      /* a sandboxed frame may refuse; the page still follows its own state */
    }
    setRoute(next)
  }, [])

  return [route, replaceRoute]
}

function MetaLine({ meta }) {
  if (meta.kind === 'loading') {
    return (
      <span className="ma-meta" data-testid="ma-meta" data-state="loading" aria-busy="true">
        <span className="ma-skel ma-skel-meta" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </span>
    )
  }
  return (
    <span className="ma-meta" data-testid="ma-meta" data-state="ready">
      {meta.text}
    </span>
  )
}

/**
 * @param {{ notice?: import('react').ReactNode }} props `notice` is one NoticeBar that is true
 *   of every tab (a long run in progress). Nothing passes one yet.
 */
export function MemoryArea({ notice = null }) {
  const [route, replaceRoute] = useMemoryRoute()

  const engineQuery = useMemoryEngine()
  const banksQuery = useMemoryBanks()
  const engine = engineState(engineQuery)

  const banks = useMemo(() => banksQuery.data?.banks ?? [], [banksQuery.data])
  const bankIds = useMemo(() => banks.map((b) => b.bank_id), [banks])
  const stats = useAllBankStats(engine === 'reachable' ? bankIds : [])

  const banksLoading = banksQuery.data === undefined && !banksQuery.isError
  const banksFailed = !!banksQuery.isError && banksQuery.data === undefined
  const statsLoading = stats.some((entry) => entry.isLoading)
  const rows = useMemo(() => bankRows(banks, stats), [banks, stats])
  const totals = useMemo(() => bankTotals(rows, banksQuery.data?.total), [rows, banksQuery.data])

  const area = {
    route,
    replaceRoute,
    engine,
    engineQuery,
    banksQuery,
    banks,
    banksLoading,
    banksFailed,
    statsLoading,
    rows,
    totals,
  }

  const meta = metaLine({ engine, banksLoading, banksFailed, statsLoading, totals })

  return (
    <div className="view ma" data-testid="memory-area">
      <div className="ma-page">
        <div>
          <div className="vh ma-head">
            <h1>Memory</h1>
            <MetaLine meta={meta} />
          </div>
          <nav className="ma-tabs" aria-label="Memory sections" data-testid="ma-tabs">
            {TABS.map((tab) => (
              <a
                key={tab.id}
                className="ma-tab"
                href={memoryTabHref(tab.id, route)}
                aria-current={route.tab === tab.id ? 'page' : undefined}
                data-testid={`ma-tab-${tab.id}`}
              >
                {tab.label}
              </a>
            ))}
            <span className="ma-tabs-gap" />
            <a className="ma-tabs-aside" href="#settings/memory" data-testid="ma-engine-settings">
              <span className="ma-go-text">Engine settings</span>
              <svg
                aria-hidden="true"
                width="11"
                height="11"
                viewBox="0 0 16 16"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
                strokeLinecap="round"
                strokeLinejoin="round"
              >
                <path d="M6 3h7v7M13 3L4 12" />
              </svg>
            </a>
          </nav>
        </div>

        {notice}

        {route.tab === 'health' && <HealthTab area={area} />}
        {route.tab === 'facts' && <FactsTab area={area} />}
        {route.tab === 'knowledge' && <KnowledgeTab area={area} />}
        {route.tab === 'operations' && <OperationsTab area={area} />}
        {route.tab === 'agents' && <AgentsTab area={area} />}
      </div>
    </div>
  )
}

export default MemoryArea
