import { describe, expect, it } from 'vitest'
import { isManagedRemote, managedByLabel, type UpstreamEntry } from '../useUpstreams'

const up = (over: Partial<UpstreamEntry>) =>
  ({
    name: 'x',
    kind: 'remote',
    url: 'http://x/v1',
    slot_name: null,
    managed_by: null,
    ...over,
  }) as UpstreamEntry

describe('managedByLabel', () => {
  it('names the agent that owns a managed entry', () => {
    expect(managedByLabel(up({ name: 'hermes.coder', managed_by: 'agent:hermes' }))).toBe(
      'managed by agent:hermes',
    )
  })

  it('is null for an operator-authored entry', () => {
    expect(managedByLabel(up({ name: 'openrouter' }))).toBeNull()
    expect(managedByLabel(up({ name: 'legacy', managed_by: undefined }))).toBeNull()
  })

  it('keeps managed entries in the providers list', () => {
    expect(isManagedRemote(up({ name: 'hermes', managed_by: 'agent:hermes' }))).toBe(true)
  })
})
