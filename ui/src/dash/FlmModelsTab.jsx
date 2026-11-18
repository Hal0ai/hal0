// Models → FLM: the FastFlowLM catalogue (GET /api/slots/flm/models) on the Models page's shared
// catalog shell, read from the same hook and row builder the FLM lane's model dropdown uses.
//
// The list shows each model's name, family, roles, size and status, with the same toolbar, sections
// and row as every other tab. The detail card holds what the model can serve, who uses it, and its
// actions: Download for one that is not installed (the same pull the lane starts when you pick
// one); Remove for an installed one, which asks inline (naming the space it frees) and then runs
// `flm remove` (DELETE /api/slots/flm/models/{tag}). Remove is disabled, with the reason beside
// it, while an FLM role uses the model.
//
// The catalogue is a closed list: there is nothing to add by repo or path. Which model the FLM
// process serves is still chosen on the lane (Slots → NPU → Lanes).
//
// `addSeq` is bumped by the page header's "Download FLM model": it shows the models that are not
// installed and puts the cursor in the search box.
import { useEffect, useRef, useState } from 'react'
import { useFlmCatalog, useRemoveFlmModel } from '@/api/hooks/useFlmCatalog'
import { usePullJob } from '@/api/hooks/useModels'
import { pageSections, sectionKey } from './models/catalog-pure'
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
  DetailNone,
  DetailSection,
  DetailUsedBy,
  StatusChip,
  toggleIn,
} from './models/catalog-shell.jsx'
import { DownloadsPane } from './models/DownloadsPane.jsx'
import { LaneCode } from './lane-code.jsx'
import {
  FLM_ROLE_FILTERS,
  FLM_SORT_FIELDS,
  FLM_STATE_FILTERS,
  filterFlmRows,
  flmRoleLines,
  flmState,
  flmUsedBy,
  sortFlmRows,
} from './models/flm-catalog-pure'
import { flmCatalogRows, formatFlmSize } from './npu/npu-models-pure'

const LANES = '#slots/npu/lanes'
const SECTIONS = [
  { key: 'inuse', label: 'In use' },
  { key: 'installed', label: 'Installed' },
  { key: 'available', label: 'Not installed' },
]

