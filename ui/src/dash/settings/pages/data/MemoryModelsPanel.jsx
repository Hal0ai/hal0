// DATA ▸ Memory ▸ Models — the engine's embedder, reranker and decision model (#3761).
//
// Three groups, each reachable from its Memory ▸ Health tile (`#settings/memory/models/<group>`):
//   Embedder        read-only. The store was built with one model; it changes only through
//                   `sudo hal0 memory embedder switch <model>` (ADR-0058), never from here.
//   Engine reranker [memory.models].reranker_*: the reranker hindsight-api runs on recall.
//                   Not the Fallback reranker panel below, which is hal0's own client.
//   Decision model  [memory.decision]: the optional lane for decision-gated engine operations.
//
// A save goes through PUT /api/settings as a partial holding only the keys that changed. Both the
// reranker and the decision keys re-render the engine's drop-in and restart it before the PUT
// answers, so the save IS the apply; the pending state says so. The pure half is memoryModels.ts.
import { useEffect, useRef, useState } from 'react'
import { useSettingsClient } from '../../data/settingsClient.js'
import { useMemoryModels } from '@/api/hooks/useMemory'
import { ApplyBadge } from '../../shared/ApplyBadge.jsx'
import { SRow } from '../../shared/SRow.jsx'
import { _advInputStyle, _schemaField } from '../../shared/SchemaRow.jsx'
import { CommandBox } from '../../../memory-area/shared/CommandBox.jsx'
import {
  DECISION_INSTALL_COMMAND,
  DECISION_KEYS,
  DECISION_PROVIDERS,
  DECISION_PROVIDER_KEY,
  EMBEDDER_SWITCH_COMMAND,
  PROVIDER_KEY,
  RERANKER_BASIC,
  RERANKER_CAPS,
  RERANKER_PROVIDERS,
  decisionServerNote,
  decisionStatus,
  effective,
  formState,
  getIn,
  localKeys,
  remoteKeys,
  remoteMissing,
  saveErrors,
} from './memoryModels'

const HEAD = { paddingBottom: 4, borderBottom: '1px solid var(--line)' }
const MUTED = { fontSize: 11, color: 'var(--fg-3)', lineHeight: 1.5 }
const PROVIDER_WORDS = {
  local: 'local, in the engine',
  litellm: 'litellm, a remote endpoint',
  tei: 'tei, a text-embeddings-inference server',
  rrf: 'rrf, no cross-encoder (fused ranking only)',
  off: 'off',
  'hindsight-cloud': 'hindsight-cloud, a hosted service (sends memory content off this box)',
}

function Group({ id, title, info, focused, children }) {
  const ref = useRef(null)
  useEffect(() => {
    if (!focused || !ref.current) return
    ref.current.scrollIntoView?.({ block: 'start' })
    ref.current.focus?.({ preventScroll: true })
  }, [focused])
  return (
    <section className="s-panel" style={{ marginBottom: 12 }} id={`memory-models-${id}`} aria-labelledby={`memory-models-${id}-title`} data-testid={`mem-models-${id}`}>
      <div className="s-row" style={HEAD}>
        <div className="k">
          <h3 id={`memory-models-${id}-title`} ref={ref} tabIndex={-1} style={{ margin: 0, fontSize: 'inherit', fontWeight: 'inherit' }}>{title}</h3>
          <FieldInfoIcon description={info} />
        </div>
      </div>
      {children}
    </section>
  )
}

