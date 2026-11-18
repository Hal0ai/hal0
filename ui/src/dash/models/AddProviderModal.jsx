// Models → Providers: the Add provider modal (one page that grows: tiles, connection, model checklist).
//
// Props:
//   open     boolean
//   onClose  () => void
//   onAdded  (name: string) => void   called with the new provider's name once it exists
//
// The API key is typed into a password input and lives in this component's state only while the
// modal is open. It goes to the probe and to the credentials route and nowhere else: not the query
// cache (the probe hook keeps no request body, and the credentials call below is a plain request,
// not a mutation whose variables the cache would hold), not a toast, not storage, not a URL.
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { apiPost } from '@/api/client'
import { ENDPOINTS } from '@/api/endpoints'
import { useProvidersCatalog, useProviderProbe, useUpstreamCreate, useUpstreams } from '@/api/hooks/useUpstreams'
import {
  buildCreateBody,
  defaultProviderName,
  formatContext,
  formatPrice,
  initialPicks,
  probeErrorMessage,
  validateProviderName,
} from './providers-pure'

const PAGE = 100
const SEARCH_ABOVE = 20
const MODEL_TYPES = ['chat', 'embed', 'rerank', 'tts', 'stt', 'image']
const URL_PATTERN = /^https?:\/\/\S+$/i
const AUTH_CHOICES = [
  { value: 'bearer', label: 'Bearer key' },
  { value: 'header', label: 'Custom header' },
  { value: 'none', label: 'None' },
]
const CUSTOM_FALLBACK = {
  id: 'custom', name: 'Custom', base_url: '', auth: 'bearer', auth_header_name: '', category: 'custom',
  notes: 'Any OpenAI-compatible endpoint.',
}

const toast = (msg, kind) => window.__hal0Toast && window.__hal0Toast(msg, kind)

function tileNote(p) {
  const first = (p.notes || '').split(/(?<=\.)\s/)[0].trim()
  return first || p.category
}

// A custom provider has no preset to name its key variable, so one is derived from the name.
function customKeyEnv(name) {
  const base = name.toUpperCase().replace(/[^A-Z0-9]+/g, '_')
  return /^[A-Z_]/.test(base) && !base.startsWith('HAL0') ? `${base}_API_KEY` : `PROVIDER_${base}_API_KEY`
}

function Field({ id, label, req, error, children, hint }) {
  return (
    <div className={'pf-row addprov-row-field' + (error ? ' has-err' : '')}>
      <label className="pf-row-lbl" htmlFor={id}>
        <span>{label}{req && <span className="pf-req" title="required">*</span>}</span>
      </label>
      <div className="pf-row-ctl">
        {children}
        {error && <div id={`addprov-err-${id.replace('addprov-', '')}`} className="addprov-err mono" role="alert">{error}</div>}
        {hint && !error && <div className="addprov-sub mono">{hint}</div>}
      </div>
    </div>
  )
}

