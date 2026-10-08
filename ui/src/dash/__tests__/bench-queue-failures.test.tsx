// Run Queue "recent failures" (#2387): the bench worker records a queued
// model it cannot resolve in failed.json (control.fail) and
// GET /api/benchmarks/queue returns it under `failed`. This pins that the
// dashboard renders each failed item's outcome and note, and renders nothing
// when the list is empty. Server-side render, no DOM needed.
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { QueueFailures } from '../Benchmarks'

describe('QueueFailures', () => {
  it('shows the outcome, note, label and time of a failed item', () => {
    const html = renderToStaticMarkup(
      <QueueFailures
        failed={[
          {
            id: 'ab12cd34',
            label: 'model.gguf',
            suite: null,
            model: 'model.gguf',
            enqueued: '2026-10-08T09:00:00Z',
            outcome: 'failed',
            note: 'ambiguous model reference',
            failed_at: '2026-10-08T09:05:00Z',
          },
        ]}
      />,
    )
    expect(html).toContain('recent failures')
    expect(html).toContain('model.gguf')
    expect(html).toContain('failed')
    expect(html).toContain('ambiguous model reference')
    expect(html).toContain('09:05')
  })

  it('renders nothing when there are no failures', () => {
    expect(renderToStaticMarkup(<QueueFailures failed={[]} />)).toBe('')
  })
})
