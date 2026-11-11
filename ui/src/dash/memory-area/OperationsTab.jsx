// Memory ▸ Operations: the engine's queue (what is waiting, running and failed) and its retries.
//
// Not built yet. Until it is, this tab says so and links to the operations list on the legacy
// tools page (`#memworkspace/operations`).
import './memory-area.css'
import { LegacyLink, StubCard } from './StubCard.jsx'

export function OperationsTab() {
  return (
    <StubCard
      heading="Operations"
      title="Operations are being moved here"
      action={
        <LegacyLink section="operations" data-testid="ma-operations-legacy">
          Open operations in legacy tools
        </LegacyLink>
      }
      data-testid="ma-operations-stub"
    >
      The queue of waiting, running and failed operations, with retry, is being moved into this tab.
      Until then it is on the legacy tools page, under Operations.
    </StubCard>
  )
}
