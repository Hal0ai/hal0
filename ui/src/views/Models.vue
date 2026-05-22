<script setup>
/**
 * Models.vue
 *
 * Design decisions:
 * - Table layout > cards. Models have many attributes; a dense table lets you
 *   scan all of them without scrolling. Card-per-model requires too many clicks.
 * - Pull flow: a "Pull model" button opens a modal with (a) curated presets and
 *   (b) a raw HF URL input. Curated list shows size + VRAM requirements.
 * - Delete confirmation warns how many slots depend on the model ("3 slots use
 *   this model — they will be unassigned"). Impact > 1 requires typing the model
 *   name to prevent accidents.
 * - Slot assignment column shows which slot is using each model inline, with a
 *   quick "Assign to" dropdown — no page navigation required.
 * - `n` to open Pull form, `/` to focus search.
 */
import { ref, computed, onMounted, onUnmounted, reactive } from 'vue'
import { useSystemStore } from '../stores/system.js'
import { useToastsStore } from '../stores/toasts.js'
import { useAgentStore } from '../stores/agent.js'
import { api } from '../composables/useApi.js'
import { usePullJob, fmtBytes, fmtSpeed, fmtEta } from '../composables/usePullJob.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import EmptyState from '../components/EmptyState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import AgentPendingChip from '../components/agent/AgentPendingChip.vue'

const system = useSystemStore()
const toasts = useToastsStore()
const agent  = useAgentStore()

// ── State ──────────────────────────────────────────────────────────────
const models   = ref([])
const loading  = ref(true)
const error    = ref(null)
const search   = ref('')
const searchEl = ref(null)

// Add model modal (was: Pull model — renamed per B3 spec)
const showPull   = ref(false)
const pullTab    = ref('curated')   // 'curated' | 'hf' | 'local'
const pullForm   = ref({ hf_url: '', name: '', quant: 'Q4_K_M' })
const pullErrors = ref({})
const pulling    = ref(false)

// ── Valid backend / capability vocab (mirrors src/hal0/registry/model.py) ──
const ALL_CAPS     = ['chat', 'embed', 'rerank', 'vision', 'asr', 'tts']
const ALL_BACKENDS = ['vulkan', 'rocm', 'cuda', 'cpu', 'moonshine', 'kokoro', 'flm']

// Per-kind compatibility — drives the chips the user sees when editing
// a model. kokoro/moonshine aren't llama-server backends; chat isn't a
// kokoro capability. Showing every combo invited misclicks.
const KIND_BACKENDS = {
  llama:     ['vulkan', 'rocm', 'cuda', 'cpu'],
  flm:       ['flm'],
  moonshine: ['moonshine'],
  kokoro:    ['kokoro'],
  unknown:   ALL_BACKENDS,
}
const KIND_CAPS = {
  llama:     ['chat', 'embed', 'rerank', 'vision'],
  flm:       ['chat', 'embed'],
  moonshine: ['asr'],
  kokoro:    ['tts'],
  unknown:   ALL_CAPS,
}
const KIND_LABEL = {
  llama:     'LLaMA',
  flm:       'FLM',
  moonshine: 'Moonshine (ASR)',
  kokoro:    'Kokoro (TTS)',
  unknown:   'Unknown',
}
const KIND_ORDER = ['llama', 'flm', 'moonshine', 'kokoro', 'unknown']

function backendsForKind(kind) {
  return KIND_BACKENDS[kind] ?? ALL_BACKENDS
}
function capsForKind(kind) {
  return KIND_CAPS[kind] ?? ALL_CAPS
}

// ── Local-file tab state ──────────────────────────────────────────────
// Two sub-modes inside the Local-file tab:
//   - 'single': register one file via /scan/preview then POST /api/models
//   - 'scan'  : preview a directory then commit edited rows via /scan
const localMode    = ref('single')   // 'single' | 'scan'
const localPath    = ref('')         // shared path input
const localName    = ref('')         // single-file display-name override
const localLicense = ref('')         // single-file license override
const localRecursive = ref(true)     // scan-directory recursive toggle

// Single-file detection preview (after Detect press, before commit).
const singleDetected = ref(null)     // DetectionResult-shaped object or null
// Editable single-file row mirrors a scan preview row: backends + caps live
// here so the user can override before committing.
const singleEditable = ref({ backends: [], capabilities: [] })
const localBusy = ref(false)

// Scan-directory preview rows: each is an editable row mirroring backend
// shape — { path, id, name, backends, capabilities, context_length, confidence, raw_hints }
const scanRows = ref([])

// ── Edit model modal ──────────────────────────────────────────────────
const editingModel    = ref(null)
const editForm        = ref({
  name: '',
  capabilities: [],
  backends: [],
  defaults: { context_size: null, n_gpu_layers: null, rope_freq_base: null, extra_args: '' },
})
const editOriginal    = ref(null)    // pristine copy for "Reset to detected" of each default field
const editSubmitting  = ref(false)
const editAdvOpen     = ref(false)
const editDetected    = ref(null)    // re-detect DetectionResult cached for diff render
const editReDetecting = ref(false)

// Per-model active pull jobs. Each row that goes into a "downloading"
// substate gets its own usePullJob instance so multiple pulls can run
// in parallel and the inline progress bars are independent (Team I
// gap #3).
const pullJobs = reactive({})  // { [modelId]: usePullJob() }

function ensureJob(modelId) {
  if (!pullJobs[modelId]) {
    pullJobs[modelId] = usePullJob()
  }
  return pullJobs[modelId]
}

function jobFor(modelId) {
  return pullJobs[modelId] ?? null
}

// Curated catalogue — populated on mount from GET /api/models/catalogue.
// Split into two shapes:
//   - pullableCatalogue: CuratedModel entries with HF coordinates (Pull button).
//   - upstreamCatalogue: HaloaiModel entries that route to a configured upstream.
const pullableCatalogue = ref([])
const upstreamCatalogue = ref([])

// Hardware-target mapping for upstream backends. Mirrors the chip palette
// used by Dashboard.hardwareTarget so the colour vocabulary stays consistent
// across views.
function backendTarget(backend) {
  const b = (backend || '').toLowerCase()
  if (b === 'flm') return { id: 'npu', label: 'NPU' }
  if (b === 'llamacpp') return { id: 'igpu', label: 'iGPU' }
  if (b === 'kokoro' || b === 'moonshine' || b === 'vibevoice') return { id: 'igpu', label: 'iGPU' }
  if (b === 'minimax') return { id: 'remote', label: 'remote' }
  if (b === 'cuda' || b === 'rocm') return { id: 'gpu', label: 'GPU' }
  if (b === 'vulkan' || b === 'metal') return { id: 'igpu', label: 'iGPU' }
  if (b === 'cpu') return { id: 'cpu', label: 'CPU' }
  return { id: 'unknown', label: b || 'unknown' }
}

// Delete confirm
const deletingModel = ref(null)
const deleting      = ref(false)

// ── Loaders ────────────────────────────────────────────────────────────
async function loadModels() {
  loading.value = true
  error.value = null
  try {
    const data = await api('/api/models')
    models.value = Array.isArray(data) ? data : (data?.models ?? [])
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadCatalogue() {
  try {
    const data = await api('/api/models/catalogue')
    pullableCatalogue.value = Array.isArray(data?.pullable) ? data.pullable : []
    upstreamCatalogue.value = Array.isArray(data?.upstream) ? data.upstream : []
  } catch (e) {
    // Catalogue is supplementary; surface a toast but don't block the page.
    toasts.error(`Failed to load catalogue: ${e.message}`)
  }
}

// ── Filtering ──────────────────────────────────────────────────────────
const filteredModels = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return models.value
  return models.value.filter(
    (m) => (m.name ?? m.id ?? '').toLowerCase().includes(q) ||
            (m.architecture ?? '').toLowerCase().includes(q)
  )
})

// ── Slot assignment helpers ────────────────────────────────────────────
function slotsForModel(modelId) {
  return system.slots.filter((s) => s.model === modelId)
}

// ── Pull model ─────────────────────────────────────────────────────────
async function pullCurated(preset) {
  pulling.value = true
  const job = ensureJob(preset.id)
  const label = preset.display_name ?? preset.name ?? preset.id
  try {
    await job.start(preset.id)
    toasts.success(`Pulling "${label}" — progress shown inline`)
    showPull.value = false
    // Make sure the row exists so the user can watch progress. The
    // backend will register the model in its registry; until then we
    // optimistically insert a placeholder row.
    if (!models.value.some((m) => m.id === preset.id)) {
      models.value = [
        ...models.value,
        { id: preset.id, name: label, size_gb: preset.size_gb, _pending: true },
      ]
    }
  } catch (e) {
    toasts.error(e.message)
  } finally {
    pulling.value = false
  }
}