function ModelRow({ spec, field, live, buf, error, disabled, onChange, registry }) {
  const id = `mem-models-${spec.key.replace(/\./g, '-')}`
  const errId = `${id}-err`
  const cur = spec.key in buf ? buf[spec.key] : spec.kind === 'boolean' ? getIn(live, spec.key) === true : getIn(live, spec.key) ?? ''
  const bad = !!error
  let control
  if (spec.kind === 'boolean') {
    control = (
      <label className="mono" style={{ display: 'inline-flex', alignItems: 'center', gap: 8, cursor: 'pointer', color: 'var(--fg-2)' }}>
        <input
          id={id} type="checkbox" checked={!!cur} disabled={disabled}
          onChange={(e) => onChange(spec.key, e.target.checked)}
          style={{ accentColor: 'var(--accent)' }}
          data-testid={id}
        />
        <span>{cur ? 'on' : 'off'}</span>
      </label>
    )
  } else {
    const num = spec.kind === 'integer'
    control = (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, maxWidth: '100%' }}>
        <input
          id={id}
          type={num ? 'number' : 'text'}
          min={num ? field?.minimum : undefined}
          max={num ? field?.maximum : undefined}
          value={cur}
          disabled={disabled}
          placeholder={field?.default != null ? String(field.default) : num ? 'engine default' : ''}
          onChange={(e) => onChange(spec.key, e.target.value)}
          className="mono"
          aria-label={spec.label}
          aria-invalid={bad || undefined}
          aria-describedby={bad ? errId : undefined}
          data-testid={id}
          style={{ ..._advInputStyle, width: num ? 120 : 260, maxWidth: '100%', borderColor: bad ? 'var(--err)' : 'var(--line)' }}
        />
        {spec.unit && <span className="mono" style={{ fontSize: 11, color: 'var(--fg-3)' }}>{spec.unit}</span>}
      </span>
    )
  }
  return (
    <>
      <SRow k={spec.label} sub={spec.hint} v={control} actions={<ApplyBadge settingsKey={spec.key} registry={registry} />} />
      {bad && (
        <div id={errId} role="alert" className="s-row mono" data-testid={`${id}-err`} style={{ fontSize: 11, color: 'var(--err)', paddingTop: 0 }}>
          {error}
        </div>
      )}
    </>
  )
}

function SaveBar({ restarts, dirty, canSave, pending, onReset, onSave, label, testid, outcome }) {
  return (
    <>
      {pending && (
        <div className="s-row mono" role="status" data-testid={`${testid}-restarting`} style={{ ...MUTED, color: 'var(--warn)' }}>
          Saving, and restarting the memory engine. Memory is unavailable for a few seconds.
        </div>
      )}
      {!pending && outcome && (
        <div className="s-row mono" role={outcome.kind === 'err' ? 'alert' : 'status'} data-testid={`${testid}-outcome`} style={{ ...MUTED, color: outcome.kind === 'err' ? 'var(--err)' : 'var(--ok)' }}>
          {outcome.text}
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: 8, padding: '8px 12px 4px', flexWrap: 'wrap' }}>
        {restarts && <span className="mono" style={{ ...MUTED, marginRight: 'auto' }}>Saving restarts the memory engine.</span>}
        {dirty && <button type="button" className="btn ghost sm" onClick={onReset}>Reset</button>}
        <button type="button" className="btn sm" disabled={!canSave} onClick={onSave} data-testid={`${testid}-save`}>
          {pending ? 'Saving…' : label}
        </button>
      </div>
    </>
  )
}

/** One save for one group: the buffer, the diff, the PUT, and what it answered. */
function useGroupForm({ keysOf, live, field, update, ready, refetch, name, extraMissing }) {
  const [buf, setBuf] = useState({})
  const [outcome, setOutcome] = useState(null)
  const [fieldErrors, setFieldErrors] = useState({})
  const keys = keysOf(buf)
  const state = formState(keys, buf, live, field)
  const missing = extraMissing ? extraMissing(buf) : {}
  const clientErrors = { ...missing, ...state.invalid }
  const canSave = ready && state.dirty.length > 0 && Object.keys(clientErrors).length === 0 && !update.isPending

  const onChange = (k, v) => {
    setBuf((b) => ({ ...b, [k]: v }))
    setFieldErrors((e) => {
      if (!(k in e)) return e
      const { [k]: _gone, ...rest } = e
      return rest
    })
    setOutcome(null)
  }
  const reset = () => { setBuf({}); setFieldErrors({}); setOutcome(null) }

  const save = async () => {
    setFieldErrors({})
    setOutcome(null)
    try {
      const resp = await update.mutateAsync(state.patch)
      setBuf({})
      const dropin = resp?._hal0?.showcase_dropin
      if (dropin && dropin.error) {
        setOutcome({ kind: 'err', text: `${name} saved, but the memory engine did not pick it up: ${dropin.error}` })
        window.__hal0Toast && window.__hal0Toast(`${name} saved, engine not restarted`, 'warn')
      } else {
        setOutcome({ kind: 'ok', text: `${name} saved. The memory engine restarted with it.` })
        window.__hal0Toast && window.__hal0Toast(`${name} saved`, 'ok')
      }
      refetch && refetch()
    } catch (e) {
      const owned = keysOf({ ...buf }).map((k) => k.key)
      const { fields, other, message } = saveErrors(e, owned)
      setFieldErrors(fields)
      const extra = other.length ? ` ${other.join('; ')}` : ''
      setOutcome({
        kind: 'err',
        text: Object.keys(fields).length
          ? 'Not saved. Fix the marked fields.' + extra
          : `Not saved: ${message}.${extra}`,
      })
      window.__hal0Toast && window.__hal0Toast(`Save failed: ${message}`, 'err')
    }
  }
  return { buf, onChange, reset, save, state, clientErrors, fieldErrors, canSave, outcome }
}

