// Models → Image → Models: the ComfyUI image-model catalog on the Models page's shared catalog
// shell, with each model's install lifecycle, its fit against this box and the actions that follow
// from its state.
//
// Data: `useComfyuiModels` (GET /api/comfyui/models: rows, engine, store), `useImageJobs` (the
// installs, polled while one runs), `useComfyuiFiles` (what removing a model would delete and
// keep, read only while a removal is being confirmed). The verdict on every model is the server's
// (`model.fit`); nothing here judges a fit. The Image Gen pane's own Models tab
// (ImageModelsPanel) shows the same catalog as cards with its first-run picks; this is the same
// data in the Models page's list-and-detail layout.
//
// `viewSwitch` is the Models | Files switch ImageModelsTab draws in the list header. `addSeq` is
// bumped by the page header's "Install image model": it shows the models that are not installed.
import { useEffect, useMemo, useRef, useState } from 'react'
import { isImageJobActive } from '@/api/hooks/imageJobState'
import {
  useComfyuiFiles,
  useComfyuiModels,
  useImageJobCancel,
  useImageJobs,
  useImageModelInstall,
  useImageModelRemove,
} from '@/api/hooks/useComfyui'
import { pageSections, sectionKey } from '@/dash/models/catalog-pure'
import {
  CatalogList,
  CatalogRow,
  CatalogToolbar,
  DetailActions,
  DetailCard,
  DetailEmpty,
  DetailFacts,
  DetailHeader,
  DetailLine,
  DetailSection,
  DetailUsedBy,
  StatusChip,
  toggleIn,
} from '@/dash/models/catalog-shell.jsx'
import { DownloadsPane } from '@/dash/models/DownloadsPane.jsx'
import {
  IMAGE_FIT_FILTERS,
  IMAGE_SORT_FIELDS,
  IMAGE_STATE_FILTERS,
  filterImageModels,
  imageState,
  imageTaskFilters,
  sortImageModels,
} from '@/dash/models/image-catalog-pure'
import { ConfirmDialog } from '@/dash/primitives.jsx'
import { useCapabilitySelection } from '@/dash/settings/pages/capabilities/useCapabilitySelection.js'
import { FitMeter } from './FitMeter'
import { LoadError, RemoveBody, Unreadable } from './ImageModelsPanel'
import { stateAction, taskLabel } from './image-models-pure'
import { fmtEta, gbLabel, gibOf, modelBadges, perUnit, removalPlan, suggestAlternative } from './models-panel-pure'
import { useJobRates } from './useJobRates'

const note = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)
const IMAGE_GEN = '#slots/image/generate'
const IMAGE_ENGINE = '#slots/image/engine'
const SECTIONS = [
  { key: 'inuse', label: 'In use' },
  { key: 'installed', label: 'Installed' },
  { key: 'available', label: 'Not installed' },
]
const BADGE_TONE = { Recommended: 'amber', Default: 'ok', 'Editor only': '', Large: 'warn', 'Non-commercial': 'warn' }