export function AddProviderModal({ open, onClose, onAdded }) {
  const qc = useQueryClient()
  const catalogQ = useProvidersCatalog()
  const upstreamsQ = useUpstreams()
  const create = useUpstreamCreate()
  const { probe, isPending: checking, clear: clearProbe } = useProviderProbe()

  const [catalogId, setCatalogId] = useState(null)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [authStyle, setAuthStyle] = useState('bearer')
  const [authHeader, setAuthHeader] = useState('')
  const [check, setCheck] = useState(null) // { rows, latency } after a successful check
  const [checkError, setCheckError] = useState(null)
  const [fieldErr, setFieldErr] = useState({}) // server messages: name | url | key
  const [formError, setFormError] = useState(null)
  const [picked, setPicked] = useState(() => new Set())
  const [types, setTypes] = useState({})
  const [q, setQ] = useState('')
  const [shown, setShown] = useState(PAGE)
  const [busy, setBusy] = useState(false)
  const [created, setCreated] = useState(null) // { name, env, models } once the provider exists
  const [keyFailed, setKeyFailed] = useState(false)
  const [tick, setTick] = useState(0)
  const [discardOpen, setDiscardOpen] = useState(false)

  const run = useRef(0) // bumped to drop an answer that is no longer wanted
  const nameRef = useRef(null)
  const resultRef = useRef(null)
  const allRef = useRef(null)

  const presets = useMemo(() => {
    const all = Object.values(catalogQ.data || {})
    const rest = all.filter((p) => p.id !== 'custom')
    const custom = all.find((p) => p.id === 'custom') || CUSTOM_FALLBACK
    return [...rest, custom]
  }, [catalogQ.data])
  const preset = presets.find((p) => p.id === catalogId) || null
  const isCustom = catalogId === 'custom'
  const taken = useMemo(
    () => (Array.isArray(upstreamsQ.data) ? upstreamsQ.data.map((u) => u.name) : []),
    [upstreamsQ.data],
  )

  function reset() {
    run.current += 1
    clearProbe()
    setCatalogId(null); setName(''); setUrl(''); setApiKey(''); setAuthStyle('bearer'); setAuthHeader('')
    setCheck(null); setCheckError(null); setFieldErr({}); setFormError(null)
    setPicked(new Set()); setTypes({}); setQ(''); setShown(PAGE)
    setBusy(false); setCreated(null); setKeyFailed(false); setDiscardOpen(false)
  }
  useEffect(() => {
    if (!open) reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  useEffect(() => { if (tick > 0) nameRef.current?.focus() }, [tick])
  useEffect(() => { if (check || checkError) resultRef.current?.focus() }, [check, checkError])

  // A changed address, key or auth makes an earlier answer stale: drop it and what hangs off it.
  function invalidate() {
    run.current += 1
    clearProbe()
    setCheck(null); setCheckError(null); setFormError(null)
    setFieldErr((f) => ({ ...f, url: undefined, key: undefined }))
    setPicked(new Set()); setTypes({}); setQ(''); setShown(PAGE)
  }

  function pickTile(p) {
    invalidate()
    setApiKey('')
    setCatalogId(p.id)
    setName(defaultProviderName(p, taken))
    setUrl(p.base_url || '')
    setAuthStyle(p.auth || 'bearer')
    setAuthHeader(p.auth === 'header' ? p.auth_header_name || '' : '')
    setFieldErr({})
    setTick((t) => t + 1)
  }

  const nameInvalid = validateProviderName(name, taken)
  const urlOk = URL_PATTERN.test(url.trim())
  const headerMissing = authStyle === 'header' && !authHeader.trim()
  const nameShown = fieldErr.name || (name ? nameInvalid : null)
  const urlShown = fieldErr.url || (url.trim() && !urlOk ? 'Enter a base URL starting with http:// or https://.' : null)
  const headerShown = headerMissing && isCustom ? 'Enter the header name the provider expects.' : null
  const canCheck = !!preset && !nameInvalid && urlOk && !headerMissing && !checking && !busy
  const rows = check ? check.rows : []
  const authNone = authStyle === 'none'

  async function runCheck() {
    if (!canCheck) return
    const mine = ++run.current
    setCheck(null); setCheckError(null); setFieldErr((f) => ({ ...f, url: undefined, key: undefined }))
    const body = {}
    if (!isCustom) body.catalog_id = catalogId
    if (isCustom || url.trim() !== (preset.base_url || '') || !preset.base_url) body.url = url.trim()
    if (authStyle) body.auth_style = authStyle
    if (authStyle === 'header') body.auth_header = authHeader.trim()
    if (apiKey.trim()) body.key = apiKey.trim()
    try {
      const r = await probe(body)
      if (run.current !== mine) return
      if (r.ok) {
        setCheck({ rows: r.models, latency: r.latency_ms })
        setPicked(initialPicks(r.models))
        setTypes({}); setQ(''); setShown(PAGE)
      } else {
        setCheckError(probeErrorMessage(r))
      }
    } catch (e) {
      if (run.current !== mine) return
      if (e && e.status === 400) {
        const msg = String(e.message || 'The provider details were not accepted.')
        setFieldErr((f) => (/key/i.test(msg) ? { ...f, key: msg } : { ...f, url: msg }))
      } else {
        setCheckError(`Could not run the check. ${e && e.message ? e.message : 'Try again.'}`)
      }
    }
  }

  const needle = q.trim().toLowerCase()
  const filtered = useMemo(
    () => (needle ? rows.filter((m) => m.id.toLowerCase().includes(needle)) : rows),
    [rows, needle],
  )
  const visible = filtered.slice(0, shown)
  const filteredPicked = filtered.filter((m) => picked.has(m.id)).length
  const allOn = filtered.length > 0 && filteredPicked === filtered.length
  useEffect(() => {
    if (allRef.current) allRef.current.indeterminate = filteredPicked > 0 && !allOn
  }, [filteredPicked, allOn])

  function toggleRow(id) {
    setPicked((s) => {
      const n = new Set(s)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }
  function toggleAll() {
    setPicked((s) => {
      const n = new Set(s)
      for (const m of filtered) {
        if (allOn) n.delete(m.id)
        else n.add(m.id)
      }
      return n
    })
  }

  async function saveKey(info) {
    if (!info.env) throw new Error('The provider has no key variable to store the key under.')
    await apiPost(ENDPOINTS.providerCredentials(info.name), { key: info.env, value: apiKey.trim() })
    qc.invalidateQueries({ queryKey: ['upstreams'] })
  }

  function finish(info, noteKeyLost) {
    const n = info.models
    if (noteKeyLost) toast(`${info.name} added, but its key was not saved.`, 'info')
    else if (n === null) toast(`${info.name} added. No models on yet: refresh its model list when it is reachable.`, 'info')
    else toast(`${info.name} added · ${n} models on`, 'ok')
    reset()
    onAdded(info.name)
    onClose()
  }

  async function submit(withModels) {
    if (busy || nameInvalid || !urlOk || headerMissing) return
    setBusy(true); setFormError(null); setFieldErr((f) => ({ ...f, name: undefined }))
    const body = buildCreateBody({
      name,
      catalogId: isCustom ? '' : catalogId || '',
      url: url.trim(),
      authStyle,
      authHeader: authStyle === 'header' ? authHeader.trim() : '',
      picked,
      probed: withModels ? rows : [],
      types,
    })
    if (isCustom && apiKey.trim()) body.auth_value_env = customKeyEnv(name)
    let made
    try {
      made = await create.mutateAsync(body)
    } catch (e) {
      setBusy(false)
      const msg = String((e && e.message) || 'The provider could not be added.')
      if (e && (e.status === 409 || (e.status === 400 && /name/i.test(msg)))) setFieldErr((f) => ({ ...f, name: msg }))
      else setFormError(msg)
      return
    }
    const info = { name: made?.name || name, env: made?.auth_value_env || '', models: withModels ? picked.size : null }
    setCreated(info)
    if (apiKey.trim()) {
      try {
        await saveKey(info)
      } catch {
        setBusy(false)
        setKeyFailed(true)
        return
      }
    }
    finish(info, false)
  }

  async function retryKey() {
    if (busy || !created) return
    setBusy(true)
    try {
      await saveKey(created)
    } catch {
      setBusy(false)
      return
    }
    finish(created, false)
  }

  // Closing after the provider exists still tells the list about it.
  function close() {
    if (created) {
      const info = created
      toast(`${info.name} added, but its key was not saved.`, 'info')
      reset()
      onAdded(info.name)
    } else {
      reset()
    }
    onClose()
  }

  if (!open) return null

  const dirty = !created && !!(catalogId || name || url || apiKey)
  const keyDescribe = [fieldErr.key ? 'addprov-err-key' : null, 'addprov-key-hint'].filter(Boolean).join(' ')

  let note = 'Pick a provider to start.'
  if (preset) note = check ? `${rows.length} offered · ${picked.size} picked` : "Check the connection to see this provider's models."

  const foot = keyFailed ? (
    <>
      <span className="addprov-note addprov-bad" role="alert">Provider added, but the key was not saved.</span>
      <span className="addprov-btns">
        <button type="button" className="btn ghost sm" onClick={close}>Close</button>
        <button type="button" className="btn sm" onClick={retryKey} disabled={busy}>Try saving the key again</button>
      </span>
    </>
  ) : (
    <>
      <span className="addprov-note">{note}</span>
      <span className="addprov-btns">
        {preset && !check && (
          <button type="button" className="btn ghost sm" onClick={() => submit(false)}
            disabled={busy || checking || !!nameInvalid || !urlOk || headerMissing}>
            Add without checking
          </button>
        )}
        <button type="button" className="btn ghost sm" onClick={() => (dirty ? setDiscardOpen(true) : close())}>Cancel</button>
        <button type="button" className="btn sm" onClick={() => submit(true)} disabled={!check || busy}>
          {check ? `Add provider · ${picked.size} on` : 'Add provider'}
        </button>
      </span>
    </>
  )

  return (
    <Modal open eyebrow="Models · add provider" title="Add provider" width={780} onClose={close} dirty={dirty} foot={foot}>
      <div className="add-provider-modal addprov" data-testid="add-provider-modal">
        <section className="addprov-sec">
          <span className="addprov-lbl mono">Provider</span>
          <div className="addprov-tiles">
            {presets.map((p) => (
              <button key={p.id} type="button" className={'addprov-tile' + (p.id === catalogId ? ' on' : '')}
                aria-pressed={p.id === catalogId} onClick={() => pickTile(p)}>
                <b>{p.name}</b>
                <span>{tileNote(p)}</span>
              </button>
            ))}
          </div>
        </section>

        {preset && (
          <section className="addprov-sec">
            <span className="addprov-lbl mono">Connection</span>
            <Field id="addprov-name" label="Name" req error={nameShown}>
              <input id="addprov-name" ref={nameRef} className={'input mono' + (nameShown ? ' input-err' : '')} value={name}
                autoComplete="off" spellCheck={false} aria-invalid={nameShown ? 'true' : 'false'}
                aria-describedby={nameShown ? 'addprov-err-name' : undefined}
                onChange={(e) => { setName(e.target.value); setFieldErr((f) => ({ ...f, name: undefined })) }} />
            </Field>
            <Field id="addprov-url" label="Base URL" req error={urlShown}>
              <input id="addprov-url" className={'input mono' + (urlShown ? ' input-err' : '')} value={url} placeholder="https://…/v1"
                autoComplete="off" spellCheck={false} aria-invalid={urlShown ? 'true' : 'false'}
                aria-describedby={urlShown ? 'addprov-err-url' : undefined}
                onChange={(e) => { setUrl(e.target.value); invalidate() }} />
            </Field>
            {isCustom && (
              <>
                <Field id="addprov-auth" label="Auth">
                  <select id="addprov-auth" className="input" value={authStyle}
                    onChange={(e) => { setAuthStyle(e.target.value); invalidate() }}>
                    {AUTH_CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                </Field>
                {authStyle === 'header' && (
                  <Field id="addprov-header" label="Header name" req error={headerShown}>
                    <input id="addprov-header" className={'input mono' + (headerShown ? ' input-err' : '')} value={authHeader}
                      autoComplete="off" spellCheck={false} placeholder="X-Api-Key"
                      aria-invalid={headerShown ? 'true' : 'false'}
                      aria-describedby={headerShown ? 'addprov-err-header' : undefined}
                      onChange={(e) => { setAuthHeader(e.target.value); invalidate() }} />
                  </Field>
                )}
              </>
            )}
            <Field id="addprov-key" label={authNone ? 'API key (optional)' : 'API key'} error={fieldErr.key}>
              <div className="addprov-keyrow">
                <input id="addprov-key" type="password" autoComplete="off" spellCheck={false}
                  className={'input mono' + (fieldErr.key ? ' input-err' : '')} value={apiKey}
                  placeholder={authNone ? 'Not needed for this provider' : 'Paste key'}
                  aria-invalid={fieldErr.key ? 'true' : 'false'} aria-describedby={keyDescribe}
                  onChange={(e) => { setApiKey(e.target.value); invalidate() }} />
                <button type="button" className="btn ghost" onClick={runCheck} disabled={!canCheck}>
                  {checking ? 'Checking…' : 'Check connection'}
                </button>
              </div>
            </Field>
            <span id="addprov-key-hint" className="addprov-hint">
              Name becomes the prefix agents use, for example <span className="mono">{name || 'name'}/model-id</span>.
              The key is stored on this box only and never shown again.
            </span>
          </section>
        )}

        {preset && checkError && (
          <div className="addprov-result err" data-testid="addprov-result" role="alert" tabIndex={-1} ref={resultRef}>
            {checkError} Nothing was saved.
          </div>
        )}
        {formError && (
          <div className="addprov-result err" data-testid="addprov-form-error" role="alert">{formError}</div>
        )}

        {check && (
          <>
            <div className="addprov-result" data-testid="addprov-result" role="status" tabIndex={-1} ref={resultRef}>
              Reachable · {check.latency} ms · {rows.length} models offered
            </div>
            <section className="addprov-sec">
              <span className="addprov-lbl mono">Models to switch on</span>
              <div className="addprov-pick">
                <div className="addprov-pick-h">
                  <input type="checkbox" id="addprov-all" ref={allRef} aria-label="Select all" checked={allOn} onChange={toggleAll} />
                  <label htmlFor="addprov-all" className="addprov-grow">Select all{needle ? ' shown' : ''}</label>
                  <button type="button" className="btn ghost sm" onClick={() => setPicked(initialPicks(rows))}>Recommended only</button>
                </div>
                {rows.length > SEARCH_ABOVE && (
                  <input type="search" className="input addprov-search" aria-label="Search models" placeholder="Search models"
                    autoComplete="off" value={q} onChange={(e) => { setQ(e.target.value); setShown(PAGE) }} />
                )}
                <div className="addprov-list">
                  {visible.map((m, i) => (
                    <div className="addprov-row" key={m.id}>
                      <input type="checkbox" id={`addprov-m-${i}`} checked={picked.has(m.id)} onChange={() => toggleRow(m.id)} />
                      <label htmlFor={`addprov-m-${i}`} className="addprov-mid">
                        <b>{m.id}</b>
                        {m.recommended && <span className="chip amber">recommended</span>}
                      </label>
                      <select className="input addprov-type" aria-label={`Type of ${m.id}`} value={types[m.id] ?? m.type}
                        onChange={(e) => setTypes((t) => ({ ...t, [m.id]: e.target.value }))}>
                        {MODEL_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                      </select>
                      <span className="addprov-num mono" title="Context">{formatContext(m.context)}</span>
                      <span className="addprov-num mono" title="Price per million tokens, in / out">{formatPrice(m)}</span>
                    </div>
                  ))}
                  {filtered.length === 0 && <div className="addprov-empty mono">No model matches.</div>}
                  {filtered.length > visible.length && (
                    <button type="button" className="btn ghost sm addprov-more" onClick={() => setShown((n) => n + PAGE)}>
                      Show more ({filtered.length - visible.length} left)
                    </button>
                  )}
                </div>
              </div>
              <span className="addprov-hint">
                Only ticked models can be called. The rest stay listed, switched off, and can be turned on later.
              </span>
            </section>
          </>
        )}
      </div>
      <ConfirmDialog open={discardOpen} title="Discard this provider?" message="What you typed will be lost."
        confirmLabel="Discard" cancelLabel="Keep editing" destructive
        onCancel={() => setDiscardOpen(false)} onConfirm={close} />
    </Modal>
  )
}
