// @vitest-environment happy-dom
//
// The Image Gen pane's frame (ImageGenTabs): the status line on top, the Generate | Models |
// Engine sub-tabs, which one opens first, the deep link, and the wiring between the panels. The
// panels and the status line are stubbed at the module boundary: they have their own tests
// (image-generate-panel, image-models-panel, image-status-line, image-engine-tab), so what is
// pinned here is what the frame hands them and does with what they report.
import React, { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import mainSrc from '../../main.tsx?raw'
import slotsSrc from '../slots.jsx?raw'

;(globalThis as unknown as { React: typeof React }).React = React
;(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true

type Json = Record<string, unknown>

const { box } = vi.hoisted(() => ({
  box: {
    models: undefined as Json | undefined,
    modelsError: false,
    jobs: undefined as Json[] | undefined,
    access: { onBox: true, url: 'http://localhost:8188', tunnel: 'ssh -L 8188:127.0.0.1:8188 <user>@box' },
    cancel: undefined as unknown as () => void,
    mounts: { generate: 0, models: 0, engine: 0 },
    unmounts: { generate: 0, models: 0, engine: 0 },
    engineVisible: undefined as boolean | undefined,
  },
}))

vi.mock('@/api/hooks/useComfyui', async (importOriginal) => ({
  ...(await importOriginal<Json>()),
  useComfyuiModels: () => ({ data: box.models, isError: box.modelsError }),
  useImageJobs: () => ({ data: box.jobs }),
}))
vi.mock('../image/useEditorAccess', () => ({ useEditorAccess: () => box.access }))

vi.mock('../image/ImageStatusLine.jsx', async () => {
  const R = await import('react')
  return {
    ImageStatusLine: (p: { ownRun?: boolean; onCancelOwnRun?: () => void }) =>
      R.createElement(
        'div',
        { 'data-testid': 'stub-status', 'data-own-run': String(!!p.ownRun) },
        p.onCancelOwnRun
          ? R.createElement('button', { 'data-testid': 'stub-status-cancel', onClick: p.onCancelOwnRun }, 'Cancel')
          : null,
      ),
  }
})

vi.mock('../image/ImageGeneratePanel.jsx', async () => {
  const R = await import('react')
  return {
    ImageGeneratePanel: (p: {
      initialModelId?: string
      pickSeq?: number
      onOpenModels: (id?: string) => void
      onRunChange: (run: { running: boolean; cancel: () => void } | null) => void
    }) => {
      R.useEffect(() => {
        box.mounts.generate++
        return () => {
          box.unmounts.generate++
        }
      }, [])
      return R.createElement(
        'div',
        {
          'data-testid': 'stub-generate',
          'data-initial': p.initialModelId ?? '',
          'data-seq': String(p.pickSeq ?? 0),
        },
        R.createElement('button', {
          'data-testid': 'stub-run-start',
          onClick: () => p.onRunChange({ running: true, cancel: box.cancel }),
        }),
        R.createElement('button', { 'data-testid': 'stub-run-end', onClick: () => p.onRunChange(null) }),
        R.createElement('button', {
          'data-testid': 'stub-open-models',
          onClick: () => p.onOpenModels('qwen-image'),
        }),
      )
    },
  }
})

vi.mock('../image/ImageModelsPanel.jsx', async () => {
  const R = await import('react')
  return {
    ImageModelsPanel: (p: {
      onGenerate: (id: string) => void
      onOpenEditor: (id: string) => void
      visible?: boolean
      focusModelId?: string
      focusSeq?: number
    }) => {
      R.useEffect(() => {
        box.mounts.models++
        return () => {
          box.unmounts.models++
        }
      }, [])
      return R.createElement(
        'div',
        {
          'data-testid': 'stub-models',
          'data-visible': String(p.visible),
          'data-focus': p.focusModelId ?? '',
          'data-focus-seq': String(p.focusSeq ?? 0),
        },
        R.createElement('button', { 'data-testid': 'stub-models-generate', onClick: () => p.onGenerate('wan22-t2v') }),
        R.createElement('button', { 'data-testid': 'stub-models-editor', onClick: () => p.onOpenEditor('ltx2-t2v') }),
      )
    },
  }
})

vi.mock('../image/ImageEngineTab.jsx', async () => {
  const R = await import('react')
  return {
    ImageEngineTab: (p: { visible?: boolean }) => {
      box.engineVisible = p.visible
      R.useEffect(() => {
        box.mounts.engine++
        return () => {
          box.unmounts.engine++
        }
      }, [])
      return R.createElement('div', { 'data-testid': 'stub-engine' })
    },
  }
})

const { ImageGenTabs } = await import('../image/ImageGenTabs.jsx')

// ── fixtures ─────────────────────────────────────────────────────────────────

const row = (id: string, over: Json = {}) => ({
  id,
  label: id,
  task: 'txt2img',
  state: 'complete',
  api_renderable: true,
  ...over,
})
const catalog = (models: Json[]) => ({
  models,
  engine: { available: true, reason: null },
  store: { free_gb: 100, path: '/m' },
  recommendation: null,
})
const job = (model_id: string, state: string) => ({ id: `j-${model_id}`, model_id, state })

let host: HTMLDivElement
let root: Root

function mount(props: Json = {}) {
  act(() => {
    root.render(React.createElement(ImageGenTabs, props))
  })
}
const byId = (id: string) => host.querySelector<HTMLElement>(`[data-testid="${id}"]`)
const tabEl = (name: 'generate' | 'models' | 'engine') => byId(`igt-tab-${name}`)!
const selected = () =>
  (['generate', 'models', 'engine'] as const).filter((n) => tabEl(n).getAttribute('aria-selected') === 'true')
const panelOf = (name: string) => host.querySelector<HTMLElement>(`#igt-panel-${name}`)!
const click = (el: HTMLElement) =>
  act(() => {
    el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  })

beforeEach(() => {
  box.models = catalog([row('sdxl-lightning')])
  box.modelsError = false
  box.jobs = []
  box.access = { onBox: true, url: 'http://localhost:8188', tunnel: 'ssh -L 8188:127.0.0.1:8188 <user>@box' }
  box.cancel = vi.fn()
  box.mounts = { generate: 0, models: 0, engine: 0 }
  box.unmounts = { generate: 0, models: 0, engine: 0 }
  box.engineVisible = undefined
  host = document.createElement('div')
  document.body.appendChild(host)
  root = createRoot(host)
})
afterEach(() => {
  act(() => root.unmount())
  host.remove()
  vi.restoreAllMocks()
})

// ── which tab opens ──────────────────────────────────────────────────────────

describe('which tab opens first', () => {
  it('image sub-tab defaults to models when nothing installed', () => {
    box.models = catalog([row('sdxl-lightning', { state: 'none' })])
    mount()
    expect(selected()).toEqual(['models'])
    expect(panelOf('models').hidden).toBe(false)
    expect(panelOf('generate').hidden).toBe(true)
  })

  it('image sub-tab defaults to generate when a renderable model is installed', () => {
    mount()
    expect(selected()).toEqual(['generate'])
    expect(panelOf('generate').hidden).toBe(false)
  })

  it('an installed model only an editor can run does not count', () => {
    box.models = catalog([row('ltx2-t2v', { task: 'txt2video', api_renderable: false })])
    mount()
    expect(selected()).toEqual(['models'])
  })

  it('image sub-tab honours deep link', () => {
    // Nothing installed would open Models; the link wins, and Generate shows its own empty state.
    box.models = catalog([])
    mount({ sub: 'generate' })
    expect(selected()).toEqual(['generate'])
    act(() => root.unmount())
    root = createRoot(host)
    mount({ sub: 'engine' })
    expect(selected()).toEqual(['engine'])
  })

  it('ignores a sub-tab it does not have', () => {
    mount({ sub: 'nonsense' })
    expect(selected()).toEqual(['generate'])
  })

  it('opens the deep link at once, before the catalog has answered', () => {
    box.models = undefined
    mount({ sub: 'models' })
    expect(selected()).toEqual(['models'])
  })

  it('waits for the catalog before choosing, then chooses once', () => {
    box.models = undefined
    mount()
    expect(selected()).toEqual([])
    box.models = catalog([row('sdxl-lightning')])
    mount()
    expect(selected()).toEqual(['generate'])
    // A later change of the catalog (the model removed) does not move the viewer.
    box.models = catalog([row('sdxl-lightning', { state: 'none' })])
    mount()
    expect(selected()).toEqual(['generate'])
  })

  it('a running download with nothing installed still opens Models', () => {
    // The spec: Models is the default whenever no renderable model is installed, whether or not
    // a download runs. The tab label says one does.
    box.models = catalog([row('sdxl-lightning', { state: 'none' })])
    box.jobs = [job('sdxl-lightning', 'running')]
    mount()
    expect(selected()).toEqual(['models'])
    expect(tabEl('models').textContent).toBe('Models · 1 downloading')
  })

  it('the jobs list does not decide the default, so it does not hold it back either', () => {
    box.models = catalog([row('sdxl-lightning', { state: 'none' })])
    box.jobs = undefined
    mount()
    expect(selected()).toEqual(['models'])
    act(() => root.unmount())
    root = createRoot(host)
    box.models = catalog([row('sdxl-lightning')])
    box.jobs = [job('qwen-image', 'running')]
    mount()
    expect(selected()).toEqual(['generate'])
  })

  // `.comfy-page` scopes no rule any more (dashboard.css defines the --comfy tokens on :root);
  // it stays on the root as a marker.
  it('the root carries the comfy-page marker class', () => {
    mount()
    expect(byId('image-gen-pane')!.classList.contains('comfy-page')).toBe(true)
    expect(byId('stub-status')).not.toBeNull()
  })
})

// ── the Models label and the URL ─────────────────────────────────────────────

describe('the Models tab label', () => {
  it('shows the count of downloads while they run', () => {
    box.jobs = [job('a', 'running'), job('b', 'queued'), job('c', 'completed')]
    mount()
    expect(tabEl('models').textContent).toBe('Models · 2 downloading')
  })

  it('is plain when none runs', () => {
    mount()
    expect(tabEl('models').textContent).toBe('Models')
  })
})

describe('the URL', () => {
  it('a tab change writes #slots/image/<sub> with replaceState, so Back is not filled with tabs', () => {
    const replace = vi.spyOn(window.history, 'replaceState')
    const push = vi.spyOn(window.history, 'pushState')
    mount()
    click(tabEl('models'))
    expect(replace).toHaveBeenCalledTimes(1)
    expect(String(replace.mock.calls[0][2])).toBe('#slots/image/models')
    click(tabEl('engine'))
    expect(String(replace.mock.calls[1][2])).toBe('#slots/image/engine')
    expect(push).not.toHaveBeenCalled()
  })

  it('a hash that changes under the pane moves it (the route state does not see replaceState)', () => {
    mount({ sub: 'generate' })
    click(tabEl('models'))
    expect(selected()).toEqual(['models'])
    window.location.hash = '#slots/image/generate'
    act(() => {
      window.dispatchEvent(new HashChangeEvent('hashchange'))
    })
    expect(selected()).toEqual(['generate'])
  })

  it('a new sub from the route moves the pane', () => {
    mount({ sub: 'generate' })
    mount({ sub: 'engine' })
    expect(selected()).toEqual(['engine'])
  })
})

// ── the tabs stay mounted ────────────────────────────────────────────────────

describe('hidden tabs', () => {
  it('stay mounted: switching hides a panel, it does not unmount it', () => {
    mount()
    click(tabEl('models'))
    click(tabEl('engine'))
    click(tabEl('generate'))
    expect(box.mounts).toEqual({ generate: 1, models: 1, engine: 1 })
    expect(box.unmounts).toEqual({ generate: 0, models: 0, engine: 0 })
    expect(panelOf('generate').hidden).toBe(false)
    expect(panelOf('models').hidden).toBe(true)
    expect(panelOf('engine').hidden).toBe(true)
  })

  it('the engine tab is told when it is the one showing', () => {
    mount()
    expect(box.engineVisible).toBe(false)
    click(tabEl('engine'))
    expect(box.engineVisible).toBe(true)
  })

  it('the models tab is told when it is the one showing (it re-reads the catalog only then)', () => {
    mount({ sub: 'models' })
    expect(byId('stub-models')!.getAttribute('data-visible')).toBe('true')
    click(tabEl('generate'))
    expect(byId('stub-models')!.getAttribute('data-visible')).toBe('false')
    click(tabEl('models'))
    expect(byId('stub-models')!.getAttribute('data-visible')).toBe('true')
  })

  it('the tab strip is a tablist the keyboard can walk', () => {
    mount()
    const strip = host.querySelector('[role="tablist"]') as HTMLElement
    expect(strip).not.toBeNull()
    act(() => {
      tabEl('generate').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }))
    })
    expect(selected()).toEqual(['models'])
    act(() => {
      tabEl('models').dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowLeft', bubbles: true }))
    })
    expect(selected()).toEqual(['generate'])
    act(() => {
      tabEl('generate').dispatchEvent(new KeyboardEvent('keydown', { key: 'End', bubbles: true }))
    })
    expect(selected()).toEqual(['engine'])
  })
})