function ImageDetail({ model, state, job, rate, engineAvailable, isDefault, alternative, installError, actions, settingDefault }) {
  if (!model) return <DetailEmpty />
  const complete = model.state === 'complete'
  const running = isImageJobActive(job)
  const act = stateAction(model, job, engineAvailable)
  const failed = job?.state === 'failed' && !complete && !!job.error
  const onState = () => {
    if (act.id === 'install') actions.onInstall(model)
    else if (act.id === 'cancel') actions.onCancel(model)
    else actions.onRemove(model)
  }
  return (
    <DetailCard testid="img-detail">
      <DetailHeader
        state={state}
        name={model.label}
        source={model.id}
        chips={<StatusChip state={state} testid="img-detail-status" />}
      />
      <DetailFacts
        facts={[
          { k: 'task', v: taskLabel(model.task) },
          { k: 'license', v: model.license },
          { k: 'size', v: `${gbLabel(model.approx_gb)} GB` },
          { k: 'speed', v: model.est_seconds ? `~${model.est_seconds} s per ${perUnit(model.task)}` : null },
          { k: 'runs through', v: model.api_renderable ? 'API and editor' : 'editor only' },
          { k: 'native size', v: model.sizes?.[0] },
        ]}
      />
      <DetailSection title="Fit on this box" testid="img-detail-fit">
        <FitMeter fit={model.fit} engineAvailable={engineAvailable} id={model.id} />
        {alternative && (
          <button
            type="button"
            className="btn ghost sm"
            style={{ marginTop: 8 }}
            data-testid={`img-alternative-${model.id}`}
            onClick={() => actions.onShow(alternative)}
          >
            Show {alternative.label} instead
          </button>
        )}
      </DetailSection>
      {model.missing?.length > 0 && !complete && (
        <DetailSection title={`Files still to download · ${gbLabel(model.missing_gb)} GB`} testid="img-detail-missing">
          {model.missing.map((path) => (
            <div key={path} className="mdl-detail-note" style={{ wordBreak: 'break-all' }}>
              {path}
            </div>
          ))}
        </DetailSection>
      )}
      {model.notes && (
        <DetailSection title="Notes">
          <div className="mdl-detail-note" style={{ marginTop: 0 }}>
            {model.notes}
          </div>
        </DetailSection>
      )}
      <DetailUsedBy
        users={isDefault ? [{ name: 'img', detail: 'default image model', href: '#settings/capabilities' }] : []}
        none="Not the default image model."
      />
      {running && job && (
        <DetailSection title="Download" testid={`img-progress-${model.id}`}>
          <DetailLine k={job.state === 'queued' && job.bytes_downloaded === 0 ? 'queued' : 'done'}>
            {gibOf(job.bytes_downloaded)}
            {job.bytes_total > 0 ? ` / ${gibOf(job.bytes_total)}` : ''} GB
          </DetailLine>
          {rate && (
            <DetailLine k="speed">
              {rate.mbPerS.toFixed(1)} MB/s · {fmtEta(rate.etaS)} left
            </DetailLine>
          )}
        </DetailSection>
      )}
      <DetailActions
        note={
          failed || (installError && !complete) ? (
            <>
              {failed && (
                <span role="alert" data-testid={`img-job-error-${model.id}`}>
                  {job.error}
                </span>
              )}
              {installError && !complete && (
                <span role="alert" data-testid={`img-install-error-${model.id}`}>
                  {installError}
                </span>
              )}
            </>
          ) : null
        }
      >
        {complete && model.api_renderable && (
          <a className="btn sm" href={IMAGE_GEN} data-testid={`img-open-gen-${model.id}`}>
            Open Image Gen →
          </a>
        )}
        {complete && !model.api_renderable && (
          <a className="btn ghost sm" href={IMAGE_ENGINE} data-testid={`img-open-editor-${model.id}`}>
            Open the ComfyUI editor →
          </a>
        )}
        {complete && model.api_renderable && !isDefault && (
          <button
            type="button"
            className="btn ghost sm"
            data-testid={`img-set-default-${model.id}`}
            disabled={settingDefault}
            onClick={() => actions.onSetDefault(model)}
          >
            Set as default
          </button>
        )}
        {act.id === 'remove' && <span className="vh-spacer" />}
        <button
          type="button"
          className={'btn sm' + (act.id === 'remove' ? ' danger' : act.id === 'cancel' ? ' ghost' : '')}
          data-testid={`img-action-${model.id}`}
          data-action={act.id}
          disabled={act.disabled}
          title={act.id === 'install' && act.disabled ? 'Image generation is unavailable on this host.' : undefined}
          onClick={onState}
        >
          {failed && act.id === 'install' ? 'Retry install' : act.label}
        </button>
      </DetailActions>
    </DetailCard>
  )
}