function validatePullHF() {
  const errs = {}
  if (!pullForm.value.hf_url.trim()) errs.hf_url = 'Required'
  else if (!pullForm.value.hf_url.includes('/')) errs.hf_url = 'Enter a HuggingFace repo path (org/model)'
  pullErrors.value = errs
  return Object.keys(errs).length === 0
}

async function submitPullHF() {
  if (!validatePullHF()) return
  pulling.value = true
  const id = pullForm.value.hf_url
  const job = ensureJob(id)
  try {
    await job.start(id, { hf_url: pullForm.value.hf_url, quant: pullForm.value.quant })
    toasts.success('Download started')
    showPull.value = false
    if (!models.value.some((m) => m.id === id)) {
      models.value = [
        ...models.value,
        { id, name: id, _pending: true },
      ]
    }
    pullForm.value = { hf_url: '', name: '', quant: 'Q4_K_M' }
  } catch (e) {
    toasts.error(e.message)
  } finally {
    pulling.value = false
  }
}

async function cancelPull(modelId) {
  const job = jobFor(modelId)
  if (!job) return
  try {
    await job.cancel()
    toasts.success(`Cancelled download for "${modelId}"`)
  } catch (e) {
    toasts.error(e.message)
  }
}

/**
 * On mount, ask the backend whether any of the rows we just loaded
 * have an in-flight pull job. If so, reattach so the SSE progress bar
 * picks up where the user left off when they navigated away. Soft-
 * fails per model so a 404 on one doesn't block the rest.
 */
async function reattachInFlightPulls() {
  for (const m of models.value) {
    if (!m?.id) continue
    const job = ensureJob(m.id)
    await job.reattach(m.id)
    // If the reattach didn't find an in-flight job, drop the entry so we
    // don't leak empty job state into the row template.
    if (!job.inFlight.value && !job.terminal.value) {
      delete pullJobs[m.id]
    }
  }
}

