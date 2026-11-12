// "Connect a coding agent": pick the agent, copy the command, and read what the command does.
//
// The commands are the ones `GET /api/memory/agents` builds, with the chosen agent's name put in
// place of their `<harness>` slot. Nothing about them is assembled here. Two steps: mint a bridge
// key for the agent, then install the plugin pointed at hal0's memory endpoint with that key.
import { useId, useState } from 'react'
import '../memory-area.css'
import { RichSelect } from '../../rich-select'
import { harnessName, installCommandFor } from '../agents-pure'
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
    const mintCommand = installCommandFor(agents.mintCommand, harness)
    const keyFile = agents.keyFile || 'the key file'
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
        {mintCommand && (
          <>
            <p className="ma-body">
              1. Mint a bridge key for <span className="ma-em">{name}</span>. The command prints it once.
            </p>
            <CommandBox
              command={mintCommand}
              copyLabel={`Copy the key command for ${name}`}
              data-testid="ma-agents-mint-command"
            />
          </>
        )}
        <p className="ma-body" data-testid="ma-agents-connect-save">
          2. Save the key, and nothing else, to <Mono>{keyFile}</Mono> on the machine where {name} runs.
        </p>
        <p className="ma-body">3. Install the plugin with that file.</p>
        <CommandBox command={command} copyLabel={`Copy the command for ${name}`} data-testid="ma-agents-command" />
        <p className="ma-body" data-testid="ma-agents-connect-what">
          The command installs the Hindsight memory plugin for <span className="ma-em">{name}</span> and points it
          at hal0&apos;s <Mono>/api/memory/compat</Mono> with the key, so what it writes goes through hal0&apos;s
          write envelope and is read back by project. It keeps what it writes in one bank per repository, named{' '}
          <Mono>coding-agent::&lt;repository&gt;</Mono>.
        </p>
        <p className="ma-body" data-testid="ma-agents-connect-cli">
          The command runs where <Mono>hal0</Mono> can reach this box, as the user who runs the agent. It writes the
          plugin&apos;s settings for that user with the tag <Mono>agent:{harness}</Mono> added to what the plugin tags
          its facts with. That tag is what the Agents table counts. The key also carries the agents scope, so it can
          start Hermes runs: keep the file private.
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
