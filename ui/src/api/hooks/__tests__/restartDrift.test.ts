import { describe, expect, it } from 'vitest'
import { restartDriftMessages } from '../useUpdates'

describe('restart drift outcomes', () => {
  it('reports each unsuccessful outcome and its reason', () => {
    expect(restartDriftMessages({ restarted: ['ok'], count: 1, failed: [{ slot: 'chat', error: 'unit failed' }], skipped_busy: ['embed'], skipped_render_error: ['brain'] })).toEqual([
      'Failed to restart chat: unit failed',
      'Skipped busy slots: embed',
      'Skipped slots whose launch configuration could not be rendered: brain. Fix their configuration before restarting.',
    ])
  })
  it('does not report a problem for a successful sweep', () => {
    expect(restartDriftMessages({ restarted: ['chat'], count: 1, failed: [], skipped_busy: [], skipped_render_error: [] })).toEqual([])
  })
})
