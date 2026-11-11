// Memory ▸ Knowledge: knowledge pages, mental models and directives.
//
// Not built yet. Until it is, this tab says so and links to where each of the three lives
// today, on the legacy tools page (`#memworkspace`).
import './memory-area.css'
import { LegacyLink, StubCard } from './StubCard.jsx'

export function KnowledgeTab() {
  return (
    <StubCard
      heading="Knowledge"
      title="Knowledge is being moved here"
      action={
        <LegacyLink section="kb" data-testid="ma-knowledge-legacy">
          Open the knowledge base in legacy tools
        </LegacyLink>
      }
      data-testid="ma-knowledge-stub"
    >
      Knowledge pages, mental models and directives are being moved into this tab. Until then they are
      on the legacy tools page, under Knowledge base,{' '}
      <a className="ma-link" href="#memworkspace/mental-models">
        Mental models
      </a>{' '}
      and{' '}
      <a className="ma-link" href="#memworkspace/directives">
        Directives
      </a>
      .
    </StubCard>
  )
}