function EmbedderGroup({ models, focused }) {
  const e = models?.embeddings
  const configured = e?.model
  const configuredProvider = e?.provider
  const recorded = typeof e?.recorded === 'string' && e.recorded ? e.recorded : null
  const recordedProvider = typeof e?.recorded_provider === 'string' && e.recorded_provider ? e.recorded_provider : null
  const built = recorded ?? configured
  return (
    <Group id="embedder" title="Embedder" focused={focused} info="Read only. The embedder turns text into vectors, and every vector in the store came from one model, so it cannot be edited like a setting. It changes only through the switch command, which re-embeds the store.">
      <SRow
        k="Model"
        sub="The embedding model and provider the store was built with."
        mono
        v={<span data-testid="mem-models-embedder-model">{built ? `${recordedProvider ?? configuredProvider ?? ''}${recordedProvider ?? configuredProvider ? ' / ' : ''}${built}` : 'not reported'}</span>}
      />
      <div className="s-row" style={{ display: 'block', ...MUTED }} data-testid="mem-models-embedder-note">
        {recorded
          ? 'The memory store was built with this model. Every stored vector came from it.'
          : 'No store has been built yet, so this is the model the first one will use.'}
        {recorded && configured && configured !== recorded && (
          <> Settings ask for <span className="mono">{configured}</span>, which has not been applied: the store still uses the model above.</>
        )}
      </div>
      <div className="s-row" style={{ display: 'block' }}>
        <div style={{ ...MUTED, marginBottom: 6 }}>To move the store to another embedder, run this on the hal0 host. It re-embeds the store, so it takes a while.</div>
        <CommandBox command={EMBEDDER_SWITCH_COMMAND} copyLabel="Copy the embedder switch command" data-testid="mem-models-embedder-cmd" />
      </div>
    </Group>
  )
}

