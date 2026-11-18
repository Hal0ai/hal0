// Models → Providers: the right panel. A provider's facts and actions, or one model's facts and edit.
//
// Props:
//   selected  { provider: string | null, model: string | null, editKey?: boolean }   what to show
//   onSelect  (next: { provider, model }) => void                  replace the selection
//
// `selected.editKey` is how the list's "Add key" banner opens the key editor here. The panel
// takes the flag, opens the editor and clears it from the selection. A key typed into the editor
// lives only in the input's own state; it is cleared on save, on cancel and when another provider
// is selected, and it is never rendered, logged or sent anywhere but the credentials route.
import { useEffect, useState } from 'react'
import { ConfirmDialog, PillToggle } from '@/dash/primitives.jsx'
import { DetailActions, DetailCard, DetailEmpty, DetailFacts, DetailHeader, DetailLine, DetailSection } from '@/dash/models/catalog-shell.jsx'
import { fmtAgo } from '@/dash/agents/bridge-format'
import { copyTextToClipboard } from '@/lib/clipboard'
import {
  useProviderCredentialSet,
  useProvidersCatalog,
  useUpstreamDelete,
  useUpstreamModelPatch,
  useUpstreamModels,
  useUpstreamRefresh,
  useUpstreamTest,
  useUpstreamUpdate,
  useUpstreams,
} from '@/api/hooks/useUpstreams'
import {
  MODEL_TYPE_CHOICES,
  formatContext,
  formatPrice,
  hostOf,
  keyState,
  providerLabel,
  providerState,
  toggleErrorMessage,
  visibleProviders,
} from '@/dash/models/providers-pure'
import { refreshModelList, toast } from '@/dash/models/ProvidersTab.jsx'

const MANAGED_NOTE = 'Managed providers cannot be edited or removed here.'

function KeyEditor({ u, onDone }) {
  const save = useProviderCredentialSet()
  const [value, setValue] = useState('')
  const [err, setErr] = useState('')
  const env = u.auth_value_env
  const inputId = `prov-key-${u.name}`

  const cancel = () => {
    setValue('')
    setErr('')
    onDone()
  }
  const submit = async (e) => {
    e.preventDefault()
    if (!value) return
    setErr('')
    try {
      await save.save({ name: u.name, key: env, value })
      setValue('')
      toast(`Key saved for ${u.name}`, 'ok')
      onDone()
    } catch (e) {
      setErr(`Could not save the key: ${e?.message || 'unknown error'}. Check the message and try again.`)
    }
  }
  return (
    <form className="prov-keyform" onSubmit={submit} data-testid="prov-key-editor" autoComplete="off">
      <label htmlFor={inputId} className="prov-keylbl mono">{env}</label>
      <input
        id={inputId}
        className="input mono"
        type="password"
        autoComplete="new-password"
        autoFocus
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="prov-hint">Stored on this box only. Never shown again.</div>
      {err && <div className="prov-msg err" role="alert">{err}</div>}
      <div className="prov-actions">
        <button type="submit" className="btn sm" disabled={!value || save.isPending}>Save key</button>
        <button type="button" className="btn ghost sm" onClick={cancel}>Cancel</button>
      </div>
    </form>
  )
}

