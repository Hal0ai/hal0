// #2382 — the pure half of the Settings ▸ Network panel.

import { describe, expect, it } from 'vitest'
import { NAV_GROUPS, VALID_IDS } from '../../SettingsNav.jsx'
import {
  networkFormDirty,
  networkFormsEqual,
  networkEditBody,
  networkFormFromSettings,
  networkSaveError,
  parseOriginLines,
} from './networkOrigins.js'

describe('network origins form', () => {
  it('reads a box with no [network] section as empty', () => {
    expect(networkFormFromSettings({ security: {} })).toEqual({ publicUrl: '', origins: '' })
    expect(networkFormFromSettings(null)).toEqual({ publicUrl: '', origins: '' })
  })

  it('sends nothing when the form matches the live section', () => {
    const live = { network: { public_url: 'https://hal0.example.com', extra_origins: ['http://box.lan:8080', 'https://b.example'] } }
    const form = networkFormFromSettings(live)
    expect(networkEditBody(form, live)).toBeNull()
    expect(networkFormDirty(form, live)).toBe(false)
  })

  it('sends origins as a diff, never the whole list', () => {
    const live = { network: { public_url: null, extra_origins: ['http://a:1', 'https://b'] } }
    // b removed, c added, a untouched: an origin someone else added to the
    // list on disk since this page loaded is not in the body, so it stays.
    expect(networkEditBody({ publicUrl: '', origins: 'http://a:1\n\n https://c ' }, live)).toEqual({
      add_origins: ['https://c'],
      remove_origins: ['https://b'],
    })
    expect(parseOriginLines('')).toEqual([])
  })

  it('sets public_url, or clears it with null', () => {
    const live = { network: { public_url: 'https://old.example', extra_origins: [] } }
    expect(networkEditBody({ publicUrl: ' https://new.example ', origins: '' }, live)).toEqual({ public_url: 'https://new.example' })
    expect(networkEditBody({ publicUrl: '  ', origins: '' }, live)).toEqual({ public_url: null })
  })

  it('marks an edit dirty', () => {
    const live = { network: { public_url: null, extra_origins: [] } }
    expect(networkFormDirty({ publicUrl: 'https://x', origins: '' }, live)).toBe(true)
    expect(networkFormDirty({ publicUrl: '', origins: 'http://a:1' }, live)).toBe(true)
  })

  it('surfaces the per-field validation reason', () => {
    const err = Object.assign(new Error('hal0 config failed schema validation'), {
      details: { 'network.extra_origins': "Value error, origin 'https://x/p' must be scheme://host[:port] only" },
    })
    expect(networkSaveError(err)).toBe("origin 'https://x/p' must be scheme://host[:port] only")
    expect(networkSaveError(new Error('boom'))).toBe('boom')
  })

  it('compares forms by normalised content', () => {
    expect(networkFormsEqual({ publicUrl: ' https://x ', origins: 'a\n\nb' }, { publicUrl: 'https://x', origins: 'a, b' })).toBe(true)
    expect(networkFormsEqual({ publicUrl: '', origins: 'a' }, { publicUrl: '', origins: 'a\nb' })).toBe(false)
  })
})

describe('Settings ▸ Network route', () => {
  it('is its own page in the GENERAL group, not a panel on Security', () => {
    const general = NAV_GROUPS.find((g: any) => g.title === 'GENERAL')
    expect(general?.items.map((i: any) => i.id)).toEqual(['overview', 'security', 'network', 'doctor'])
    expect(VALID_IDS).toContain('network')
  })
})
