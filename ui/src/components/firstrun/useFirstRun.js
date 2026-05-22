/**
 * useFirstRun — singleton state machine + real-backend wiring for the
 * 8-step FirstRun wizard.
 *
 * Replaces the throwaway `firstrun-proto/useFirstRunState.js` composable
 * that stubbed `applyAll()` with a setTimeout fake. This one talks to the
 * real install endpoints in `src/hal0/api/routes/`:
 *
 *   GET  /api/auth/status                — auto-skip password step
 *   POST /api/auth/password              — set the dashboard password
 *   GET  /api/hardware                   — probe summary + disk_free_mb
 *   GET  /api/config/models              — current models.roots + extensions
 *   GET  /api/install/state              — sentinel/first-run gate
 *   GET  /api/install/curated-models     — primary chat picker source
 *   GET  /api/capabilities               — backends + catalogs + selections
 *   POST /api/models/{id}/pull           — kick off a HF pull
 *   GET  /api/models/{id}/pull/stream    — SSE progress (also poll-fallback)
 *   POST /api/capabilities/{slot}/{child} — register a capability slot model
 *   POST /api/install/pick-default       — primary chat: register + assign + pull
 *   POST /api/install/complete           — write the first-run sentinel
 *
 * Singleton-at-module-scope so the per-step components mount/unmount
 * without losing form state mid-wizard.
 */
import { reactive, computed, ref } from 'vue'
import { api } from '../../composables/useApi.js'

let _state = null

// Hardware-aware smart defaults. Per handoff doc:
//   - NPU box   → embed ON (npu), stt ON (npu), tts OFF, img OFF
//   - GPU box   → embed ON (vulkan/rocm), stt OFF, tts OFF, img OFF
//   - CPU-only  → embed OFF, stt OFF, tts OFF, img OFF
const SMART_DEFAULTS = {
  embed: {
    npu:    { backend: 'npu',        enabled: true },
    vulkan: { backend: 'gpu-vulkan', enabled: true },
    rocm:   { backend: 'gpu-rocm',   enabled: true },
    cpu:    { backend: 'cpu',        enabled: false },
  },
  stt: {
    npu:   { backend: 'npu', enabled: true },
    other: { enabled: false },
  },
  // Locked off-by-default in the IA-grilling session — wait for voice-agent UX.
  tts: { enabled: false },
  // 7-12 GB pull; opt-in only.
  img: { enabled: false },
}

function pickBackendTier(hw) {
  // NPU tier is reserved for Strix Halo specifically. The FLM toolbox
  // image ships AMD-XDNA-on-Strix-Halo binaries and the curated NPU
  // tags are validated on that hardware; advertising NPU on any future
  // XDNA-bearing chip just because npu_present=true would push
  // operators onto an unsupported runtime. Once we validate a second
  // XDNA platform, widen this list rather than dropping back to the
  // bare npu_present check.
  if (hw?.npu_present && hw?.platform === 'strix-halo') return 'npu'
  const v = hw?.gpu_vendor || ''
  if (v === 'amd' && !hw?.is_uma) return 'rocm'
  if (v === 'nvidia') return 'rocm'  // closest analogue in our catalog; CUDA not in capability matrix
  if ((hw?.gpus || []).some?.((g) => g.vulkan_capable)) return 'vulkan'
  return 'cpu'
}

// Heuristic: which curated chat models are gated on HuggingFace. Without a
// `gated` field on `CuratedModel` we infer from id/family. Llama family is
// gated; tweak the regex when the backend grows a proper flag.
const GATED_RE = /^(llama-|meta-llama|whisper-v3-npu)/i

function isGated(curated) {
  if (!curated) return false
  if (curated.gated === true) return true
  return GATED_RE.test(curated.id || '') || GATED_RE.test(curated.hf_repo || '')
}

