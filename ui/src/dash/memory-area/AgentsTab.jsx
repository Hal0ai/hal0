// Memory ▸ Agents: what reads and writes memory, and how to connect a coding agent.
//
// Not built yet. Until it is, this tab says so and links to the agents list and install command
// on the legacy tools page (`#memworkspace/agents`).
import './memory-area.css'
import { LegacyLink, StubCard } from './StubCard.jsx'

export function AgentsTab() {
  return (
    <StubCard
      heading="Agents"
      title="Agents are being moved here"
      action={
        <LegacyLink section="agents" data-testid="ma-agents-legacy">
          Open agents in legacy tools
        </LegacyLink>
      }
      data-testid="ma-agents-stub"
    >
      The list of connected coding agents and the command that installs the memory plugin are being moved
      into this tab. Until then they are on the legacy tools page, under Agents.
    </StubCard>
  )
}
