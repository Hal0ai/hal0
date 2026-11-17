// @vitest-environment happy-dom
//
// Memory ▸ Facts while the workspace modules download: a loading card, an error card with
// "Try again" when the download fails, and the workspace once a retry succeeds.
import { createElement } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { makeArea } from './area-test-fixtures'
import { cleanup, click, mount, settle } from './shared/test-harness'

const loader = vi.hoisted(() => ({ load: vi.fn() }))

vi.mock('../memory-workspace-modules', () => ({
  loadMemoryWorkspace: () => loader.load(),
  memoryWorkspaceReady: () => typeof (window as unknown as { MemV2Workspace?: unknown }).MemV2Workspace === 'function',
}))

const { FactsTab } = await import('./FactsTab.jsx')

const byId = (host: HTMLElement, id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const render = () => mount(createElement(FactsTab, { area: makeArea({ hash: '#memory/facts?bank=Hal0::Agents' }) })).host
const install = () => {
  ;(window as unknown as { MemV2Workspace?: unknown }).MemV2Workspace = () =>
    createElement('div', { 'data-testid': 'workspace-stub' })
}

beforeEach(() => {
  loader.load.mockReset()
})
afterEach(() => {
  cleanup()
  delete (window as unknown as { MemV2Workspace?: unknown }).MemV2Workspace
})

describe('Facts tab workspace loading', () => {
  it('shows a loading card until the modules arrive, then the workspace', async () => {
    let done!: () => void
    loader.load.mockReturnValue(
      new Promise<void>((resolve) => {
        done = () => {
          install()
          resolve()
        }
      }),
    )
    const host = render()
    await settle()
    expect(byId(host, 'ma-facts-workspace-loading')).not.toBeNull()
    done()
    await settle()
    expect(byId(host, 'ma-facts-workspace-loading')).toBeNull()
    expect(byId(host, 'workspace-stub')).not.toBeNull()
  })

  it('says the page could not load part of itself, and retries', async () => {
    loader.load.mockRejectedValueOnce(new Error('404'))
    const host = render()
    await settle()
    const failed = byId(host, 'ma-facts-workspace-failed')!
    expect(failed.textContent).toContain('could not load part of itself')
    expect(failed.textContent).toContain('reload the page')

    loader.load.mockImplementationOnce(async () => install())
    click(failed.querySelector('button')!)
    await settle()
    expect(loader.load).toHaveBeenCalledTimes(2)
    expect(byId(host, 'ma-facts-workspace-failed')).toBeNull()
    expect(byId(host, 'workspace-stub')).not.toBeNull()
  })
})