// ── wiring between the panels ────────────────────────────────────────────────

describe('wiring', () => {
  it('generate run stays cancellable from the status line after a tab switch', () => {
    mount()
    expect(byId('stub-status-cancel')).toBeNull()
    expect(byId('stub-status')!.getAttribute('data-own-run')).toBe('false')

    click(byId('stub-run-start')!)
    expect(byId('stub-status')!.getAttribute('data-own-run')).toBe('true')

    // Leave Generate: the panel stays mounted, so its run is still the status line's to cancel.
    click(tabEl('models'))
    click(tabEl('engine'))
    expect(box.unmounts.generate).toBe(0)
    click(byId('stub-status-cancel')!)
    expect(box.cancel).toHaveBeenCalledTimes(1)

    // The run ends: nothing left to cancel.
    click(byId('stub-run-end')!)
    expect(byId('stub-status-cancel')).toBeNull()
    expect(byId('stub-status')!.getAttribute('data-own-run')).toBe('false')
  })

  it('models generate action selects the model in Generate', () => {
    mount({ sub: 'models' })
    expect(byId('stub-generate')!.getAttribute('data-initial')).toBe('')
    click(byId('stub-models-generate')!)
    expect(selected()).toEqual(['generate'])
    expect(byId('stub-generate')!.getAttribute('data-initial')).toBe('wan22-t2v')
    expect(byId('stub-generate')!.getAttribute('data-seq')).toBe('1')

    // The same model asked for again is a new request: the sequence moves.
    click(tabEl('models'))
    click(byId('stub-models-generate')!)
    expect(byId('stub-generate')!.getAttribute('data-initial')).toBe('wan22-t2v')
    expect(byId('stub-generate')!.getAttribute('data-seq')).toBe('2')
  })

  it("Generate's Open Models switches to the Models tab", () => {
    mount()
    click(byId('stub-open-models')!)
    expect(selected()).toEqual(['models'])
  })

  // The 409 "not installed" link names the model the render needs: the Models tab gets it to
  // scroll to and focus. A new request is a new sequence number, so the same model asked for
  // again is focused again.
  it("Generate's Open Models passes the missing model to the Models tab", () => {
    mount()
    expect(byId('stub-models')!.getAttribute('data-focus')).toBe('')
    click(byId('stub-open-models')!)
    expect(byId('stub-models')!.getAttribute('data-focus')).toBe('qwen-image')
    expect(byId('stub-models')!.getAttribute('data-focus-seq')).toBe('1')

    click(tabEl('generate'))
    click(byId('stub-open-models')!)
    expect(byId('stub-models')!.getAttribute('data-focus')).toBe('qwen-image')
    expect(byId('stub-models')!.getAttribute('data-focus-seq')).toBe('2')
  })

  it('open in ComfyUI opens the editor in a new tab when the page is on the box', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    mount({ sub: 'models' })
    click(byId('stub-models-editor')!)
    expect(open).toHaveBeenCalledWith('http://localhost:8188', '_blank', 'noopener,noreferrer')
    expect(selected()).toEqual(['models'])
  })

  it('open in ComfyUI off the box takes the viewer to the Engine tab, where the tunnel is', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null)
    box.access = { onBox: false, url: null as unknown as string, tunnel: 'ssh -L 8188:127.0.0.1:8188 <user>@box' }
    mount({ sub: 'models' })
    click(byId('stub-models-editor')!)
    expect(open).not.toHaveBeenCalled()
    expect(selected()).toEqual(['engine'])
  })
})

