// The "Agents" card: one row per agent hal0 has evidence of, and what the evidence is.
//
// Reads and Writes are two columns of words, so who only reads and who also writes can be told
// apart without colour. What each kind of agent reads and writes is fixed text describing what
// hal0's code does for that kind; what varies per row is the evidence: the banks that hold facts
// tagged with the agent's name, and how many.
import '../memory-area.css'
import { rowsSummary } from '../agents-pure'
import { memoryTabHref } from '../memory-route-pure'
import { EmptyState } from '../shared/EmptyState.jsx'
import { StatusChip } from '../shared/StatusChip.jsx'
import { formatCount, formatRelativeTime } from '../shared/format-pure'

const Mono = ({ children }) => <span className="ma-mono ma-em">{children}</span>

// What each kind of agent reads and writes. One sentence per line of the cell.
function readsOf(row) {
  switch (row.kind) {
    case 'hermes':
      return ['Recalls from shared memory and its own private facts before a turn, through hal0.']
    case 'hermes-profile':
      return ["Recalls from shared memory and this profile's own private facts before a turn, through hal0."]
    case 'pi':
      return [
        "Uses hal0's memory tools when it chooses to.",
        "Its Hindsight plugin also recalls from the repository's bank before each turn, straight from the engine.",
      ]
    case 'coding':
      return ['Through the Hindsight plugin, straight from the engine. hal0 does not see what it reads.']
    case 'client':
      return ["May read shared memory and its own private facts through hal0's memory endpoint, with its key."]
    default:
      return ['Not known.']
  }
}

function writesOf(row) {
  switch (row.kind) {
    case 'hermes':
      return [
        'Conversation turns and checkpoints, private to Hermes.',
        'Facts it chooses to keep, shared unless marked private.',
      ]
    case 'hermes-profile':
      return [
        'Conversation turns and checkpoints, private to this profile.',
        'Facts it chooses to keep, shared unless marked private.',
      ]
    case 'pi':
      return [
        <>
          Facts it saves with hal0&apos;s memory tools, tagged <Mono>agent:pi</Mono>.
        </>,
        <>
          Its Hindsight plugin writes each turn to a bank per repository, tagged{' '}
          <Mono>project:&lt;repository&gt;</Mono>.
        </>,
      ]
    case 'coding':
      return [
        <>
          A bank per repository, named <Mono>coding-agent::&lt;repository&gt;</Mono>, through the Hindsight
          plugin.
        </>,
      ]
    case 'client':
      return [
        <>
          May write shared facts, or private ones under its own name, through the same endpoint. hal0 tags
          them <Mono>agent:{row.id}</Mono>.
        </>,
      ]
    default:
      return ['Facts tagged with this name. Nothing else is known about the writer.']
  }
}

function Lines({ lines }) {
  return (
    <>
      {lines.map((line, at) => (
        <span className="ma-agents-line" key={at}>
          {line}
        </span>
      ))}
    </>
  )
}

function Where({ row }) {
  if (row.sightings.length === 0) {
    return (
      <span className="ma-agents-line">
        No facts tagged <Mono>agent:{row.id}</Mono> on any bank.
      </span>
    )
  }
  return (
    <>
      {row.sightings.map((s) => (
        <span className="ma-agents-line" key={`${s.bank}|${s.tag}`} data-testid="ma-agents-sighting">
          <Mono>{s.bank}</Mono>: {formatCount(s.count)} {s.count === 1 ? 'fact' : 'facts'} tagged{' '}
          <Mono>{s.tag}</Mono>
        </span>
      ))}
    </>
  )
}

const TONE = { 'seen writing': 'ok', connected: 'neutral', 'not seen': 'neutral' }

function Status({ row }) {
  const lastUsed = row.client?.last_seen_at ? formatRelativeTime(row.client.last_seen_at * 1000) : null
  return (
    <div className="ma-agents-status">
      <StatusChip tone={TONE[row.status]} data-testid={`ma-agents-status-${row.id}`}>
        {row.status}
      </StatusChip>
      {row.client && (
        <span className="ma-agents-note">
          Holds a key with the memory scope. {lastUsed ? `Key last used ${lastUsed}.` : 'Key not used yet.'}
        </span>
      )}
    </div>
  )
}

function Skeleton() {
  return (
    <div className="ma-card-body" aria-busy="true" data-testid="ma-agents-loading">
      {Array.from({ length: 4 }, (_, at) => (
        <span className="ma-skel ma-skel-line" aria-hidden="true" key={at} />
      ))}
      <span className="sr-only">Loading</span>
    </div>
  )
}

function CodingBanks({ route, banks, unattributed }) {
  if (banks.length === 0) return null
  return (
    <div className="ma-agents-banks" data-testid="ma-agents-coding-banks">
      <p className="ma-body">
        Coding agents have written to {formatCount(banks.length)}{' '}
        {banks.length === 1 ? 'bank' : 'banks'} through the Hindsight plugin:{' '}
        {banks.map((bank, at) => (
          <span key={bank}>
            {at > 0 && ', '}
            <a className="ma-link ma-mono" href={memoryTabHref('facts', { bank, project: route?.project ?? null })}>
              {bank}
            </a>
          </span>
        ))}
        .
      </p>
      {unattributed.length > 0 && (
        <p className="ma-body" data-testid="ma-agents-unattributed">
          {formatCount(unattributed.length)} of {banks.length === 1 ? 'this 1 bank' : `these ${formatCount(banks.length)} banks`}{' '}
          {unattributed.length === 1 ? 'holds' : 'hold'} no fact tagged with an agent&apos;s name, so no row
          above counts {unattributed.length === 1 ? 'it' : 'them'}. The plugin tags what it writes with an
          agent&apos;s name only where that was set up; the last card on this page says how.
        </p>
      )}
    </div>
  )
}