function FlmDetail({ row, state, pull, remove }) {
  const [confirming, setConfirming] = useState(false)
  // A different model, or one that is no longer installed, drops a pending confirmation.
  useEffect(() => setConfirming(false), [row?.id, row?.installed])
  if (!row) return <DetailEmpty />

  const size = formatFlmSize(row.sizeBytes)
  const inUse = row.inUseBy.length > 0
  const pulling = !!state.download
  const removing = remove.isPending && remove.variables === row.id
  const failed = remove.isError && remove.variables === row.id
  const warnings = remove.isSuccess && remove.variables === row.id ? (remove.data?.warnings ?? []) : []
  const whyNot = inUse && row.installed && !confirming

  return (
    <DetailCard testid="flm-detail">
      <DetailHeader
        state={state}
        name={row.id}
        source={row.family ? `FastFlowLM · ${row.family}` : 'FastFlowLM'}
        chips={<StatusChip state={state} testid="flm-detail-status" />}
      />
      <DetailFacts
        facts={[
          { k: 'family', v: row.family },
          { k: 'roles', v: row.caps.join(' · ') },
          { k: 'size', v: size },
          { k: 'audio input', v: row.audio ? 'yes' : 'no' },
          { k: 'runs on', v: 'NPU' },
          { k: 'engine', v: 'FastFlowLM' },
        ]}
      />
      <DetailSection title="Roles it can serve" testid="flm-detail-roles">
        {flmRoleLines(row).map((l) => (
          <DetailLine key={l.cap} k={l.label}>
            {l.value}
          </DetailLine>
        ))}
      </DetailSection>
      <DetailUsedBy
        users={row.inUseBy.map((u) => ({ name: u.slot, detail: u.role, href: LANES }))}
        none="No FLM role is set to this model."
      />
      <DetailSection title="On disk">
        {row.installed ? (
          <DetailLine k="size">{size ?? '—'}</DetailLine>
        ) : (
          <DetailNone>{size ? `Not downloaded. Needs ${size}.` : 'Not downloaded.'}</DetailNone>
        )}
      </DetailSection>
      <DetailActions
        note={
          whyNot || failed || warnings.length > 0 ? (
            <>
              {whyNot && (
                <span data-testid={`flm-remove-why-${row.id}`}>
                  In use ({flmUsedBy(row)}). Switch that role to another model on the NPU lanes before removing it.
                </span>
              )}
              {failed && (
                <span role="alert" data-testid={`flm-remove-error-${row.id}`}>
                  {remove.error?.message ?? 'Remove failed.'}
                </span>
              )}
              {warnings.length > 0 && <span data-testid={`flm-remove-warning-${row.id}`}>{warnings.join(' ')}</span>}
            </>
          ) : null
        }
      >
        {!row.installed && (
          <button
            type="button"
            className="btn sm"
            data-testid={`flm-download-${row.id}`}
            disabled={pull.inFlight}
            onClick={() => pull.start(row.id)}
          >
            {pulling
              ? `Downloading${pull.pct != null ? ` ${Math.round(pull.pct)}%` : '…'}`
              : `Download${size ? ` · ${size}` : ''}`}
          </button>
        )}
        {row.installed && !confirming && (
          <>
            <a className="btn ghost sm" href={LANES} data-testid={`flm-lanes-${row.id}`}>
              Choose on the NPU lanes →
            </a>
            <span className="vh-spacer" />
            <button
              type="button"
              className="btn danger sm"
              data-testid={`flm-remove-${row.id}`}
              disabled={inUse || remove.isPending || pulling}
              title={inUse ? `In use (${flmUsedBy(row)}). Switch that role to another model on the NPU lanes first.` : undefined}
              onClick={() => {
                remove.reset()
                setConfirming(true)
              }}
            >
              {removing ? 'Removing…' : 'Remove'}
            </button>
          </>
        )}
        {row.installed && confirming && (
          <div className="mdl-detail-confirm" data-testid={`flm-remove-confirm-${row.id}`}>
            <span>
              Remove {row.id} from disk{size ? ` and free ${size}` : ''}?
            </span>
            <button
              type="button"
              className="btn sm danger"
              data-testid={`flm-remove-yes-${row.id}`}
              onClick={() => {
                setConfirming(false)
                remove.mutate(row.id)
              }}
            >
              Remove
            </button>
            <button
              type="button"
              className="btn ghost sm"
              data-testid={`flm-remove-no-${row.id}`}
              onClick={() => setConfirming(false)}
            >
              Cancel
            </button>
          </div>
        )}
      </DetailActions>
    </DetailCard>
  )
}