function RerankerGroup({ settingsData, ready, field, update, registry, refetch, focused }) {
  const form = useGroupForm({
    keysOf: (buf) => {
      const p = effective(buf, settingsData, PROVIDER_KEY, 'local')
      return [{ key: PROVIDER_KEY, kind: 'string' }, ...RERANKER_BASIC, ...RERANKER_CAPS, ...localKeys(p), ...remoteKeys(p)]
    },
    live: settingsData, field, update, ready, refetch,
    name: 'Engine reranker',
    extraMissing: (buf) => remoteMissing(effective(buf, settingsData, PROVIDER_KEY, 'local'), buf, settingsData),
  })
  const provider = effective(form.buf, settingsData, PROVIDER_KEY, 'local')
  const advanced = [...RERANKER_CAPS, ...localKeys(provider), ...remoteKeys(provider)]
  const remote = remoteKeys(provider)
  const err = (k) => form.fieldErrors[k] || form.clientErrors[k]
  const row = (spec) => (
    <ModelRow
      key={spec.key} spec={spec} field={field(spec.key)} live={settingsData} buf={form.buf}
      error={err(spec.key)}
      disabled={!ready} onChange={form.onChange} registry={registry}
    />
  )
  // A remote provider's endpoint is the one thing it cannot work without, so it sits outside the
  // disclosure; the caps and timeouts do not.
  const endpoint = remote.filter((k) => k.key.endsWith('reranker_base_url') || k.key.endsWith('reranker_api_model'))
  const advancedRows = advanced.filter((k) => !endpoint.includes(k))
  return (
    <Group id="reranker" title="Engine reranker" focused={focused} info="hal0.toml [memory.models] · the second-pass ranking hindsight-api runs on every recall. The Fallback reranker panel below is a different component: hal0's own client.">
      <SRow
        k="Provider"
        sub="Where the engine scores recall candidates: in the engine itself (local), a litellm or tei endpoint, or rrf, which skips cross-encoder scoring."
        v={
          <select
            value={provider} disabled={!ready} aria-label="Reranker provider"
            onChange={(e) => form.onChange(PROVIDER_KEY, e.target.value)}
            style={_advInputStyle} data-testid="mem-models-reranker-provider"
          >
            {RERANKER_PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_WORDS[p]}</option>)}
          </select>
        }
        actions={<ApplyBadge settingsKey={PROVIDER_KEY} registry={registry} />}
      />
      {RERANKER_BASIC.map(row)}
      {endpoint.map(row)}
      <details style={{ padding: '4px 12px' }} data-testid="mem-models-reranker-advanced">
        <summary style={{ cursor: 'pointer', fontSize: 11, color: 'var(--fg-3)' }}>Advanced: candidate caps, batching and timeouts</summary>
        {advancedRows.map(row)}
      </details>
      <SaveBar
        restarts testid="mem-models-reranker" label="Save engine reranker"
        dirty={form.state.dirty.length > 0} canSave={form.canSave} pending={update.isPending}
        onReset={form.reset} onSave={form.save} outcome={form.outcome}
      />
    </Group>
  )
}

function DecisionGroup({ settingsData, ready, field, update, registry, refetch, models, modelsError, focused }) {
  const form = useGroupForm({
    keysOf: () => [{ key: DECISION_PROVIDER_KEY, kind: 'string' }, ...DECISION_KEYS],
    live: settingsData, field, update, ready, refetch, name: 'Decision model',
  })
  const provider = effective(form.buf, settingsData, DECISION_PROVIDER_KEY, 'local')
  const status = decisionStatus(models?.decision)
  const note = decisionServerNote(status)
  // The note describes the saved provider; while the form holds an unsaved one it would mislead.
  const noteShown = note && provider === status?.provider
  const err = (k) => form.fieldErrors[k] || (k in form.buf ? form.clientErrors[k] : null)
  return (
    <Group id="decision" title="Decision model" focused={focused} info="hal0.toml [memory.decision] · an optional small model for the engine's decision-gated operations. With it off or unreachable the extraction model makes those judgments.">
      <SRow
        k="Provider"
        sub="local runs hal0's own decision server on this box. hindsight-cloud uses a hosted service. off leaves the judgments to the extraction model."
        v={
          <select
            value={provider} disabled={!ready} aria-label="Decision model provider"
            onChange={(e) => form.onChange(DECISION_PROVIDER_KEY, e.target.value)}
            style={_advInputStyle} data-testid="mem-models-decision-provider"
          >
            {DECISION_PROVIDERS.map((p) => <option key={p} value={p}>{PROVIDER_WORDS[p]}</option>)}
          </select>
        }
        actions={<ApplyBadge settingsKey={DECISION_PROVIDER_KEY} registry={registry} />}
      />
      {provider === 'hindsight-cloud' && (
        <div className="s-row mono" role="note" data-testid="mem-models-decision-cloud" style={{ ...MUTED, color: 'var(--warn)' }}>
          hindsight-cloud sends memory content to a hosted service outside this box. Choose it only if that is acceptable for what your memory holds.
        </div>
      )}
      <SRow
        k="Server"
        sub="Whether the decision server answers, as the memory models read reports it."
        v={
          modelsError ? <span className="mono" style={{ color: 'var(--err)', fontSize: 11 }}>could not read</span>
            : !models ? <span className="mono" style={{ color: 'var(--fg-4)', fontSize: 11 }}>reading…</span>
            : status?.provider === 'off' ? <span className="mono" style={{ fontSize: 11, color: 'var(--fg-3)' }} data-testid="mem-models-decision-status">off</span>
            : <span className="mono" style={{ fontSize: 11, color: status?.reachable ? 'var(--ok)' : 'var(--warn)' }} data-testid="mem-models-decision-status">{status?.reachable ? 'reachable' : 'not answering'}</span>
        }
      />
      {noteShown && (
        <div className="s-row" style={{ display: 'block', ...MUTED, color: note.tone === 'warn' ? 'var(--warn)' : 'var(--fg-3)' }} data-testid="mem-models-decision-note" role={note.tone === 'warn' ? 'status' : undefined}>
          <div style={{ marginBottom: note.tone === 'warn' ? 6 : 0 }}>{note.text}</div>
          {note.tone === 'warn' && (
            <CommandBox command={DECISION_INSTALL_COMMAND} copyLabel="Copy the decision server install command" data-testid="mem-models-decision-cmd" />
          )}
        </div>
      )}
      {DECISION_KEYS.map((spec) => (
        <ModelRow
          key={spec.key} spec={spec} field={field(spec.key)} live={settingsData} buf={form.buf}
          error={err(spec.key)} disabled={!ready || provider !== 'local'} onChange={form.onChange} registry={registry}
        />
      ))}
      {provider !== 'local' && (
        <div className="s-row" style={{ display: 'block', ...MUTED }}>These apply to the local decision server only.</div>
      )}
      <SaveBar
        restarts testid="mem-models-decision" label="Save decision model"
        dirty={form.state.dirty.length > 0} canSave={form.canSave} pending={update.isPending}
        onReset={form.reset} onSave={form.save} outcome={form.outcome}
      />
    </Group>
  )
}