/**
 * @param {{
 *   state: 'loading'|'ready'|'unreachable'|'banks-error'|'tags-error',
 *   rows?: import('../agents-pure').AgentRow[],
 *   route?: object,
 *   codingBanks?: string[]|null,
 *   unattributed?: string[],
 *   failedBanks?: string[],
 *   bankListPartial?: boolean,
 *   onRecheck?: () => void,
 *   rechecking?: boolean,
 *   onGoToConnect?: () => void,
 * }} props `codingBanks` is null when the agents route did not answer.
 */
export function AgentsCard({
  state,
  rows = [],
  route,
  codingBanks = [],
  unattributed = [],
  failedBanks = [],
  bankListPartial = false,
  onRecheck,
  rechecking = false,
  onGoToConnect,
}) {
  const recheck = (
    <button
      type="button"
      className="btn ghost sm"
      disabled={rechecking}
      onClick={onRecheck}
      data-testid="ma-agents-recheck"
    >
      {rechecking ? 'Checking' : 'Check again'}
    </button>
  )

  let body
  let chip = null
  if (state === 'loading') {
    body = <Skeleton />
  } else if (state === 'unreachable') {
    chip = <StatusChip tone="err">unreachable</StatusChip>
    body = (
      <EmptyState title="The memory engine is not answering" data-testid="ma-agents-unreachable">
        Which agents have written is read from the engine&apos;s banks, so no agent is listed until it
        answers, and none is shown as not seen. Health says what is known and where to look.
      </EmptyState>
    )
  } else if (state === 'banks-error') {
    body = (
      <EmptyState title="The bank list did not load" action={recheck} data-testid="ma-agents-banks-error">
        The engine answered its health check but not the request for its banks, so there is no bank to look
        for agents in.
      </EmptyState>
    )
  } else if (state === 'tags-error') {
    body = (
      <EmptyState title="The banks' tags did not load" action={recheck} data-testid="ma-agents-tags-error">
        An agent is recognised by the tag on the facts it wrote. hal0 did not answer the request for any
        bank&apos;s tags, so no agent is listed, and none is shown as not seen.
      </EmptyState>
    )
  } else if (rows.length === 0) {
    body = (
      <EmptyState
        title="No agents seen yet"
        data-testid="ma-agents-empty"
        action={
          <button type="button" className="btn ghost sm" onClick={onGoToConnect} data-testid="ma-agents-to-connect">
            Go to Connect a coding agent
          </button>
        }
      >
        An agent appears here once hal0 has installed it on this box, once a key with the memory scope
        exists for it, or once a bank holds a fact tagged with its name. To connect a coding agent, use the
        command in the last card on this page.
      </EmptyState>
    )
  } else {
    body = (
      <div className="ma-table-region" role="region" aria-label="Agents table" tabIndex={0}>
        <table className="ma-table ma-agents-table" data-testid="ma-agents-table">
          <thead>
            <tr>
              <th scope="col">Agent</th>
              <th scope="col">Reads</th>
              <th scope="col">Writes</th>
              <th scope="col">Where its facts are</th>
              <th scope="col">Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.id} data-testid={`ma-agents-row-${row.id}`} data-status={row.status}>
                <th scope="row">
                  {row.name}
                  {row.idNote && <span className="ma-agents-id ma-mono">{row.idNote}</span>}
                </th>
                <td>
                  <Lines lines={readsOf(row)} />
                </td>
                <td>
                  <Lines lines={writesOf(row)} />
                </td>
                <td>
                  <Where row={row} />
                </td>
                <td>
                  <Status row={row} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  const listed = state === 'ready'
  return (
    <section className="card ma-card" aria-labelledby="ma-agents-h" data-testid="ma-agents-card" data-state={state}>
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id="ma-agents-h">
          Agents
        </h2>
        <span className="ma-card-gap" />
        {listed && rows.length > 0 && (
          <span className="ma-card-meta" data-testid="ma-agents-summary">
            {rowsSummary(rows)}
          </span>
        )}
        {chip}
      </div>
      {body}
      {listed && codingBanks === null && (
        <div className="ma-agents-banks" data-testid="ma-agents-coding-banks-error">
          <p className="ma-body">
            The list of banks written by coding agents did not load, so it is not shown here. The table above
            is read separately.
          </p>
        </div>
      )}
      {listed && codingBanks !== null && (
        <CodingBanks route={route} banks={codingBanks} unattributed={unattributed} />
      )}
      {listed && failedBanks.length > 0 && (
        <div className="ma-agents-banks" data-testid="ma-agents-partial">
          <p className="ma-body">
            The tags of {formatCount(failedBanks.length)} {failedBanks.length === 1 ? 'bank' : 'banks'} did not
            load, so facts there are in no count on this page:{' '}
            {failedBanks.map((bank, at) => (
              <span key={bank}>
                {at > 0 && ', '}
                <Mono>{bank}</Mono>
              </span>
            ))}
            .
          </p>
          {recheck}
        </div>
      )}
      {listed && rows.length > 0 && (
        <div className="ma-card-foot ma-agents-foot">
          <span>
            Each count is one tag on one bank: evidence that the agent has written there, not its total.
            Counts are not added across tags, because one fact can carry more than one.
          </span>
          <span>
            seen writing: a bank holds facts tagged with the agent&apos;s name. connected: a key with the memory
            scope exists for that name; it does not say the key has been used for memory. not seen: neither.
          </span>
          {bankListPartial && <span>Only the first page of the engine&apos;s bank list was read.</span>}
        </div>
      )}
    </section>
  )
}
