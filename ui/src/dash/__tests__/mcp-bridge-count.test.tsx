// @vitest-environment happy-dom
//
// The MCP server list (connections.jsx McpServersPanel, the Agents ▸ MCP tab) shows how many
// bridge clients can reach this box: a "N bridge clients" link to #agent/mcp on the hal0-agents
// row, or in the section strip when that row is not listed. Revoked clients are not counted.
// A real render of the panel; the data hooks are mocked at the module boundary.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React

const { box } = vi.hoisted(() => ({
  box: { servers: [] as unknown[], clients: undefined as unknown },
}))

vi.mock('@/api/hooks/useMcp', () => ({
  useMcpServers: () => ({ data: box.servers, isPending: false }),
}))
vi.mock('@/api/hooks/useConfigUrls', () => ({ useConfigUrls: () => ({ data: undefined }) }))
vi.mock('@/api/hooks/useBridge', () => ({
  useBridgeClients: () => ({ data: box.clients }),
}))

await import('../connections.jsx')

const server = (name: string) => ({
  id: name,
  name,
  bundled: true,
  state: 'running',
  transport: 'streamable-http',
  connect_url: `http://hal0.lan/mcp/${name.replace('hal0-', '')}`,
  tools: 3,
  tool_details: [],
  resources: 0,
  prompts: 0,
  connected: [],
  description: `${name} server`,
})

const client = (name: string, revoked = false) => ({
  name,
  note: '',
  scopes: ['agents'],
  created_at: 1,
  created_by: 'cli',
  revoked_at: revoked ? 2 : null,
  last_seen_at: null,
})

let root: Root | null = null
let container: HTMLElement

async function mount() {
  const Panel = (window as unknown as { McpServersPanel: React.ComponentType }).McpServersPanel
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root!.render(<Panel />)
  })
}

const counts = () => Array.from(container.querySelectorAll('[data-testid="bridge-client-count"]')) as HTMLElement[]

beforeEach(() => {
  ;(window as unknown as { Icon: unknown }).Icon = () => null
  box.servers = [server('hal0-admin'), server('hal0-agents'), server('hal0-memory')]
  box.clients = { items: [client('laptop'), client('phone'), client('old', true)] }
})

afterEach(() => {
  if (root) act(() => root!.unmount())
  root = null
  document.body.innerHTML = ''
})

describe('McpServersPanel bridge-client count', () => {
  it('sits on the hal0-agents row as a plain label, and leaves revoked clients out', async () => {
    await mount()
    const chips = counts()
    expect(chips).toHaveLength(1)
    expect(chips[0].textContent).toBe('2 bridge clients')
    // the panel is the MCP tab, so a link to #agent/mcp would do nothing
    expect(chips[0].tagName).toBe('SPAN')
    expect(chips[0].getAttribute('href')).toBeNull()
    expect(chips[0].closest('.mcprow')!.querySelector('.nm')!.textContent).toBe('hal0-agents')
  })

  it('reads "1 bridge client" for one', async () => {
    box.clients = { items: [client('laptop'), client('old', true)] }
    await mount()
    expect(counts()[0].textContent).toBe('1 bridge client')
  })

  it('moves to the section strip when hal0-agents is not listed', async () => {
    box.servers = [server('hal0-admin'), server('hal0-memory')]
    await mount()
    const chips = counts()
    expect(chips).toHaveLength(1)
    expect(chips[0].textContent).toBe('2 bridge clients')
    expect(chips[0].closest('.mcprow')).toBeNull()
    expect(chips[0].closest('.cpane-strip')).not.toBeNull()
  })

  it('shows nothing until the clients have been read', async () => {
    box.clients = undefined
    await mount()
    expect(counts()).toHaveLength(0)
  })
})