function ProviderPanel({ u, selected, onSelect, catalog }) {
  const listing = useUpstreamModels(u.name)
  const state = providerState(u, listing.data)
  const key = keyState(u)
  const label = providerLabel(u, catalog)
  const test = useUpstreamTest()
  const update = useUpstreamUpdate()
  const refresh = useUpstreamRefresh()
  const del = useUpstreamDelete()
  const upstreams = useUpstreams()
  const [editing, setEditing] = useState(false)
  const [confirm, setConfirm] = useState(false)
  const [testLine, setTestLine] = useState(null)

  // The list's "Add key" button asks for the editor through the selection.
  useEffect(() => {
    if (selected.editKey && state !== 'managed') {
      setEditing(true)
      onSelect({ provider: u.name, model: null })
    }
  }, [selected.editKey, state, u.name, onSelect])

  const managed = state === 'managed'
  const checked = listing.data?.fetched_at ? fmtAgo(listing.data.fetched_at, Date.now()) : 'never'
  const keyFact = managed
    ? 'held by the agent'
    : key === 'not-needed'
      ? 'not needed'
      : `${u.auth_value_env || '—'} · ${u.auth_key_present ? 'set' : 'not set'}`

  const runTest = async () => {
    setTestLine(null)
    try {
      const r = await test.mutateAsync(u.name)
      setTestLine(
        r.ok
          ? { ok: true, text: `Reachable · ${r.latency_ms ?? '?'} ms · ${r.models_count ?? 0} models` }
          : { ok: false, text: r.error || 'The provider did not answer. Check the address and the key.' },
      )
    } catch {
      setTestLine({ ok: false, text: 'Could not run the test. Try again.' })
    }
  }

  const setAdvertise = async (next) => {
    try {
      await update.mutateAsync({ name: u.name, patch: { advertise_models: next } })
    } catch {
      toast('Could not change this setting. Try again.', 'err')
    }
  }

  const remove = async () => {
    setConfirm(false)
    try {
      await del.mutateAsync(u.name)
      toast(`${u.name} removed`, 'ok')
      const rest = visibleProviders(upstreams.data).filter((x) => x.name !== u.name)
      onSelect({ provider: rest[0]?.name ?? null, model: null })
    } catch {
      toast(`Could not remove ${u.name}. Try again.`, 'err')
    }
  }

  const facts = [
    { k: 'Name', v: u.name, testid: 'prov-fact-name' },
    { k: 'Base URL', v: u.url, wrap: true, wide: true, testid: 'prov-fact-url' },
    { k: 'Key', v: keyFact, testid: 'prov-fact-key' },
    { k: 'Model list checked', v: checked, testid: 'prov-fact-checked' },
  ]

  return (
    <DetailCard testid="prov-detail-provider">
      <DetailHeader
        state={{ installed: u.enabled !== false, inUseBy: [] }}
        name={label}
        source={u.name === label ? hostOf(u.url) : `${u.name} · ${hostOf(u.url)}`}
        chips={
          <>
            <span className="chip">{state === 'managed' ? 'read-only' : state === 'not-converted' ? 'routes everything' : 'per-model'}</span>
            {key === 'missing' && <span className="chip warn">no key</span>}
          </>
        }
      />
      <DetailFacts facts={facts} />
      <DetailSection title="In app model pickers">
        <DetailLine k="Shown in app model pickers">
          <PillToggle
            on={u.advertise_models}
            disabled={managed || update.isPending}
            label="Show this provider's models in app model pickers"
            stateText={u.advertise_models ? 'on' : 'off'}
            onToggle={setAdvertise}
          />
        </DetailLine>
      </DetailSection>
      {managed ? (
        <DetailActions note={MANAGED_NOTE} />
      ) : (
        <>
          <DetailSection title="Actions">
            <div className="prov-actions">
              <button type="button" className="btn ghost sm" disabled={test.isPending} onClick={runTest}>
                {test.isPending ? 'Testing…' : 'Test connection'}
              </button>
              {state === 'not-converted' ? (
                <button type="button" className="btn ghost sm" disabled={refresh.isPending} onClick={() => refreshModelList(refresh, u, label)}>
                  Choose models
                </button>
              ) : (
                <button type="button" className="btn ghost sm" disabled={refresh.isPending} onClick={() => refreshModelList(refresh, u, label)}>
                  Refresh model list
                </button>
              )}
              {key !== 'not-needed' && u.auth_value_env && !editing && (
                <button type="button" className="btn ghost sm" onClick={() => setEditing(true)}>
                  {u.auth_key_present ? 'Replace key' : 'Add key'}
                </button>
              )}
              <button type="button" className="btn danger sm" onClick={() => setConfirm(true)}>Remove</button>
            </div>
            {testLine && (
              <div className={'prov-msg' + (testLine.ok ? ' ok' : ' err')} role="status" data-testid="prov-test-line">
                {testLine.text}
              </div>
            )}
            {key !== 'not-needed' && !u.auth_value_env && (
              <div className="prov-msg" data-testid="prov-no-key-slot">This provider has no key slot configured.</div>
            )}
            {editing && u.auth_value_env && <KeyEditor key={u.name} u={u} onDone={() => setEditing(false)} />}
          </DetailSection>
          <ConfirmDialog
            open={confirm}
            destructive
            title={`Remove ${u.name}?`}
            message="Its models stop being callable through hal0. The stored key is kept on this box."
            confirmLabel="Remove provider"
            onCancel={() => setConfirm(false)}
            onConfirm={remove}
          />
        </>
      )}
    </DetailCard>
  )
}

function PriceEditor({ m, onSave, onCancel }) {
  const str = (v) => (v === null || v === undefined ? '' : String(v))
  const [ctx, setCtx] = useState(str(m.context))
  const [pin, setPin] = useState(str(m.price_in))
  const [pout, setPout] = useState(str(m.price_out))
  const num = (s) => (s.trim() === '' ? null : Number(s))
  const bad = [ctx, pin, pout].some((s) => s.trim() !== '' && !(Number(s) >= 0))
  return (
    <div className="prov-edit" data-testid="prov-edit">
      <label className="prov-field">
        <span>Context (tokens)</span>
        <input className="input mono" inputMode="numeric" value={ctx} onChange={(e) => setCtx(e.target.value)} />
      </label>
      <label className="prov-field">
        <span>{`Price in (per ${m.price_unit})`}</span>
        <input className="input mono" inputMode="decimal" value={pin} onChange={(e) => setPin(e.target.value)} />
      </label>
      <label className="prov-field">
        <span>{`Price out (per ${m.price_unit})`}</span>
        <input className="input mono" inputMode="decimal" value={pout} onChange={(e) => setPout(e.target.value)} />
      </label>
      {bad && <div className="prov-msg err" role="alert">Use numbers of 0 or more, or leave a field empty.</div>}
      <div className="prov-actions">
        <button
          type="button"
          className="btn sm"
          disabled={bad}
          onClick={() => onSave({ context: num(ctx), price_in: num(pin), price_out: num(pout) })}
        >
          Save
        </button>
        <button type="button" className="btn ghost sm" onClick={onCancel}>Cancel</button>
      </div>
    </div>
  )
}

