// The Image Gen pane (Slots → Image Gen): the GPU status line on top, then the Generate | Models |
// Engine sub-tabs.
//
// Which tab opens: the route's sub-tab (`#slots/image/<sub>`, `sub`) when it names one; else
// Models when no renderable model is installed (nothing to generate with yet, and a download that
// is running shows on the tab's label), else Generate. The default is chosen once, when the
// catalog has answered, and a later change of it does not move the viewer. A tab change rewrites
// the URL with replaceState, so Back does not fill with tabs.
//
// All three panels stay mounted and the inactive ones are hidden. A Generate run lives for
// minutes and leaving the tab does not end it: the panel stays to report it (`onRunChange`) and
// to cancel it, which the status line offers for as long as it runs (`ownRun`).
//
// The root carries `.comfy-page`. It is only a marker: it scopes no rule (dashboard.css defines the
// `--comfy*` tokens on :root).
import { useCallback, useEffect, useRef, useState } from 'react'
import './image-tabs.css'
import { isImageJobActive } from '@/api/hooks/imageJobState'
import { useComfyuiModels, useImageJobs } from '@/api/hooks/useComfyui'
import { useImageRunStore } from '@/stores/useImageRunStore'
import { ImageEngineTab } from './ImageEngineTab'
import { ImageGeneratePanel } from './ImageGeneratePanel'
import { ImageModelsPanel } from './ImageModelsPanel'
import { renderableInstalled } from './image-models-pure'
import { useEditorAccess } from './useEditorAccess'

const TABS = [
  { id: 'generate', label: 'Generate' },
  { id: 'models', label: 'Models' },
  { id: 'engine', label: 'Engine' },
]
const IDS = TABS.map((t) => t.id)
const validTab = (id) => (IDS.includes(id) ? id : null)

// `#slots/image/<sub>`: the sub-tab the URL names now (replaceState moves the URL without telling
// the router, so the route's own `sub` can be behind it).
function subFromHash() {
  const m = /^#slots\/image\/([^/?]+)/.exec(window.location.hash || '')
  return m ? validTab(m[1]) : null
}

// What opens when the URL names no tab. null while the catalog has not answered.
function defaultTab(modelsQ) {
  if (modelsQ.data === undefined && !modelsQ.isError) return null
  return renderableInstalled(modelsQ.data?.models ?? []).length > 0 ? 'generate' : 'models'
}

/**
 * @param {object} props
 * @param {string | null} [props.sub]  the route's sub-tab (`#slots/image/<sub>`); wins over the default
 */
export function ImageGenTabs({ sub }) {
  const modelsQ = useComfyuiModels()
  const jobsQ = useImageJobs()
  const access = useEditorAccess()

  const fallback = defaultTab(modelsQ)
  const [tab, setTab] = useState(() => validTab(sub) ?? fallback)
  useEffect(() => {
    if (tab == null && fallback) setTab(fallback)
  }, [tab, fallback])
  // A route that names a tab moves the pane to it: a link followed while it is open, and the URL
  // typed over a tab the pane has since left (the router does not see replaceState).
  useEffect(() => {
    const next = validTab(sub)
    if (next) setTab(next)
  }, [sub])
  useEffect(() => {
    const onHash = () => {
      const next = subFromHash()
      if (next) setTab(next)
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const select = useCallback((next) => {
    setTab(next)
    try {
      window.history.replaceState(window.history.state, '', `#slots/image/${next}`)
    } catch {
      /* a sandboxed frame refuses; the tab still changes */
    }
  }, [])

  // Models → Generate: the model asked for, and a counter so the same model asked for again is a
  // new request.
  const [pick, setPick] = useState({ modelId: undefined, seq: 0 })
  const generateWith = useCallback(
    (modelId) => {
      setPick((p) => ({ modelId, seq: p.seq + 1 }))
      select('generate')
    },
    [select],
  )
  // The 409 "not installed" link passes the missing model's id: the Models tab scrolls to its card
  // and focuses it. The counter makes the same model asked for again a new request.
  const [focus, setFocus] = useState({ modelId: undefined, seq: 0 })
  const openModels = useCallback(
    (modelId) => {
      setFocus((f) => ({ modelId, seq: f.seq + 1 }))
      select('models')
    },
    [select],
  )
  // On the box the editor opens; anywhere else the Engine tab says why it cannot, and how to.
  const openEditor = useCallback(() => {
    if (access.onBox && access.url) window.open(access.url, '_blank', 'noopener,noreferrer')
    else select('engine')
  }, [access.onBox, access.url, select])

  const tabRefs = useRef({})
  const onKeyDown = (e) => {
    const at = IDS.indexOf(tab)
    let next = null
    if (e.key === 'ArrowRight') next = IDS[(at + 1) % IDS.length]
    else if (e.key === 'ArrowLeft') next = IDS[(at - 1 + IDS.length) % IDS.length]
    else if (e.key === 'Home') next = IDS[0]
    else if (e.key === 'End') next = IDS[IDS.length - 1]
    if (!next) return
    e.preventDefault()
    select(next)
    tabRefs.current[next]?.focus()
  }

  const downloading = (jobsQ.data ?? []).filter(isImageJobActive).length

  return (
    <div className="comfy-page igt" data-testid="image-gen-pane">
      <div className="igt-tabs" role="tablist" aria-label="Image Gen">
        {TABS.map((t) => {
          const on = tab === t.id
          return (
            <button
              key={t.id}
              ref={(el) => {
                tabRefs.current[t.id] = el
              }}
              type="button"
              role="tab"
              id={`igt-tab-${t.id}`}
              className={`igt-tab${on ? ' on' : ''}`}
              data-testid={`igt-tab-${t.id}`}
              aria-selected={on}
              aria-controls={`igt-panel-${t.id}`}
              tabIndex={on || (tab == null && t.id === IDS[0]) ? 0 : -1}
              onClick={() => select(t.id)}
              onKeyDown={onKeyDown}
            >
              {t.id === 'models' && downloading > 0 ? `${t.label} · ${downloading} downloading` : t.label}
            </button>
          )
        })}
      </div>

      {tab == null && (
        <div className="igt-loading" data-testid="igt-loading">
          Loading…
        </div>
      )}

      <div
        role="tabpanel"
        id="igt-panel-generate"
        className="igt-panel"
        aria-labelledby="igt-tab-generate"
        hidden={tab !== 'generate'}
      >
        <ImageGeneratePanel
          initialModelId={pick.modelId}
          pickSeq={pick.seq}
          onOpenModels={openModels}
          onRunChange={useImageRunStore.getState().setRun}
        />
      </div>
      <div
        role="tabpanel"
        id="igt-panel-models"
        className="igt-panel"
        aria-labelledby="igt-tab-models"
        hidden={tab !== 'models'}
      >
        <ImageModelsPanel
          onGenerate={generateWith}
          onOpenEditor={openEditor}
          visible={tab === 'models'}
          focusModelId={focus.modelId}
          focusSeq={focus.seq}
        />
      </div>
      <div
        role="tabpanel"
        id="igt-panel-engine"
        className="igt-panel"
        aria-labelledby="igt-tab-engine"
        hidden={tab !== 'engine'}
      >
        <ImageEngineTab visible={tab === 'engine'} />
      </div>
    </div>
  )
}
