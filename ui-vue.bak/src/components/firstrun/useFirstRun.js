/**
 * useFirstRun — v2 state machine for the FirstRun /firstrun route.
 *
 * Three-state design (replacing the v1 8-step linear wizard):
 *
 *   pick     — bundle picker (Lite | Default | Pro | Max + LMX kit)
 *   confirm  — per-slot install list + optional NPU trio toggle
 *   progress — per-row download bars + inline retry/skip controls
 *
 * Tier picked → confirm-state populates the install list from the
 * curated catalog (preferring the curated chat model for that RAM tier);
 * Install triggers parallel pulls and capability registration, then
 * routes back to `/` and writes the first-run sentinel.
 *
 * Singleton-at-module-scope so the per-state components mount/unmount
 * without losing form state mid-flow.
 *
 * Endpoint contract (preserved from v1):
 *   GET  /api/hardware                    — RAM/CPU/GPU/NPU probe
 *   GET  /api/install/state               — first_run sentinel
 *   GET  /api/install/curated-models      — chat-model curated catalog
 *   GET  /api/capabilities                — backends + catalogs + selections
 *   POST /api/install/pick-default        — primary chat: register+assign+pull
 *   POST /api/models/{id}/pull            — kick off a HF pull
 *   GET  /api/models/{id}/pull/stream     — SSE progress
 *   POST /api/capabilities/{slot}/{child} — register capability slot model
 *   POST /api/install/complete            — write sentinel
 */
import { reactive, computed, ref } from 'vue'
import { api } from '../../composables/useApi.js'

let _state = null

/**
 * Bundle catalog — mirrors the design source's MOCK_DATA.bundles. The
 * runtime adds derived fields (recommended, fits, gated, installed) on
 * top of these immutable rows.
 */
export const BUNDLES = Object.freeze([
  {
    id: 'lite',
    name: 'Lite',
    ram: 16,
    sizeGB: 1.2,
    desc: 'Chat only — a small LLM on CPU/GPU.',
    includes: [
      { label: 'chat (1.2B params)', active: true },
      { label: 'embed', active: false },
      { label: 'voice', active: false },
      { label: 'image', active: false },
    ],
  },
  {
    id: 'default',
    name: 'Default',
    ram: 32,
    sizeGB: 8.4,
    desc: 'Mainstream chat + embed + transcription + TTS.',
    includes: [
      { label: 'chat (qwen3.5-9b)', active: true },
      { label: 'embed (nomic-v1.5)', active: true },
      { label: 'voice (whisper-base + kokoro)', active: true },
      { label: 'image', active: false },
    ],
  },
  {
    id: 'pro',
    name: 'Pro',
    ram: 64,
    sizeGB: 38,
    desc: 'Chat + coder + rerank + full A/V + image.',
    includes: [
      { label: 'chat + coder (qwen3.6-27b, qwen3-coder-30b)', active: true },
      { label: 'embed + rerank', active: true },
      { label: 'voice', active: true },
      { label: 'image (sd-turbo)', active: true },
    ],
  },
  {
    id: 'max',
    name: 'Max',
    ram: 100,
    sizeGB: 75,
    desc: 'Pro + NPU trio + bigger models.',
    includes: [
      { label: 'chat + coder + NPU agent', active: true },
      { label: 'embed + rerank + embed-npu', active: true },
      { label: 'voice (whisper-large + kokoro + stt-npu)', active: true },
      { label: 'image (flux-2-klein)', active: true },
    ],
  },
])

/**
 * Detail manifests per tier (only `pro` is detailed in the v0.3 design
 * mock; the others fall back to the per-tier `includes` summary in the
 * confirm card). Slot rows mirror MOCK_DATA.bundleDetails.
 */