// ── Local-file tab ─────────────────────────────────────────────────────
// Slugify a filename into a registry id. Backend will overwrite if it has
// stronger heuristics, but a sane default avoids "id required" errors.
function slugFromPath(p) {
  if (!p) return ''
  const base = String(p).split('/').pop() || ''
  const stem = base.replace(/\.[^.]+$/, '')
  return stem.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

function slugFromName(name) {
  if (!name) return ''
  return String(name).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}

function toggleArrayMember(arr, v) {
  const i = arr.indexOf(v)
  if (i >= 0) arr.splice(i, 1)
  else arr.push(v)
}

function resetLocalState() {
  localPath.value = ''
  localName.value = ''
  localLicense.value = ''
  localRecursive.value = true
  singleDetected.value = null
  singleEditable.value = { backends: [], capabilities: [] }
  scanRows.value = []
}

// Single-file: run detect via scan/preview to populate suggested fields.
async function detectSingleFile() {
  if (!localPath.value.trim()) {
    toasts.error('Path is required')
    return
  }
  localBusy.value = true
  try {
    const data = await api('/api/models/scan/preview', {
      method: 'POST',
      body: JSON.stringify({ paths: [localPath.value.trim()], recursive: false }),
    })
    const row = (data?.preview ?? [])[0]
    if (!row) {
      toasts.error('No file detected at that path')
      singleDetected.value = null
      return
    }
    singleDetected.value = row
    const kind = row.kind ?? 'unknown'
    const allowedBackends = new Set(backendsForKind(kind))
    const allowedCaps = new Set(capsForKind(kind))
    singleEditable.value = {
      kind,
      backends: (row.suggested_backends ?? []).filter((b) => allowedBackends.has(b)),
      capabilities: (row.suggested_capabilities ?? []).filter((c) => allowedCaps.has(c)),
    }
    if (!localName.value) localName.value = (row.suggested_name?.trim() || slugFromPath(row.path))
  } catch (e) {
    toasts.error(e.message)
  } finally {
    localBusy.value = false
  }
}

async function submitSingleFile() {
  if (!singleDetected.value) {
    await detectSingleFile()
    return
  }
  localBusy.value = true
  try {
    const det = singleDetected.value
    const detectedName = (det.suggested_name || '').trim()
    const idSource = detectedName || slugFromPath(det.path)
    const id = idSource.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || slugFromPath(det.path)
    const body = {
      id,
      name: localName.value || detectedName || id,
      path: det.path,
      license: localLicense.value || 'unknown',
      backends: singleEditable.value.backends,
      capabilities: singleEditable.value.capabilities,
      metadata: det.context_length != null
        ? { discovered: true, source: 'manual', context_length: det.context_length }
        : { discovered: true, source: 'manual' },
    }
    await api('/api/models', { method: 'POST', body: JSON.stringify(body) })
    toasts.success(`Registered "${body.name}"`)
    showPull.value = false
    resetLocalState()
    await loadModels()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    localBusy.value = false
  }
}

// Scan-directory: preview a tree, render editable rows.
async function previewScan() {
  if (!localPath.value.trim()) {
    toasts.error('Path is required')
    return
  }
  localBusy.value = true
  try {
    const data = await api('/api/models/scan/preview', {
      method: 'POST',
      body: JSON.stringify({ paths: [localPath.value.trim()], recursive: localRecursive.value }),
    })
    const rows = (data?.preview ?? []).map((r) => {
      const detected = (r.suggested_name || '').trim()
      const slug = slugFromPath(r.path)
      const kind = r.kind ?? 'unknown'
      // Constrain detected backends/caps to the kind's allowed sets so
      // upstream noise (e.g., GGUF with stray asr filename token) doesn't
      // leak into the commit body.
      const allowedBackends = new Set(backendsForKind(kind))
      const allowedCaps = new Set(capsForKind(kind))
      return {
        path: r.path,
        size_bytes: r.size_bytes ?? 0,
        name: detected || slug,
        kind,
        backends: (r.suggested_backends ?? []).filter((b) => allowedBackends.has(b)),
        capabilities: (r.suggested_capabilities ?? []).filter((c) => allowedCaps.has(c)),
        context_length: r.context_length,
        confidence: r.confidence,
        // Pre-select GGUF llama models the parser was confident about.
        // Unknown / heuristic-only rows are opt-in.
        selected: r.confidence === 'high' && kind === 'llama',
      }
    })
    scanRows.value = rows
    if (rows.length === 0) toasts.error('No models found in that path')
  } catch (e) {
    toasts.error(e.message)
  } finally {
    localBusy.value = false
  }
}

async function commitScan() {
  const picked = scanRows.value.filter((r) => r.selected)
  if (picked.length === 0) {
    toasts.error('Select at least one row to register')
    return
  }
  localBusy.value = true
  try {
    const body = {
      rows: picked.map((r) => ({
        path: r.path,
        id: slugFromName(r.name) || slugFromPath(r.path),
        name: r.name,
        backends: r.backends,
        capabilities: r.capabilities,
      })),
    }
    const data = await api('/api/models/scan', { method: 'POST', body: JSON.stringify(body) })
    const n = (data?.added ?? []).length
    toasts.success(`Registered ${n} model(s)`)
    showPull.value = false
    resetLocalState()
    await loadModels()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    localBusy.value = false
  }
}

const scanSelectedCount = computed(() => scanRows.value.filter((r) => r.selected).length)
const scanAllSelected = computed(() => scanRows.value.length > 0 && scanRows.value.every((r) => r.selected))
const scanNoneSelected = computed(() => scanRows.value.length > 0 && scanRows.value.every((r) => !r.selected))

function toggleScanAll() {
  const target = !scanAllSelected.value
  scanRows.value.forEach((r) => { r.selected = target })
}

// ── Edit metadata ──────────────────────────────────────────────────────
function inferKindFromModel(model) {
  const bks = new Set(model.backends ?? [])
  if (bks.has('kokoro')) return 'kokoro'
  if (bks.has('moonshine')) return 'moonshine'
  if (bks.has('flm')) return 'flm'
  if (bks.has('vulkan') || bks.has('rocm') || bks.has('cuda') || bks.has('cpu')) return 'llama'
  return 'unknown'
}

function openEdit(model) {
  editingModel.value = model
  const d = model.defaults ?? {}
  const kind = inferKindFromModel(model)
  const allowedBackends = new Set(backendsForKind(kind))
  const allowedCaps = new Set(capsForKind(kind))
  editForm.value = {
    name: model.name ?? model.id ?? '',
    kind,
    capabilities: (model.capabilities ?? []).filter((c) => allowedCaps.has(c)),
    backends: (model.backends ?? []).filter((b) => allowedBackends.has(b)),
    defaults: {
      context_size:    d.context_size ?? null,
      n_gpu_layers:    d.n_gpu_layers ?? null,
      rope_freq_base:  d.rope_freq_base ?? null,
      extra_args:      d.extra_args ?? '',
    },
  }
  // Snapshot the model so "Reset to detected" can restore individual
  // default fields without a re-detect round-trip.
  editOriginal.value = JSON.parse(JSON.stringify({
    capabilities: model.capabilities ?? [],
    backends: model.backends ?? [],
    defaults: d,
  }))
  editAdvOpen.value = false
  editDetected.value = null
}

function onEditKindChange() {
  // Changing kind resets backends + capabilities to that kind's defaults
  // so we never end up with kokoro+chat or vulkan+tts combos that the
  // backend would reject (or worse, silently accept).
  const k = editForm.value.kind
  editForm.value.backends = [...backendsForKind(k)]
  editForm.value.capabilities = capsForKind(k).slice(0, 1) // first cap as a sane default
}

function resetDefaultField(key) {
  const orig = editOriginal.value?.defaults ?? {}
  editForm.value.defaults[key] = orig[key] ?? (key === 'extra_args' ? '' : null)
}

async function reDetectModel() {
  if (!editingModel.value?.path) {
    toasts.error('Model has no path to detect from')
    return
  }
  editReDetecting.value = true
  try {
    const data = await api('/api/models/scan/preview', {
      method: 'POST',
      body: JSON.stringify({ paths: [editingModel.value.path], recursive: false }),
    })
    const row = (data?.preview ?? [])[0]
    if (!row) {
      toasts.error('Detection returned no results')
      return
    }
    editDetected.value = row
  } catch (e) {
    toasts.error(e.message)
  } finally {
    editReDetecting.value = false
  }
}

function applyDetected() {
  const d = editDetected.value
  if (!d) return
  editForm.value.capabilities = [...(d.suggested_capabilities ?? [])]
  editForm.value.backends     = [...(d.suggested_backends ?? [])]
  editDetected.value = null
  toasts.success('Detected values applied')
}

async function submitEdit() {
  if (!editingModel.value) return
  editSubmitting.value = true
  try {
    // Strip empty extra_args to null so the backend stores absence cleanly.
    const defaults = { ...editForm.value.defaults }
    if (defaults.extra_args === '' || defaults.extra_args == null) defaults.extra_args = null
    // Drop the whole defaults block if every field is null — keeps the
    // PUT body small and the registry TOML tidy.
    const allEmpty = Object.values(defaults).every((v) => v == null)
    const body = {
      name: editForm.value.name,
      capabilities: editForm.value.capabilities,
      backends: editForm.value.backends,
      defaults: allEmpty ? null : defaults,
    }
    await api(`/api/models/${encodeURIComponent(editingModel.value.id)}`, {
      method: 'PUT',
      body: JSON.stringify(body),
    })
    toasts.success('Model updated')
    editingModel.value = null
    await loadModels()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    editSubmitting.value = false
  }
}

// ── Delete model ───────────────────────────────────────────────────────
const deletingModelSlots = computed(() => {
  if (!deletingModel.value) return []
  return slotsForModel(deletingModel.value.id)
})

async function confirmDelete() {
  if (!deletingModel.value) return
  deleting.value = true
  const name = deletingModel.value.name ?? deletingModel.value.id
  try {
    // Backend defaults to force_cascade=true — unloads referencing slots
    // and clears their [model].default. The response carries the list of
    // affected slot names so we can surface a precise toast.
    const res = await api(`/api/models/${encodeURIComponent(deletingModel.value.id)}`, {
      method: 'DELETE',
    })
    const cleared = (res?.affected_slots ?? []).length
    toasts.success(
      cleared > 0
        ? `Deleted ${name}; ${cleared} slot(s) cleared`
        : `Deleted "${name}"`,
    )
    // Optimistic prune — full refresh below catches up state.
    models.value = models.value.filter((m) => m.id !== deletingModel.value.id)
    deletingModel.value = null
    await loadModels()
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    deleting.value = false
  }
}

// ── Assign to slot ─────────────────────────────────────────────────────
async function assignToSlot(model, slotName) {
  if (!slotName) return
  try {
    await api(`/api/slots/${slotName}/load`, {
      method: 'POST',
      body: JSON.stringify({ model: model.id }),
    })
    toasts.success(`Loading "${model.name ?? model.id}" into "${slotName}"`)
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  }
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────
function handleKey(e) {
  if ((e.target instanceof HTMLInputElement) || (e.target instanceof HTMLTextAreaElement)) return
  if (e.key === 'n') { e.preventDefault(); showPull.value = true }
  else if (e.key === '/') { e.preventDefault(); searchEl.value?.focus() }
  else if (e.key === 'Escape') {
    if (showPull.value) { showPull.value = false; resetLocalState() }
    else if (editingModel.value) editingModel.value = null
  }
}

onMounted(async () => {
  window.addEventListener('keydown', handleKey)
  await loadModels()
  // Catalogue loads in parallel with reattach — both are independent of
  // each other and of the installed-models list.
  loadCatalogue()
  // Reattach any in-flight pull jobs so the user sees live progress
  // even if they navigated away mid-download (Team I gap #3).
  await reattachInFlightPulls()
})
onUnmounted(() => {
  window.removeEventListener('keydown', handleKey)
  // Active EventSource cleanup is handled by each usePullJob's onUnmounted.
})

// ── Formatting ─────────────────────────────────────────────────────────
function fmtSize(model) {
  if (model.size_gb != null) return `${Number(model.size_gb).toFixed(1)} GB`
  if (model.size_bytes != null) return `${(model.size_bytes / 1e9).toFixed(1)} GB`
  return '—'
}

const QUANTS = ['Q4_K_M', 'Q5_K_M', 'Q8_0', 'F16', 'BF16']
</script>

<template>
  <div class="models-page">
    <PageHeader eyebrow="Registry" title="Models" subtitle="Local model registry and downloads">
      <template #actions>
        <div class="search-wrap">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2" class="search-icon" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M21 21l-4.35-4.35M10.5 18a7.5 7.5 0 100-15 7.5 7.5 0 000 15z"/>
          </svg>
          <input
            ref="searchEl"
            v-model="search"
            class="search-input"
            type="search"
            placeholder="Search models… (/)"
            aria-label="Search models"
          />
        </div>
        <button class="btn-primary" type="button" @click="showPull = true">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>
          </svg>
          Add model
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <!-- ── Error ──────────────────────────────────────────── -->
      <div v-if="error" class="error-banner" role="alert">
        <span>{{ error }}</span>
        <button type="button" class="btn-link" @click="loadModels">Retry</button>
      </div>

      <!-- ── Loading ────────────────────────────────────────── -->
      <template v-if="loading">
        <Card v-for="i in 5" :key="i"><LoadingSkeleton :lines="2" /></Card>
      </template>

      <!-- ── Empty ──────────────────────────────────────────── -->
      <template v-else-if="models.length === 0">
        <Card :padded="false">
          <EmptyState
            icon="M20 7l-8-4-8 4m16 0l-8 4m8-4v10l-8 4m0-10L4 7m8 4v10M4 7v10l8 4"
            title="No models in the registry yet."
            description="Pull from Hugging Face, scan a directory you already have on disk (NFS mounts are fine), or pick from the curated catalog."
            cta-label="Add a model"
            @cta="showPull = true"
          />
        </Card>
      </template>

      <!-- ── Models table ──────────────────────────────────── -->
      <template v-else>
        <div v-if="filteredModels.length === 0" class="no-results">
          No models match "{{ search }}"
        </div>

        <Card :padded="false" v-else>
          <div class="table-wrap" role="region" aria-label="Models table" tabindex="0">
            <table class="models-table">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Size</th>
                  <th scope="col">Quant</th>
                  <th scope="col">Architecture</th>
                  <th scope="col">Used by</th>
                  <th scope="col">Actions</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="model in filteredModels" :key="model.id">
                  <td>
                    <div class="model-name-cell">
                      <span class="model-name">{{ model.name ?? model.id }}</span>
                      <span v-if="model.name && model.id !== model.name" class="model-id">{{ model.id }}</span>
                      <!-- Capability + backend badges (read-only). Click does
                           nothing — pure visual classification. -->
                      <div v-if="(model.capabilities && model.capabilities.length) || (model.backends && model.backends.length)" class="model-badges">
                        <span v-for="c in (model.capabilities ?? [])" :key="`cap-${c}`" class="badge badge-cap">{{ c }}</span>
                        <span v-for="b in (model.backends ?? [])" :key="`bk-${b}`" class="badge badge-bk">{{ b }}</span>
                      </div>
                      <!-- Pending-approval chip(s). Rendered when a gated MCP
                           tool (model_pull / model_delete) is queued for
                           this model. Links to /agent?tab=inbox. -->
                      <div
                        v-if="agent.pendingForResource('model', model.id).length"
                        class="model-pending"
                      >
                        <AgentPendingChip
                          v-for="p in agent.pendingForResource('model', model.id)"
                          :key="p.id"
                          :entry="p"
                        />
                      </div>
                      <!-- Inline pull progress: rendered when a row has an
                           active or recently-terminated pull job. SSE-driven;
                           updates instantly as bytes land. -->
                      <div
                        v-if="jobFor(model.id) && (jobFor(model.id).inFlight.value || jobFor(model.id).state.value === 'failed')"
                        class="row-pull"
                        role="status"
                        :aria-label="`Downloading ${model.name ?? model.id}`"
                      >
                        <div class="row-pull-bar" :aria-valuenow="jobFor(model.id).pct.value ?? 0" aria-valuemin="0" aria-valuemax="100" role="progressbar">
                          <div class="row-pull-fill" :style="{ width: (jobFor(model.id).pct.value ?? 0) + '%' }" />
                        </div>
                        <div class="row-pull-meta mono">
                          <span v-if="jobFor(model.id).state.value === 'failed'" class="row-pull-err">
                            <strong>{{ jobFor(model.id).error.value?.code }}</strong>:
                            {{ jobFor(model.id).error.value?.message }}
                          </span>
                          <template v-else>
                            <span>{{ jobFor(model.id).pct.value ?? 0 }}%</span>
                            <span v-if="jobFor(model.id).total.value">
                              · {{ fmtBytes(jobFor(model.id).downloaded.value) }} / {{ fmtBytes(jobFor(model.id).total.value) }}
                            </span>
                            <span v-if="jobFor(model.id).speedBps.value">· {{ fmtSpeed(jobFor(model.id).speedBps.value) }}</span>
                            <span v-if="jobFor(model.id).etaS.value">· {{ fmtEta(jobFor(model.id).etaS.value) }}</span>
                            <button
                              v-if="jobFor(model.id).inFlight.value"
                              type="button"
                              class="row-pull-cancel"
                              @click="cancelPull(model.id)"
                              :aria-label="`Cancel download for ${model.name ?? model.id}`"
                            >Cancel</button>
                          </template>
                        </div>
                      </div>
                    </div>
                  </td>
                  <td class="mono-cell">{{ fmtSize(model) }}</td>
                  <td class="mono-cell">{{ model.quant ?? '—' }}</td>
                  <td class="mono-cell">{{ model.architecture ?? '—' }}</td>
                  <td>
                    <div class="slots-cell">
                      <template v-if="slotsForModel(model.id).length > 0">
                        <span
                          v-for="s in slotsForModel(model.id)"
                          :key="s.name"
                          class="slot-badge"
                          :class="s.status === 'running' ? 'badge-running' : ''"
                        >{{ s.name }}</span>
                      </template>
                      <span v-else class="text-faint">—</span>
                    </div>
                  </td>
                  <td>
                    <div class="row-actions">
                      <!-- Assign to slot -->
                      <select
                        class="assign-select"
                        @change="(e) => { assignToSlot(model, e.target.value); e.target.value = '' }"
                        :aria-label="`Assign ${model.name ?? model.id} to slot`"
                      >
                        <option value="">Assign…</option>
                        <option v-for="s in system.slots" :key="s.name" :value="s.name">{{ s.name }}</option>
                      </select>

                      <!-- Edit name -->
                      <button
                        class="act-btn"
                        type="button"
                        @click="openEdit(model)"
                        :aria-label="`Edit ${model.name ?? model.id}`"
                        title="Edit metadata"
                      >
                        <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                        </svg>
                      </button>

                      <!-- Delete -->
                      <button
                        class="act-btn act-danger"
                        type="button"
                        @click="deletingModel = model"
                        :aria-label="`Delete ${model.name ?? model.id}`"
                        title="Delete model"
                      >
                        <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
                          <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                        </svg>
                      </button>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </Card>
      </template>

      <!-- ── Catalogue ──────────────────────────────────────────────
           Discovery surface: pullable curated models. The upstream-
           routed catalogue subsection is hidden until upstream routing
           is reworked (see PLAN.md follow-up); the endpoint still
           returns both lists so it can come back later. -->
      <section class="catalogue-section" aria-labelledby="catalogue-title">
        <header class="catalogue-header">
          <h2 id="catalogue-title" class="catalogue-title">Catalogue</h2>
          <span class="catalogue-counts mono">{{ pullableCatalogue.length }} pullable</span>
        </header>

        <!-- Pullable -->
        <div class="catalogue-group" aria-labelledby="catalogue-pullable-title">
          <h3 id="catalogue-pullable-title" class="catalogue-subtitle">Pullable</h3>
          <Card :padded="false" v-if="pullableCatalogue.length > 0">
            <ul class="cat-list">
              <li
                v-for="entry in pullableCatalogue"
                :key="entry.id"
                class="cat-row"
              >
                <div class="cat-row-main">
                  <div class="cat-row-title">
                    <span class="cat-name">{{ entry.display_name }}</span>
                    <span class="cat-id mono">{{ entry.id }}</span>
                  </div>
                  <p class="cat-desc">{{ entry.description }}</p>
                </div>
                <div class="cat-row-meta">
                  <span class="mono-chip">{{ entry.size_gb }} GB</span>
                  <span class="mono-chip">{{ entry.license }}</span>
                  <span class="cap-chip">{{ entry.capability }}</span>
                </div>
                <button
                  class="btn-sm-accent"
                  type="button"
                  :disabled="pulling"
                  @click="pullCurated(entry)"
                >
                  <span v-if="jobFor(entry.id)?.inFlight.value" class="spinner" aria-hidden="true" />
                  {{ jobFor(entry.id)?.inFlight.value ? 'Pulling…' : 'Pull' }}
                </button>
              </li>
            </ul>
          </Card>
          <Card v-else><LoadingSkeleton :lines="2" /></Card>
        </div>
      </section>
    </div>

    <!-- ── Pull model modal ──────────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showPull" class="modal-overlay" @click.self="showPull = false; resetLocalState()">
          <div class="modal-box modal-wide" role="dialog" aria-modal="true" aria-labelledby="pull-title">
            <div class="modal-header">
              <h2 id="pull-title" class="modal-title">Add model</h2>
              <button class="modal-close" type="button" @click="showPull = false; resetLocalState()" aria-label="Close">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>

            <!-- Tab bar -->
            <div class="pull-tabs" role="tablist">
              <button role="tab" :aria-selected="pullTab === 'curated'" class="pull-tab" :class="{ active: pullTab === 'curated' }" @click="pullTab = 'curated'">Curated</button>
              <button role="tab" :aria-selected="pullTab === 'hf'" class="pull-tab" :class="{ active: pullTab === 'hf' }" @click="pullTab = 'hf'">HuggingFace</button>
              <button role="tab" :aria-selected="pullTab === 'local'" class="pull-tab" :class="{ active: pullTab === 'local' }" @click="pullTab = 'local'">Local file</button>
            </div>

            <div class="modal-body">
              <!-- Curated tab -->
              <div v-if="pullTab === 'curated'" class="curated-list" role="tabpanel">
                <div
                  v-for="preset in pullableCatalogue"
                  :key="preset.id"
                  class="curated-row"
                >
                  <div class="curated-info">
                    <span class="curated-name">{{ preset.display_name }}</span>
                    <span class="curated-desc">{{ preset.description }}</span>
                  </div>
                  <div class="curated-meta">
                    <span class="mono-chip">{{ preset.size_gb }} GB</span>
                    <span class="mono-chip">{{ preset.license }}</span>
                  </div>
                  <button
                    class="btn-sm-accent"
                    type="button"
                    :disabled="pulling"
                    @click="pullCurated(preset)"
                  >
                    <span v-if="jobFor(preset.id)?.inFlight.value" class="spinner" aria-hidden="true" />
                    {{ jobFor(preset.id)?.inFlight.value ? 'Pulling…' : 'Pull' }}
                  </button>
                </div>
                <div v-if="pullableCatalogue.length === 0" class="curated-empty">
                  Loading catalogue…
                </div>
              </div>

              <!-- HuggingFace tab -->
              <div v-if="pullTab === 'hf'" role="tabpanel">
                <div class="field">
                  <label class="field-label" for="hf-url">HuggingFace repo <span class="req">*</span></label>
                  <input
                    id="hf-url"
                    v-model="pullForm.hf_url"
                    class="field-input"
                    :class="{ 'field-error': pullErrors.hf_url }"
                    placeholder="org/model-name-GGUF"
                    autocomplete="off"
                  />
                  <p v-if="pullErrors.hf_url" class="field-err">{{ pullErrors.hf_url }}</p>
                  <p class="field-hint">e.g. Qwen/Qwen3-4B-GGUF or bartowski/Meta-Llama-3-8B-Instruct-GGUF</p>
                </div>
                <div class="field">
                  <label class="field-label" for="quant">Quantization</label>
                  <select id="quant" v-model="pullForm.quant" class="field-input">
                    <option v-for="q in QUANTS" :key="q" :value="q">{{ q }}</option>
                  </select>
                </div>
              </div>

              <!-- Local-file tab — two sub-actions toggled inline. -->
              <div v-if="pullTab === 'local'" role="tabpanel">
                <div class="local-mode-toggle" role="tablist" aria-label="Local-file mode">
                  <button
                    role="tab"
                    :aria-selected="localMode === 'single'"
                    class="local-mode-btn"
                    :class="{ active: localMode === 'single' }"
                    @click="localMode = 'single'; singleDetected = null"
                  >Register single file</button>
                  <button
                    role="tab"
                    :aria-selected="localMode === 'scan'"
                    class="local-mode-btn"
                    :class="{ active: localMode === 'scan' }"
                    @click="localMode = 'scan'; scanRows = []"
                  >Scan directory</button>
                </div>

                <!-- ── Single-file ──────────────────────────────────── -->
                <div v-if="localMode === 'single'" class="local-pane">
                  <div class="field">
                    <label class="field-label" for="local-path-single">Absolute path <span class="req">*</span></label>
                    <input
                      id="local-path-single"
                      v-model="localPath"
                      class="field-input"
                      placeholder="/mnt/ai-models/llama-3.1-8b.Q4_K_M.gguf"
                      autocomplete="off"
                    />
                    <p class="field-hint">Must be readable by the hal0 service user.</p>
                  </div>
                  <div class="field">
                    <label class="field-label" for="local-name">Display name (optional)</label>
                    <input id="local-name" v-model="localName" class="field-input" placeholder="Defaults to filename" />
                  </div>
                  <div class="field">
                    <label class="field-label" for="local-license">License (optional)</label>
                    <input id="local-license" v-model="localLicense" class="field-input" placeholder="Apache-2.0" />
                  </div>

                  <!-- Detection result (after pressing Detect) -->
                  <div v-if="singleDetected" class="detect-block">
                    <div class="detect-head">
                      <span class="detect-label">Detected</span>
                      <span class="kind-badge" :class="`kind-${singleEditable.kind}`">{{ KIND_LABEL[singleEditable.kind] }}</span>
                      <span class="mono-chip">conf {{ singleDetected.confidence ?? '—' }}</span>
                      <span v-if="singleDetected.context_length" class="mono-chip">ctx {{ singleDetected.context_length }}</span>
                    </div>
                    <div class="field">
                      <label class="field-label">Capabilities</label>
                      <div class="check-row">
                        <label v-for="c in capsForKind(singleEditable.kind)" :key="`scap-${c}`" class="check-pill">
                          <input
                            type="checkbox"
                            :checked="singleEditable.capabilities.includes(c)"
                            @change="toggleArrayMember(singleEditable.capabilities, c)"
                          />
                          <span>{{ c }}</span>
                        </label>
                      </div>
                    </div>
                    <div class="field">
                      <label class="field-label">Backends</label>
                      <div class="check-row">
                        <label v-for="b in backendsForKind(singleEditable.kind)" :key="`sbk-${b}`" class="check-pill">
                          <input
                            type="checkbox"
                            :checked="singleEditable.backends.includes(b)"
                            @change="toggleArrayMember(singleEditable.backends, b)"
                          />
                          <span>{{ b }}</span>
                        </label>
                      </div>
                    </div>
                  </div>
                </div>

                <!-- ── Scan-directory ──────────────────────────────── -->
                <div v-if="localMode === 'scan'" class="local-pane">
                  <div class="field">
                    <label class="field-label" for="local-path-scan">Directory path <span class="req">*</span></label>
                    <input
                      id="local-path-scan"
                      v-model="localPath"
                      class="field-input"
                      placeholder="/mnt/ai-models"
                      autocomplete="off"
                    />
                  </div>
                  <label class="check-inline">
                    <input type="checkbox" v-model="localRecursive" />
                    <span>Recursive</span>
                  </label>

                  <div v-if="scanRows.length > 0" class="scan-preview">
                    <p class="field-hint">{{ scanRows.length }} candidate(s) — edit per row, then commit.</p>
                    <div class="scan-table-wrap">
                      <table class="scan-table">
                        <thead>
                          <tr>
                            <th class="col-pick">
                              <input
                                type="checkbox"
                                :checked="scanAllSelected"
                                :indeterminate.prop="!scanAllSelected && !scanNoneSelected"
                                @change="toggleScanAll"
                                :aria-label="scanAllSelected ? 'Deselect all' : 'Select all'"
                              />
                            </th>
                            <th class="col-name">Name</th>
                            <th class="col-kind">Kind</th>
                            <th class="col-tags">Capabilities · Backends</th>
                            <th class="col-path">Path</th>
                            <th class="col-ctx">Ctx</th>
                            <th class="col-conf">Conf</th>
                          </tr>
                        </thead>
                        <tbody>
                          <tr v-for="(row, idx) in scanRows" :key="row.path" :class="{ 'row-unpicked': !row.selected }">
                            <td class="col-pick">
                              <input
                                type="checkbox"
                                v-model="scanRows[idx].selected"
                                :aria-label="`Include ${row.name}`"
                              />
                            </td>
                            <td class="col-name">
                              <input v-model="scanRows[idx].name" class="scan-input scan-name" placeholder="Name" />
                              <div class="scan-id-hint mono">id: {{ slugFromName(row.name) || slugFromPath(row.path) }}</div>
                            </td>
                            <td class="col-kind">
                              <span class="kind-badge" :class="`kind-${row.kind}`">{{ KIND_LABEL[row.kind] }}</span>
                            </td>
                            <td class="col-tags">
                              <div class="tag-row">
                                <span v-for="c in row.capabilities" :key="`r${idx}c${c}`" class="tag tag-cap">{{ c }}</span>
                                <span v-for="b in row.backends" :key="`r${idx}b${b}`" class="tag tag-bk">{{ b }}</span>
                                <span v-if="row.capabilities.length === 0 && row.backends.length === 0" class="tag tag-empty">—</span>
                              </div>
                            </td>
                            <td class="mono-cell scan-path col-path" :title="row.path">{{ row.path }}</td>
                            <td class="mono-cell">{{ row.context_length ?? '—' }}</td>
                            <td class="mono-cell">{{ row.confidence ?? '—' }}</td>
                          </tr>
                        </tbody>
                      </table>
                      <p class="field-hint scan-foot">Capabilities + backends are pre-detected and locked here — open each model's Edit panel after registering to fine-tune.</p>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div class="modal-footer" v-if="pullTab === 'hf'">
              <button class="btn-ghost" type="button" @click="showPull = false" :disabled="pulling">Cancel</button>
              <button class="btn-primary" type="button" @click="submitPullHF" :disabled="pulling">
                <span v-if="pulling" class="spinner" aria-hidden="true" />
                {{ pulling ? 'Pulling…' : 'Pull model' }}
              </button>
            </div>

            <!-- Local-file footer: action depends on sub-mode + state. -->
            <div class="modal-footer" v-if="pullTab === 'local'">
              <button class="btn-ghost" type="button" @click="showPull = false; resetLocalState()" :disabled="localBusy">Cancel</button>
              <template v-if="localMode === 'single'">
                <button
                  v-if="!singleDetected"
                  class="btn-primary"
                  type="button"
                  @click="detectSingleFile"
                  :disabled="localBusy"
                >
                  <span v-if="localBusy" class="spinner" aria-hidden="true" />
                  Detect
                </button>
                <button
                  v-else
                  class="btn-primary"
                  type="button"
                  @click="submitSingleFile"
                  :disabled="localBusy"
                >
                  <span v-if="localBusy" class="spinner" aria-hidden="true" />
                  Register
                </button>
              </template>
              <template v-else>
                <button
                  v-if="scanRows.length === 0"
                  class="btn-primary"
                  type="button"
                  @click="previewScan"
                  :disabled="localBusy"
                >
                  <span v-if="localBusy" class="spinner" aria-hidden="true" />
                  Preview
                </button>
                <button
                  v-else
                  class="btn-primary"
                  type="button"
                  @click="commitScan"
                  :disabled="localBusy || scanSelectedCount === 0"
                >
                  <span v-if="localBusy" class="spinner" aria-hidden="true" />
                  Register {{ scanSelectedCount }} of {{ scanRows.length }}
                </button>
              </template>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Edit model modal ──────────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="editingModel" class="modal-overlay" @click.self="editingModel = null">
          <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="edit-model-title">
            <div class="modal-header">
              <h2 id="edit-model-title" class="modal-title">Edit model</h2>
              <button class="modal-close" type="button" @click="editingModel = null" aria-label="Close">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>
            <div class="modal-body">
              <div class="field">
                <label class="field-label" for="edit-model-name">Display name</label>
                <input id="edit-model-name" v-model="editForm.name" class="field-input" placeholder="Human-readable name" />
              </div>
              <p class="field-hint mono-text">ID: {{ editingModel.id }}</p>

              <div class="field">
                <label class="field-label" for="edit-model-kind">Kind</label>
                <select id="edit-model-kind" v-model="editForm.kind" class="field-input" @change="onEditKindChange">
                  <option v-for="k in KIND_ORDER" :key="k" :value="k">{{ KIND_LABEL[k] }}</option>
                </select>
                <p class="field-hint">Picks the runtime family. Capabilities + backends below are filtered to what this kind supports.</p>
              </div>

              <div class="field">
                <label class="field-label">Capabilities</label>
                <div class="check-row">
                  <label v-for="c in capsForKind(editForm.kind)" :key="`ecap-${c}`" class="check-pill">
                    <input
                      type="checkbox"
                      :checked="editForm.capabilities.includes(c)"
                      @change="toggleArrayMember(editForm.capabilities, c)"
                    />
                    <span>{{ c }}</span>
                  </label>
                </div>
              </div>

              <div class="field">
                <label class="field-label">Backends</label>
                <div class="check-row">
                  <label v-for="b in backendsForKind(editForm.kind)" :key="`ebk-${b}`" class="check-pill">
                    <input
                      type="checkbox"
                      :checked="editForm.backends.includes(b)"
                      @change="toggleArrayMember(editForm.backends, b)"
                    />
                    <span>{{ b }}</span>
                  </label>
                </div>
              </div>

              <!-- Re-detect from file: round-trips through /scan/preview, then
                   diffs against current editable values. Apply overwrites. -->
              <div class="redetect-block" v-if="editingModel.path">
                <button
                  type="button"
                  class="btn-ghost btn-xs"
                  @click="reDetectModel"
                  :disabled="editReDetecting"
                >
                  <span v-if="editReDetecting" class="spinner" aria-hidden="true" />
                  Re-detect from file
                </button>
                <div v-if="editDetected" class="redetect-diff">
                  <p class="field-hint">
                    Detected backends: <span class="mono-text">{{ (editDetected.suggested_backends ?? []).join(', ') || '—' }}</span><br/>
                    Detected capabilities: <span class="mono-text">{{ (editDetected.suggested_capabilities ?? []).join(', ') || '—' }}</span><br/>
                    Detected ctx: <span class="mono-text">{{ editDetected.context_length ?? '—' }}</span>
                  </p>
                  <button type="button" class="btn-primary btn-xs" @click="applyDetected">Apply detected</button>
                </div>
              </div>

              <!-- Advanced disclosure: per-field defaults + Reset-to-detected. -->
              <details class="adv-block" :open="editAdvOpen" @toggle="editAdvOpen = $event.target.open">
                <summary class="adv-summary">Advanced</summary>
                <div class="field">
                  <div class="field-label-row">
                    <label class="field-label" for="edit-ctx">context_size</label>
                    <button type="button" class="btn-link btn-xs" @click="resetDefaultField('context_size')">Reset to detected</button>
                  </div>
                  <input
                    id="edit-ctx"
                    v-model.number="editForm.defaults.context_size"
                    type="number"
                    min="0"
                    class="field-input"
                    placeholder="Inherit slot default"
                  />
                </div>
                <div class="field">
                  <div class="field-label-row">
                    <label class="field-label" for="edit-ngl">n_gpu_layers</label>
                    <button type="button" class="btn-link btn-xs" @click="resetDefaultField('n_gpu_layers')">Reset to detected</button>
                  </div>
                  <input
                    id="edit-ngl"
                    v-model.number="editForm.defaults.n_gpu_layers"
                    type="number"
                    class="field-input"
                    placeholder="-1 = all on GPU, 0 = CPU"
                  />
                </div>
                <div class="field">
                  <div class="field-label-row">
                    <label class="field-label" for="edit-rope">rope_freq_base</label>
                    <button type="button" class="btn-link btn-xs" @click="resetDefaultField('rope_freq_base')">Reset to detected</button>
                  </div>
                  <input
                    id="edit-rope"
                    v-model.number="editForm.defaults.rope_freq_base"
                    type="number"
                    step="any"
                    class="field-input"
                    placeholder="e.g. 10000"
                  />
                </div>
                <div class="field">
                  <div class="field-label-row">
                    <label class="field-label" for="edit-extra">extra_args</label>
                    <button type="button" class="btn-link btn-xs" @click="resetDefaultField('extra_args')">Reset to detected</button>
                  </div>
                  <textarea
                    id="edit-extra"
                    v-model="editForm.defaults.extra_args"
                    class="field-input"
                    rows="2"
                    placeholder="--threads 4 --batch-size 512"
                  ></textarea>
                  <p class="field-hint">Merged with slot extra_args at launch (slot wins on conflict).</p>
                </div>
              </details>
            </div>
            <div class="modal-footer">
              <button class="btn-ghost" type="button" @click="editingModel = null" :disabled="editSubmitting">Cancel</button>
              <button class="btn-primary" type="button" @click="submitEdit" :disabled="editSubmitting">
                <span v-if="editSubmitting" class="spinner" aria-hidden="true" />
                {{ editSubmitting ? 'Saving…' : 'Save' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Delete confirm ──────────────────────────────────────────
         Backend cascade defaults to true: unload referencing slots,
         clear their [model].default, drop registry row. Disk files are
         NEVER touched — call it out explicitly so operators don't
         expect a cleanup. -->
    <ConfirmDialog
      :open="!!deletingModel"
      :title="`Delete &quot;${deletingModel?.name ?? deletingModel?.id ?? ''}&quot;?`"
      :message="deletingModelSlots.length > 0
        ? `This will unload it from ${deletingModelSlots.length} slot(s): ${deletingModelSlots.map((s) => '\`' + s.name + '\`').join(', ')} and clear the model from their config. Files on disk untouched.`
        : 'Files on disk untouched.'"
      danger
      confirm-label="Delete model"
      :impact="deletingModelSlots.length > 1 ? 2 : 1"
      :confirm-text="deletingModelSlots.length > 1 ? (deletingModel?.name ?? deletingModel?.id ?? '') : ''"
      :loading="deleting"
      @update:open="(v) => { if (!v) deletingModel = null }"
      @confirm="confirmDelete"
      @cancel="deletingModel = null"
    />
  </div>
</template>

<style scoped>
.models-page { display: flex; flex-direction: column; min-height: 100%; }
.page-body   { padding: 20px 24px; display: flex; flex-direction: column; gap: 12px; }

/* Search */
.search-wrap { position: relative; }
.search-icon { position: absolute; left: 9px; top: 50%; transform: translateY(-50%); color: var(--color-fg-faint); pointer-events: none; }
.search-input {
  padding: 6px 10px 6px 30px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 12.5px;
  outline: none;
  width: 220px;
  transition: border-color 0.1s, width 0.2s;
}
.search-input:focus { border-color: var(--color-border-hi); width: 280px; }
.search-input::placeholder { color: var(--color-fg-faint); }

/* Error */
.error-banner {
  display: flex; align-items: center; gap: 12px; justify-content: space-between;
  padding: 10px 16px;
  background: color-mix(in oklch, var(--color-danger) 10%, var(--color-surface));
  border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent);
  border-radius: var(--radius-lg);
  color: var(--color-danger);
  font-size: 13px;
}

.no-results { padding: 32px; text-align: center; color: var(--color-fg-faint); font-size: 13px; }

/* ── Table ────────────────────────────────────────────────────── */
.table-wrap { overflow-x: auto; }
.models-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.models-table thead { background: var(--hal0-bg-sunken); }
.models-table th {
  padding: 10px 16px;
  text-align: left;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--hal0-accent);
  font-family: var(--font-mono);
  border-bottom: 1px solid var(--color-border);
  font-weight: 500;
  white-space: nowrap;
}
.models-table td {
  padding: 11px 16px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  vertical-align: middle;
}
.models-table tbody tr:last-child td { border-bottom: none; }
.models-table tbody tr:hover td { background: var(--color-surface-2); }

.model-name-cell { display: flex; flex-direction: column; gap: 2px; }
.model-name { font-family: var(--font-mono); font-weight: 600; color: var(--color-fg); }
.model-id   { font-family: var(--font-mono); font-size: 10.5px; color: var(--color-fg-faint); }
.mono-cell  { font-family: var(--font-mono); font-size: 11.5px; font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }

/* Inline pull progress (Team I gap #3) */
.row-pull { display: flex; flex-direction: column; gap: 4px; margin-top: 4px; }
.row-pull-bar {
  position: relative;
  width: 100%;
  max-width: 320px;
  height: 4px;
  background: var(--color-surface-3);
  border-radius: 4px;
  overflow: hidden;
}
.row-pull-fill {
  height: 100%;
  background: var(--color-accent);
  border-radius: 4px;
  transition: width 0.3s ease;
}
.row-pull-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: var(--color-fg-faint);
  flex-wrap: wrap;
}
.row-pull-cancel {
  margin-left: auto;
  padding: 1px 8px;
  border-radius: 4px;
  border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent);
  background: transparent;
  color: var(--color-danger);
  font-size: 10.5px;
  cursor: pointer;
  font-family: inherit;
}
.row-pull-cancel:hover { background: color-mix(in oklch, var(--color-danger) 10%, transparent); }
.row-pull-err { color: var(--color-danger); }
.row-pull-err strong { font-family: var(--font-mono); }

