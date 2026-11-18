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

// The variable names the credentials route refuses for a key, mirrored from src/hal0/api/_env_store.py
// (is_process_control_env: PROCESS_CONTROL_ENV_NAMES, PROCESS_CONTROL_ENV_PREFIXES, the _PROXY suffix,
// the HF Hub redirect names and prefixes; the TLS trust names) and the HAL0_ service namespace
// (routes/secrets.py is_protected_secret). They are tested against the FINAL variable name.
const RESERVED_ENV_NAMES = new Set([
  'PATH', 'HOME', 'SHELL', 'IFS', 'ENV', 'BASH_ENV', 'NODE_OPTIONS', 'PERL5OPT', 'RUBYOPT', 'GCONV_PATH',
  'PERL5LIB', 'NODE_PATH', 'SHELLOPTS', 'BASHOPTS', 'PS4', 'HERMES_HOME', 'SSLKEYLOGFILE',
  'NODE_TLS_REJECT_UNAUTHORIZED',
  'HF_ENDPOINT', 'HF_INFERENCE_ENDPOINT', 'HUGGINGFACE_CO_STAGING', 'HF_TOKEN_PATH', 'HF_ASSETS_CACHE',
  'HUGGINGFACE_ASSETS_CACHE',
  'SSL_CERT_FILE', 'SSL_CERT_DIR', 'REQUESTS_CA_BUNDLE', 'CURL_CA_BUNDLE', 'NODE_EXTRA_CA_CERTS',
])
const RESERVED_ENV_PREFIXES = [
  'LD_', 'DYLD_', 'PYTHON', 'UV_', 'PIP_', 'NPM_CONFIG_', 'GIT_', 'OPENSSL_', 'XDG_',
  'HF_HUB_', 'HUGGINGFACE_HUB_', 'HF_XET_',
  'HAL0_',
]
function isReservedEnv(env) {
  const n = env.toUpperCase()
  return RESERVED_ENV_NAMES.has(n) || n.endsWith('_PROXY') || RESERVED_ENV_PREFIXES.some((p) => n.startsWith(p))
}

// The variable a provider's key is stored under, derived from its NAME so that two providers made
// from one preset never share a key: `minimax` -> MINIMAX_API_KEY, `minimax-2` -> MINIMAX_2_API_KEY.
// A reserved or digit-leading name gets a PROVIDER_ prefix; a variable another provider already
// uses gets a numeric suffix.
export function keyEnvFor(name, used = []) {
  let stem = name.toUpperCase().replace(/[^A-Z0-9]/g, '_')
  if (/^[0-9]/.test(stem) || isReservedEnv(`${stem}_API_KEY`)) stem = `PROVIDER_${stem}`
  let env = `${stem}_API_KEY`
  for (let n = 2; used.includes(env); n += 1) env = `${stem}_${n}_API_KEY`
  return env
}

// Same rule as the server (upstreams/registry.py normalise_key): printable ASCII, no whitespace,
// after one optional leading "Bearer " (exactly one space, then the token).
const KEY_MESSAGE = 'The key must be printable ASCII without spaces.'
function keyProblem(raw) {
  const t = raw.trim()
  if (!t) return null
  if (/^bearer\s*$/i.test(t)) return KEY_MESSAGE
  let bare = t
  if (/^bearer\s/i.test(t)) {
    if (t[6] !== ' ' || /^\s/.test(t.slice(7))) return KEY_MESSAGE
    bare = t.slice(7)
  }
  return /^[\x21-\x7e]+$/.test(bare) ? null : KEY_MESSAGE
}

// A request that has not answered by then is given up on, so a hung server never traps the modal.
const REQUEST_TIMEOUT_MS = 30000
const TIMED_OUT = Symbol('timed out')
function withTimeout(promise) {
  let timer
  const limit = new Promise((_, reject) => { timer = setTimeout(() => reject(TIMED_OUT), REQUEST_TIMEOUT_MS) })
  return Promise.race([promise, limit]).finally(() => clearTimeout(timer))
}
const CREATE_TIMEOUT_MESSAGE = 'hal0 did not answer. The provider may or may not have been added; close this and check the list.'
const KEY_TIMEOUT_MESSAGE = 'hal0 did not answer while saving the key.'

const DISCARD_COPY = 'Discard unsaved changes?'
const UNIT_WORDS = { Mtok: 'million tokens', Mchar: 'million characters', img: 'image' }
const priceTitle = (m) => `Price, in / out, per ${UNIT_WORDS[m.price_unit] || m.price_unit}`

