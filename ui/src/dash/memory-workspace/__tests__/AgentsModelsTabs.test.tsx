// @vitest-environment happy-dom
//
// AgentsTab + ModelsTab component smoke tests: mount with createRoot under a
// QueryClientProvider, mock apiGet/apiPatch at the client boundary (same
// harness as agentOverviewHooks.test.tsx).
import React, { act } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { window: typeof globalThis }).window = globalThis
;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

vi.mock('@/api/client', async () => {
  const actual = await vi.importActual<typeof import('@/api/client')>('@/api/client')
  return { ...actual, apiGet: vi.fn(), apiPost: vi.fn(), apiPatch: vi.fn(), apiDelete: vi.fn() }
})

const client = await import('@/api/client')
const { AgentsTab } = await import('../AgentsTab')
const { ModelsTab } = await import('../ModelsTab')

function newClient() {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } })
}

async function mount(node: React.ReactElement) {
  const qc = newClient()
  const host = document.createElement('div')
  document.body.appendChild(host)
  const root = createRoot(host)
  await act(async () => {
    root.render(React.createElement(QueryClientProvider, { client: qc }, node))
  })
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0))
    })
  }
  return { host, root, qc }
}

function setValue(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('AgentsTab', () => {
  beforeEach(() => {
    vi.mocked(client.apiGet).mockReset()
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('renders coding-agent banks and a filled-in install command', async () => {
    vi.mocked(client.apiGet).mockResolvedValue({
      banks: ['coding-agent::alice', 'coding-agent::bob'],
      install: {
        harnesses: ['claude-code', 'codex'],
        command: 'npx @vectorize-io/hindsight-coding-agents@0.8.5 install <harness> --server self-hosted --api-url http://hal0.local:7177',
      },
    })
    const { host } = await mount(React.createElement(AgentsTab))
    expect(host.innerHTML).toContain('coding-agent::alice')
    expect(host.innerHTML).toContain('coding-agent::bob')
    expect(host.querySelector('[data-testid="memws-install-command"]')?.textContent).toContain('install claude-code --server')
    expect(host.querySelector('[data-testid="memws-install-command"]')?.textContent).toContain('http://hal0.local:7177')
  })

  it('shows an engine error state when the agents endpoint fails', async () => {
    vi.mocked(client.apiGet).mockRejectedValue(new Error('boom'))
    const { host } = await mount(React.createElement(AgentsTab))
    expect(host.querySelector('[data-testid="memws-error"]')).toBeTruthy()
  })
})

describe('ModelsTab', () => {
  beforeEach(() => {
    vi.mocked(client.apiGet).mockReset()
    vi.mocked(client.apiPatch).mockReset()
  })
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('renders model identity cards and an NPU lane when present', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url.includes('/models')) {
        return {
          embeddings: { provider: 'local', model: 'bge-m3' },
          reranker: { provider: 'local', model: 'bge-reranker' },
          decision: { provider: 'local', model: 'qwen2.5-7b', reachable: true },
        }
      }
      if (url.includes('/lanes')) {
        return { decision: { health: 'ok' }, llm: {}, banks: [], npu: { slot: 'npu0', status: 'loaded' } }
      }
      if (url.includes('/config')) {
        return { recall_decision_model_prune: true }
      }
      return {}
    })
    const { host } = await mount(React.createElement(ModelsTab, { bank: 'shared' }))
    expect(host.querySelector('[data-testid="memws-embeddings-card"]')?.textContent).toContain('bge-m3')
    expect(host.querySelector('[data-testid="memws-decision-reachable"]')?.textContent).toContain('reachable')
    expect(host.querySelector('[data-testid="memws-npu-card"]')).toBeTruthy()
    const toggle = host.querySelector<HTMLInputElement>('[data-testid="memws-toggle-recall_decision_model_prune"]')
    expect(toggle?.checked).toBe(true)
  })

  it('PATCHes bank config when a toggle is flipped', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url.includes('/models')) return { embeddings: {}, reranker: {}, decision: { reachable: false } }
      if (url.includes('/lanes')) return { decision: {}, llm: {}, banks: [], npu: null }
      if (url.includes('/config')) return { recall_decision_model_prune: false }
      return {}
    })
    vi.mocked(client.apiPatch).mockResolvedValue({})
    const { host } = await mount(React.createElement(ModelsTab, { bank: 'shared' }))
    const toggle = host.querySelector<HTMLInputElement>('[data-testid="memws-toggle-recall_decision_model_prune"]')!
    await act(async () => {
      toggle.click()
    })
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
    }
    expect(client.apiPatch).toHaveBeenCalledWith(
      expect.stringContaining('/api/memory/engine/v1/default/banks/shared/config'),
      { updates: { recall_decision_model_prune: true } },
    )
  })

  it('renders mission fields from bank config and saves edits via the same PATCH', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url.includes('/models')) return { embeddings: {}, reranker: {}, decision: { reachable: false } }
      if (url.includes('/lanes')) return { decision: {}, llm: {}, banks: [], npu: null }
      if (url.includes('/config')) return { retain_mission: 'extract carefully', reflect_mission: 'be terse' }
      return {}
    })
    vi.mocked(client.apiPatch).mockResolvedValue({})
    const { host } = await mount(React.createElement(ModelsTab, { bank: 'shared' }))
    const retainField = host.querySelector<HTMLTextAreaElement>('[data-testid="memws-mission-retain_mission"]')
    expect(retainField?.value).toBe('extract carefully')

    await act(async () => {
      setValue(retainField!, 'extract more carefully')
    })
    const saveBtn = host.querySelector<HTMLButtonElement>('[data-testid="memws-mission-save-retain_mission"]')!
    await act(async () => {
      saveBtn.click()
    })
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
    }
    expect(client.apiPatch).toHaveBeenCalledWith(
      expect.stringContaining('/api/memory/engine/v1/default/banks/shared/config'),
      { updates: { retain_mission: 'extract more carefully' } },
    )
  })

  it('renders and edits default_observation_scopes as a tag-scope list', async () => {
    vi.mocked(client.apiGet).mockImplementation(async (url: string) => {
      if (url.includes('/models')) return { embeddings: {}, reranker: {}, decision: { reachable: false } }
      if (url.includes('/lanes')) return { decision: {}, llm: {}, banks: [], npu: null }
      if (url.includes('/config')) return { default_observation_scopes: [['project:*'], ['topic:x', 'topic:y']] }
      return {}
    })
    vi.mocked(client.apiPatch).mockResolvedValue({})
    const { host } = await mount(React.createElement(ModelsTab, { bank: 'shared' }))
    const row0 = host.querySelector<HTMLInputElement>('[data-testid="memws-scope-row-0"]')
    const row1 = host.querySelector<HTMLInputElement>('[data-testid="memws-scope-row-1"]')
    expect(row0?.value).toBe('project:*')
    expect(row1?.value).toBe('topic:x, topic:y')

    const addBtn = host.querySelector<HTMLButtonElement>('[data-testid="memws-scope-add"]')!
    await act(async () => {
      addBtn.click()
    })
    const row2 = host.querySelector<HTMLInputElement>('[data-testid="memws-scope-row-2"]')!
    await act(async () => {
      setValue(row2, 'agent:*')
    })
    const saveBtn = host.querySelector<HTMLButtonElement>('[data-testid="memws-scope-save"]')!
    await act(async () => {
      saveBtn.click()
    })
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0))
      })
    }
    expect(client.apiPatch).toHaveBeenCalledWith(
      expect.stringContaining('/api/memory/engine/v1/default/banks/shared/config'),
      {
        updates: {
          default_observation_scopes: [['project:*'], ['topic:x', 'topic:y'], ['agent:*']],
        },
      },
    )
  })
})