/* Slots cell */
.slots-cell { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; }
.slot-badge {
  font-family: var(--font-mono); font-size: 10px;
  padding: 2px 6px; border-radius: 4px;
  background: var(--color-surface-2); border: 1px solid var(--color-border);
  color: var(--color-fg-faint);
}
.slot-badge.badge-running { background: color-mix(in srgb, var(--hal0-accent) 12%, transparent); border-color: color-mix(in srgb, var(--hal0-accent) 35%, transparent); color: var(--hal0-accent); }
.text-faint { color: var(--color-fg-faint); }

/* Row actions */
.row-actions { display: flex; align-items: center; gap: 6px; }
.assign-select {
  padding: 4px 7px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  font-size: 11.5px;
  cursor: pointer;
  max-width: 140px;
}
.assign-select:focus { outline: none; border-color: var(--color-border-hi); }

.act-btn {
  width: 28px; height: 28px;
  display: grid; place-items: center;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-faint);
  cursor: pointer;
  transition: background 0.1s, color 0.1s;
  flex-shrink: 0;
}
.act-btn:hover { background: var(--color-surface-2); color: var(--color-fg); }
.act-danger:hover { background: color-mix(in oklch, var(--color-danger) 12%, transparent); color: var(--color-danger); border-color: color-mix(in oklch, var(--color-danger) 30%, transparent); }

