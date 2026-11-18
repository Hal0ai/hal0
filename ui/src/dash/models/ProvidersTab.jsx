// Models → Providers: the list. One section per provider, its models switched on and off in place.
//
// Props:
//   selected  { provider: string | null, model: string | null, editKey?: boolean }
//             what the detail panel shows; `editKey` asks the panel to open its key editor
//   onSelect  (next: { provider, model, editKey? }) => void      replace the selection
//   onAdd     () => void                                         open the Add provider modal
//
// A section loads its model rows only while it is expanded. Switches read the cached listing, so
// a refused change shows the row back where it was (the patch hook rolls the cache back).
import { useEffect, useState } from 'react'
import { PillToggle } from '@/dash/primitives.jsx'
import { CatalogToolbar } from '@/dash/models/catalog-shell.jsx'
import {
  useProvidersCatalog,
  useUpstreamModelPatch,
  useUpstreamModels,
  useUpstreamRefresh,
  useUpstreams,
} from '@/api/hooks/useUpstreams'
import {
  OFF_PAGE,
  countLabel,
  formatContext,
  formatPrice,
  keyState,
  providerState,
  rowFlag,
  sectionRows,
  toggleErrorMessage,
} from '@/dash/models/providers-pure'

const TYPE_CHIPS = [
  { id: 'chat', label: 'chat' },
  { id: 'embed', label: 'embed' },
  { id: 'tts', label: 'speech' },
  { id: 'stt', label: 'transcribe' },
  { id: 'rerank', label: 'rerank' },
  { id: 'image', label: 'image' },
]

const SHOW_CHIPS = [
  { id: 'all', label: 'All' },
  { id: 'on', label: 'On' },
  { id: 'off', label: 'Available' },
]

const KEY_CHIP = {
  set: { label: 'key set', tone: 'ok' },
  missing: { label: 'no key', tone: 'warn' },
  'not-needed': { label: 'no key needed', tone: '' },
  managed: { label: 'agent key', tone: '' },
}

export function toast(msg, kind = 'info') {
  if (typeof window !== 'undefined' && window.__hal0Toast) window.__hal0Toast(msg, kind)
}

/** Providers listed here: operator remotes by name, then managed ones. Slot-backed upstreams
 * are local models, not providers, so they never appear. */
export function visibleProviders(list) {
  const remotes = (Array.isArray(list) ? list : []).filter((u) => u.kind !== 'slot' && !u.slot_name)
  const byName = (a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0)
  const operator = remotes.filter((u) => providerState(u) !== 'managed').sort(byName)
  const managed = remotes.filter((u) => providerState(u) === 'managed').sort(byName)
  return [...operator, ...managed]
}

export function providerLabel(u, catalog) {
  return (u.catalog_id && catalog?.[u.catalog_id]?.name) || u.name
}

/** Who keeps a managed provider up to date, in plain words. */
export function ownerLabel(u) {
  if (!u.managed_by) return 'hal0'
  const m = /^agent:([A-Za-z0-9_-]+)$/.exec(u.managed_by)
  if (!m) return 'an agent'
  return `the ${m[1][0].toUpperCase()}${m[1].slice(1)} agent`
}

function hostOf(url) {
  try {
    return new URL(url).host
  } catch {
    return url || ''
  }
}

function initials(label) {
  return label.replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '··'
}

/** Refresh the model list. A not-converted provider becomes per-model with everything on;
 * the answer's `warning` is shown verbatim. */
export async function refreshModelList(refresh, u, label) {
  try {
    const res = await refresh.mutateAsync(u.name)
    if (res?.warning) toast(res.warning, 'warn')
    else if (!u.curated && res?.curated) {
      toast(`${label} converted. Everything it offered is on; switch off what you do not use.`, 'ok')
    } else toast(`${label}: model list refreshed`, 'ok')
  } catch {
    toast(`Could not fetch the model list from ${label}. Check the key and the address, then try again.`, 'err')
  }
}

