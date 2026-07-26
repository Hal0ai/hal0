import { describe, expect, it } from 'vitest'

import { UPDATE_CHANNEL_NAMES } from '../../../../api/hooks/useUpdates'

describe('UpdatesPage channel selector', () => {
  it('has preview in the accepted UI channel vocabulary', () => {
    expect(UPDATE_CHANNEL_NAMES).toEqual(['stable', 'preview', 'nightly'])
  })
})