/* Pull modal tabs */
.pull-tabs { display: flex; border-bottom: 1px solid var(--color-border); padding: 0 20px; }
.pull-tab {
  padding: 10px 16px;
  font-size: 13px;
  font-weight: 500;
  color: var(--color-fg-faint);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  margin-bottom: -1px;
  transition: color 0.1s, border-color 0.1s;
}
.pull-tab:hover { color: var(--color-fg-muted); }
.pull-tab.active { color: var(--hal0-accent); border-bottom-color: var(--hal0-accent); }

/* Curated list */
.curated-list { display: flex; flex-direction: column; gap: 8px; }
.curated-row {
  display: flex; align-items: center; gap: 12px;
  padding: 12px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
}
.curated-info { flex: 1; display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.curated-name { font-size: 13px; font-weight: 600; color: var(--color-fg); }
.curated-desc { font-size: 11.5px; color: var(--color-fg-faint); }
.curated-meta { display: flex; gap: 6px; flex-shrink: 0; }
.mono-chip {
  font-family: var(--font-mono); font-size: 10.5px;
  padding: 2px 7px; border-radius: 4px;
  background: var(--color-surface-3); border: 1px solid var(--color-border);
  color: var(--color-fg-faint);
}
.btn-sm-accent {
  display: flex; align-items: center; gap: 5px;
  padding: 5px 12px; border-radius: var(--radius);
  background: var(--hal0-accent); color: #000;
  font-family: var(--font-mono);
  font-size: 11.5px; font-weight: 500; border: none; cursor: pointer;
  flex-shrink: 0; transition: background 0.15s;
}
.btn-sm-accent:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-sm-accent:disabled { opacity: 0.45; cursor: not-allowed; }

/* Shared */
.modal-overlay { position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,0.6); backdrop-filter: blur(4px); display: flex; align-items: center; justify-content: center; padding: 16px; }
.modal-box { background: var(--color-surface); border: 1px solid var(--color-border-hi); border-radius: var(--radius-xl); width: min(540px, 100%); max-height: 90vh; display: flex; flex-direction: column; box-shadow: 0 24px 64px rgba(0,0,0,0.6); overflow: hidden; }
.modal-sm { width: min(380px, 100%); }
.modal-header { display: flex; align-items: center; justify-content: space-between; padding: 16px 20px; border-bottom: 1px solid var(--color-border); }
.modal-title { font-size: 15px; font-weight: 600; color: var(--color-fg); margin: 0; }
.modal-close { width: 28px; height: 28px; border-radius: var(--radius); background: transparent; border: 1px solid transparent; color: var(--color-fg-faint); cursor: pointer; display: grid; place-items: center; }
.modal-close:hover { background: var(--color-surface-2); color: var(--color-fg); }
.modal-body { padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; flex: 1; }
.modal-footer { padding: 16px 20px; border-top: 1px solid var(--color-border); display: flex; justify-content: flex-end; gap: 8px; }