export function useFirstRun() {
  if (_state) return _state

  // ── Async load state ──────────────────────────────────────────────
  const loading = ref(true)
  const loadError = ref(null)

  // Server-derived snapshots
  const hardware       = ref(null)        // GET /api/hardware (flattened shape)
  const curated        = ref([])          // GET /api/install/curated-models
  const customAllowed  = ref(false)
  const capsData       = reactive({ backends: [], catalogs: {}, selections: {} })
  const installState   = ref({ first_run: true })
  const passwordAlreadySet = ref(false)
  const modelsConfig   = ref(null)        // GET /api/config/models
  const modelsConfigWritable = ref(true)  // becomes false on PUT failure

  // ── User-driven form state ────────────────────────────────────────
  const form = reactive({
    // step 1 — password
    password: '',
    // step 1 — first-run claim OTP (printed by install.sh at the tail of
    // the summary box; lives in $VAR_DIR/.first-run.lock). The wizard
    // sends it on POST /api/auth/password so a LAN browser can claim
    // ownership; on the loopback bypass path the server tolerates an
    // empty value, so the UI always sends the field even when blank
    // rather than trying to auto-detect "am I on localhost".
    firstRunToken: '',
    // step 2 — model dirs (list-of-strings; legacy `modelDir` first entry)
    modelDirs: ['/var/lib/hal0/models'],
    // step 3 — primary chat
    primaryId: null,
    // step 4 — capabilities
    caps: {
      embed:  { enabled: false, backend: null, model: null, provider: null },
      rerank: { enabled: false, backend: null, model: null, provider: null },
      stt:    { enabled: false, backend: null, model: null, provider: null },
      tts:    { enabled: false, backend: null, model: null, provider: null },
      img:    { enabled: false, backend: null, model: null, provider: null },
    },
    // step 5 — HF token. Stashed in localStorage so a tab reload keeps it;
    // real backend wiring (tracked-in: #78) lands when /api/secrets/hf-token
    // exists. The orchestrator's pull task currently picks HF_TOKEN out of
    // the API process env, so a browser-set token isn't reachable from the
    // server until that endpoint lands.
    hfToken: '',
    // step 6 — license
    licenseAccepted: false,
    // step 7 — bundled agent (Phase 8 / ADR-0004). Default 'none' so the
    // wizard never silently installs a third-party app; user must
    // deliberately pick one. 'pi-coder' is CLI shape, 'hermes' is
    // service shape (and requires Hermes-side hal0-awareness probe).
    agentChoice: 'none',
    // Whether Hermes is hal0-aware on the upstream side. Server-side
    // probe (TBD endpoint) sets this; until it lands the option stays
    // selectable but the install path will 409 with a clean message.
    hermesHal0Aware: true,
  })

  // ── Per-model pull tracking (step 7) ──────────────────────────────
  // items[] entries: { key, slot|null, child|null, modelId, label, kind,
  //   sizeGb, state, bytesDownloaded, bytesTotal, pct, error, eventSource }
  const pull = reactive({
    started: false,
    items: [],
    done: false,
    error: null,
  })

  // ── Loaders ───────────────────────────────────────────────────────
  async function load() {
    loading.value = true
    loadError.value = null
    try {
      const [hw, st, cur, caps, auth, mcfg] = await Promise.all([
        api('/api/hardware').catch(() => null),
        api('/api/install/state').catch(() => ({ first_run: true })),
        api('/api/install/curated-models').catch(() => ({ models: [], custom_allowed: false })),
        api('/api/capabilities').catch(() => ({ backends: [], catalogs: {}, selections: {} })),
        api('/api/auth/status').catch(() => ({ password_set: false })),
        api('/api/config/models').catch(() => null),
      ])
      hardware.value = hw
      installState.value = st || { first_run: true }
      curated.value = cur?.models || []
      customAllowed.value = !!cur?.custom_allowed
      capsData.backends   = caps?.backends   ?? []
      capsData.catalogs   = caps?.catalogs   ?? {}
      capsData.selections = caps?.selections ?? {}
      passwordAlreadySet.value = !!auth?.password_set
      modelsConfig.value = mcfg
      if (mcfg?.roots?.length) form.modelDirs = [...mcfg.roots]

      // Restore any cached HF token from a prior wizard session (see
      // form.hfToken comment for why this is localStorage-only).
      try {
        const cached = window.localStorage.getItem('hal0:firstrun:hf_token')
        if (cached) form.hfToken = cached
      } catch { /* private-mode browsers may throw */ }

      if (installState.value.first_run) {
        applySmartDefaults()
      } else {
        hydrateFromSelections()
      }
    } catch (e) {
      loadError.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  function applySmartDefaults() {
    const tier = pickBackendTier(hardware.value || {})
    const e = SMART_DEFAULTS.embed[tier] || SMART_DEFAULTS.embed.cpu
    Object.assign(form.caps.embed, e)
    const s = tier === 'npu' ? SMART_DEFAULTS.stt.npu : SMART_DEFAULTS.stt.other
    Object.assign(form.caps.stt, s)
    form.caps.tts.enabled = SMART_DEFAULTS.tts.enabled
    form.caps.img.enabled = SMART_DEFAULTS.img.enabled
    pickFirstAvailableModels()
  }

  // Pre-select the first cataloged option per capability so smart defaults
  // produce a complete pick instead of an "enabled but no model" half-state.
  function pickFirstAvailableModels() {
    const cats = capsData.catalogs || {}
    const guess = (slot, child, preferBackend) => {
      const opts = cats?.[slot]?.[child] || []
      if (preferBackend) {
        const hit = opts.find((o) => o.backend === preferBackend)
        if (hit) return hit
      }
      return opts[0] || null
    }
    const set = (key, slot, child) => {
      if (form.caps[key].model) return
      const o = guess(slot, child, form.caps[key].backend)
      if (o) Object.assign(form.caps[key], { model: o.id, backend: o.backend, provider: o.provider })
    }
    set('embed',  'embed', 'embed')
    set('rerank', 'embed', 'rerank')
    set('stt',    'voice', 'stt')
    set('tts',    'voice', 'tts')
    set('img',    'img',   'img')
  }

  function hydrateFromSelections() {
    const sel = capsData.selections || {}
    const get = (slot, child) => sel?.[slot]?.[child] || {}
    const map = [
      ['embed',  ['embed',  'embed']],
      ['rerank', ['embed',  'rerank']],
      ['stt',    ['voice',  'stt']],
      ['tts',    ['voice',  'tts']],
      ['img',    ['img',    'img']],
    ]
    for (const [key, [slot, child]] of map) {
      const s = get(slot, child)
      form.caps[key] = {
        enabled: !!s.enabled,
        backend: s.backend ?? null,
        model:   s.model   ?? null,
        provider: s.provider ?? null,
      }
    }
    pickFirstAvailableModels()
  }

  // ── Catalog helpers (for the capability dropdowns) ────────────────
  // `/api/capabilities` returns the GROUPED shape — one row per model
  // id, with `backends: [{id, provider, downloaded, pullable}, ...]`.
  // The wizard's dropdown template iterates options expecting the OLD
  // FLAT shape (`o.backend` as a top-level string + `o.provider`), so
  // we flatten back to per-(model × backend) rows here. That keeps the
  // existing setCapModel / sizeFor / pickFirstAvailableModels /
  // enabledList code paths working without rewriting the template.
  // When a row lacks a `backends` array (legacy or registry-only
  // entries), we fall back to the row itself — that preserves any
  // already-flat shape some path might still produce.
  function _flattenCatalog(rows) {
    if (!Array.isArray(rows)) return []
    const out = []
    for (const r of rows) {
      const backends = Array.isArray(r.backends) ? r.backends : null
      if (backends && backends.length > 0) {
        for (const b of backends) {
          out.push({
            id: r.id,
            backend: b.id,
            provider: b.provider,
            size_gb: r.size_gb,
            capabilities: r.capabilities,
            downloaded: b.downloaded,
            pullable: b.pullable,
            license: r.license,
            license_url: r.license_url,
          })
        }
      } else if (r.backend) {
        out.push(r)
      }
    }
    return out
  }
  const optionsFor = (slot, child) => _flattenCatalog(capsData.catalogs?.[slot]?.[child])

  function setCapModel(key, slot, child, comboKey) {
    const opts = optionsFor(slot, child)
    const opt = opts.find((o) => `${o.backend}::${o.id}` === comboKey)
    if (opt) Object.assign(form.caps[key], { backend: opt.backend, model: opt.id, provider: opt.provider })
  }

  function sizeFor(key, slot, child) {
    const o = optionsFor(slot, child).find(
      (x) => x.id === form.caps[key].model && x.backend === form.caps[key].backend,
    )
    return o?.size_gb ?? 0
  }

  // ── Computed: primary, totals, gating, fits ───────────────────────
  const primaryModel = computed(
    () => curated.value.find((m) => m.id === form.primaryId) || null,
  )

  const totalDownloadGb = computed(() => {
    let t = primaryModel.value?.size_gb || 0
    if (form.caps.embed.enabled)  t += sizeFor('embed',  'embed', 'embed')
    if (form.caps.rerank.enabled) t += sizeFor('rerank', 'embed', 'rerank')
    if (form.caps.stt.enabled)    t += sizeFor('stt',    'voice', 'stt')
    if (form.caps.tts.enabled)    t += sizeFor('tts',    'voice', 'tts')
    if (form.caps.img.enabled)    t += sizeFor('img',    'img',   'img')
    return t
  })

  const diskFreeGb = computed(
    () => (hardware.value?.disk_free_mb || 0) / 1024,
  )
  const fits = computed(() => totalDownloadGb.value <= diskFreeGb.value)

  // The HF-token step only renders when at least one selected model is
  // gated. Capability-catalog entries don't carry a gating flag today, so
  // for now only the primary chat model is checked (it's the historical
  // source of gated picks: Llama family).
  const gatedModels = computed(() => {
    const out = []
    if (isGated(primaryModel.value)) out.push(primaryModel.value)
    return out
  })
  const needsHfToken = computed(() => gatedModels.value.length > 0)

  // List of things that will be downloaded — drives steps 6 (licenses),
  // 7 (per-model progress bars), and 8 (the "you ended up with N" summary).
  const enabledList = computed(() => {
    const list = []
    if (primaryModel.value) {
      list.push({
        kind: 'primary',
        slot: null, child: null,
        modelId: primaryModel.value.id,
        label:   primaryModel.value.display_name,
        sizeGb:  primaryModel.value.size_gb,
        license: primaryModel.value.license,
        license_url: primaryModel.value.license_url,
      })
    }
    const capRow = (key, slot, child, capName) => {
      const c = form.caps[key]
      if (!c.enabled || !c.model) return
      const o = optionsFor(slot, child).find((x) => x.id === c.model && x.backend === c.backend)
      list.push({
        kind: capName,
        slot, child,
        modelId: c.model,
        label:   `${capName} — ${c.model} (${c.backend})`,
        sizeGb:  o?.size_gb ?? 0,
        license: o?.license ?? 'See repo page',
        license_url: o?.license_url ?? null,
        backend: c.backend,
        provider: c.provider,
      })
    }
    capRow('embed',  'embed', 'embed',  'embed')
    capRow('rerank', 'embed', 'rerank', 'rerank')
    capRow('stt',    'voice', 'stt',    'stt')
    capRow('tts',    'voice', 'tts',    'tts')
    capRow('img',    'img',   'img',    'img')
    return list
  })

  // ── Step 1 — password ─────────────────────────────────────────────
  async function submitPassword() {
    if (!form.password) return
    // Always include first_run_token, even when empty. The API tolerates
    // an empty value when the request originates on the loopback
    // interface (operator running curl on the box itself); for every
    // other origin the OTP is required. The UI doesn't know the
    // request's source — sending blindly keeps the loopback path working
    // without trying to autodetect localhost.
    await api('/api/auth/password', {
      method: 'POST',
      body: JSON.stringify({
        password: form.password,
        first_run_token: form.firstRunToken || '',
      }),
    })
    passwordAlreadySet.value = true
    form.password = ''
    form.firstRunToken = ''
  }

  // "Skip — leave open" path. Flips the box to trusted-LAN posture
  // (HAL0_AUTH_DISABLED=1 in /etc/hal0/api.env + deferred restart) so
  // Settings → Authentication and every other writer-scoped admin
  // route stops 401'ing. The endpoint is gated server-side on
  // no-password + lockfile-present, so this only fires during the
  // first-run claim window. We swallow individual error codes —
  // the wizard advances either way, since the user explicitly chose
  // "skip" and there's nothing else to do on this step.
  async function disableAuthForSkip() {
    try {
      await api('/api/auth/disable', { method: 'POST' })
    } catch (e) {
      // Surface for telemetry but don't block the wizard. The most
      // common failure is "claim window closed" (lockfile already
      // consumed by an earlier run) — harmless, the box stays in
      // whatever posture it was already in.
      // eslint-disable-next-line no-console
      console.warn('disableAuthForSkip failed:', e?.message || e)
    }
  }

  // ── Step 2 — model dirs ───────────────────────────────────────────
  async function persistModelDirs() {
    const roots = form.modelDirs.map((s) => String(s).trim()).filter(Boolean)
    if (!roots.length) return  // server would 422; UI gates the Next button
    try {
      const r = await api('/api/config/models', {
        method: 'PUT',
        body: JSON.stringify({ roots }),
      })
      modelsConfig.value = r
      modelsConfigWritable.value = true
    } catch (e) {
      // Don't block the wizard on a persistence failure — surface it but
      // let the user continue with the default root. (Edge case: a path
      // they typed is unwriteable; the registry scan will skip it and the
      // user can fix it later from Settings → Storage.)
      modelsConfigWritable.value = false
      throw e
    }
  }

  // ── Step 5 — HF token ─────────────────────────────────────────────
  function persistHfToken() {
    // Cached in localStorage so a refresh doesn't lose it. The orchestrator
    // can only see HF_TOKEN from the API process env, so this stash is for
    // the wizard's own convenience until /api/secrets/hf-token lands
    // (tracked-in: #78).
    try {
      if (form.hfToken) {
        window.localStorage.setItem('hal0:firstrun:hf_token', form.hfToken)
      } else {
        window.localStorage.removeItem('hal0:firstrun:hf_token')
      }
    } catch { /* private-mode browsers may throw */ }
  }

  // ── Step 7 — pulls + capability registration ──────────────────────
  function _itemKey(it) { return `${it.kind}:${it.modelId}` }

  function _initPullItems() {
    pull.items = enabledList.value.map((it) => ({
      key: _itemKey(it),
      kind: it.kind,
      slot: it.slot,
      child: it.child,
      modelId: it.modelId,
      label: it.label,
      backend: it.backend,
      provider: it.provider,
      sizeGb: it.sizeGb,
      state: 'queued',  // queued | running | completed | failed | cancelled
      bytesDownloaded: 0,
      bytesTotal: 0,
      pct: 0,
      error: null,
      eventSource: null,
      registered: false,  // capability-registration done (POST /api/capabilities/...)
    }))
  }

  function _subscribePullProgress(item) {
    if (item.eventSource) {
      try { item.eventSource.close() } catch { /* ignore */ }
      item.eventSource = null
    }
    const es = new EventSource(`/api/models/${item.modelId}/pull/stream`)
    item.eventSource = es
    es.onmessage = (evt) => {
      try {
        const snap = JSON.parse(evt.data)
        item.state = snap.state || item.state
        item.bytesDownloaded = snap.bytes_downloaded ?? item.bytesDownloaded
        item.bytesTotal = snap.bytes_total ?? item.bytesTotal
        item.pct = item.bytesTotal
          ? Math.min(100, Math.round((item.bytesDownloaded / item.bytesTotal) * 100))
          : item.pct
        if (snap.state === 'completed') {
          item.pct = 100
          es.close(); item.eventSource = null
          _onItemComplete(item)
        } else if (snap.state === 'failed' || snap.state === 'cancelled') {
          item.error = snap.error || `pull ${snap.state}`
          es.close(); item.eventSource = null
          _checkAllDone()
        }
      } catch (err) {
        console.error('pull SSE parse failed', err)
      }
    }
    es.onerror = () => {
      // Browser auto-reconnects; fall back to polling so a flaky
      // connection still moves the progress bar.
      _pollPullStatus(item)
    }
  }

  async function _pollPullStatus(item) {
    if (item.state === 'completed' || item.state === 'failed' || item.state === 'cancelled') return
    try {
      const s = await api(`/api/models/${item.modelId}/pull/status`)
      item.state = s.state || item.state
      item.bytesDownloaded = s.bytes_downloaded ?? item.bytesDownloaded
      item.bytesTotal = s.bytes_total ?? item.bytesTotal
      item.pct = item.bytesTotal
        ? Math.min(100, Math.round((item.bytesDownloaded / item.bytesTotal) * 100))
        : item.pct
      if (s.state === 'completed') {
        item.pct = 100
        _onItemComplete(item); return
      }
      if (s.state === 'failed' || s.state === 'cancelled') {
        item.error = s.error || `pull ${s.state}`
        _checkAllDone(); return
      }
      setTimeout(() => _pollPullStatus(item), 800)
    } catch {
      setTimeout(() => _pollPullStatus(item), 1500)
    }
  }

  async function _onItemComplete(item) {
    // For capability models, now flip the orchestrator to enabled=true. For
    // the primary chat row, /api/install/pick-default already wrote the
    // slot TOML — nothing to do here.
    if (item.slot && item.child && !item.registered) {
      try {
        await api(`/api/capabilities/${item.slot}/${item.child}`, {
          method: 'POST',
          body: JSON.stringify({
            backend: item.backend,
            provider: item.provider,
            model: item.modelId,
            enabled: true,
          }),
        })
        item.registered = true
      } catch (e) {
        // Pull succeeded but the orchestrator refused — surface as a
        // per-row error so the operator can retry from step 7's retry
        // button without re-pulling.
        item.error = `capability register failed: ${e?.message || e}`
      }
    }
    _checkAllDone()
  }

  function _checkAllDone() {
    const terminal = pull.items.every(
      (it) => it.state === 'completed' || it.state === 'failed' || it.state === 'cancelled',
    )
    if (terminal) {
      pull.done = true
      pull.error = pull.items.some((it) => it.state !== 'completed')
        ? 'one or more downloads failed — retry below'
        : null
    }
  }

  async function _startOne(item) {
    item.state = 'running'
    item.error = null
    item.pct = 0
    item.bytesDownloaded = 0
    try {
      if (item.kind === 'primary') {
        // pick-default = register + assign + pull, in one shot.
        await api('/api/install/pick-default', {
          method: 'POST',
          body: JSON.stringify({ model_id: item.modelId, slot: 'primary' }),
        })
      } else {
        // Capability models: kick the pull directly. The orchestrator's
        // POST step happens after the pull completes (see _onItemComplete).
        await api(`/api/models/${item.modelId}/pull`, { method: 'POST' })
      }
      _subscribePullProgress(item)
    } catch (e) {
      item.state = 'failed'
      item.error = e?.message || String(e)
      _checkAllDone()
    }
  }

  async function startAllPulls() {
    pull.started = true
    pull.done = false
    pull.error = null
    _initPullItems()
    if (pull.items.length === 0) {
      // Nothing-to-do install (operator skipped chat + every capability).
      // _checkAllDone short-circuits to terminal=true on an empty array,
      // so the wizard flips to step 8 immediately rather than dangling
      // on a blank progress screen.
      _checkAllDone()
      return
    }
    await Promise.all(pull.items.map((it) => _startOne(it)))
  }

  // Re-try one failed/cancelled pull without resetting the others. Wired
  // to the per-row "Retry" button on step 7.
  async function retryItem(key) {
    const it = pull.items.find((x) => x.key === key)
    if (!it) return
    if (it.eventSource) { try { it.eventSource.close() } catch { /* ignore */ } it.eventSource = null }
    pull.done = false
    await _startOne(it)
  }

  // ── Step 7 — bundled agent (Phase 8) ──────────────────────────────
  // POST /api/agents/install in fire-and-forget mode; we don't block the
  // wizard on the agent driver's shell-out because the dashboard's
  // /agent page is the right place to recover from a failed install.
  // Errors are surfaced as a return value so the view layer can toast.
  async function installAgent() {
    const choice = form.agentChoice
    if (!choice || choice === 'none') return null
    try {
      const rec = await api('/api/agents/install', {
        method: 'POST',
        body: JSON.stringify({ name: choice }),
      })
      return { ok: true, record: rec }
    } catch (e) {
      return { ok: false, error: e }
    }
  }

  // ── Step 8 — complete ─────────────────────────────────────────────
  async function markComplete() {
    try { await api('/api/install/complete', { method: 'POST' }) }
    catch (e) { console.warn('install/complete failed', e) }
  }

  function dispose() {
    for (const it of pull.items) {
      if (it.eventSource) { try { it.eventSource.close() } catch { /* ignore */ } it.eventSource = null }
    }
  }

  _state = {
    // status
    loading, loadError,
    hardware, curated, customAllowed, capsData, installState,
    passwordAlreadySet, modelsConfig, modelsConfigWritable,
    // form
    form,
    // pull
    pull,
    // computed
    primaryModel, totalDownloadGb, diskFreeGb, fits,
    gatedModels, needsHfToken, enabledList,
    // helpers
    optionsFor, setCapModel, sizeFor,
    // actions
    load, submitPassword, disableAuthForSkip, persistModelDirs, persistHfToken,
    startAllPulls, retryItem, markComplete, dispose,
    installAgent,
  }
  load()
  return _state
}