function ModelRow({ u, m, state, selected, onPick, onToggle }) {
  const flag = rowFlag(m)
  const call = m.canonical_id || `${u.name}/${m.id}`
  return (
    <div
      className={'prov-row' + (selected ? ' sel' : '') + (m.enabled ? '' : ' off')}
      data-testid={`prov-model-${u.name}-${m.id}`}
    >
      <PillToggle
        on={m.enabled}
        disabled={state !== 'per-model'}
        label={`${m.enabled ? 'Switch off' : 'Switch on'} ${call}`}
        onToggle={(next) => onToggle(m, next)}
      />
      <button
        type="button"
        className="prov-pick"
        aria-label={`Show details of ${call}`}
        aria-current={selected ? 'true' : undefined}
        onClick={onPick}
      >
        <span className="prov-mid">
          <b>{m.id}</b>
          {flag && <span className={'chip prov-flag ' + (flag === 'gone' ? 'err' : 'amber')}>{flag === 'gone' ? 'gone' : 'recommended'}</span>}
          <small>{call}</small>
        </span>
        <span className="prov-meta">
          <span className="chip">{m.type}</span>
          <span className="num">{formatContext(m.context)}</span>
          <span className="num">{formatPrice(m)}</span>
        </span>
      </button>
    </div>
  )
}