.field { display: flex; flex-direction: column; gap: 5px; }
.field-label { font-size: 12.5px; font-weight: 600; color: var(--color-fg-muted); }
.req { color: var(--color-danger); }
.field-input { padding: 7px 10px; border-radius: var(--radius); border: 1px solid var(--color-border); background: var(--color-surface-2); color: var(--color-fg); font-size: 13px; outline: none; transition: border-color 0.1s; box-sizing: border-box; width: 100%; }
.field-input:focus { border-color: var(--color-border-hi); }
.field-error { border-color: var(--color-danger) !important; }
.field-err  { font-size: 11.5px; color: var(--color-danger); margin: 0; }
.field-hint { font-size: 11.5px; color: var(--color-fg-faint); margin: 0; }
.mono-text  { font-family: var(--font-mono); }

.btn-primary { display: flex; align-items: center; gap: 6px; padding: 7px 16px; border-radius: var(--radius); background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-size: 12px; font-weight: 500; border: none; cursor: pointer; transition: background 0.15s; }
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-ghost { padding: 7px 16px; border-radius: var(--radius); border: 1px solid var(--color-border); background: transparent; color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer; transition: border-color 0.15s, color 0.15s; }
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-link { background: transparent; border: none; color: var(--hal0-accent); font-size: 13px; cursor: pointer; }
.btn-link:hover { text-decoration: underline; }

