import { describe, expect, it } from 'vitest'
import { isManagedRemote, type UpstreamEntry } from '../useUpstreams'

const up = (over: Partial<UpstreamEntry>) =>
  ({
    name: 'x',
    kind: 'remote',
    url: 'http://x/v1',
    slot_name: null,
    managed_by: null,
    ...over,
  }) as UpstreamEntry

describe('isManagedRemote', () => {
  it('keeps managed entries in the providers list', () => {
    expect(isManagedRemote(up({ name: 'hermes', managed_by: 'agent:hermes' }))).toBe(true)
  })
})