const BUNDLE_DETAILS = Object.freeze({
  lite: {
    models: [
      { slot: 'primary', model: 'llama-3.2-1b-instruct', size: '1.2 GB', tag: 'chat default' },
    ],
    npu: [],
  },
  default: {
    models: [
      { slot: 'primary', model: 'Qwen3.5-9B-Instruct-Q4_K_M', size: '5.4 GB', tag: 'chat default' },
      { slot: 'embed',   model: 'nomic-embed-text-v1.5',      size: '350 MB', tag: 'embed default' },
      { slot: 'stt',     model: 'whisper-base',               size: '150 MB', tag: 'stt default' },
      { slot: 'tts',     model: 'kokoro-v1',                  size: '400 MB', tag: 'tts default · cpu' },
    ],
    npu: [],
  },
  pro: {
    models: [
      { slot: 'primary',  model: 'Qwen3.6-27B-MTP-Q4_K_M',     size: '18.8 GB', tag: 'chat default' },
      { slot: 'coder',    model: 'Qwen3-Coder-30B-A3B-Q4_K_M', size: '18.6 GB', tag: 'chat coder' },
      { slot: 'embed',    model: 'nomic-embed-text-v1.5',      size: '350 MB',  tag: 'embed default' },
      { slot: 'rerank',   model: 'bge-reranker-v2-m3-q4_k_m',  size: '400 MB',  tag: 'rerank default' },
      { slot: 'stt',      model: 'whisper-base',                size: '150 MB',  tag: 'stt default' },
      { slot: 'tts',      model: 'kokoro-v1',                   size: '400 MB',  tag: 'tts default · cpu' },
      { slot: 'img',      model: 'sd-turbo',                     size: '1.2 GB',  tag: 'img default' },
    ],
    npu: [
      { slot: 'agent',     model: 'gemma3:1b',        size: '1.0 GB', tag: 'npu chat' },
      { slot: 'stt-npu',   model: 'whisper-v3-turbo', size: '400 MB', tag: 'coresident' },
      { slot: 'embed-npu', model: 'embed-gemma-300m', size: '350 MB', tag: 'coresident' },
    ],
  },
  max: {
    models: [
      { slot: 'primary',  model: 'Qwen3.6-35B-MTP-Q4_K_M',     size: '22 GB',   tag: 'chat default' },
      { slot: 'coder',    model: 'Qwen3-Coder-30B-A3B-Q4_K_M', size: '18.6 GB', tag: 'chat coder' },
      { slot: 'embed',    model: 'nomic-embed-text-v1.5',      size: '350 MB',  tag: 'embed default' },
      { slot: 'rerank',   model: 'bge-reranker-v2-m3-q4_k_m',  size: '400 MB',  tag: 'rerank default' },
      { slot: 'stt',      model: 'whisper-large-v3',           size: '3.0 GB',  tag: 'stt default' },
      { slot: 'tts',      model: 'kokoro-v1',                   size: '400 MB',  tag: 'tts default · cpu' },
      { slot: 'img',      model: 'flux-2-klein-9b',             size: '12 GB',   tag: 'img default' },
    ],
    npu: [
      { slot: 'agent',     model: 'gemma3:1b',        size: '1.0 GB', tag: 'npu chat' },
      { slot: 'stt-npu',   model: 'whisper-v3-turbo', size: '400 MB', tag: 'coresident' },
      { slot: 'embed-npu', model: 'embed-gemma-300m', size: '350 MB', tag: 'coresident' },
    ],
  },
})

/**
 * RAM → recommended tier mapping. Picks the largest tier whose minimum
 * is ≤ detected RAM. Returns null when even Lite (16 GB) doesn't fit.
 */
export function pickRecommendedTier(ramGb) {
  if (!Number.isFinite(ramGb) || ramGb <= 0) return null
  const fit = BUNDLES.filter((b) => b.ram <= ramGb)
  if (!fit.length) return null
  return fit[fit.length - 1].id
}

/**
 * Heuristic: which models are gated on HuggingFace. Without a `gated`
 * field on the catalog row we infer from id family. Llama + whisper-v3
 * NPU are gated; tweak when backend grows a proper flag.
 */
const GATED_RE = /^(llama-|meta-llama|whisper-v3-npu)/i

function isGatedModel(idOrRow) {
  if (!idOrRow) return false
  if (typeof idOrRow === 'object') {
    if (idOrRow.gated === true) return true
    return GATED_RE.test(idOrRow.id || '') || GATED_RE.test(idOrRow.hf_repo || '')
  }
  return GATED_RE.test(String(idOrRow))
}

/**
 * Bundle has any gated model? Inferred from BUNDLE_DETAILS.
 */
function bundleHasGated(tierId) {
  const det = BUNDLE_DETAILS[tierId]
  if (!det) return false
  for (const row of [...(det.models || []), ...(det.npu || [])]) {
    if (isGatedModel(row.model)) return true
  }
  return false
}

