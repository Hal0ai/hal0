// The Image Gen pane's Models tab: the image-model catalog by task, with each model's install
// lifecycle, its fit against this box and the actions that follow from its state.
//
// Data: `useComfyuiModels` (GET /api/comfyui/models: rows, engine, store, recommendation),
// `useImageJobs` (the installs, polled while one runs), `useComfyuiFiles` (what removing a model
// would delete and keep). The verdict on every card is the server's (`model.fit`); nothing here
// judges a fit. The pure parts (grouping, filters, badges, the removal plan, the first-run
// picks) are image-models-pure.ts and models-panel-pure.ts.
//
// Mounted by ImageGenTabs, which keeps it mounted while another tab shows (`visible` says which).
// `onGenerate(modelId)` switches to the Generate tab with that model; `onOpenEditor(modelId)`
// opens the ComfyUI editor for an editor-only model. `focusModelId` asks for a model's card to be
// scrolled to and focused (the Generate tab's "not installed" link); `focusSeq` makes a repeat
// request a new one.
import { useEffect, useMemo, useRef, useState } from 'react'
import './image.css'
import { isImageJobActive } from '@/api/hooks/imageJobState'
import {
  useComfyuiFiles,
  useComfyuiModels,
  useImageJobCancel,
  useImageJobs,
  useImageModelInstall,
  useImageModelRemove,
} from '@/api/hooks/useComfyui'
import { fmtBytes } from '@/api/hooks/useModels'
import { useMemoryBudget } from '@/api/hooks/useStatsHardware'
import { copyTextToClipboard } from '@/lib/clipboard'
import { ConfirmDialog } from '@/dash/primitives.jsx'
import { useCapabilitySelection } from '@/dash/settings/pages/capabilities/useCapabilitySelection.js'
import { groupByTask, renderableInstalled } from './image-models-pure'
import { ImageFirstRun } from './ImageFirstRun'
import { ImageModelCard } from './ImageModelCard'
import {
  fixCommands,
  firstRunPicks,
  isFullPrecision,
  passesFilter,
  recommendationReason,
  removalPlan,
  suggestAlternative,
} from './models-panel-pure'
import { useJobRates } from './useJobRates'

const note = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)

const FILTERS = [
  { id: 'all', label: 'All' },
  { id: 'fits', label: 'Fits this box' },
  { id: 'installed', label: 'Installed' },
]

// The models folder cannot be read by the hal0 user: the server's message, and the two commands
// that fix it, copyable. The panel is replaced, not annotated: there is no catalog to show.
function Unreadable({ error, onRetry }) {
  // The commands name the path the server says it could not read. Without one there is nothing
  // honest to put in them, so the message and Retry stand alone.
  const path = typeof error.details?.path === 'string' ? error.details.path : ''
  const commands = path ? fixCommands(path).join('\n') : ''
  // hal0 is often served over plain http on a LAN, where there is no navigator.clipboard.
  const copy = async () => {
    if (await copyTextToClipboard(commands)) note('Commands copied', 'ok')
    else note('Could not copy: select the commands and copy them by hand', 'err')
  }
  return (
    <div className="imp" data-testid="imp-panel">
      <div className="imp-fix" data-testid="imp-unreadable" role="alert">
        <h2>The image model folder is not readable</h2>
        <p>{error.message}</p>
        {commands && (
          <>
            <p>Run these on the host, then retry:</p>
            <pre data-testid="imp-fix-commands">{commands}</pre>
          </>
        )}
        <div className="imp-fix-actions">
          {commands && (
            <button className="btn ghost sm" onClick={copy}>
              Copy commands
            </button>
          )}
          <button className="btn sm" onClick={onRetry}>
            Retry
          </button>
        </div>
      </div>
    </div>
  )
}

function LoadError({ error, onRetry }) {
  return (
    <div className="imp" data-testid="imp-panel">
      <div className="imp-fix" data-testid="imp-load-error" role="alert">
        <h2>Could not load the image models</h2>
        <p>{error?.message || 'The API did not answer.'}</p>
        <div className="imp-fix-actions">
          <button className="btn sm" onClick={onRetry}>
            Retry
          </button>
        </div>
      </div>
    </div>
  )
}

