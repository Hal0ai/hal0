// Memory ▸ Agents: what reads and writes this box's memory, and how to connect another agent.
//
// Top to bottom:
//   Agents            one row per agent there is evidence of: what it reads and writes, the banks
//                     that hold facts tagged with its name, and a status that says only what the
//                     evidence supports.
//   Built into hal0   the dashboard, brain chat and Open WebUI. No status: none of them is a
//                     thing that connects.
//   no known writer   only when facts tagged `agent:unknown` exist: how many, and on which banks.
//   Connect           the install command for a coding agent, and what running it does.
//
// The page describes memory as it is on this box today: the banks the engine really has, and
// coding agents that reach the engine through the Hindsight plugin. It has no primary action.
//
// What is read, and from where, is in agents-data. A tab state that hides a figure never shows a
// zero in its place: while evidence is loading or could not be read, no agent is "not seen".
import { useMemo, useRef } from 'react'
import './memory-area.css'
import {
  useAgentsRoute,
  useAllBankTags,
  useInstalledAgents,
  useMemoryKeyClients,
  useTagsRefresh,
} from './agents-data'
import {
  BRAIN_ID,
  OPENWEBUI_ID,
  agentRows,
  builtInSightings,
  readAgentsRoute,
  tagsState,
  unattributedCodingBanks,
  unknownSummary,
} from './agents-pure'
import { AgentsCard } from './agents/AgentsCard.jsx'
import { BuiltInCard, UnknownCard } from './agents/BuiltInCard.jsx'
import { ConnectCard } from './agents/ConnectCard.jsx'
import { EmptyState } from './shared/EmptyState.jsx'

const settled = (query) => query?.data !== undefined || !!query?.isError

/** @param {{ area: object }} props What MemoryArea holds: the route, the engine check, the banks. */
export function AgentsTab({ area }) {
  const { route, engine, banks, banksLoading, banksFailed, banksQuery } = area
  const agentsQuery = useAgentsRoute()
  const installedQuery = useInstalledAgents()
  const clientsQuery = useMemoryKeyClients()
  const refreshTags = useTagsRefresh()
  const connectHeading = useRef(null)

  const agents = readAgentsRoute(agentsQuery)
  const listedIds = useMemo(() => banks.map((b) => b.bank_id), [banks])
  // The coding-agent banks are engine banks too. A long bank list arrives one page at a time, so
  // one of them may be missing from the page MemoryArea holds: it is read here either way.
  const routeBanks = agents.kind === 'ready' ? agents.banks : null
  const bankIds = useMemo(
    () => [...new Set([...listedIds, ...(routeBanks ?? [])])],
    [listedIds, routeBanks],
  )
  const reachable = engine === 'reachable'
  const tags = useAllBankTags(reachable && !banksLoading && !banksFailed ? bankIds : [])
  const evidence = tagsState(tags)

  const frame = (state, children) => (
    <div className="ma-agents" data-testid="ma-agents" data-state={state}>
      {children}
    </div>
  )

  if (engine === 'fallback') {
    return frame(
      'unsupported',
      <section className="card ma-card" aria-label="Agents" data-testid="ma-agents-fallback">
        <EmptyState title="Memory is not running on Hindsight">
          This page reads the Hindsight engine&apos;s banks and tags. Memory is running on another engine,
          which has neither.
        </EmptyState>
      </section>,
    )
  }

  const connect = (
    <ConnectCard
      agents={agents}
      onRecheck={() => agentsQuery.refetch?.()}
      rechecking={!!agentsQuery.isFetching}
      headingRef={connectHeading}
    />
  )
  const goToConnect = () => {
    const heading = connectHeading.current
    if (!heading) return
    heading.scrollIntoView?.({ block: 'start' })
    heading.focus?.()
  }

  // Why the list of agents cannot be shown, when it cannot. In each of these states the built-in
  // card says what is fixed by the code and leaves the counts out.
  let blocked = null
  if (engine === 'unreachable') blocked = 'unreachable'
  else if (engine === 'loading' || banksLoading) blocked = 'loading'
  else if (banksFailed) blocked = 'banks-error'
  else if (evidence.allFailed) blocked = 'tags-error'
  else if (evidence.loading || agents.kind === 'loading' || !settled(installedQuery) || !settled(clientsQuery)) {
    blocked = 'loading'
  }

  if (blocked) {
    return frame(
      blocked,
      <>
        <AgentsCard
          state={blocked}
          onRecheck={blocked === 'banks-error' ? () => banksQuery.refetch?.() : refreshTags}
          rechecking={blocked === 'banks-error' ? !!banksQuery.isFetching : false}
        />
        <BuiltInCard state={blocked === 'loading' ? 'loading' : 'unknown'} />
        {connect}
      </>,
    )
  }

  const rows = agentRows({
    tags,
    installed: installedQuery.data?.agents ?? [],
    clients: clientsQuery.data?.items ?? [],
    harnesses: agents.kind === 'ready' ? agents.harnesses : [],
  })
  const unknown = unknownSummary(tags)

  return frame(
    rows.length === 0 ? 'empty' : 'ready',
    <>
      <AgentsCard
        state="ready"
        rows={rows}
        route={route}
        codingBanks={agents.kind === 'failed' ? null : (routeBanks ?? [])}
        unattributed={unattributedCodingBanks(routeBanks, tags)}
        failedBanks={evidence.failed}
        bankListPartial={(banksQuery.data?.total ?? 0) > banks.length}
        onRecheck={refreshTags}
        onGoToConnect={goToConnect}
      />
      <BuiltInCard
        state="ready"
        brain={builtInSightings(tags, BRAIN_ID)}
        openwebui={builtInSightings(tags, OPENWEBUI_ID)}
        hasUnknown={unknown !== null}
      />
      {unknown && <UnknownCard summary={unknown} route={route} />}
      {connect}
    </>,
  )
}