.spinner { width: 11px; height: 11px; border: 2px solid rgba(255,255,255,0.3); border-top-color: white; border-radius: 50%; animation: spin 0.7s linear infinite; flex-shrink: 0; }
@keyframes spin { to { transform: rotate(360deg); } }

/* ── Catalogue ────────────────────────────────────────────────── */
.catalogue-section { display: flex; flex-direction: column; gap: 14px; margin-top: 8px; }
.catalogue-header { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; }
.catalogue-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--hal0-accent);
  font-family: var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin: 0;
}
.catalogue-counts { font-size: 11px; color: var(--color-fg-faint); }
.catalogue-group { display: flex; flex-direction: column; gap: 6px; }
.catalogue-subtitle { font-size: 12.5px; font-weight: 600; color: var(--color-fg); margin: 0; }
.catalogue-subnote { font-size: 11.5px; color: var(--color-fg-faint); font-style: italic; margin: 0 0 2px 0; }

.cat-list { list-style: none; padding: 0; margin: 0; }
.cat-row {
  display: flex; align-items: center; gap: 12px;
  padding: 10px 14px;
  border-bottom: 1px solid var(--color-border);
}
.cat-row:last-child { border-bottom: none; }
.cat-row:hover { background: var(--color-surface-2); }
.cat-row-main { flex: 1; display: flex; flex-direction: column; gap: 3px; min-width: 0; }
.cat-row-title { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.cat-name { font-size: 13px; font-weight: 600; color: var(--color-fg); }
.cat-id { font-size: 10.5px; color: var(--color-fg-faint); }
.cat-id-strong { font-size: 12.5px; color: var(--color-fg); font-weight: 500; }
.cat-desc { font-size: 11.5px; color: var(--color-fg-faint); margin: 0; }
.cat-row-meta { display: flex; align-items: center; gap: 6px; flex-shrink: 0; flex-wrap: wrap; justify-content: flex-end; }

.cap-chip {
  font-family: var(--font-mono); font-size: 10.5px;
  padding: 2px 7px; border-radius: 4px;
  background: color-mix(in oklch, var(--hal0-accent) 10%, transparent);
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 28%, transparent);
  color: var(--hal0-accent);
}
.owned-by { font-size: 10.5px; color: var(--color-fg-faint); }