export function ImageCatalogView({ viewSwitch = null, addSeq = 0 }) {
  // The verdict on every model moves with what the box has free: re-read it while this shows.
  const modelsQuery = useComfyuiModels({ poll: true })
  const jobsQuery = useImageJobs()
  // The scan walks the tree and sniffs headers: it runs only while a removal is being confirmed.
  const [removing, setRemoving] = useState(null)
  const filesQuery = useComfyuiFiles({ enabled: !!removing })
  const install = useImageModelInstall()
  const remove = useImageModelRemove()
  const cancel = useImageJobCancel()
  const sel = useCapabilitySelection('img', 'img', { withProvider: true })

  const [text, setText] = useState('')
  const [states, setStates] = useState([])
  const [tasks, setTasks] = useState([])
  const [fits, setFits] = useState([])
  const [sortField, setSortField] = useState('catalog')
  const [sortDir, setSortDir] = useState('asc')
  const [selId, setSelId] = useState(null)
  const [installErrors, setInstallErrors] = useState({})
  const [removeError, setRemoveError] = useState(null)
  const root = useRef(null)

  // The header's "Install image model": show what can be installed.
  useEffect(() => {
    if (!addSeq) return
    setStates(['available'])
    setTasks([])
    setFits([])
    setText('')
    root.current?.querySelector('.mdl-search')?.focus()
  }, [addSeq])

  const jobs = jobsQuery.data
  const rates = useJobRates(jobs, jobsQuery.dataUpdatedAt || 0)
  const jobsByModel = useMemo(() => new Map((jobs ?? []).map((j) => [j.model_id, j])), [jobs])

  const data = modelsQuery.data
  const models = Array.isArray(data?.models) ? data.models : null

  // An unreadable tree replaces the view whatever it showed before: the catalog it holds is
  // stale. Any other failure only does when there is no catalog to show; a failed refetch keeps
  // the last one on screen.
  if (modelsQuery.isError) {
    const error = modelsQuery.error
    const retry = () => modelsQuery.refetch()
    if (error?.code === 'comfyui.models_dir_not_writable') return <Unreadable error={error} onRetry={retry} />
  }
  const available = !!data?.engine?.available
  const defaultId = sel.selection?.enabled && sel.selection?.model ? sel.selection.model : null
  // The freshest job: the /jobs poll over the copy the catalog row carried.
  const jobFor = (m) => jobsByModel.get(m.id) ?? m.job ?? null
  const stateOf = (m) => imageState(m, jobFor(m), defaultId, available)

  const every = models ?? []
  const shown = sortImageModels(
    filterImageModels(every, { q: text, states, tasks, fits: fits.includes('fits') }, stateOf),
    sortField,
    sortDir,
  )
  const paged = pageSections(
    SECTIONS.map((s) => ({ ...s, rows: shown.filter((m) => sectionKey(stateOf(m)) === s.key) })),
    1,
    'all',
  )
  // The selection follows the list: the chosen model while it is shown, else the first installed.
  const selected = shown.find((m) => m.id === selId) ?? shown.find((m) => m.state === 'complete') ?? shown[0] ?? null
  const filtered = !!text.trim() || states.length > 0 || tasks.length > 0 || fits.length > 0
  const installedCount = every.filter((m) => m.state === 'complete').length
  const free = data?.store?.free_gb

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
  const actions = {
    onInstall: startInstall,
    onCancel: cancelInstall,
    onRemove: (model) => {
      setRemoveError(null)
      setRemoving(model)
    },
    onSetDefault: setDefault,
    // The suggested model may be hidden by a filter: clear them so its row shows.
    onShow: (model) => {
      setText('')
      setStates([])
      setTasks([])
      setFits([])
      setSelId(model.id)
    },
  }

  const plan = removing ? removalPlan(removing, filesQuery.data?.files ?? [], every) : null
  const alternative =
    selected && available && selected.fit?.verdict === 'wont_fit' ? suggestAlternative(selected, every) : null

  return (
    <div className="models-layout" style={{ marginTop: 18 }} data-testid="img-catalog-view" ref={root}>
      <CatalogList
        toolbar={
          <CatalogToolbar
            q={text}
            onQ={setText}
            placeholder="search name, id, task…"
            groups={[
              { id: 'state', label: 'state', chips: IMAGE_STATE_FILTERS, selected: states, onToggle: (id) => setStates((s) => toggleIn(s, id)) },
              { id: 'task', label: 'task', chips: imageTaskFilters(every), selected: tasks, onToggle: (id) => setTasks((s) => toggleIn(s, id)) },
              // The verdicts are only shown when the engine can run, so only then can they filter.
              ...(available
                ? [{ id: 'fit', label: 'fit', chips: IMAGE_FIT_FILTERS, selected: fits, onToggle: (id) => setFits((s) => toggleIn(s, id)) }]
                : []),
            ]}
            sort={{
              fields: IMAGE_SORT_FIELDS,
              field: sortField,
              dir: sortDir,
              onField: setSortField,
              onDir: () => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc')),
            }}
            onClear={() => {
              setText('')
              setStates([])
              setTasks([])
              setFits([])
            }}
          />
        }
        title="Image / ComfyUI"
        headerExtra={viewSwitch}
        right={`${every.length} in the catalog · ${installedCount} installed · ${
          free == null ? 'free space unknown' : `${free} GB free`
        }`}
        note={
          models && !available ? (
            <div className="mdl-list-note" role="status" data-testid="img-engine-note">
              Image generation is unavailable on this host{data.engine?.reason ? `: ${data.engine.reason}` : '.'} The
              catalog is listed, but nothing can be installed.
            </div>
          ) : null
        }
        paged={paged}
        loading={!models && !modelsQuery.isError ? <span data-testid="img-loading">Loading models…</span> : null}
        error={
          !models && modelsQuery.isError ? <LoadError error={modelsQuery.error} onRetry={() => modelsQuery.refetch()} /> : null
        }
        empty={filtered ? 'No models match — adjust the search or filters.' : 'The image catalog is empty.'}
        renderRow={(m) => (
          <CatalogRow
            key={m.id}
            testid={`img-model-${m.id}`}
            statusTestid={`img-state-${m.id}`}
            state={stateOf(m)}
            selected={selected?.id === m.id}
            onSelect={() => setSelId(m.id)}
            name={m.label}
            sub={[taskLabel(m.task), m.license].filter(Boolean).join(' · ')}
            size={`${gbLabel(m.approx_gb)} GB`}
            tags={
              <>
                {modelBadges(m, defaultId).map((label) => (
                  <span key={label} className={`chip ${BADGE_TONE[label] ?? ''}`}>
                    {label}
                  </span>
                ))}
                {m.state === 'partial' && !isImageJobActive(jobFor(m)) && (
                  <span className="chip warn" title={`${gbLabel(m.missing_gb)} GB still to download`}>
                    partly installed
                  </span>
                )}
              </>
            }
          />
        )}
      />

      <div className="models-sidebar">
        <ImageDetail
          model={selected}
          state={selected ? stateOf(selected) : null}
          job={selected ? jobFor(selected) : null}
          rate={selected ? rates[selected.id] : undefined}
          engineAvailable={available}
          isDefault={!!selected && defaultId === selected.id}
          alternative={alternative}
          installError={selected ? installErrors[selected.id] : undefined}
          actions={actions}
          settingDefault={sel.applyCapability.isPending}
        />
        <DownloadsPane />
      </div>

      <ConfirmDialog
        open={!!removing}
        destructive
        onCancel={() => setRemoving(null)}
        onConfirm={confirmRemove}
        confirmDisabled={remove.isPending || filesQuery.isLoading}
        title={`Remove ${removing?.label ?? ''}?`}
        message={
          plan ? (
            <RemoveBody plan={plan} filesKnown={!!filesQuery.data} filesLoading={filesQuery.isLoading} error={removeError} />
          ) : null
        }
        confirmLabel="Remove model"
      />
    </div>
  )
}