function Field({ id, label, req, error, errRef, children }) {
  return (
    <div className={'pf-row addprov-row-field' + (error ? ' has-err' : '')}>
      <label className="pf-row-lbl" htmlFor={id}>
        <span>{label}{req && <span className="pf-req" title="required">*</span>}</span>
      </label>
      <div className="pf-row-ctl">
        {children}
        {error && (
          <div id={`addprov-err-${id.replace('addprov-', '')}`} className="addprov-err mono" role="alert" tabIndex={-1} ref={errRef}>
            {error}
          </div>
        )}
      </div>
    </div>
  )
}

const messageOf = (e, fallback) => String((e && e.message) || fallback)

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
  const [fieldErr, setFieldErr] = useState({}) // server messages: name | url | key | header
  const [formError, setFormError] = useState(null)
  const [credErr, setCredErr] = useState(null) // why the key could not be saved
  const [picked, setPicked] = useState(() => new Set())
  const [types, setTypes] = useState({})
  const [q, setQ] = useState('')
  const [shown, setShown] = useState(PAGE)
  const [busy, setBusy] = useState(false) // a create or credentials request is in flight
  const [created, setCreated] = useState(null) // { name, env, url, models } once the provider exists
  const [keyFailed, setKeyFailed] = useState(false)
  const [unknownCreate, setUnknownCreate] = useState(false) // create got no answer: the provider may exist
  const [tick, setTick] = useState(0)
  const [errTick, setErrTick] = useState(0)
  const [discardOpen, setDiscardOpen] = useState(false)

  const run = useRef(0) // probe: bumped to drop an answer that is no longer wanted
  const sub = useRef(0) // create / key save: same idea; bumped on reset and unmount
  const reported = useRef(false) // onAdded has been called for the current provider
  const nameRef = useRef(null)
  const resultRef = useRef(null)
  const allRef = useRef(null)
  const credErrRef = useRef(null)

  const presets = useMemo(() => {
    const all = Object.values(catalogQ.data || {})
    const rest = all.filter((p) => p.id !== 'custom')
    const custom = all.find((p) => p.id === 'custom') || CUSTOM_FALLBACK
    return [...rest, custom]
  }, [catalogQ.data])
  const preset = presets.find((p) => p.id === catalogId) || null
  const isCustom = catalogId === 'custom'
  const upstreams = useMemo(() => (Array.isArray(upstreamsQ.data) ? upstreamsQ.data : []), [upstreamsQ.data])
  const taken = useMemo(() => upstreams.map((u) => u.name), [upstreams])
  const usedEnvs = useMemo(() => upstreams.map((u) => u.auth_value_env).filter(Boolean), [upstreams])

  function reset() {
    run.current += 1
    sub.current += 1
    reported.current = false
    clearProbe()
    setCatalogId(null); setName(''); setUrl(''); setApiKey(''); setAuthStyle('bearer'); setAuthHeader('')
    setCheck(null); setCheckError(null); setFieldErr({}); setFormError(null); setCredErr(null)
    setPicked(new Set()); setTypes({}); setQ(''); setShown(PAGE)
    setBusy(false); setCreated(null); setKeyFailed(false); setUnknownCreate(false); setDiscardOpen(false)
  }
  // Every opening starts clean, and so does every closing: nothing typed survives a reopen.
  useEffect(() => {
    reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])
  // No late answer may touch state or call back once the modal is gone.
  useEffect(() => () => { run.current += 1; sub.current += 1 }, [])

  useEffect(() => { if (tick > 0) nameRef.current?.focus() }, [tick])
  useEffect(() => { if (check || checkError) resultRef.current?.focus() }, [check, checkError])
  useEffect(() => { if (errTick > 0) credErrRef.current?.focus() }, [errTick])

  // A changed address, key or auth makes an earlier answer stale: drop it and what hangs off it.
  function invalidate() {
    run.current += 1
    clearProbe()
    setCheck(null); setCheckError(null); setFormError(null)
    setFieldErr((f) => ({ ...f, url: undefined, key: undefined, header: undefined }))
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
  const keyBad = keyProblem(apiKey)
  const nameShown = fieldErr.name || (name ? nameInvalid : null)
  const urlShown = fieldErr.url || (url.trim() && !urlOk ? 'Enter a base URL starting with http:// or https://.' : null)
  const headerShown = fieldErr.header || (headerMissing && isCustom ? 'Enter the header name the provider expects.' : null)
  const keyShown = credErr || fieldErr.key || keyBad
  const locked = busy || unknownCreate
  const ready = !!preset && !nameInvalid && urlOk && !headerMissing && !keyBad && !locked
  const canCheck = ready && !checking
  const rows = check ? check.rows : []
  const authNone = authStyle === 'none'

  async function runCheck() {
    if (!canCheck) return
    const mine = ++run.current
    const hadCheck = !!check
    setCheck(null); setCheckError(null); setFormError(null)
    setFieldErr((f) => ({ ...f, url: undefined, key: undefined, header: undefined }))
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
        if (hadCheck) {
          // A repeat check with nothing edited keeps the picks and type overrides that still apply.
          const ids = new Set(r.models.map((m) => m.id))
          setPicked((s) => new Set([...s].filter((id) => ids.has(id))))
          setTypes((t) => Object.fromEntries(Object.entries(t).filter(([id]) => ids.has(id))))
        } else {
          setPicked(initialPicks(r.models))
          setTypes({})
        }
        setQ(''); setShown(PAGE)
      } else {
        setCheckError(probeErrorMessage(r))
      }
    } catch (e) {
      if (run.current !== mine) return
      if (e && e.status === 400) {
        const msg = messageOf(e, 'The provider details were not accepted.')
        if (/header/i.test(msg) && authStyle === 'header' && isCustom) setFieldErr((f) => ({ ...f, header: msg }))
        else if (/\bkey\b/i.test(msg)) setFieldErr((f) => ({ ...f, key: msg }))
        else if (/\burl\b/i.test(msg)) setFieldErr((f) => ({ ...f, url: msg }))
        else setFormError(msg)
      } else {
        setCheckError(`Could not run the check. ${messageOf(e, 'Try again.')}`)
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

  async function saveKey(info, value) {
    if (!info.env) throw new Error('The provider has no key variable to store the key under.')
    try {
      await withTimeout(apiPost(ENDPOINTS.providerCredentials(info.name), { key: info.env, value }))
    } catch (e) {
      throw e === TIMED_OUT ? new Error(KEY_TIMEOUT_MESSAGE) : e
    }
    qc.invalidateQueries({ queryKey: ['upstreams'] })
  }

  // The one place that reports a created provider: once, then the modal closes.
  function complete(info, keyLost) {
    if (reported.current) return
    reported.current = true
    if (keyLost) toast(`${info.name} added, but its key was not saved.`, 'info')
    else if (info.models === null) toast(`${info.name} added. No models on yet: refresh its model list when it is reachable.`, 'info')
    else toast(`${info.name} added · ${info.models} models on`, 'ok')
    onAdded(info.name)
    reset()
    onClose()
  }

  function keyFailure(e) {
    setBusy(false)
    setKeyFailed(true)
    setCredErr(messageOf(e, 'The key could not be saved.'))
    setErrTick((t) => t + 1)
  }

  async function submit(withModels) {
    if (!ready || created) return
    const token = ++sub.current
    const keyValue = apiKey.trim()
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
    // The key is stored under a variable named after the provider, so a second provider from one
    // preset never overwrites the first one's key.
    if (authStyle !== 'none') body.auth_value_env = keyEnvFor(name, usedEnvs)
    let made
    try {
      made = await withTimeout(create.mutateAsync(body))
    } catch (e) {
      if (sub.current !== token) return
      setBusy(false)
      if (e === TIMED_OUT) {
        sub.current += 1 // a late answer is ignored
        setUnknownCreate(true)
        setFormError(CREATE_TIMEOUT_MESSAGE)
        return
      }
      const msg = messageOf(e, 'The provider could not be added.')
      const aboutName = /name/i.test(msg) && !/auth_value_env|reserved|\benv\b/i.test(msg)
      if (e && (e.status === 409 || (e.status === 400 && aboutName))) setFieldErr((f) => ({ ...f, name: msg }))
      else setFormError(msg)
      return
    }
    if (sub.current !== token) return
    const info = {
      name: made?.name || name,
      env: made?.auth_value_env || body.auth_value_env || '',
      url: url.trim(),
      models: withModels ? picked.size : null,
    }
    setCreated(info)
    if (keyValue) {
      try {
        await saveKey(info, keyValue)
      } catch (e) {
        if (sub.current !== token) return
        keyFailure(e)
        return
      }
      if (sub.current !== token) return
    }
    complete(info, false)
  }

  async function retryKey() {
    if (busy || !created) return
    const keyValue = apiKey.trim()
    const problem = !keyValue ? 'Enter the key to save.' : keyProblem(keyValue)
    if (problem) {
      setCredErr(problem)
      setErrTick((t) => t + 1)
      return
    }
    const token = ++sub.current
    setBusy(true); setCredErr(null)
    try {
      await saveKey(created, keyValue)
    } catch (e) {
      if (sub.current !== token) return
      keyFailure(e)
      return
    }
    if (sub.current !== token) return
    complete(created, false)
  }

  // Close paths: inert while a request is in flight; after the provider exists they still tell the list.
  function close() {
    if (busy) return
    if (unknownCreate) {
      // Whether the provider exists is not known: show the list as it is, never claim an add.
      qc.invalidateQueries({ queryKey: ['upstreams'] })
      reset()
      onClose()
    } else if (created) complete(created, true)
    else {
      reset()
      onClose()
    }
  }

  if (!open) return null

  const dirty = !created && !unknownCreate && !!(catalogId || name || url || apiKey)

  let note = 'Pick a provider to start.'
  if (preset) note = check ? `${rows.length} offered · ${picked.size} picked` : "Check the connection to see this provider's models."

  const foot = unknownCreate ? (
    <>
      <span className="addprov-note addprov-bad" role="status">No answer from hal0.</span>
      <span className="addprov-btns">
        <button type="button" className="btn sm" onClick={close}>Close</button>
      </span>
    </>
  ) : created ? (
    <>
      <span className="addprov-note addprov-bad" role="status">
        {keyFailed ? 'Provider added, but the key was not saved.' : 'Saving the key…'}
      </span>
      <span className="addprov-btns">
        <button type="button" className="btn ghost sm" onClick={close} disabled={busy}>Close</button>
        {keyFailed && (
          <button type="button" className="btn sm" onClick={retryKey} disabled={busy}>
            {busy ? 'Saving…' : 'Try saving the key again'}
          </button>
        )}
      </span>
    </>
  ) : (
    <>
      <span className="addprov-note">{note}</span>
      <span className="addprov-btns">
        {preset && !check && (
          <button type="button" className="btn ghost sm" onClick={() => submit(false)} disabled={!ready || checking}>
            Add without checking
          </button>
        )}
        <button type="button" className="btn ghost sm" disabled={busy}
          onClick={() => (dirty ? setDiscardOpen(true) : close())}>Cancel</button>
        <button type="button" className="btn sm" onClick={() => submit(true)} disabled={!check || busy}>
          {busy ? 'Adding…' : check ? `Add provider · ${picked.size} on` : 'Add provider'}
        </button>
      </span>
    </>
  )

  const keyField = (
    <Field id="addprov-key" label={authNone ? 'API key (optional)' : 'API key'} error={keyShown} errRef={credErrRef}>
      <div className="addprov-keyrow">
        <input id="addprov-key" type="password" autoComplete="off" spellCheck={false}
          className={'input mono' + (keyShown ? ' input-err' : '')} value={apiKey}
          placeholder={authNone ? 'Not needed for this provider' : 'Paste key'}
          aria-invalid={keyShown ? 'true' : 'false'}
          aria-describedby={keyShown ? 'addprov-err-key addprov-key-hint' : 'addprov-key-hint'}
          disabled={locked}
          onChange={(e) => {
            setApiKey(e.target.value)
            setCredErr(null)
            if (!created) invalidate()
          }} />
        {!created && (
          <button type="button" className="btn ghost" onClick={runCheck} disabled={!canCheck}>
            {checking ? 'Checking…' : 'Check connection'}
          </button>
        )}
      </div>
    </Field>
  )

  return (
    <Modal open eyebrow="Models · add provider" title="Add provider" width={780} onClose={close} dirty={dirty}
      dismissable={!busy} confirmDiscard={DISCARD_COPY} foot={foot}>
      <div className="add-provider-modal addprov" data-testid="add-provider-modal">
        {created ? (
          <>
            <section className="addprov-sec">
              <span className="addprov-lbl mono">Provider</span>
              <div className="addprov-summary" data-testid="addprov-summary">
                <b>{created.name}</b> <span className="mono">{created.url}</span>
              </div>
            </section>
            <section className="addprov-sec">
              <span className="addprov-lbl mono">Key</span>
              {keyField}
              <span id="addprov-key-hint" className="addprov-hint">
                The provider exists. Fix the key and save it again, or close and add it later from the provider's page.
              </span>
            </section>
          </>
        ) : (
          <>
            <section className="addprov-sec">
              <span className="addprov-lbl mono">Provider</span>
              <div className="addprov-tiles">
                {presets.map((p) => (
                  <button key={p.id} type="button" className={'addprov-tile' + (p.id === catalogId ? ' on' : '')}
                    aria-pressed={p.id === catalogId} disabled={locked} onClick={() => pickTile(p)}>
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
                    aria-describedby={nameShown ? 'addprov-err-name' : undefined} disabled={locked}
                    onChange={(e) => { setName(e.target.value); setFieldErr((f) => ({ ...f, name: undefined })) }} />
                </Field>
                <Field id="addprov-url" label="Base URL" req error={urlShown}>
                  <input id="addprov-url" className={'input mono' + (urlShown ? ' input-err' : '')} value={url} placeholder="https://…/v1"
                    autoComplete="off" spellCheck={false} aria-invalid={urlShown ? 'true' : 'false'}
                    aria-describedby={urlShown ? 'addprov-err-url' : undefined} disabled={locked}
                    onChange={(e) => { setUrl(e.target.value); invalidate() }} />
                </Field>
                {isCustom && (
                  <>
                    <Field id="addprov-auth" label="Auth">
                      <select id="addprov-auth" className="input" value={authStyle} disabled={locked}
                        onChange={(e) => { setAuthStyle(e.target.value); invalidate() }}>
                        {AUTH_CHOICES.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                      </select>
                    </Field>
                    {authStyle === 'header' && (
                      <Field id="addprov-header" label="Header name" req error={headerShown}>
                        <input id="addprov-header" className={'input mono' + (headerShown ? ' input-err' : '')} value={authHeader}
                          autoComplete="off" spellCheck={false} placeholder="X-Api-Key" disabled={locked}
                          aria-invalid={headerShown ? 'true' : 'false'}
                          aria-describedby={headerShown ? 'addprov-err-header' : undefined}
                          onChange={(e) => { setAuthHeader(e.target.value); invalidate() }} />
                      </Field>
                    )}
                  </>
                )}
                {keyField}
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
                      <input type="checkbox" id="addprov-all" ref={allRef} checked={allOn} disabled={busy} onChange={toggleAll} />
                      <label htmlFor="addprov-all" className="addprov-grow">{needle ? 'Select all shown' : 'Select all'}</label>
                      <button type="button" className="btn ghost sm" disabled={busy} onClick={() => setPicked(initialPicks(rows))}>Recommended only</button>
                    </div>
                    {rows.length > SEARCH_ABOVE && (
                      <input type="search" className="input addprov-search" aria-label="Search models" placeholder="Search models"
                        autoComplete="off" value={q} disabled={busy} onChange={(e) => { setQ(e.target.value); setShown(PAGE) }} />
                    )}
                    <div className="addprov-list">
                      {visible.map((m, i) => (
                        <div className="addprov-row" key={m.id}>
                          <input type="checkbox" id={`addprov-m-${i}`} checked={picked.has(m.id)} disabled={busy} onChange={() => toggleRow(m.id)} />
                          <label htmlFor={`addprov-m-${i}`} className="addprov-mid">
                            <b>{m.id}</b>
                            {m.recommended && <span className="chip amber">recommended</span>}
                          </label>
                          <select className="input addprov-type" aria-label={`Type of ${m.id}`} value={types[m.id] ?? m.type} disabled={busy}
                            onChange={(e) => setTypes((t) => ({ ...t, [m.id]: e.target.value }))}>
                            {MODEL_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
                          </select>
                          <span className="addprov-num mono" title="Context">{formatContext(m.context)}</span>
                          <span className="addprov-num mono" title={priceTitle(m)}>{formatPrice(m)}</span>
                        </div>
                      ))}
                      {filtered.length === 0 && <div className="addprov-empty mono">No model matches.</div>}
                      {filtered.length > visible.length && (
                        <button type="button" className="btn ghost sm addprov-more" disabled={busy} onClick={() => setShown((n) => n + PAGE)}>
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
          </>
        )}
      </div>
      <ConfirmDialog open={discardOpen} title="Discard this provider?" message={DISCARD_COPY}
        confirmLabel="Discard" cancelLabel="Keep editing" destructive
        onCancel={() => setDiscardOpen(false)} onConfirm={() => { setDiscardOpen(false); reset(); onClose() }} />
    </Modal>
  )
}