export function FlmModelsTab({ addSeq = 0 }) {
  const q = useFlmCatalog()
  const pull = usePullJob()
  const remove = useRemoveFlmModel()
  const [text, setText] = useState('')
  const [states, setStates] = useState([])
  const [roles, setRoles] = useState([])
  const [sortField, setSortField] = useState('name')
  const [sortDir, setSortDir] = useState('asc')
  const [selId, setSelId] = useState(null)
  const root = useRef(null)

  // The header's "Download FLM model": show what can be downloaded.
  useEffect(() => {
    if (!addSeq) return
    setStates(['available'])
    setRoles([])
    setText('')
    root.current?.querySelector('.mdl-search')?.focus()
  }, [addSeq])

  const all = flmCatalogRows(q.data?.models)
  const shown = sortFlmRows(filterFlmRows(all, { q: text, states, roles }, pull), sortField, sortDir)
  const paged = pageSections(
    SECTIONS.map((s) => ({ ...s, rows: shown.filter((r) => sectionKey(flmState(r, pull)) === s.key) })),
    1,
    'all',
  )
  // The selection follows the list: the chosen row while it is shown, else the first installed.
  const selected = shown.find((r) => r.id === selId) ?? shown.find((r) => r.installed) ?? shown[0] ?? null
  const installed = all.filter((r) => r.installed)
  const onDisk = formatFlmSize(installed.reduce((n, r) => n + (r.sizeBytes ?? 0), 0))
  const unreachable = !!q.data && !q.data.probe_ok && all.length === 0
  const filtered = !!text.trim() || states.length > 0 || roles.length > 0

  return (
    <div className="models-layout" style={{ marginTop: 18 }} data-testid="flm-models-tab" ref={root}>
      <CatalogList
        toolbar={
          <CatalogToolbar
            q={text}
            onQ={setText}
            placeholder="search name, family…"
            groups={[
              { id: 'state', label: 'state', chips: FLM_STATE_FILTERS, selected: states, onToggle: (id) => setStates((s) => toggleIn(s, id)) },
              { id: 'role', label: 'role', chips: FLM_ROLE_FILTERS, selected: roles, onToggle: (id) => setRoles((s) => toggleIn(s, id)) },
            ]}
            sort={{
              fields: FLM_SORT_FIELDS,
              field: sortField,
              dir: sortDir,
              onField: setSortField,
              onDir: () => setSortDir((d) => (d === 'asc' ? 'desc' : 'asc')),
            }}
            onClear={() => {
              setText('')
              setStates([])
              setRoles([])
            }}
          />
        }
        title="FLM Models"
        right={`${all.length} in the catalogue · ${installed.length} on disk${onDisk ? ` · ${onDisk}` : ''}`}
        note={
          <>
            <div className="mdl-list-note" data-testid="flm-models-note">
              FastFlowLM models for the NPU: a closed catalogue, downloaded by tag. Pick which one the FLM process serves
              on the{' '}
              <a href={LANES} data-testid="flm-models-lane-link">
                NPU lanes
              </a>{' '}
              tab.
            </div>
            {pull.error && (
              <div className="mdl-list-msg err" role="alert" data-testid="flm-models-pull-error">
                {pull.error.message}
              </div>
            )}
          </>
        }
        paged={paged}
        loading={q.isLoading ? <span data-testid="flm-models-loading">Loading…</span> : null}
        error={q.isError ? <span data-testid="flm-models-error">Could not read the FLM catalogue.</span> : null}
        empty={
          unreachable ? (
            <span data-testid="flm-models-empty">FLM is not reachable on this box, so its catalogue is empty.</span>
          ) : filtered ? (
            'No models match — adjust the search or filters.'
          ) : (
            'The FLM catalogue is empty.'
          )
        }
        renderRow={(r) => (
          <CatalogRow
            key={r.id}
            testid={`flm-model-${r.id}`}
            statusTestid={`flm-state-${r.id}`}
            sizeTestid={`flm-size-${r.id}`}
            state={flmState(r, pull)}
            selected={selected?.id === r.id}
            onSelect={() => setSelId(r.id)}
            name={r.id}
            sub={r.family ? `FastFlowLM · ${r.family}` : 'FastFlowLM'}
            size={formatFlmSize(r.sizeBytes) ?? '—'}
            tags={
              <>
                {r.caps.map((c) => (
                  <span key={c} className="chip">
                    {c}
                  </span>
                ))}
                {r.audio && (
                  <span className="chip info" title="also takes audio input">
                    audio
                  </span>
                )}
                <LaneCode lane="flm" />
              </>
            }
          />
        )}
      />
      <div className="models-sidebar">
        <FlmDetail row={selected} state={selected ? flmState(selected, pull) : null} pull={pull} remove={remove} />
        <DownloadsPane />
      </div>
    </div>
  )
}