/* Hardware chips — mirror Dashboard's palette. */
.hw-chip {
  font-family: var(--font-mono); font-size: 10px;
  padding: 2px 6px; border-radius: 4px; letter-spacing: 0.04em;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  flex-shrink: 0;
}
.hw-chip.hw-npu  { color: var(--color-warning); border-color: color-mix(in oklch, var(--color-warning), transparent 60%); background: color-mix(in oklch, var(--color-warning), transparent 88%); }
.hw-chip.hw-gpu  { color: var(--color-danger);  border-color: color-mix(in oklch, var(--color-danger),  transparent 60%); background: color-mix(in oklch, var(--color-danger),  transparent 88%); }
.hw-chip.hw-igpu { color: var(--color-success); border-color: color-mix(in oklch, var(--color-success), transparent 60%); background: color-mix(in oklch, var(--color-success), transparent 88%); }
.hw-chip.hw-cpu  { color: var(--color-fg-muted); border-color: var(--color-border-hi); }
.hw-chip.hw-remote { color: var(--color-fg-faint); border-color: var(--color-border-hi); opacity: 0.85; }
.hw-chip.hw-unknown { opacity: 0.6; text-transform: lowercase; }

.curated-empty { font-size: 12px; color: var(--color-fg-faint); padding: 8px 4px; }

.fade-enter-active, .fade-leave-active { transition: opacity 0.12s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* ── Model badges (row-level capability + backend chips) ────────── */
.model-badges { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.model-pending { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 4px; }
.badge {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  line-height: 1.5;
  letter-spacing: 0.02em;
}
.badge-cap {
  color: var(--hal0-accent);
  border-color: color-mix(in oklch, var(--hal0-accent) 28%, transparent);
  background: color-mix(in oklch, var(--hal0-accent) 10%, transparent);
}
.badge-bk { color: var(--color-fg-faint); }

/* ── Wider modal for Local-file tab (scan-preview table needs room) ── */
.modal-wide { width: min(820px, 100%); }

/* ── Local-file mode toggle (Single / Scan) ─────────────────────── */
.local-mode-toggle {
  display: inline-flex;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  overflow: hidden;
  margin-bottom: 12px;
}
.local-mode-btn {
  padding: 6px 14px;
  font-size: 12px;
  background: transparent;
  border: none;
  color: var(--color-fg-faint);
  cursor: pointer;
  font-family: inherit;
}
.local-mode-btn + .local-mode-btn { border-left: 1px solid var(--color-border); }
.local-mode-btn.active { background: var(--hal0-accent); color: #000; }
.local-pane { display: flex; flex-direction: column; gap: 12px; }

/* Checkbox pill group used in Local-file + Edit modals. */
.check-row { display: flex; flex-wrap: wrap; gap: 6px; }
.check-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 9px;
  border-radius: 999px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  font-size: 11.5px;
  cursor: pointer;
  user-select: none;
}
.check-pill input[type="checkbox"] { margin: 0; width: 11px; height: 11px; accent-color: var(--hal0-accent); }
.check-pill:has(input:checked) {
  background: color-mix(in oklch, var(--hal0-accent) 14%, transparent);
  border-color: color-mix(in oklch, var(--hal0-accent) 40%, transparent);
  color: var(--hal0-accent);
}
.check-pill-sm { padding: 2px 6px; font-size: 10.5px; }
.check-inline { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--color-fg-muted); }
.check-inline input { accent-color: var(--hal0-accent); }

.detect-block {
  display: flex; flex-direction: column; gap: 10px;
  padding: 12px;
  border: 1px dashed var(--color-border-hi);
  border-radius: var(--radius-lg);
  background: var(--color-surface-2);
}
.detect-head { display: flex; align-items: center; gap: 8px; }
.detect-label {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--hal0-accent); font-family: var(--font-mono); font-weight: 600;
}

/* Scan preview table */
.scan-preview { display: flex; flex-direction: column; gap: 6px; }
.scan-table-wrap { overflow-x: auto; border: 1px solid var(--color-border); border-radius: var(--radius); }
.scan-table { width: 100%; border-collapse: collapse; font-size: 11.5px; table-layout: fixed; }
.scan-table .col-pick { width: 32px; text-align: center; }
.scan-table .col-name { width: 240px; min-width: 240px; }
.scan-table .col-kind { width: 140px; }
.scan-table .col-tags { width: 220px; }
.scan-table .col-path { width: auto; }
.scan-table .col-ctx  { width: 64px; }
.scan-table .col-conf { width: 64px; }
.scan-table tr.row-unpicked td { opacity: 0.55; }
.scan-table tr.row-unpicked td.col-pick { opacity: 1; }
.scan-id-hint { font-size: 10.5px; color: var(--color-fg-faint); margin-top: 2px; line-height: 1.2; }
.scan-foot { margin-top: 8px; }

.kind-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.2px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  white-space: nowrap;
}
.kind-llama     { border-color: color-mix(in oklch, var(--hal0-accent) 60%, var(--color-border)); color: var(--hal0-accent); }
.kind-flm       { border-color: color-mix(in oklch, var(--color-warning) 60%, var(--color-border)); color: var(--color-warning); }
.kind-moonshine { border-color: color-mix(in oklch, var(--color-info, var(--hal0-accent)) 60%, var(--color-border)); color: var(--color-info, var(--hal0-accent)); }
.kind-kokoro    { border-color: color-mix(in oklch, var(--color-success) 60%, var(--color-border)); color: var(--color-success); }
.kind-unknown   { border-color: var(--color-border); color: var(--color-fg-faint); }

.tag-row { display: flex; flex-wrap: wrap; gap: 3px; }
.tag {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 3px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-fg-muted);
  white-space: nowrap;
}
.tag-cap   { border-style: solid; }
.tag-bk    { border-style: dashed; }
.tag-empty { color: var(--color-fg-faint); }
.scan-table th {
  text-align: left; padding: 6px 8px;
  background: var(--hal0-bg-sunken);
  color: var(--hal0-accent);
  font-family: var(--font-mono);
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  font-weight: 500;
  border-bottom: 1px solid var(--color-border);
}
.scan-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
  color: var(--color-fg-muted);
}
.scan-table tr:last-child td { border-bottom: none; }
.scan-path {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  direction: rtl;            /* keep the meaningful tail visible on truncation */
  text-align: left;
  unicode-bidi: plaintext;
}
.scan-name { font-weight: 500; }
.scan-input {
  width: 100%;
  padding: 3px 6px;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-fg);
  font-size: 11px;
  outline: none;
  box-sizing: border-box;
  display: block;
}
.scan-input + .scan-input { margin-top: 3px; }
.scan-input.mono { font-family: var(--font-mono); }
.scan-checks { display: flex; flex-wrap: wrap; gap: 3px; max-width: 220px; }

/* Re-detect + diff block in Edit modal */
.redetect-block { display: flex; flex-direction: column; gap: 8px; }
.redetect-diff {
  padding: 10px;
  border: 1px dashed var(--color-border-hi);
  border-radius: var(--radius);
  background: var(--color-surface-2);
  display: flex; flex-direction: column; gap: 8px;
}

/* Advanced disclosure */
.adv-block { border-top: 1px solid var(--color-border); padding-top: 12px; }
.adv-summary {
  cursor: pointer;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-fg-muted);
  margin-bottom: 8px;
  list-style: none;
  user-select: none;
}
.adv-summary::-webkit-details-marker { display: none; }
.adv-summary::before { content: "▸ "; font-family: var(--font-mono); }
details[open] > .adv-summary::before { content: "▾ "; }

.field-label-row { display: flex; align-items: baseline; justify-content: space-between; gap: 8px; }
.btn-xs { padding: 3px 8px; font-size: 10.5px; }
</style>