// What removing a model deletes and what stays, from the file listing. The server has the last
// word (it keeps a file another complete model loads); this shows the same rule beforehand.
function RemoveBody({ plan, filesKnown, filesLoading, error }) {
  return (
    <div className="imp-rm">
      <h4>Deleted</h4>
      <ul data-testid="imp-remove-delete">
        {plan.deleted.map((f) => (
          <li key={f.path}>
            <span>{f.path}</span>
            <span className="sz">{fmtBytes(f.size)}</span>
          </li>
        ))}
        <li>
          <span>workflows/{plan.workflow}</span>
        </li>
      </ul>
      {plan.kept.length > 0 && (
        <>
          <h4>Kept, because another installed model uses them</h4>
          <ul data-testid="imp-remove-kept">
            {plan.kept.map((f) => (
              <li key={f.path}>
                <span>{f.path}</span>
                <span className="sz">used by {f.usedBy.join(', ')}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {filesLoading && <p className="none">Reading the file list…</p>}
      {!filesKnown && !filesLoading && (
        <p className="none">
          The file list could not be read, so this cannot show which files stay. The server keeps any file
          another installed model uses.
        </p>
      )}
      {error && (
        <div className="err" role="alert" data-testid="imp-remove-error">
          {error}
        </div>
      )}
    </div>
  )
}

/**
 * @param {object} props
 * @param {(modelId: string) => void} props.onGenerate   open the Generate tab with this model
 * @param {(modelId: string) => void} props.onOpenEditor open the ComfyUI editor for this model
 * @param {boolean} [props.visible]  this tab is the one showing: the catalog is re-read only then
 * @param {string} [props.focusModelId]  scroll to this model's card and focus it
 * @param {number} [props.focusSeq]      bumped on every such request, so a repeat is a new one
 */
export function ImageModelsPanel({ onGenerate, onOpenEditor, visible = true, focusModelId, focusSeq = 0 }) {
  // The verdict on every card moves with what the box has free: re-read it while the tab shows.
  const modelsQuery = useComfyuiModels({ poll: visible })
  const jobsQuery = useImageJobs()
  // The scan walks the tree and sniffs headers: it runs only while a removal is being confirmed.
  const [removing, setRemoving] = useState(null)
  const filesQuery = useComfyuiFiles({ enabled: !!removing })
  const install = useImageModelInstall()
  const remove = useImageModelRemove()
  const cancel = useImageJobCancel()
  const memory = useMemoryBudget()
  const sel = useCapabilitySelection('img', 'img', { withProvider: true })

  const [filter, setFilter] = useState('all')
  const [expanded, setExpanded] = useState({})
  const [installErrors, setInstallErrors] = useState({})
  const [removeError, setRemoveError] = useState(null)
  const listRef = useRef(null)
  const rootRef = useRef(null)

  const jobs = jobsQuery.data
  const rates = useJobRates(jobs, jobsQuery.dataUpdatedAt || 0)
  const jobsByModel = useMemo(() => new Map((jobs ?? []).map((j) => [j.model_id, j])), [jobs])

  const data = modelsQuery.data
  const models = data?.models

  // ── focusing a model ───────────────────────────────────────────────────────

  // A request names a model; it is met once its card is on screen. A filter that hides the card is
  // cleared and a group folded behind "More" is opened first, and a catalog that has not arrived
  // yet is waited for. A model the catalog does not list is let go.
  const [wanted, setWanted] = useState(null)
  useEffect(() => {
    setWanted(focusModelId ? { id: focusModelId, seq: focusSeq } : null)
  }, [focusModelId, focusSeq])
  const wantedModel = wanted && models ? models.find((m) => m.id === wanted.id) : null
  useEffect(() => {
    if (!wanted || !models) return
    if (!wantedModel) {
      setWanted(null)
      return
    }
    const card = rootRef.current?.querySelector(`[data-testid="imp-card-${CSS.escape(wanted.id)}"]`)
    if (!card) {
      // Hidden by the filter or folded away: show it, and come back when it has rendered.
      setFilter('all')
      setExpanded((prev) => (prev[wantedModel.task] ? prev : { ...prev, [wantedModel.task]: true }))
      return
    }
    card.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
    card.focus({ preventScroll: true })
    setWanted(null)
  })
  const defaultId = sel.selection?.enabled && sel.selection?.model ? sel.selection.model : null

  // An unreadable tree replaces the panel whatever it showed before: the catalog it holds is
  // stale. Any other failure only does when there is no catalog to show; a failed refetch keeps
  // the last one on screen.
  if (modelsQuery.isError) {
    const error = modelsQuery.error
    const retry = () => modelsQuery.refetch()
    if (error?.code === 'comfyui.models_dir_not_writable') return <Unreadable error={error} onRetry={retry} />
    if (!models) return <LoadError error={error} onRetry={retry} />
  }
  if (!data || !models) {
    return (
      <div className="imp-empty" data-testid="imp-loading">
        Loading models…
      </div>
    )
  }

  const available = !!data.engine?.available
  // The freshest job: the /jobs poll over the copy the catalog row carried.
  const jobFor = (m) => jobsByModel.get(m.id) ?? m.job ?? null

  // ── actions ────────────────────────────────────────────────────────────────

  const startInstall = async (model) => {
    setInstallErrors(({ [model.id]: _cleared, ...rest }) => rest)
    try {
      await install.mutateAsync(model.id)
    } catch (e) {
      setInstallErrors((prev) => ({ ...prev, [model.id]: e?.message || 'The install was refused.' }))
    }
  }
  const cancelInstall = async (model) => {
    try {
      await cancel.mutateAsync(model.id)
    } catch (e) {
      note(`Could not cancel ${model.label} — ${e?.message || 'see logs'}`, 'err')
    }
  }
  const setDefault = async (model) => {
    try {
      await sel.applyCapability.mutateAsync({
        slot: 'img',
        child: 'img',
        body: { model: model.id, enabled: true, provider: 'comfyui' },
      })
      note(`${model.label} is now the default image model`, 'ok')
    } catch (e) {
      note(`Could not set the default — ${e?.message || 'see logs'}`, 'err')
    }
  }
  const askRemove = (model) => {
    setRemoveError(null)
    setRemoving(model)
  }
  const confirmRemove = async () => {
    const model = removing
    if (!model) return
    try {
      await remove.mutateAsync({ id: model.id })
      note(`Removed ${model.label}`, 'ok')
      setRemoving(null)
    } catch (e) {
      setRemoveError(e?.message || 'The removal failed.')
    }
  }
  const useAlternative = (alt) => (alt.state === 'complete' ? onGenerate?.(alt.id) : startInstall(alt))

  const actions = {
    onInstall: startInstall,
    onCancel: cancelInstall,
    onRemove: askRemove,
    onGenerate: (m) => onGenerate?.(m.id),
    onOpenEditor: (m) => onOpenEditor?.(m.id),
    onSetDefault: setDefault,
    onUseAlternative: useAlternative,
  }

  // ── what to show ───────────────────────────────────────────────────────────

  const anyInstalling = (jobs ?? []).some(isImageJobActive) || models.some((m) => isImageJobActive(m.job))
  const picks = available && renderableInstalled(models).length === 0 && !anyInstalling ? firstRunPicks(models) : null
  const recommendation = data.recommendation
  const reason =
    picks && recommendation?.id === picks.recommended.id
      ? recommendationReason(recommendation, memory.data?.ram?.source)
      : null
  const firstRunError = picks
    ? [picks.lead, ...picks.others].map((m) => installErrors[m.id]).find(Boolean) || null
    : null

  const groups = groupByTask(models.filter((m) => passesFilter(m, filter, available)))
  // A full-precision variant waits behind "More" unless it is installed or installing: a model
  // you have should not need a click to find its Remove button.
  const tucked = (m) => isFullPrecision(m.id) && m.state !== 'complete' && !isImageJobActive(jobFor(m))

  const plan = removing ? removalPlan(removing, filesQuery.data?.files ?? [], models) : null
  const free = data.store?.free_gb

  return (
    <div className="imp" data-testid="imp-panel" ref={rootRef}>
      {!available && (
        <div className="imp-note" role="status" data-testid="imp-engine-note">
          Image generation is unavailable on this host{data.engine?.reason ? `: ${data.engine.reason}` : '.'}
        </div>
      )}

      {picks && (
        <ImageFirstRun
          picks={picks}
          reason={reason}
          error={firstRunError}
          onInstall={startInstall}
          onBrowse={() => listRef.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })}
        />
      )}

      <div className="imp-bar" ref={listRef}>
        <div className="imp-filters" role="group" aria-label="Filter models">
          {FILTERS.filter((f) => f.id !== 'fits' || available).map((f) => (
            <button
              key={f.id}
              className="imp-filter"
              aria-pressed={filter === f.id}
              onClick={() => setFilter(f.id)}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="imp-store" data-testid="imp-store">
          {free == null ? 'Model store · free space unknown' : `Model store · ${free} GB free`}
        </div>
      </div>

      {groups.length === 0 && (
        <div className="imp-empty" data-testid="imp-empty">
          No models match this filter.
        </div>
      )}

      {groups.map((group) => {
        const open = !!expanded[group.task]
        const hidden = group.models.filter(tucked)
        const shown = open ? group.models : group.models.filter((m) => !tucked(m))
        return (
          <section key={group.task} data-testid={`imp-group-${group.task}`}>
            <h3 className="imp-group-h">
              {group.label}
              <span className="ct">· {group.models.length}</span>
            </h3>
            <div className="imp-grid">
              {shown.map((m) => {
                const alternative = available && m.fit?.verdict === 'wont_fit' ? suggestAlternative(m, models) : null
                return (
                  <ImageModelCard
                    key={m.id}
                    model={m}
                    job={jobFor(m)}
                    rate={rates[m.id]}
                    engineAvailable={available}
                    isDefault={defaultId === m.id}
                    alternative={alternative}
                    alternativeJob={alternative ? jobFor(alternative) : null}
                    installError={installErrors[m.id]}
                    actions={actions}
                    settingDefault={sel.applyCapability.isPending}
                  />
                )
              })}
            </div>
            {hidden.length > 0 && (
              <button
                className="btn ghost sm imp-more"
                aria-expanded={open}
                onClick={() => setExpanded((prev) => ({ ...prev, [group.task]: !open }))}
              >
                {open ? 'Fewer' : `More (${hidden.length})`}
              </button>
            )}
          </section>
        )
      })}

      <ConfirmDialog
        open={!!removing}
        destructive
        onCancel={() => setRemoving(null)}
        onConfirm={confirmRemove}
        confirmDisabled={remove.isPending || filesQuery.isLoading}
        title={`Remove ${removing?.label ?? ''}?`}
        message={
          plan ? (
            <RemoveBody
              plan={plan}
              filesKnown={!!filesQuery.data}
              filesLoading={filesQuery.isLoading}
              error={removeError}
            />
          ) : null
        }
        confirmLabel="Remove model"
      />
    </div>
  )
}
