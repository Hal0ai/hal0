// hal0 v3 dashboard — Memory workspace view smoke tests.
import { describe, expect, it } from 'vitest'
import { hindsightConsoleUrl, tabFromParam } from '../MemoryWorkspaceView'

describe('tabFromParam', () => {
  it('defaults to overview for null/unknown params', () => {
    expect(tabFromParam(null)).toBe('overview')
    expect(tabFromParam('bogus')).toBe('overview')
  })

  it('passes through known tabs', () => {
    expect(tabFromParam('kb')).toBe('kb')
    expect(tabFromParam('mental-models')).toBe('mental-models')
    expect(tabFromParam('directives')).toBe('directives')
    expect(tabFromParam('operations')).toBe('operations')
    expect(tabFromParam('agents')).toBe('agents')
    expect(tabFromParam('models')).toBe('models')
  })
})

describe('hindsightConsoleUrl', () => {
  it('builds a control-plane URL on the fixed default port', () => {
    expect(hindsightConsoleUrl('hal0.local')).toBe('http://hal0.local:9999')
  })
})