/**
 * @param {{ registry: object, focus?: 'embedder'|'reranker'|'decision'|null }} props `focus` is
 *   the deep-link group: it is scrolled to and its heading focused.
 */
export function MemoryModelsPanel({ registry, focus = null }) {
  const { settings, update, schema } = useSettingsClient({ schema: true })
  const modelsQuery = useMemoryModels()
  const schemaData = schema.data || null
  const field = (k) => _schemaField(schemaData, k)
  const ready = !!settings.data && !!schemaData
  const models = modelsQuery.data
  const common = { settingsData: settings.data, ready, field, update, registry, refetch: () => modelsQuery.refetch?.() }

  return (
    <div id="memory-models" data-testid="mem-models-panel">
      <h3 className="mono" style={{ fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: 'var(--fg-4)', margin: '16px 2px 6px', fontWeight: 'normal' }}>
        Models
      </h3>
      {settings.isError && (
        <div className="s-row" role="alert" style={{ padding: '8px 12px' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--err)' }}>
            Could not load settings: {settings.error?.message || 'see logs'}. The fields below cannot be saved.
          </span>
        </div>
      )}
      {modelsQuery.isError && (
        <div className="s-row" role="alert" data-testid="mem-models-read-failed" style={{ padding: '8px 12px', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span className="mono" style={{ fontSize: 11, color: 'var(--err)' }}>
            Could not read the memory models: {modelsQuery.error?.message || 'is memory enabled on this install?'}. The embedder and the decision server status are unknown.
          </span>
          <button type="button" className="btn ghost sm" onClick={() => modelsQuery.refetch?.()}>Try again</button>
        </div>
      )}
      {!settings.isError && !ready && (
        <div className="s-row mono" role="status" data-testid="mem-models-loading" style={MUTED}>Loading model settings…</div>
      )}
      <EmbedderGroup models={models} focused={focus === 'embedder'} />
      <RerankerGroup {...common} focused={focus === 'reranker'} />
      <DecisionGroup {...common} models={models} modelsError={modelsQuery.isError} focused={focus === 'decision'} />
    </div>
  )
}
