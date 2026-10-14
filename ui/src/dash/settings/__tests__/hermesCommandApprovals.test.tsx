// Settings ▸ Security ▸ Hermes command approvals (Hermes approvals.mode).
//
// The view must show the operator setting, say plainly when overrides.yaml
// wins over it, and keep the smart/manual help text honest (smart falls back
// to asking a human) and separate from hal0's MCP approval queue.
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { HermesCommandApprovalsView } from '../pages/server/HermesCommandApprovalsPanel.jsx'
import {
  COMMAND_APPROVALS_INTRO,
  COMMAND_APPROVALS_MODES,
  commandApprovalsNotices,
} from '../pages/server/commandApprovals.js'
import { ENDPOINTS } from '@/api/endpoints'

;(globalThis as unknown as { React: typeof React }).React = React

const base = {
  agent_id: 'hermes',
  mode: 'smart',
  effective: 'smart',
  source: 'default',
  default: 'smart',
  allowed: ['smart', 'manual'],
  invalid_value: null,
  overrides_path: '/etc/hal0/agents/hermes/overrides.yaml',
  rendered: 'smart',
  in_sync: true,
}

const render = (props: Record<string, unknown>) =>
  renderToStaticMarkup(<HermesCommandApprovalsView {...props} />)

describe('Hermes command approvals panel', () => {
  it('targets the hermes command-approvals route', () => {
    expect(ENDPOINTS.agentCommandApprovals('hermes')).toBe('/api/agents/hermes/command-approvals')
  })

  it('marks the active mode and shows its help text', () => {
    const html = render({ status: base })
    expect(html).toMatch(/data-testid="cmd-approvals-smart"[^>]*aria-checked="true"|aria-checked="true"[^>]*data-testid="cmd-approvals-smart"/)
    expect(html).toContain('falls back to asking you')
    expect(html).toContain('>default<')
  })

  it('explains smart, manual and the MCP queue distinction', () => {
    const smart = COMMAND_APPROVALS_MODES.find((m) => m.id === 'smart')!
    expect(smart.help).toMatch(/fails or answers anything but APPROVE/)
    expect(smart.help).toMatch(/asking you/)
    expect(COMMAND_APPROVALS_MODES.map((m) => m.id)).toEqual(['smart', 'manual'])
    expect(COMMAND_APPROVALS_INTRO).toMatch(/separate from hal0's approval queue for gated MCP tool calls/)
    expect(COMMAND_APPROVALS_INTRO).toMatch(/terminal tool is on/)
  })

  it('says when overrides.yaml wins over the setting', () => {
    const status = { ...base, mode: 'manual', effective: 'smart', source: 'overrides' }
    const html = render({ status })
    expect(html).toContain('data-testid="cmd-approvals-overridden"')
    expect(html).toContain('In effect: smart')
    expect(html).toContain('/etc/hal0/agents/hermes/overrides.yaml')
    // The selected control is still the operator's setting, not the override.
    expect(html).toMatch(/aria-checked="true"[^>]*data-testid="cmd-approvals-manual"|data-testid="cmd-approvals-manual"[^>]*aria-checked="true"/)
  })

  it('reports drift, an invalid stored value and an unprovisioned agent', () => {
    expect(commandApprovalsNotices({ ...base, rendered: 'off', in_sync: false }).map((n) => n.testid)).toEqual([
      'cmd-approvals-drift',
    ])
    expect(commandApprovalsNotices({ ...base, invalid_value: 'bogus' })[0].text).toContain('"bogus" is invalid')
    expect(commandApprovalsNotices({ ...base, rendered: null, in_sync: null }).map((n) => n.testid)).toEqual([
      'cmd-approvals-not-provisioned',
    ])
    expect(commandApprovalsNotices(undefined)).toEqual([])
  })

  it('disables the controls while applying and shows save errors', () => {
    const html = render({ status: base, pending: true, saveError: 'Hermes restart failed' })
    expect(html).toContain('Applying…')
    expect(html).toContain('data-testid="cmd-approvals-save-error"')
    expect(html).not.toContain('data-testid="cmd-approvals-saved"')
  })

  it('shows a load error without enabling the controls', () => {
    const html = render({ status: undefined, error: 'API error 401' })
    expect(html).toContain('Could not read the setting: API error 401')
    expect((html.match(/disabled=""/g) || []).length).toBe(2)
  })
})
