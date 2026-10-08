// Connected accounts (OAuth) must be reachable from the dashboard (#2267).
//
// #2252 shipped ConnectedAccountsPanel, but it was only rendered by the
// legacy ConnectionsView, and main.jsx redirects #connections to
// #slots/endpoints — so no navigation path led to it. The panel now lives at
// Settings ▸ Integrations ▸ Connected Accounts (#settings/accounts).
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { afterEach, describe, expect, it } from 'vitest'

import { NAV_GROUPS, SECTION_ALIASES, VALID_IDS } from '../SettingsNav.jsx'
import { ConnectedAccountsPage } from '../pages/integrations/ConnectedAccountsPage.jsx'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React

type Win = { ConnectedAccountsPanel?: () => React.ReactElement }
const win = globalThis as unknown as Win

describe('Settings ▸ Integrations ▸ Connected Accounts (#2267)', () => {
  afterEach(() => {
    delete win.ConnectedAccountsPanel
  })

  it('lists Connected Accounts in the INTEGRATIONS nav group', () => {
    const integrations = NAV_GROUPS.find((g) => g.title === 'INTEGRATIONS')
    expect(integrations?.items.map((i) => i.id)).toContain('accounts')
    expect(VALID_IDS).toContain('accounts')
  })

  it('resolves the oauth alias to the accounts section', () => {
    expect(SECTION_ALIASES.oauth).toBe('accounts')
  })

  it('renders the window-registered ConnectedAccountsPanel', () => {
    win.ConnectedAccountsPanel = () => <div data-testid="oauth-panel">Connected accounts</div>
    const html = renderToStaticMarkup(<ConnectedAccountsPage />)
    expect(html).toContain('data-testid="oauth-panel"')
  })
})