// ── the style sheets ─────────────────────────────────────────────────────────

describe('style order', () => {
  // image.css overrides overhaul.css rules of equal specificity (the fit meter reuses the top
  // bar ruler's classes), so it must load after it. Every image module is reached from
  // slots.jsx, which main.tsx imports after overhaul.css.
  it('image.css loads after overhaul.css', () => {
    const overhaul = mainSrc.indexOf("import './dash/overhaul.css'")
    const slots = mainSrc.indexOf("import './dash/slots.jsx'")
    expect(overhaul).toBeGreaterThan(-1)
    expect(slots).toBeGreaterThan(overhaul)
    // Nothing imported before overhaul.css reaches the image modules.
    expect(mainSrc.slice(0, overhaul)).not.toMatch(/dash\/image\//)
    expect(slotsSrc).toMatch(/from '\.\/image\/ImageGenTabs(\.jsx)?'/)
  })
})

describe('the --comfy tokens', () => {
  // The old pane's stylesheet re-declared them under `.comfy-page`. It is gone: dashboard.css
  // defines them on :root, which is all the pane draws with. The e2e spec imagegen-tabs-v3 reads
  // them back off the mounted root in a browser, since vitest does not load CSS.
  it('no longer come from a sheet of the deleted pane', () => {
    expect(mainSrc).not.toMatch(/comfyui-pane/)
  })
})
