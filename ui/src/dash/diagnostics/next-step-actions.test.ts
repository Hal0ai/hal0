// @vitest-environment happy-dom
//
// The rest of the unit suite runs in the `node` environment (vitest.config.ts)
// since nothing else touches the DOM; `openDocTarget` needs a real `window`
// for the window.open/location.hash assertions below, so this file opts into
// happy-dom on its own rather than paying the DOM setup cost project-wide.
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest'
import { actionForNextStep, isExternalDocTarget, openDocTarget } from './next-step-actions'

describe('actionForNextStep', () => {
  it('maps the four repairable-service restart commands', () => {
    expect(actionForNextStep({ kind: 'command', target: 'systemctl restart hal0-api' })).toEqual({
      kind: 'serviceRestart',
      unit: 'hal0-api.service',
    })
    expect(
      actionForNextStep({ kind: 'command', target: 'systemctl restart hindsight-api' }),
    ).toEqual({ kind: 'serviceRestart', unit: 'hindsight-api.service' })
    expect(
      actionForNextStep({ kind: 'command', target: 'systemctl restart hal0-openwebui' }),
    ).toEqual({ kind: 'serviceRestart', unit: 'hal0-openwebui.service' })
    expect(
      actionForNextStep({ kind: 'command', target: 'systemctl restart hal0-agent@hermes' }),
    ).toEqual({ kind: 'serviceRestart', unit: 'hal0-agent@hermes.service' })
  })

  it('maps a per-slot restart command', () => {
    expect(actionForNextStep({ kind: 'command', target: 'hal0 slot restart chat' })).toEqual({
      kind: 'slotRestart',
      slot: 'chat',
    })
  })

  it('returns null for an unmapped command', () => {
    expect(actionForNextStep({ kind: 'command', target: 'hal0 slot create' })).toBeNull()
    expect(actionForNextStep({ kind: 'command', target: 'hal0 serve' })).toBeNull()
  })

  it('returns null for non-command kinds regardless of target text', () => {
    expect(actionForNextStep({ kind: 'doc', target: 'systemctl restart hal0-api' })).toBeNull()
    expect(actionForNextStep({ kind: 'manual', target: 'systemctl restart hal0-api' })).toBeNull()
  })
})

describe('isExternalDocTarget', () => {
  it('recognises absolute http(s) urls only', () => {
    expect(isExternalDocTarget('https://hal0.dev/docs')).toBe(true)
    expect(isExternalDocTarget('http://hal0.dev/docs')).toBe(true)
    expect(isExternalDocTarget('/docs/guides/manage-slots')).toBe(false)
    expect(isExternalDocTarget('#settings/updates')).toBe(false)
  })
})

describe('openDocTarget', () => {
  const originalOpen = window.open
  let openSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    openSpy = vi.fn()
    window.open = openSpy as unknown as typeof window.open
    window.location.hash = ''
  })

  afterEach(() => {
    window.open = originalOpen
  })

  it('opens an external url directly in a new tab', () => {
    openDocTarget('https://hal0.dev/changelog')
    expect(openSpy).toHaveBeenCalledWith('https://hal0.dev/changelog', '_blank', 'noopener')
  })

  it('navigates an in-app hash route without opening a tab', () => {
    openDocTarget('#settings/updates')
    expect(window.location.hash).toBe('#settings/updates')
    expect(openSpy).not.toHaveBeenCalled()
  })

  it('resolves a docs-site path against hal0.dev', () => {
    openDocTarget('/docs/operate/services/#mdns-discovery-toggle')
    expect(openSpy).toHaveBeenCalledWith(
      'https://hal0.dev/docs/operate/services/#mdns-discovery-toggle',
      '_blank',
      'noopener',
    )
  })
})
