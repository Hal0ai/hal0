// "Built into hal0": the parts of hal0 itself that touch memory, and the callout for facts whose
// writer hal0 could not name.
//
// These rows carry no status chip. A part of hal0 is not something that connects or fails to;
// what it reads and writes is fixed by the code, and the only thing that varies is how many
// facts carry its tag.
import '../memory-area.css'
import { BRAIN_ID, OPENWEBUI_ID, UNKNOWN_TAG } from '../agents-pure'
import { memoryTabHref } from '../memory-route-pure'
import { GoLink } from '../shared/GoLink.jsx'
import { StatStrip } from '../shared/StatStrip.jsx'
import { formatCount } from '../shared/format-pure'

const Mono = ({ children }) => <span className="ma-mono ma-em">{children}</span>

/** How many facts carry a part's tag: a skeleton while loading, a dash when it is not known. */
function Tagged({ id, sightings, state }) {
  if (state === 'loading') {
    return (
      <>
        <span className="ma-skel ma-skel-cell" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </>
    )
  }
  if (state !== 'ready') {
    return (
      <span className="ma-missing" role="img" aria-label="not reported">
        –
      </span>
    )
  }
  if (sightings.length === 0) {
    return (
      <span className="ma-agents-line">
        No facts tagged <Mono>agent:{id}</Mono> on any bank.
      </span>
    )
  }
  return (
    <>
      {sightings.map((s) => (
        <span className="ma-agents-line" key={`${s.bank}|${s.tag}`}>
          <Mono>{s.bank}</Mono>: {formatCount(s.count)} {s.count === 1 ? 'fact' : 'facts'} tagged{' '}
          <Mono>{s.tag}</Mono>
        </span>
      ))}
    </>
  )
}

/**
 * @param {{
 *   state: 'loading'|'ready'|'unknown',
 *   brain?: import('../agents-pure').Sighting[],
 *   openwebui?: import('../agents-pure').Sighting[],
 *   hasUnknown?: boolean,
 * }} props `state` is whether the tag counts are known: `unknown` when they could not be read.
 */
export function BuiltInCard({ state, brain = [], openwebui = [], hasUnknown = false }) {
  return (
    <section className="card ma-card" aria-labelledby="ma-agents-builtin-h" data-testid="ma-agents-builtin">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id="ma-agents-builtin-h">
          Built into hal0
        </h2>
        <span className="ma-card-gap" />
        <span className="ma-card-meta">3 parts of hal0</span>
      </div>
      <div className="ma-table-region" role="region" aria-label="Built into hal0 table" tabIndex={0}>
        <table className="ma-table ma-agents-table ma-agents-builtin-table">
          <thead>
            <tr>
              <th scope="col">Part</th>
              <th scope="col">Reads</th>
              <th scope="col">Writes</th>
              <th scope="col">Facts carrying its tag</th>
            </tr>
          </thead>
          <tbody>
            <tr data-testid="ma-agents-builtin-dashboard">
              <th scope="row">
                Dashboard
                <span className="ma-agents-id">you, on the Facts tab</span>
              </th>
              <td>
                <span className="ma-agents-line">Every bank, to show its facts.</span>
              </td>
              <td>
                <span className="ma-agents-line">
                  Facts you add by hand, and the changes you make when you curate one.
                </span>
                <span className="ma-agents-line">
                  It sends no agent name, so a fact added here is tagged <Mono>{UNKNOWN_TAG}</Mono>.
                </span>
              </td>
              <td>
                <span className="ma-agents-line">
                  {hasUnknown ? (
                    <>
                      Counted with the facts tagged <Mono>{UNKNOWN_TAG}</Mono> below.
                    </>
                  ) : (
                    'It has no tag of its own.'
                  )}
                </span>
              </td>
            </tr>
            <tr data-testid="ma-agents-builtin-brain">
              <th scope="row">
                Brain chat
                <span className="ma-agents-id">the assistant in the chat drawer</span>
              </th>
              <td>
                <span className="ma-agents-line">
                  Searches and lists saved facts when it chooses to, with its memory tools.
                </span>
              </td>
              <td>
                <span className="ma-agents-line">
                  One fact each time it uses its save tool, and nothing otherwise. It does not keep its
                  conversations.
                </span>
                <span className="ma-agents-line">
                  Saved to shared memory under the name <Mono>{BRAIN_ID}</Mono>, or kept private when its
                  profile is set to.
                </span>
              </td>
              <td data-testid="ma-agents-builtin-brain-count">
                <Tagged id={BRAIN_ID} sightings={brain} state={state} />
              </td>
            </tr>
            <tr data-testid="ma-agents-builtin-openwebui">
              <th scope="row">
                Open WebUI
                <span className="ma-agents-id">the chat companion</span>
              </th>
              <td>
                <span className="ma-agents-line">
                  Shared memory only, as a context source before a chat turn with a model that runs on this
                  box.
                </span>
              </td>
              <td>
                <span className="ma-agents-line">The context source does not write.</span>
                <span className="ma-agents-line">
                  A model in Open WebUI can save a fact with hal0&apos;s memory tools; hal0 tags it{' '}
                  <Mono>agent:{OPENWEBUI_ID}</Mono>.
                </span>
              </td>
              <td data-testid="ma-agents-builtin-openwebui-count">
                <Tagged id={OPENWEBUI_ID} sightings={openwebui} state={state} />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  )
}

/**
 * Facts tagged `agent:unknown`: how many, on which banks, and what the tag means.
 *
 * @param {{ summary: import('../agents-pure').UnknownSummary, route?: object }} props
 */
export function UnknownCard({ summary, route }) {
  const top = summary.banks[0]
  const one = summary.banks.length === 1
  return (
    <section className="card ma-card" aria-labelledby="ma-agents-unknown-h" data-testid="ma-agents-unknown">
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id="ma-agents-unknown-h">
          Facts with no known writer
        </h2>
      </div>
      <StatStrip
        label="Facts with no known writer"
        inCard
        cells={[
          {
            id: 'unknown',
            label: `Tagged ${UNKNOWN_TAG}`,
            value: summary.total,
            unit: summary.total === 1 ? 'fact' : 'facts',
            helper: one ? (
              <>
                in the bank <span className="ma-mono">{top.bank}</span>
              </>
            ) : (
              `on ${formatCount(summary.banks.length)} banks`
            ),
          },
        ]}
        data-testid="ma-agents-unknown-strip"
      />
      <div className="ma-card-body ma-agents-unknown-body">
        <p className="ma-body">
          These facts were written through hal0 by a caller that sent no agent name, so hal0 could not say
          who wrote them and tagged them <Mono>{UNKNOWN_TAG}</Mono>. Facts added by hand from this dashboard
          are among them.
        </p>
        {!one && (
          <ul className="ma-agents-unknown-banks" aria-label="Facts with no known writer, by bank">
            {summary.banks.map((b) => (
              <li key={b.bank} data-testid="ma-agents-unknown-bank">
                <Mono>{b.bank}</Mono>: {formatCount(b.count)} {b.count === 1 ? 'fact' : 'facts'}
              </li>
            ))}
          </ul>
        )}
        <GoLink
          href={memoryTabHref('facts', { bank: top.bank, project: route?.project ?? null })}
          data-testid="ma-agents-unknown-facts"
        >
          Open {top.bank} in Facts
        </GoLink>
      </div>
    </section>
  )
}
