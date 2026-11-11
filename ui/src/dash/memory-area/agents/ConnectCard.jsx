// "Connect a coding agent": pick the agent, copy the command, and read what the command does.
//
// The command is the one `GET /api/memory/agents` builds, with the chosen agent's name put in
// place of its `<harness>` slot. Nothing about it is assembled here. The sentences under it say
// what running it does today: it installs the Hindsight plugin for that agent and points the
// plugin at this box's memory engine, which the plugin then talks to directly.
import { useId, useState } from 'react'
import '../memory-area.css'
import { RichSelect } from '../../rich-select'
import { commandApiUrl, harnessName, installCommandFor, isLoopbackUrl } from '../agents-pure'
import { CommandBox } from '../shared/CommandBox.jsx'
import { EmptyState } from '../shared/EmptyState.jsx'

const Mono = ({ children }) => <span className="ma-mono ma-em">{children}</span>

/**
 * @param {{
 *   agents: ReturnType<typeof import('../agents-pure').readAgentsRoute>,
 *   onRecheck?: () => void,
 *   rechecking?: boolean,
 *   headingRef?: import('react').Ref<HTMLHeadingElement>,
 * }} props `agents` is the agents route's answer as `readAgentsRoute` read it.
 */
export function ConnectCard({ agents, onRecheck, rechecking = false, headingRef }) {
  const labelId = useId()
  const [picked, setPicked] = useState(null)

  let body
  if (agents.kind === 'loading') {
    body = (
      <div className="ma-card-body" aria-busy="true" data-testid="ma-agents-connect-loading">
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="ma-skel ma-skel-line" aria-hidden="true" />
        <span className="sr-only">Loading</span>
      </div>
    )
  } else if (agents.kind === 'failed' || agents.harnesses.length === 0) {
    body = (
      <EmptyState
        title="The install command did not load"
        data-testid="ma-agents-connect-error"
        action={
          <button
            type="button"
            className="btn ghost sm"
            disabled={rechecking}
            onClick={onRecheck}
            data-testid="ma-agents-connect-recheck"
          >
            {rechecking ? 'Checking' : 'Check again'}
          </button>
        }
      >
        hal0 did not answer the request for the command, so none is shown here. Nothing on this box has
        changed.
      </EmptyState>
    )
  } else {
    const harness = picked && agents.harnesses.includes(picked) ? picked : agents.harnesses[0]
    const name = harnessName(harness)
    const command = installCommandFor(agents.command, harness)
    const apiUrl = commandApiUrl(command)
    const loopback = isLoopbackUrl(apiUrl)
    body = (
      <div className="ma-card-body ma-agents-connect-body">
        <p className="ma-body">
          Choose the coding agent, then run the command on the machine where that agent runs, as the user
          who runs it.
        </p>
        <div className="ma-agents-pick">
          <span className="ma-eyebrow" id={labelId}>
            Coding agent
          </span>
          <div className="ma-agents-pick-select">
            <RichSelect
              aria-label="Coding agent"
              value={harness}
              options={agents.harnesses.map((id) => ({
                id,
                row: harnessName(id),
                right: harnessName(id) === id ? undefined : <span className="ma-mono">{id}</span>,
              }))}
              onChange={setPicked}
              data-testid="ma-agents-harness"
            />
          </div>
        </div>
        <CommandBox command={command} copyLabel={`Copy the command for ${name}`} data-testid="ma-agents-command" />
        <p className="ma-body" data-testid="ma-agents-connect-what">
          The command runs the Hindsight coding-agents installer, which adds the Hindsight memory plugin to{' '}
          <span className="ma-em">{name}</span>
          {apiUrl ? (
            <>
              {' '}
              and points it at this box&apos;s memory engine at <Mono>{apiUrl}</Mono>
            </>
          ) : (
            " and points it at this box's memory engine"
          )}
          . From then on the plugin talks to the engine directly, not through hal0, and keeps what it writes
          in one bank per repository, named <Mono>coding-agent::&lt;repository&gt;</Mono>.
        </p>
        {apiUrl && (
          <p className="ma-body" data-testid="ma-agents-connect-reach" data-loopback={loopback ? 'true' : 'false'}>
            {loopback ? (
              <>
                That address is the box&apos;s own loopback address. It reaches the engine only from this box, so
                an agent on another machine cannot use the command as it is.
              </>
            ) : (
              <>An agent on another machine must be able to reach that address for the plugin to work.</>
            )}
          </p>
        )}
        <p className="ma-body" data-testid="ma-agents-connect-cli">
          On this box, <Mono>hal0 memory agents install {harness}</Mono> runs the same installer, then writes the
          plugin&apos;s settings for the user who ran it with the tag <Mono>agent:{harness}</Mono> added to what the
          plugin tags its facts with. That tag is what the Agents table counts.
        </p>
        <p className="ma-body">
          Hermes, and Pi installed by hal0, need no command: hal0 connects them to memory when it installs
          them.
        </p>
      </div>
    )
  }

  return (
    <section
      className="card ma-card ma-agents-connect"
      aria-labelledby="ma-agents-connect-h"
      data-testid="ma-agents-connect"
    >
      <div className="ma-card-head">
        <h2 className="ma-eyebrow" id="ma-agents-connect-h" tabIndex={-1} ref={headingRef}>
          Connect a coding agent
        </h2>
      </div>
      {body}
    </section>
  )
}
