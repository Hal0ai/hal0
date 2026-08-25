// @vitest-environment happy-dom
//
// Runner Images — set-as-family-default confirm flow (runner-catalogue-v2,
// PR #2043 review follow-up).
//
// A REAL render + click test, not a pure-helper test: reviewer found that
// runner-images.jsx referenced ConfirmDialog without an import, relying on
// the legacy primitives.jsx window-global. The pure-helper suite never
// rendered the component tree, so an unresolved identifier could pass the
// suite and only crash in the browser. This test mounts RunnerImagesView
// with real ReactDOM against happy-dom and drives the flow — Set-as-default
// click → confirm dialog (naming the in_use_by slots) → confirm fires the
// PUT /api/settings mutation — so any identifier that resolves neither via
// import nor test-installed global fails the run at render time.
//
// The data hooks are mocked at the module boundary (contract-shaped rows);
// primitives.jsx's ConfirmDialog/Modal render for real.
import React from 'react'
import { createRoot } from 'react-dom/client'
import { act } from 'react-dom/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'

;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true
;(globalThis as unknown as { React: typeof React }).React = React
// Icon glyphs come from chrome.jsx's window global — irrelevant here.
;(globalThis as unknown as { Icons: unknown }).Icons = new Proxy({}, { get: () => null })

// vi.mock factories are hoisted above imports, so the spy + fixture rows they
// close over must be hoisted too. The "spy" is a plain call log — enough to
// assert exact mutation payloads without pulling vi.fn through the hoist.
const { mutateSpy, ROWS } = vi.hoisted(() => ({
  mutateSpy: [] as unknown[],
  ROWS: [
    {
      id: 'rocmfpx-combined',
      image: 'ghcr.io/hal0ai/hal0-combined',
      tag: '0822',
      digest: null,
      size_bytes: 7_340_032_000,
      manifest_key: null,
      ownership: 'owned',
      publish: 'external',
      notes: null,
      build: null,
      local_path: null,
      downloaded_at: null,
      discovered_at: null,
      updated_at: null,
      extra: {},
      available_tags: ['0824', '0822'],
      is_default: { family: 'rocmfpx', source: 'release' },
      in_use_by: ['agent', 'utility'],
    },
  ],
}))

vi.mock('@/api/hooks/useRunnerImages', () => ({
  useRunnerImages: () => ({ data: ROWS, isPending: false, isError: false, error: null }),
  useRunnerImageSync: () => ({ isPending: false, mutateAsync: async () => ({ images: ROWS }) }),
  useRunnerImagePullJob: () => ({
    imageId: null, jobId: null, state: 'idle', layersDone: 0, layersTotal: 0,
    line: null, error: null, pct: null, inFlight: false, terminal: false,
    start: async () => ({}), cancel: async () => {}, reset: () => {}, reattach: async () => {},
  }),
  useRunnerImagePullsList: () => ({ data: [] }),
  useDownloadedRunnerImages: () => ({ data: [] }),
  useRunnerImage: () => ({ data: null }),
  useSetDefaultImage: () => ({
    isPending: false,
    mutate: (vars: unknown) => { (mutateSpy as unknown[]).push(vars) },
  }),
}))

const { RunnerImagesView } = await import('../runner-images.jsx')

function textButtons(root: HTMLElement): HTMLButtonElement[] {
  return Array.from(root.querySelectorAll('button'))
}

describe('RunnerImagesView set-as-default confirm flow', () => {
  afterEach(() => {
    ;(mutateSpy as unknown[]).length = 0
    document.body.innerHTML = ''
  })

  it('renders, opens the confirm naming in_use_by slots, and confirm fires the settings mutation', () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    act(() => {
      root.render(React.createElement(RunnerImagesView))
    })

    // Contract surfaces are up: defaults strip row + set-default CTA.
    expect(host.querySelector('[data-testid="ri-defaults"]')).toBeTruthy()
    const setBtn = host.querySelector('[data-testid="ri-set-default"]') as HTMLButtonElement
    expect(setBtn).toBeTruthy()
    // The headline tag already IS the rocmfpx default, so re-pinning it is a
    // no-op — the CTA gates until a different tag is picked.
    expect(setBtn.disabled).toBe(true)
    // No dialog yet.
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    // Pick the newer tag from the tag <select> → CTA arms.
    const pick = host.querySelector('[data-testid="ri-tag-pick"]') as HTMLSelectElement
    expect(pick).toBeTruthy()
    act(() => {
      pick.value = '0824'
      pick.dispatchEvent(new Event('change', { bubbles: true }))
    })
    expect(setBtn.disabled).toBe(false)

    // Click Set-as-default → the confirm dialog opens and names the slots
    // that will drift to the new default.
    act(() => { setBtn.click() })
    expect(host.querySelector('.modal-backdrop')).toBeTruthy()
    expect(host.textContent).toContain('Set rocmfpx default')
    expect(host.textContent).toContain('ghcr.io/hal0ai/hal0-combined:0824')
    expect(host.textContent).toContain('agent, utility')

    // Confirm → exactly one PUT /api/settings mutation with the family +
    // selected ref; the dialog closes.
    const confirmBtn = textButtons(host).find(b => b.textContent === 'Set default')
    expect(confirmBtn).toBeTruthy()
    act(() => { confirmBtn!.click() })
    expect(mutateSpy).toEqual([{ family: 'rocmfpx', ref: 'ghcr.io/hal0ai/hal0-combined:0824' }])
    expect(host.querySelector('.modal-backdrop')).toBeNull()

    act(() => { root.unmount() })
  })

  it('cancel closes the dialog without firing the mutation', () => {
    const host = document.createElement('div')
    document.body.appendChild(host)
    const root = createRoot(host)
    act(() => {
      root.render(React.createElement(RunnerImagesView))
    })
    const pick = host.querySelector('[data-testid="ri-tag-pick"]') as HTMLSelectElement
    act(() => {
      pick.value = '0824'
      pick.dispatchEvent(new Event('change', { bubbles: true }))
    })
    act(() => { (host.querySelector('[data-testid="ri-set-default"]') as HTMLButtonElement).click() })
    const cancelBtn = textButtons(host).find(b => b.textContent === 'Cancel')
    expect(cancelBtn).toBeTruthy()
    act(() => { cancelBtn!.click() })
    expect(host.querySelector('.modal-backdrop')).toBeNull()
    expect(mutateSpy).toEqual([])
    act(() => { root.unmount() })
  })
})