function ProviderSection({ u, catalog, selected, expanded, searching, loadSearch, filter, onHeader, onSelect }) {
  const label = providerLabel(u, catalog)
  const key = keyState(u)
  const isSel = selected.provider === u.name
  const [offShown, setOffShown] = useState(OFF_PAGE)
  // A search opens every section for the rows it shows, without touching the remembered state.
  const shown = expanded || searching
  const listing = useUpstreamModels(u.name, { enabled: expanded || loadSearch })
  const state = providerState(u, listing.data)
  const patch = useUpstreamModelPatch()
  const refresh = useUpstreamRefresh()

  const rows = listing.data?.models ?? []
  const parts = sectionRows(rows, filter, offShown)
  const owner = ownerLabel(u)
  const matches = parts.on.length + parts.offTotal
  const noMatch = searching && !expanded && !!listing.data && matches === 0
  const body = shown && !noMatch
  const chip = KEY_CHIP[key]

  const toggle = async (m, next) => {
    try {
      await patch.mutateAsync({ name: u.name, modelId: m.id, patch: { enabled: next } })
      toast(`${u.name}/${m.id} switched ${next ? 'on' : 'off'}`, 'ok')
    } catch (err) {
      toast(toggleErrorMessage(err || {}), 'err')
    }
  }

  const renderRow = (m) => (
    <ModelRow
      key={m.id}
      u={u}
      m={m}
      state={state}
      selected={isSel && selected.model === m.id}
      onPick={() => onSelect({ provider: u.name, model: m.id })}
      onToggle={toggle}
    />
  )

  return (
    <section className="prov-sec" data-testid={`prov-section-${u.name}`}>
      <button
        type="button"
        className={'prov-head' + (isSel ? ' sel' : '') + (noMatch ? ' muted' : '')}
        aria-expanded={shown}
        data-testid={`prov-head-${u.name}`}
        onClick={onHeader}
      >
        <span className="prov-chev" aria-hidden="true">{expanded ? '▾' : '▸'}</span>
        <span className="prov-logo" aria-hidden="true">{initials(label)}</span>
        <span className="prov-name">{label}</span>
        <span className="prov-host">{hostOf(u.url)}</span>
        {noMatch && <span className="prov-nomatch">no match</span>}
        <span className="prov-chips">
          <span className={'chip' + (chip.tone ? ' ' + chip.tone : '')}>{chip.label}</span>
          <span className="chip">{searching && listing.data ? `${matches} match` : countLabel(u)}</span>
        </span>
      </button>
      {body && (
        <div className="prov-body">
          {state === 'managed' && (
            <div className="prov-banner info" data-testid={`prov-banner-managed-${u.name}`}>
              <p>{`Created and kept up to date by ${owner}. Read-only here.`}</p>
            </div>
          )}
          {state === 'not-converted' && (
            <div className="prov-banner" data-testid={`prov-banner-convert-${u.name}`}>
              <p>Added before per-model switches. It still sends any model it lists.</p>
              <button
                type="button"
                className="btn sm"
                disabled={refresh.isPending}
                onClick={() => refreshModelList(refresh, u, label)}
              >
                Choose models
              </button>
            </div>
          )}
          {key === 'missing' && (
            <div className="prov-banner" data-testid={`prov-banner-key-${u.name}`}>
              <p>No key stored. Models that are on will fail until a key is added.</p>
              <button
                type="button"
                className="btn sm"
                onClick={() => onSelect({ provider: u.name, model: null, editKey: true })}
              >
                Add key
              </button>
            </div>
          )}
          {listing.isLoading && <div className="prov-msg">Loading models…</div>}
          {listing.isError && (
            <div className="prov-msg err" role="alert">
              Could not load this provider's models. Reload the list to try again.
            </div>
          )}
          {listing.data?.stale && (
            <div className="prov-msg quiet">Model list could not be refreshed; showing the last known list.</div>
          )}
          {listing.data && rows.length === 0 && (
            <div className="prov-msg">This provider has not listed any models yet.</div>
          )}
          {listing.data && rows.length > 0 && parts.on.length === 0 && parts.offTotal === 0 && (
            <div className="prov-msg">No models match the filter.</div>
          )}
          {parts.on.length > 0 && (
            <div data-testid={`prov-on-${u.name}`}>
              <div className="prov-subh">{`On · ${parts.on.length}`}</div>
              {parts.on.map(renderRow)}
            </div>
          )}
          {parts.offTotal > 0 && (
            <div data-testid={`prov-off-${u.name}`}>
              <div className="prov-subh">{`Available · ${parts.offTotal}`}</div>
              {parts.off.map(renderRow)}
              {parts.offHidden > 0 && (
                <div className="prov-more">
                  <span>{`${parts.offHidden} more available`}</span>
                  <button type="button" className="btn ghost sm" onClick={() => setOffShown((n) => n + OFF_PAGE)}>
                    {`Show ${Math.min(OFF_PAGE, parts.offHidden)} more`}
                  </button>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </section>
  )
}

export function ProvidersTab({ selected, onSelect, onAdd }) {
  const upstreams = useUpstreams()
  const catalogQ = useProvidersCatalog()
  const sel = selected || { provider: null, model: null }
  const [q, setQ] = useState('')
  const [show, setShow] = useState('all')
  const [type, setType] = useState('any')
  // Per provider, this session only. Unset means: open when it is the selected provider.
  const [open, setOpen] = useState({})
  // What starts a listing load while searching trails the box by 250 ms; filtering is immediate.
  const [loadQ, setLoadQ] = useState('')
  useEffect(() => {
    const t = setTimeout(() => setLoadQ(q), 250)
    return () => clearTimeout(t)
  }, [q])

  const providers = visibleProviders(upstreams.data)
  const filter = { q, show, type }
  const groups = [
    {
      id: 'show',
      label: 'Show',
      chips: SHOW_CHIPS.map((c) => ({ ...c, id: 'show-' + c.id })),
      selected: ['show-' + show],
      onToggle: (id) => setShow(id.slice('show-'.length)),
    },
    {
      id: 'type',
      label: 'Type',
      chips: TYPE_CHIPS.map((c) => ({ ...c, id: 'type-' + c.id })),
      selected: type === 'any' ? [] : ['type-' + type],
      onToggle: (id) => {
        const t = id.slice('type-'.length)
        setType((cur) => (cur === t ? 'any' : t))
      },
    },
  ]

  const header = (u) => {
    const expanded = open[u.name] ?? sel.provider === u.name
    if (sel.provider === u.name) {
      setOpen((o) => ({ ...o, [u.name]: !expanded }))
      return
    }
    setOpen((o) => ({ ...o, [u.name]: true }))
    onSelect({ provider: u.name, model: null })
  }

  if (upstreams.isLoading) {
    return (
      <div className="providers-tab mdl-list" data-testid="providers-tab">
        <div className="mdl-list-msg">Loading providers…</div>
      </div>
    )
  }
  if (upstreams.isError) {
    return (
      <div className="providers-tab mdl-list" data-testid="providers-tab">
        <div className="mdl-list-msg err" role="alert">Could not load providers. Reload the page to try again.</div>
      </div>
    )
  }
  if (providers.length === 0) {
    return (
      <div className="providers-tab mdl-list" data-testid="providers-tab">
        <div className="prov-empty" data-testid="prov-empty">
          <div className="prov-empty-title">No providers yet</div>
          <p>Add a remote provider to switch its models on and call them through hal0.</p>
          <button type="button" className="btn" onClick={onAdd}>+ Add provider</button>
        </div>
      </div>
    )
  }

  return (
    <div className="providers-tab mdl-list" data-testid="providers-tab">
      <CatalogToolbar
        q={q}
        onQ={setQ}
        placeholder="Search models across providers"
        groups={groups}
        onClear={() => {
          setQ('')
          setShow('all')
          setType('any')
        }}
      />
      {providers.map((u) => (
        <ProviderSection
          key={u.name}
          u={u}
          catalog={catalogQ.data}
          selected={sel}
          expanded={open[u.name] ?? sel.provider === u.name}
          searching={q.trim() !== ''}
          loadSearch={loadQ.trim() !== ''}
          filter={filter}
          onHeader={() => header(u)}
          onSelect={(next) => {
            setOpen((o) => ({ ...o, [u.name]: true }))
            onSelect(next)
          }}
        />
      ))}
    </div>
  )
}