export function useFirstRun() {
  if (_state) return _state

  // ── Async-load status ─────────────────────────────────────────────
  const loading = ref(true)
  const loadError = ref(null)

  // Server snapshots
  const hardware     = ref(null)
  const curated      = ref([])
  const installState = ref({ first_run: true })
  const capsData     = reactive({ backends: [], catalogs: {}, selections: {} })

  // State machine: 'pick' | 'confirm' | 'progress'
  const view = ref('pick')

  // Picked tier id (null until user clicks a Pick button).
  const pickedTier = ref(null)
  // NPU trio opt-in toggle (confirm state).
  const withNpu = ref(false)

  // Per-model pull tracking (progress state).
  // items[] = { key, slot, model, size, tag, kind, state, pct, bytesDownloaded,
  //   bytesTotal, rate, eta, error, eventSource, registered }
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
      const [hw, st, cur, caps] = await Promise.all([
        api('/api/hardware').catch(() => null),
        api('/api/install/state').catch(() => ({ first_run: true })),
        api('/api/install/curated-models').catch(() => ({ models: [], custom_allowed: false })),
        api('/api/capabilities').catch(() => ({ backends: [], catalogs: {}, selections: {} })),
      ])
      hardware.value = hw
      installState.value = st || { first_run: true }
      curated.value = cur?.models || []
      capsData.backends   = caps?.backends   ?? []
      capsData.catalogs   = caps?.catalogs   ?? {}
      capsData.selections = caps?.selections ?? {}
    } catch (e) {
      loadError.value = e?.message || String(e)
    } finally {
      loading.value = false
    }
  }

  // ── Computed: hardware-derived ────────────────────────────────────
  /** Detected unified-or-system RAM in GB. */
  const ramGb = computed(() => {
    const hw = hardware.value || {}
    const mb = hw.unified_memory_mb || hw.ram_total_mb || 0
    return Math.round(mb / 1024)
  })

  /** Free disk in GB on the first model dir. */
  const diskFreeGb = computed(() => {
    const hw = hardware.value || {}
    return Math.round((hw.disk_free_mb || 0) / 1024)
  })

  /** GPU display name. */
  const gpuLabel = computed(() => {
    const hw = hardware.value || {}
    return hw.gpu_name || hw.gpu_vendor || '—'
  })

  /** NPU presence (true when detected). */
  const npuPresent = computed(() => !!hardware.value?.npu_present)

  /** Recommended tier id for the detected RAM. */
  const recommendedTier = computed(() => pickRecommendedTier(ramGb.value))

  /** Re-entry: dashboard already initialised on this box. */
  const isReEntered = computed(() => installState.value?.first_run === false)

  /** RAM below the Lite minimum (16 GB) — even Lite won't run. */
  const ramTooLow = computed(() => ramGb.value > 0 && ramGb.value < 16)

  /** HF_TOKEN cached in localStorage. */
  const hasHfToken = computed(() => {
    try {
      return !!window.localStorage.getItem('hf_token')
    } catch {
      return false
    }
  })

  /**
   * Per-tier state — drives the TierCard's chip + button.
   *   'recommended' | 'available' | 'unfit' | 'installed' | 'gated-no-hf'
   *
   * Precedence: ramTooLow > unfit > installed > recommended > gated > available.
   * "installed" only applies on re-entry (else first_run=true and nothing is on disk).
   */
  function tierStateFor(tierId) {
    const b = BUNDLES.find((x) => x.id === tierId)
    if (!b) return 'available'
    if (b.ram > ramGb.value) return 'unfit'
    if (isReEntered.value) return 'installed'
    if (bundleHasGated(tierId) && !hasHfToken.value) return 'gated-no-hf'
    if (tierId === recommendedTier.value) return 'recommended'
    return 'available'
  }

  /** Bundles with derived state baked on for the card grid/table. */
  const bundles = computed(() =>
    BUNDLES.map((b) => ({
      ...b,
      _state: tierStateFor(b.id),
      _fits: b.ram <= ramGb.value,
      _recommended: b.id === recommendedTier.value,
      _hasGated: bundleHasGated(b.id),
    })),
  )

  /** Current bundle details for the confirm state. */
  const currentBundle = computed(() =>
    pickedTier.value ? bundles.value.find((b) => b.id === pickedTier.value) : null,
  )

  const currentDetails = computed(() =>
    pickedTier.value ? BUNDLE_DETAILS[pickedTier.value] || { models: [], npu: [] } : null,
  )

  /** Combined install list — slot rows + NPU rows (when withNpu). */
  const installList = computed(() => {
    const d = currentDetails.value
    if (!d) return []
    const out = [...d.models]
    if (withNpu.value && d.npu?.length) out.push(...d.npu)
    return out
  })

  /** Aggregate download size for the picked bundle (rough — uses tag sizes). */
  const aggregateSizeGb = computed(() => {
    const d = currentDetails.value
    if (!d) return 0
    const rows = [...d.models, ...(withNpu.value ? d.npu || [] : [])]
    let total = 0
    for (const r of rows) {
      const m = String(r.size).match(/([\d.]+)\s*(GB|MB)/i)
      if (!m) continue
      const n = parseFloat(m[1])
      total += m[2].toUpperCase() === 'GB' ? n : n / 1024
    }
    return total
  })

  /** Does the picked bundle fit on disk? */
  const fitsDisk = computed(() => aggregateSizeGb.value <= diskFreeGb.value)

  /** Picked bundle has gated models and no HF_TOKEN. */
  const needsHfToken = computed(() => {
    if (!pickedTier.value) return false
    return bundleHasGated(pickedTier.value) && !hasHfToken.value
  })

  // ── State transitions ────────────────────────────────────────────
  function pickBundle(tierId) {
    const b = BUNDLES.find((x) => x.id === tierId)
    if (!b) return
    if (b.ram > ramGb.value) return  // unfit — never advance
    pickedTier.value = tierId
    withNpu.value = false
    view.value = 'confirm'
  }

  function backToPicker() {
    view.value = 'pick'
  }

  function startInstall() {
    if (!pickedTier.value) return
    view.value = 'progress'
    pull.started = true
    pull.done = false
    pull.error = null
    _initPullItems()
    _startAll()
  }

  // ── Pull lifecycle ───────────────────────────────────────────────
  function _initPullItems() {
    const rows = installList.value
    pull.items = rows.map((r) => ({
      key: `${r.slot}:${r.model}`,
      slot: r.slot,
      model: r.model,
      size: r.size,
      tag: r.tag || '',
      kind: r.slot === 'primary' ? 'primary' : 'capability',
      state: 'queued',  // queued | pulling | verifying | done | failed | paused
      pct: 0,
      bytesDownloaded: 0,
      bytesTotal: 0,
      rate: '',
      eta: '',
      error: null,
      eventSource: null,
      registered: false,
    }))
  }

  function _itemModelId(item) {
    // Lemonade slug — lowercase + dashes; mirrors how the catalog
    // exposes model_id for the MOCK_DATA entries.
    return String(item.model)
      .toLowerCase()
      .replace(/[^a-z0-9.+_-]+/g, '-')
      .replace(/^-+|-+$/g, '')
  }

  function _subscribePullProgress(item) {
    if (item.eventSource) {
      try { item.eventSource.close() } catch { /* ignore */ }
      item.eventSource = null
    }
    const modelId = _itemModelId(item)
    let es
    try {
      es = new EventSource(`/api/models/${modelId}/pull/stream`)
    } catch {
      // Browser / sandbox without EventSource — fall back to polling.
      _pollPullStatus(item)
      return
    }
    item.eventSource = es
    es.onmessage = (evt) => {
      try {
        const snap = JSON.parse(evt.data)
        const next = snap.state || item.state
        item.state = next === 'completed' ? 'done' : next === 'running' ? 'pulling' : next
        item.bytesDownloaded = snap.bytes_downloaded ?? item.bytesDownloaded
        item.bytesTotal = snap.bytes_total ?? item.bytesTotal
        item.pct = item.bytesTotal
          ? Math.min(100, Math.round((item.bytesDownloaded / item.bytesTotal) * 100))
          : item.pct
        if (snap.rate) item.rate = snap.rate
        if (snap.eta) item.eta = snap.eta
        if (next === 'completed') {
          item.pct = 100
          item.state = 'done'
          es.close(); item.eventSource = null
          _onItemComplete(item)
        } else if (next === 'failed' || next === 'cancelled') {
          item.error = snap.error || `pull ${next}`
          item.state = 'failed'
          es.close(); item.eventSource = null
          _checkAllDone()
        }
      } catch (err) {
        console.error('pull SSE parse failed', err)
      }
    }
    es.onerror = () => {
      _pollPullStatus(item)
    }
  }

  async function _pollPullStatus(item) {
    if (item.state === 'done' || item.state === 'failed') return
    const modelId = _itemModelId(item)
    try {
      const s = await api(`/api/models/${modelId}/pull/status`)
      const next = s.state === 'completed' ? 'done' : s.state === 'running' ? 'pulling' : s.state
      item.state = next || item.state
      item.bytesDownloaded = s.bytes_downloaded ?? item.bytesDownloaded
      item.bytesTotal = s.bytes_total ?? item.bytesTotal
      item.pct = item.bytesTotal
        ? Math.min(100, Math.round((item.bytesDownloaded / item.bytesTotal) * 100))
        : item.pct
      if (s.state === 'completed') {
        item.state = 'done'; item.pct = 100
        _onItemComplete(item); return
      }
      if (s.state === 'failed' || s.state === 'cancelled') {
        item.state = 'failed'
        item.error = s.error || `pull ${s.state}`
        _checkAllDone(); return
      }
      setTimeout(() => _pollPullStatus(item), 800)
    } catch {
      setTimeout(() => _pollPullStatus(item), 1500)
    }
  }

  async function _onItemComplete(item) {
    // For capability rows, register the slot mapping after the weight
    // is on disk. Primary chat uses /api/install/pick-default which does
    // both at once (see _startOne).
    if (item.kind === 'capability' && !item.registered) {
      const slot = item.slot
      // Map slot → (capability slot group, child). Mirrors the old
      // useFirstRun's enabledList capRow helper, but with the v0.3 tier
      // detail slots as keys.
      const child = (
        slot === 'embed' ? ['embed', 'embed'] :
        slot === 'rerank' ? ['embed', 'rerank'] :
        slot === 'stt' || slot === 'stt-npu' ? ['voice', 'stt'] :
        slot === 'tts' ? ['voice', 'tts'] :
        slot === 'img' ? ['img', 'img'] :
        slot === 'agent' || slot === 'coder' ? null :  // chat slots — no capability registration
        slot === 'embed-npu' ? ['embed', 'embed'] :
        null
      )
      if (!child) {
        item.registered = true
        _checkAllDone()
        return
      }
      try {
        await api(`/api/capabilities/${child[0]}/${child[1]}`, {
          method: 'POST',
          body: JSON.stringify({
            model: _itemModelId(item),
            enabled: true,
          }),
        })
        item.registered = true
      } catch (e) {
        item.error = `capability register failed: ${e?.message || e}`
      }
    }
    _checkAllDone()
  }

  function _checkAllDone() {
    const terminal = pull.items.every((it) => it.state === 'done' || it.state === 'failed')
    if (terminal) {
      pull.done = true
      pull.error = pull.items.some((it) => it.state !== 'done')
        ? 'one or more downloads failed — retry below'
        : null
    }
  }

  async function _startOne(item) {
    item.state = 'pulling'
    item.error = null
    item.pct = 0
    item.bytesDownloaded = 0
    const modelId = _itemModelId(item)
    try {
      if (item.kind === 'primary') {
        await api('/api/install/pick-default', {
          method: 'POST',
          body: JSON.stringify({ model_id: modelId, slot: 'primary' }),
        })
      } else {
        await api(`/api/models/${modelId}/pull`, { method: 'POST' })
      }
      _subscribePullProgress(item)
    } catch (e) {
      item.state = 'failed'
      item.error = e?.message || String(e)
      _checkAllDone()
    }
  }

  async function _startAll() {
    if (pull.items.length === 0) {
      _checkAllDone()
      return
    }
    await Promise.all(pull.items.map((it) => _startOne(it)))
  }

  /** Retry one failed row without touching the others. */
  async function retryItem(key) {
    const it = pull.items.find((x) => x.key === key)
    if (!it) return
    if (it.eventSource) { try { it.eventSource.close() } catch { /* ignore */ } it.eventSource = null }
    pull.done = false
    await _startOne(it)
  }

  /** Mark a single row as skipped — removes it from the pending set. */
  function skipItem(key) {
    const it = pull.items.find((x) => x.key === key)
    if (!it) return
    if (it.eventSource) { try { it.eventSource.close() } catch { /* ignore */ } it.eventSource = null }
    it.state = 'done'
    it.error = null
    it.pct = 100
    it.registered = true  // pretend, so _onItemComplete short-circuits
    _checkAllDone()
  }

  /** "Pause all" — best-effort; today just closes the SSE streams. */
  function pauseAll() {
    for (const it of pull.items) {
      if (it.eventSource) { try { it.eventSource.close() } catch { /* ignore */ } it.eventSource = null }
      if (it.state === 'pulling') it.state = 'paused'
    }
  }

  // ── Complete + dispose ───────────────────────────────────────────
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
    hardware, curated, installState, capsData,
    // state machine
    view, pickedTier, withNpu,
    // pull progress
    pull,
    // derived hardware
    ramGb, diskFreeGb, gpuLabel, npuPresent,
    recommendedTier, isReEntered, ramTooLow, hasHfToken,
    // bundle state
    bundles, currentBundle, currentDetails, installList,
    aggregateSizeGb, fitsDisk, needsHfToken,
    // transitions
    pickBundle, backToPicker, startInstall,
    retryItem, skipItem, pauseAll,
    // lifecycle
    load, markComplete, dispose,
    // helpers (exported for templates + tests)
    tierStateFor,
  }
  load()
  return _state
}

/** Test-only — reset the singleton between Vitest runs. */
export function __resetFirstRunSingleton() { _state = null }
