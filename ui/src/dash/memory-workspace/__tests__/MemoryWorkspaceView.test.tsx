// hal0 v3 dashboard — Memory workspace view smoke tests.
import { describe, expect, it } from 'vitest'
import { hindsightConsoleUrl, resolveConsoleUrl, tabFromParam } from '../MemoryWorkspaceView'

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

describe('resolveConsoleUrl', () => {
  it('uses the config-driven console_url when present', () => {
    expect(resolveConsoleUrl('http://console.example:9001', 'hal0.local')).toBe(
      'http://console.example:9001',
    )
  })

  it('returns null (hides link) when console_url is explicitly null', () => {
    expect(resolveConsoleUrl(null, 'hal0.local')).toBeNull()
  })

  it('falls back to the fixed-port default when the field is absent', () => {
    expect(resolveConsoleUrl(undefined, 'hal0.local')).toBe('http://hal0.local:9999')
  })
})