function ModelPanel({ u, modelId, onSelect, catalog }) {
  const label = providerLabel(u, catalog)
  const listing = useUpstreamModels(u.name)
  const state = providerState(u, listing.data)
  const patch = useUpstreamModelPatch()
  const [editing, setEditing] = useState(false)
  const m = listing.data?.models.find((x) => x.id === modelId)
  const readOnly = state !== 'per-model'
  const back = () => onSelect({ provider: u.name, model: null })

  if (!m) {
    return (
      <DetailCard testid="prov-detail-model">
        <DetailSection title={modelId}>
          <div className="prov-msg">{listing.isLoading ? 'Loading…' : 'This model is no longer in the list.'}</div>
          <div className="prov-actions">
            <button type="button" className="btn ghost sm" onClick={back}>Back to provider</button>
          </div>
        </DetailSection>
      </DetailCard>
    )
  }
  const call = m.canonical_id || `${u.name}/${m.id}`

  const send = async (body, done) => {
    try {
      await patch.mutateAsync({ name: u.name, modelId: m.id, patch: body })
      done?.()
    } catch (err) {
      toast(toggleErrorMessage(err || {}), 'err')
    }
  }
  const copy = async () => {
    toast((await copyTextToClipboard(call)) ? 'Name copied' : 'Could not copy the name', 'info')
  }

  return (
    <DetailCard testid="prov-detail-model">
      <DetailHeader
        state={{ installed: m.enabled, inUseBy: m.in_use_by ?? [] }}
        name={m.id}
        source={label}
        chips={<span className="chip">{m.enabled ? 'on' : 'off'}</span>}
      />
      <DetailSection title="Call it as">
        <div className="prov-call">
          <code className="mono" data-testid="prov-call-name">{call}</code>
          <button type="button" className="btn ghost sm" onClick={copy}>Copy</button>
        </div>
      </DetailSection>
      <DetailSection title="Facts">
        <DetailLine k="Type">
          <select
            className="input mono"
            aria-label="Model type"
            value={m.type}
            disabled={readOnly}
            onChange={(e) => send({ type: e.target.value })}
          >
            {MODEL_TYPE_CHOICES.map(({ id, label }) => (
              <option key={id} value={id}>{label}</option>
            ))}
          </select>
        </DetailLine>
        {editing ? (
          <PriceEditor m={m} onCancel={() => setEditing(false)} onSave={(body) => send(body, () => setEditing(false))} />
        ) : (
          <>
            <DetailLine k="Context">{formatContext(m.context)}</DetailLine>
            <DetailLine k="Price">{formatPrice(m)}</DetailLine>
            {!readOnly && (
              <div className="prov-actions">
                <button type="button" className="btn ghost sm" onClick={() => setEditing(true)}>Edit</button>
              </div>
            )}
          </>
        )}
        <DetailLine k="Offered by provider">{m.discovered ? 'yes' : 'no longer listed'}</DetailLine>
      </DetailSection>
      <DetailActions note={readOnly ? (state === 'managed' ? MANAGED_NOTE : 'Choose models for this provider to change its models.') : null}>
        {!readOnly && (
          <button
            type="button"
            className="btn sm"
            disabled={patch.isPending}
            onClick={() => send({ enabled: !m.enabled })}
          >
            {m.enabled ? 'Switch off' : 'Switch on'}
          </button>
        )}
        <button type="button" className="btn ghost sm" onClick={back}>Back to provider</button>
      </DetailActions>
    </DetailCard>
  )
}

export function ProviderDetail({ selected, onSelect }) {
  const upstreams = useUpstreams()
  const catalogQ = useProvidersCatalog()
  const sel = selected || { provider: null, model: null }
  const u = sel.provider ? (Array.isArray(upstreams.data) ? upstreams.data : []).find((x) => x.name === sel.provider) : null

  let body
  if (!u) body = <DetailEmpty>Select a provider to see its details.</DetailEmpty>
  else if (sel.model) body = <ModelPanel key={`${u.name}/${sel.model}`} u={u} modelId={sel.model} onSelect={onSelect} catalog={catalogQ.data} />
  else body = <ProviderPanel key={u.name} u={u} selected={sel} onSelect={onSelect} catalog={catalogQ.data} />
  return (
    <div className="provider-detail" data-testid="provider-detail">
      {body}
    </div>
  )
}
