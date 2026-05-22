<script setup>
/**
 * Slots.vue
 *
 * Design decisions:
 * - Slots render as compact rows in a table-like list, not big cards.
 *   Status, model, port, and actions are all visible at once — no hover-to-reveal.
 * - Lifecycle actions (load/unload/restart/swap) are inline, next to each slot.
 *   No separate detail page; an edit drawer slides in from the right without losing
 *   context of the full list.
 * - Error state persists on the row; it doesn't get cleared by the next poll
 *   until the user dismisses or the slot recovers.
 * - Hardware-aware create form: pulls /api/hardware, shows VRAM fit inline
 *   in the model dropdown ("fits ✓" vs "won't fit ✗").
 * - `n` key opens New Slot form, Esc closes it.
 */
import { ref, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useToastsStore } from '../stores/toasts.js'
import { useAgentStore } from '../stores/agent.js'
import { useSlotMetrics } from '../composables/useStats.js'
import { api } from '../composables/useApi.js'
import { useEvents } from '../composables/useEvents.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import EmptyState from '../components/EmptyState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import SlotCard from '../components/SlotCard.vue'
import NPUBackendCard from '../components/capabilities/NPUBackendCard.vue'
import CapabilitiesSection from '../components/capabilities/CapabilitiesSection.vue'
import AgentPendingChip from '../components/agent/AgentPendingChip.vue'

// Mirror src/hal0/slots/__init__.py BUILTIN_SLOTS — these cannot be deleted.
const BUILTIN_SLOTS = new Set(['primary', 'embed', 'stt', 'tts'])

// Slots whose UX lives entirely in the capability cards (EmbedCard,
// VoiceCard, ImgCard). They're still real slots in the backend, but we
// hide them from the regular slot grid so operators don't have two
// places that look like they configure the same thing.
const CAPABILITY_OWNED_SLOTS = new Set([
  'embed', 'embed-rerank', 'stt', 'tts', 'img',
])

// Hard ordering for the slot grid. Slots listed here take the first
// positions in the given order; anything else sorts alphabetically
// after them. NPU rides at the very end as a separate card.
const SLOT_ORDER = ['primary', 'nano']
function slotSortKey(name) {
  const i = SLOT_ORDER.indexOf(name)
  return i >= 0 ? [0, i, name] : [1, 0, name]
}

const { metrics: slotMetrics, history: slotHistory } = useSlotMetrics(2500)

const route  = useRoute()
const router = useRouter()
const system = useSystemStore()
const toasts = useToastsStore()
const agent  = useAgentStore()

// ── State ──────────────────────────────────────────────────────────────
const loading    = ref(false)
const actionBusy = ref({})  // { [slotName]: 'load'|'unload'|'restart'|'delete'|... }
const rowErrors  = ref({})  // { [slotName]: errorMessage } — persistent per row

// Available models for load/swap form
const models = ref([])
const hardware = ref(null)

// Create slot modal
const showCreate = ref(false)
const createForm = ref(defaultCreateForm())
const createErrors = ref({})
const creating = ref(false)

// Per-slot model selection for the inline load dropdown — lives OUTSIDE
// the augmented-slots computed so it survives system.slots polls.
// Previously `_selectedModel` was carried on the wrapper object, which
// was rebuilt every poll, so the user's selection vanished on the next
// 2.5s refresh and "Start" got an empty model id.
const slotSelections = ref({})

// Edit slot drawer
const editingSlot      = ref(null)
const editForm         = ref({})
const editOriginal     = ref({})   // snapshot for change detection
const editing          = ref(false)
const showAdvanced     = ref(false) // ▸ Advanced disclosure (Edit modal)
const ctxSizeDirty     = ref(false) // user manually touched edit ctx_size
const ctxSizeDirtyNew  = ref(false) // user manually touched create ctx_size

// Restart-required confirm (post-save)
const pendingRestartSlot = ref(null)
const restarting         = ref(false)

// Delete confirm
const deletingSlot = ref(null)
const deleting     = ref(false)

// Fields that require a slot restart to apply
const RESTART_FIELDS = new Set(['ctx_size', 'n_gpu_layers'])
// Slot states considered "live" for swap-vs-config dispatch
const RUNNING_STATES = new Set(['running', 'ready', 'serving', 'idle'])

// Logs drawer
const logsSlot    = ref(null)
const logsLines   = ref([])
const logsLoading = ref(false)
let logEs = null

function defaultCreateForm() {
  return {
    name:      '',
    type:      'llama-server',
    backend:   'vulkan',
    model:     '',
    port:      '',
    ctx_size:  4096,
    auto_start: false,
  }
}

// ── Hardware + models loader ───────────────────────────────────────────
async function loadHardware() {
  try {
    const hw = await api('/api/hardware')
    hardware.value = hw
  } catch {
    // Phase 0: use mock for form rendering
    hardware.value = { gpu_name: 'GPU', gtt_total_mb: null, vram_total_mb: null }
  }
}

async function loadModels() {
  try {
    const data = await api('/api/models')
    models.value = Array.isArray(data) ? data : (data?.models ?? [])
  } catch {
    models.value = []
  }
}

// VRAM fit check for the hardware-aware model selector
const availMemMb = computed(() => {
  if (!hardware.value) return null
  return hardware.value.gtt_total_mb ?? hardware.value.vram_total_mb ?? null
})

// Strix-Halo-only NPU card visibility. The FLM toolbox image only
// runs on Strix Halo today; surfacing the card on any other XDNA host
// (or, worse, on a virtio "NPU" in a misconfigured probe) leads
// operators to a backend they can't use.
const showNpuCard = computed(
  () => !!hardware.value?.npu_present && hardware.value?.platform === 'strix-halo',
)

function modelFit(model) {
  if (availMemMb.value === null) return null
  const reqMb = (model.size_gb ?? 0) * 1024 * 1.1  // 10% overhead
  if (reqMb === 0) return null
  return reqMb <= availMemMb.value
}

function modelFitLabel(model) {
  const fit = modelFit(model)
  if (fit === null) return ''
  return fit ? '✓' : '✗'
}

// ── Slot lifecycle actions ─────────────────────────────────────────────
async function slotAction(slotName, action, body = null) {
  actionBusy.value[slotName] = action
  rowErrors.value[slotName] = null
  try {
    await api(`/api/slots/${slotName}/${action}`, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    })
    toasts.success(`${action} "${slotName}" queued`)
    await system.fetchStatus()
  } catch (e) {
    rowErrors.value[slotName] = e.message
    toasts.error(`${action} ${slotName}: ${e.message}`)
  } finally {
    actionBusy.value[slotName] = null
  }
}

async function doLoad(slot) {
  // The card's footer <select> was removed in favour of the inline swap
  // trigger on the model label, so slotSelections is only populated by
  // the legacy row layout (kept behind v-if="false"). Fall back to the
  // slot's persisted model_id so Start on a previously-loaded slot just
  // reloads its model; if even that is empty the POST body is omitted
  // and the API uses the slot's TOML default.
  const model = slotSelections.value[slot.name]
    || slot._selectedModel
    || slot.model_id
    || slot.model
  if (!model) {
    await slotAction(slot.name, 'load')
    return
  }
  await slotAction(slot.name, 'load', { model })
}

// Standalone Swap modal removed in B2 — swap is now dispatched from the
// Edit modal's Save button when only the model changed on a running slot.
// SlotCard inline swap dropdown (C1) calls /api/slots/{name}/swap directly.

// ── Create slot ────────────────────────────────────────────────────────
function validateCreate() {
  const errs = {}
  if (!createForm.value.name.trim()) errs.name = 'Required'
  if (createForm.value.name && !/^[a-z0-9-]+$/.test(createForm.value.name)) {
    errs.name = 'Lowercase letters, digits, and hyphens only'
  }
  if (createForm.value.port && !/^\d+$/.test(createForm.value.port)) {
    errs.port = 'Must be a number (or leave blank for auto)'
  }
  if (createForm.value.ctx_size < 256 || createForm.value.ctx_size > 131072) {
    errs.ctx_size = 'Must be between 256 and 131072'
  }
  createErrors.value = errs
  return Object.keys(errs).length === 0
}

async function submitCreate() {
  if (!validateCreate()) return
  creating.value = true
  try {
    const body = {
      name:       createForm.value.name,
      type:       createForm.value.type,
      backend:    createForm.value.backend,
      auto_start: createForm.value.auto_start,
      ctx_size:   Number(createForm.value.ctx_size),
    }
    if (createForm.value.model) body.model = createForm.value.model
    if (createForm.value.port) {
      body.port = Number(createForm.value.port)
    } else {
      // SlotConfig.port is required (Pydantic) and the API doesn't auto-
      // assign — mirror the CLI behaviour (slot_commands.py:slot_create)
      // by picking the first free port in 8081-8099 client-side.
      try {
        const existing = await api('/api/slots')
        const used = new Set((existing ?? []).map(s => Number(s.port) || 0))
        for (let p = 8081; p < 8100; p++) {
          if (!used.has(p)) { body.port = p; break }
        }
      } catch {
        body.port = 8081
      }
    }
    await api('/api/slots', { method: 'POST', body: JSON.stringify(body) })
    toasts.success(`Slot "${body.name}" created on port ${body.port}`)
    showCreate.value = false
    createForm.value = defaultCreateForm()
    createErrors.value = {}
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    creating.value = false
  }
}

// ── Edit slot ──────────────────────────────────────────────────────────
function openEdit(slot) {
  editingSlot.value = slot
  const initial = {
    backend:        slot.backend ?? 'vulkan',
    model:          slot.model_id ?? slot.model ?? '',
    ctx_size:       slot.ctx_size ?? 4096,
    auto_start:     slot.auto_start ?? false,
    // Advanced — Model
    n_gpu_layers:   slot.n_gpu_layers ?? -1,
    rope_freq_base: slot.rope_freq_base ?? 0,
    // Advanced — Server
    workers:        slot.workers ?? 1,
    idle_timeout_s: slot.idle_timeout_s ?? 300,
    extra_args:     slot.extra_args ?? '',
  }
  editForm.value = { ...initial }
  editOriginal.value = { ...initial }
  showAdvanced.value = false
  ctxSizeDirty.value = false
}

// Currently-selected model object for the Edit modal (for hints/preview/defaults)
const editSelectedModel = computed(
  () => models.value.find((m) => m.id === editForm.value.model) ?? null,
)
const createSelectedModel = computed(
  () => models.value.find((m) => m.id === createForm.value.model) ?? null,
)

// Models compatible with a given backend (slot.backend ∈ model.backends).
// Models without a backends list (legacy/unscanned) are treated as universal
// to avoid hiding everything on a fresh registry.
function filterCompatibleModels(backend) {
  return models.value.filter((m) => {
    const bs = Array.isArray(m.backends) ? m.backends : []
    if (bs.length === 0) return true
    return bs.includes(backend)
  })
}
const compatibleEditModels   = computed(() => filterCompatibleModels(editForm.value.backend))
const compatibleCreateModels = computed(() => filterCompatibleModels(createForm.value.backend))

// Auto-fill ctx_size on model pick, unless the user has already typed in
// the ctx_size field (dirty flag). Triggered from the model <select> @change.
function applyModelDefaults(form, dirtyRef) {
  const m = models.value.find((x) => x.id === form.model)
  if (!m) return
  if (!dirtyRef.value) {
    form.ctx_size = m.defaults?.context_size ?? 4096
  }
}
function onEditModelChange()   { applyModelDefaults(editForm.value,   ctxSizeDirty) }
function onCreateModelChange() { applyModelDefaults(createForm.value, ctxSizeDirtyNew) }

// CTA when zero compatible models for the current backend: close the modal
// and route to /models where the user can register one. B3 will swap this
// to deep-link the Add modal; until then, the route alone is enough.
function gotoAddModel() {
  editingSlot.value = null
  showCreate.value = false
  router.push('/models')
}

// ── Flag merge preview (mirrors src/hal0/launchers/flag_merge.py) ─────
//
// Tokenises model-defaults and slot extra_args, lets slot flags overwrite
// model-default flags by name, except for append-list flags that accept
// repeats (--lora / --draft-model / --override-kv). Malformed input falls
// back to dumb concat — same policy as the backend.
const APPEND_LIST_FLAGS = new Set(['--lora', '--draft-model', '--override-kv'])

function tokenise(s) {
  if (!s) return []
  return String(s).trim().split(/\s+/).filter(Boolean)
}
function mergeFlagsPreview(modelDefaults, slotExtra) {
  const modelToks = tokenise(modelDefaults)
  const slotToks  = tokenise(slotExtra)
  try {
    // Collect slot's named flags (skip append-list flags from dedup set).
    const slotNames = new Set()
    for (const t of slotToks) {
      if (t.startsWith('--') && !APPEND_LIST_FLAGS.has(t)) slotNames.add(t)
    }
    // Strip matching --flag (value?) pairs from model defaults.
    const cleanedModel = []
    let i = 0
    while (i < modelToks.length) {
      const tok = modelToks[i]
      if (tok.startsWith('--') && slotNames.has(tok)) {
        i++
        // skip the value if next token isn't another flag
        if (i < modelToks.length && !modelToks[i].startsWith('--')) i++
        continue
      }
      cleanedModel.push(tok)
      i++
    }
    return [...cleanedModel, ...slotToks].join(' ')
  } catch {
    return [...modelToks, ...slotToks].join(' ')
  }
}
const effectiveFlagsPreview = computed(() =>
  mergeFlagsPreview(
    editSelectedModel.value?.defaults?.extra_args ?? '',
    editForm.value.extra_args ?? '',
  ),
)

// What changed since openEdit? Returns a set of field names.
function changedEditFields() {
  const changed = new Set()
  for (const k of Object.keys(editForm.value)) {
    if (editForm.value[k] !== editOriginal.value[k]) changed.add(k)
  }
  return changed
}

async function submitEdit() {
  if (!editingSlot.value) return
  editing.value = true
  try {
    const changed = changedEditFields()
    const slot = editingSlot.value
    const isLive = RUNNING_STATES.has(slot.status)
    const slotName = slot.name

    // Fast path: only `model` changed AND the slot is live → /swap.
    //
    // The backend swap is unload → systemctl restart → wait-for-health,
    // which routinely takes 10-30s and can hit a 180s health timeout on
    // failure. Awaiting that inline locks the modal for minutes. We
    // close the modal optimistically, surface a toast, and let the slot
    // state SSE ring (liveStates) animate the card through
    // warming → ready. On error the swap's promise rejects and we toast
    // the failure even though the modal is already gone.
    if (changed.size === 1 && changed.has('model') && isLive && editForm.value.model) {
      const targetModel = editForm.value.model
      editingSlot.value = null
      editing.value = false
      toasts.success(`Swapping "${slotName}" → ${targetModel}…`)
      api(`/api/slots/${slotName}/swap`, {
        method: 'POST',
        body: JSON.stringify({ model_id: targetModel }),
      })
        .then(() => {
          toasts.success(`"${slotName}" ready on ${targetModel}`)
          return system.fetchStatus()
        })
        .catch((e) => {
          toasts.error(`Swap "${slotName}" failed: ${e.message}`)
        })
      return
    }

    // Slow path: PUT full config. Fast on the server (no container
    // restart), so we keep the inline await.
    if (changed.size === 0) {
      editingSlot.value = null
      return
    }
    await api(`/api/slots/${slotName}/config`, {
      method: 'PUT',
      body: JSON.stringify(editForm.value),
    })
    toasts.success(`Slot "${slotName}" updated`)

    // Restart prompt: only when restart-required fields changed AND the
    // slot is live. Otherwise the new config takes effect on next load.
    const restartNeeded = [...changed].some((f) => RESTART_FIELDS.has(f))
    if (restartNeeded && isLive) {
      pendingRestartSlot.value = slot
    }
    editingSlot.value = null
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    editing.value = false
  }
}

async function confirmRestart() {
  if (!pendingRestartSlot.value) return
  restarting.value = true
  const name = pendingRestartSlot.value.name
  try {
    await api(`/api/slots/${name}/restart`, { method: 'POST' })
    toasts.success(`Restart "${name}" queued`)
    pendingRestartSlot.value = null
    await system.fetchStatus()
  } catch (e) {
    toasts.error(`restart ${name}: ${e.message}`)
  } finally {
    restarting.value = false
  }
}

// ── Delete slot ────────────────────────────────────────────────────────
async function confirmDelete() {
  if (!deletingSlot.value) return
  deleting.value = true
  try {
    await api(`/api/slots/${deletingSlot.value.name}`, { method: 'DELETE' })
    toasts.success(`Slot "${deletingSlot.value.name}" deleted`)
    deletingSlot.value = null
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    deleting.value = false
  }
}

// ── Logs drawer ────────────────────────────────────────────────────────
async function openLogs(slot) {
  closeLogs()
  logsSlot.value = slot
  logsLoading.value = true
  logsLines.value = []
  try {
    const data = await api(`/api/slots/${slot.name}/logs?lines=200`)
    logsLines.value = (data?.logs ?? '').split('\n').filter(Boolean)
  } catch (e) {
    logsLines.value = [`Error: ${e.message}`]
  } finally {
    logsLoading.value = false
  }
  // SSE tail
  try {
    logEs = new EventSource(`/api/slots/${slot.name}/logs/stream`)
    logEs.onmessage = (ev) => {
      logsLines.value.push(ev.data)
      if (logsLines.value.length > 2000) logsLines.value = logsLines.value.slice(-2000)
    }
    logEs.onerror = () => {}
  } catch {}
}

function closeLogs() {
  if (logEs) { logEs.close(); logEs = null }
  logsSlot.value = null
  logsLines.value = []
}

// ── Keyboard shortcuts ─────────────────────────────────────────────────
function handleKey(e) {
  if ((e.target instanceof HTMLInputElement) || (e.target instanceof HTMLTextAreaElement)) return
  if (e.key === 'n' && !showCreate.value && !editingSlot.value) {
    e.preventDefault()
    showCreate.value = true
  } else if (e.key === 'Escape') {
    if (showCreate.value) { showCreate.value = false }
    else if (editingSlot.value) { editingSlot.value = null }
    else if (logsSlot.value) { closeLogs() }
    else if (pendingRestartSlot.value) { pendingRestartSlot.value = null }
  }
}

// ── Events ring → per-slot live state ─────────────────────────────────
//
// C2: dropped per-slot /api/slots/{name}/state/stream EventSources.
// Now subscribes to the shared `useEvents` ring (footer owns the single
// SSE connection to /api/events/stream) and filters for `slot.state`
// events. Event payload shape from src/hal0/slots/manager.py:367-385:
//   { type: 'slot.state', source: 'slot:<name>',
//     data: { slot, from, to, model_id?, message?, error? } }
//
// The 5s /api/slots poll stays as a safety net (first paint, missed
// frames during reconnect). Augmented `slots` computed prefers the
// ring-derived state over the polled snapshot's `status` field.
const events = useEvents()

// Walk the ring once and keep the latest slot.state event per slot.
// Newest is LAST, so iterating in order and overwriting yields latest.
const liveStates = computed(() => {
  const out = {}
  for (const evt of events.events.value) {
    if (evt?.type !== 'slot.state') continue
    const d = evt.data || {}
    const name = d.slot
      ?? (typeof evt.source === 'string' && evt.source.startsWith('slot:')
          ? evt.source.slice(5) : null)
    if (!name) continue
    out[name] = {
      state: d.to ?? d.state,
      model_id: d.model_id,
      updated_at: evt.ts,
    }
  }
  return out
})

// ── Augmented slots list ──────────────────────────────────────────────
//
// Overlays the ring-derived `liveStates[name].state` onto the polled
// snapshot's `status` field. Without this, transitions only appear on
// the next 5s poll tick; with it, SlotCard re-renders within ~1 RTT of
// a state machine transition.
//
// `_selectedModel` is read from slotSelections (not stored on the
// wrapper object), so the dropdown's pending selection survives across
// store polls — see the slotSelections declaration above.
const slots = computed(() => {
  const rows = system.slots
    .filter((s) => !CAPABILITY_OWNED_SLOTS.has(s.name))
    .map((s) => {
      const live = liveStates.value[s.name]
      const status = live?.state ?? s.status
      return { ...s, status, _selectedModel: slotSelections.value[s.name] ?? '' }
    })
  rows.sort((a, b) => {
    const ka = slotSortKey(a.name)
    const kb = slotSortKey(b.name)
    if (ka[0] !== kb[0]) return ka[0] - kb[0]
    if (ka[1] !== kb[1]) return ka[1] - kb[1]
    return ka[2].localeCompare(kb[2])
  })
  return rows
})

// Open detail panel if navigated to /slots/:name
watch(() => route.params.name, (name) => {
  if (name) {
    const slot = system.slots.find((s) => s.name === name)
    if (slot) openEdit(slot)
  }
}, { immediate: true })

onMounted(async () => {
  window.addEventListener('keydown', handleKey)
  await Promise.all([loadModels(), loadHardware()])
  // No per-slot stream setup — the shared useEvents ring is owned by the
  // footer and already streaming. Polling (/api/slots via system store)
  // remains as the safety net for first paint + missed frames.
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKey)
  if (logEs) logEs.close()
  // Nothing to tear down for slot state: useEvents is a shared singleton
  // owned by the footer; this view only consumes its ref.
})

// ── Display helpers ────────────────────────────────────────────────────
// state-dot vocab:
//   state-running (green)  → only `serving` (a request is in-flight)
//   state-idle    (yellow) → any warm/loaded state (running/ready/idle/warming/starting/pulling)
//   state-error   (red)    → terminal failure
//   state-offline (grey)   → process down / unloading
// Mirrors SlotCard.vue dot semantics: dot color = readiness, top rail = in-flight.
const stateClass = (s) => ({
  serving: 'state-running',
  running: 'state-idle', ready: 'state-idle', idle: 'state-idle',
  warming: 'state-idle', starting: 'state-idle', pulling: 'state-idle',
  error: 'state-error',
  offline: 'state-offline', unloading: 'state-offline',
}[s] ?? 'state-offline')

const BACKENDS = ['vulkan', 'rocm', 'cuda', 'cpu', 'metal']
const SLOT_TYPES = ['llama-server', 'flm', 'moonshine', 'kokoro']
</script>

<template>
  <div class="slots-page">
    <PageHeader eyebrow="Lifecycle" title="Slots" subtitle="Inference slot lifecycle management">
      <template #actions>
        <span class="kbd-hint" aria-hidden="true">Press <kbd>N</kbd> to create</span>
        <button class="btn-primary" type="button" @click="showCreate = true">
          <svg width="13" height="13" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>
          </svg>
          New slot
        </button>
      </template>
    </PageHeader>

    <div class="page-body">
      <!-- ── Slots table ──────────────────────────────────────── -->
      <template v-if="system.loading && slots.length === 0">
        <Card v-for="i in 3" :key="i" class="slot-row-skel"><LoadingSkeleton :lines="2" /></Card>
      </template>

      <template v-else-if="slots.length === 0">
        <!-- All system slots are capability-owned; show NPU card alone
             rather than an empty "create a slot" prompt. Strix-Halo-only;
             elsewhere we render an explicit empty state. -->
        <div class="slots-grid" role="list" aria-label="Inference slots">
          <NPUBackendCard v-if="showNpuCard" />
          <p v-else class="empty-slots-msg">
            No slots configured. The Capabilities section above creates
            them automatically; create a custom slot with the form below
            once you've pulled a model.
          </p>
        </div>
      </template>

      <template v-else>
        <div class="slots-grid" role="list" aria-label="Inference slots">
          <div
            v-for="slot in slots"
            :key="slot.name"
            class="slot-cell"
          >
            <SlotCard
              :slot="slot"
              :metrics="slotMetrics[slot.name]"
              :spark-data="slotHistory[slot.name] || { tps: [], pps: [] }"
              :action-loading="actionBusy[slot.name]"
              @action="(a) => a === 'load' ? doLoad(slot) : slotAction(slot.name, a)"
              @logs="openLogs(slot)"
              @edit="openEdit(slot)"
              @delete="deletingSlot = slot"
            />
            <!-- Pending-approval chip(s) for any gated MCP tool queued
                 against this slot (slot_delete / slot_restart / etc.).
                 Per ADR-0004 §5: inline indicator, bell is canonical. -->
            <div
              v-if="agent.pendingForResource('slot', slot.name).length"
              class="slot-pending"
            >
              <AgentPendingChip
                v-for="p in agent.pendingForResource('slot', slot.name)"
                :key="p.id"
                :entry="p"
              />
            </div>
          </div>
          <!-- NPU backend rides in the same grid as the other slot
               cards now — it serves chat-shaped models the same way a
               local llama.cpp slot does. Strix-Halo-only; see
               showNpuCard above. -->
          <NPUBackendCard v-if="showNpuCard" />
        </div>
      </template>

      <!-- Capability slots — embed / voice / img cards. -->
      <CapabilitiesSection />

      <!-- Legacy row layout retained for the row-error rendering path
           (banners persist across the new grid by re-rendering the error
           via a toast). The list below is hidden in the default flow but
           kept in source so older bookmarks to anchors still resolve. -->
      <template v-if="false">
        <div class="slots-list" role="list">
          <div
            v-for="slot in slots"
            :key="slot.name"
            class="slot-row"
            :class="{ 'slot-row-error': rowErrors[slot.name] }"
            role="listitem"
          >
            <!-- Left: state + name + model -->
            <div class="slot-left">
              <span class="state-dot" :class="stateClass(slot.status)" :title="slot.status" aria-hidden="true" />
              <div class="slot-names">
                <span class="slot-name">{{ slot.name }}</span>
                <span class="slot-model" v-if="slot.model_id || slot.model">{{ slot.model_id || slot.model }}</span>
                <span class="slot-model text-faint" v-else>no model loaded</span>
              </div>
            </div>

            <!-- Center: meta chips -->
            <div class="slot-chips">
              <span class="chip chip-port" v-if="slot.port">:{{ slot.port }}</span>
              <span class="chip chip-type" v-if="slot.type">{{ slot.type }}</span>
              <span class="chip" :class="'chip-state-' + stateClass(slot.status)">{{ slot.status ?? 'offline' }}</span>
            </div>

            <!-- Persistent error banner -->
            <div v-if="rowErrors[slot.name]" class="row-error">
              <span>{{ rowErrors[slot.name] }}</span>
              <button type="button" class="row-error-dismiss" @click="rowErrors[slot.name] = null" aria-label="Dismiss error">×</button>
            </div>

            <!-- Right: actions -->
            <div class="slot-actions">
              <!-- Load (when no model loaded) -->
              <template v-if="!(slot.model_id || slot.model) || slot.status === 'offline'">
                <select
                  class="model-select"
                  :value="slot._selectedModel"
                  @change="(e) => { slotSelections[slot.name] = e.target.value }"
                  :aria-label="`Select model for slot ${slot.name}`"
                >
                  <option value="">Select model…</option>
                  <option v-for="m in filterCompatibleModels(slot.backend)" :key="m.id" :value="m.id">
                    {{ m.name ?? m.id }}{{ m.size_gb ? ` — ${m.size_gb}GB` : '' }} {{ modelFitLabel(m) }}
                  </option>
                </select>
                <button
                  class="btn-act btn-load"
                  type="button"
                  :disabled="!!actionBusy[slot.name]"
                  @click="doLoad(slot)"
                  :aria-busy="!!actionBusy[slot.name]"
                >
                  <span v-if="actionBusy[slot.name] === 'load'" class="spinner" aria-hidden="true" />
                  Load
                </button>
              </template>

              <!-- Running actions -->
              <template v-else>
                <button
                  class="btn-act btn-sm"
                  type="button"
                  :disabled="!!actionBusy[slot.name]"
                  @click="slotAction(slot.name, 'restart')"
                  :aria-busy="actionBusy[slot.name] === 'restart'"
                  :title="`Restart slot ${slot.name}`"
                >
                  <span v-if="actionBusy[slot.name] === 'restart'" class="spinner" aria-hidden="true" />
                  <svg v-else width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
                    <path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/>
                  </svg>
                  Restart
                </button>
                <!-- Swap button removed in B2 — model swap is dispatched
                     from the Edit modal's Save when only the model changed
                     on a running slot. C1 will add an inline dropdown to
                     SlotCard. -->
                <button
                  class="btn-act btn-sm btn-danger-ghost"
                  type="button"
                  :disabled="!!actionBusy[slot.name]"
                  @click="slotAction(slot.name, 'unload')"
                  :aria-busy="actionBusy[slot.name] === 'unload'"
                  :title="`Unload slot ${slot.name}`"
                >
                  <span v-if="actionBusy[slot.name] === 'unload'" class="spinner" aria-hidden="true" />
                  Unload
                </button>
              </template>

              <!-- Logs -->
              <button
                class="btn-act btn-sm btn-ghost"
                type="button"
                @click="openLogs(slot)"
                :aria-label="`View logs for slot ${slot.name}`"
              >
                <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/>
                </svg>
                Logs
              </button>

              <!-- Edit -->
              <button
                class="btn-act btn-sm btn-ghost"
                type="button"
                @click="openEdit(slot)"
                :aria-label="`Edit slot ${slot.name} configuration`"
              >
                <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/>
                </svg>
                Edit
              </button>

              <!-- Delete -->
              <button
                class="btn-act btn-sm btn-danger-ghost"
                type="button"
                @click="deletingSlot = slot"
                :aria-label="`Delete slot ${slot.name}`"
              >
                <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
                </svg>
              </button>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- ── Create slot modal ──────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="showCreate" class="modal-overlay" @click.self="showCreate = false">
          <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="create-slot-title">
            <div class="modal-header">
              <h2 id="create-slot-title" class="modal-title">New slot</h2>
              <button class="modal-close" type="button" @click="showCreate = false" aria-label="Close">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>

            <div class="modal-body">
              <!-- Name -->
              <div class="field">
                <label class="field-label" for="slot-name">Slot name <span class="req">*</span></label>
                <input id="slot-name" v-model="createForm.name" class="field-input" :class="{ 'field-error': createErrors.name }" placeholder="e.g. primary, embed, stt" autocomplete="off" spellcheck="false" />
                <p v-if="createErrors.name" class="field-err">{{ createErrors.name }}</p>
                <p class="field-hint">Lowercase letters, digits, hyphens. Used as systemd unit name.</p>
              </div>

              <!-- Type -->
              <div class="field">
                <label class="field-label" for="slot-type">Slot type</label>
                <select id="slot-type" v-model="createForm.type" class="field-input">
                  <option v-for="t in SLOT_TYPES" :key="t" :value="t">{{ t }}</option>
                </select>
              </div>

              <!-- Backend -->
              <div class="field">
                <label class="field-label" for="slot-backend">Backend</label>
                <select id="slot-backend" v-model="createForm.backend" class="field-input">
                  <option v-for="b in BACKENDS" :key="b" :value="b">{{ b }}</option>
                </select>
              </div>

              <!-- Model (filtered by backend, hardware-aware) -->
              <div class="field">
                <label class="field-label" for="slot-model">Initial model</label>
                <template v-if="compatibleCreateModels.length === 0">
                  <div class="empty-models">
                    <p class="empty-models-msg">No models compatible with backend <code class="mono">{{ createForm.backend }}</code>.</p>
                    <button type="button" class="btn-ghost btn-sm" @click="gotoAddModel">Add a model →</button>
                  </div>
                </template>
                <template v-else>
                  <select id="slot-model" v-model="createForm.model" class="field-input" @change="onCreateModelChange">
                    <option value="">None (load later)</option>
                    <option v-for="m in compatibleCreateModels" :key="m.id" :value="m.id">
                      {{ m.name ?? m.id }}
                      {{ m.size_gb ? `— ${m.size_gb}GB` : '' }}
                      {{ modelFit(m) === true ? '✓ fits' : modelFit(m) === false ? '✗ may not fit' : '' }}
                    </option>
                  </select>
                  <p v-if="availMemMb" class="field-hint">{{ (availMemMb / 1024).toFixed(1) }}GB available. ✓ = fits, ✗ = may exceed memory.</p>
                </template>
              </div>

              <!-- Context size -->
              <div class="field">
                <label class="field-label" for="slot-ctx">Context size (tokens)</label>
                <input id="slot-ctx" v-model.number="createForm.ctx_size" @input="ctxSizeDirtyNew = true" type="number" min="256" max="131072" step="512" class="field-input" :class="{ 'field-error': createErrors.ctx_size }" />
                <p v-if="createSelectedModel?.metadata?.context_length" class="field-hint">max {{ createSelectedModel.metadata.context_length }}</p>
                <p v-if="createErrors.ctx_size" class="field-err">{{ createErrors.ctx_size }}</p>
              </div>

              <!-- Port -->
              <div class="field">
                <label class="field-label" for="slot-port">Port (optional)</label>
                <input id="slot-port" v-model="createForm.port" class="field-input" :class="{ 'field-error': createErrors.port }" placeholder="Auto-assign if blank" />
                <p v-if="createErrors.port" class="field-err">{{ createErrors.port }}</p>
              </div>

              <!-- Auto-start -->
              <label class="field-check">
                <input type="checkbox" v-model="createForm.auto_start" />
                Auto-start this slot when hal0 starts
              </label>
            </div>

            <div class="modal-footer">
              <button class="btn-ghost" type="button" @click="showCreate = false" :disabled="creating">Cancel</button>
              <button class="btn-primary" type="button" @click="submitCreate" :disabled="creating">
                <span v-if="creating" class="spinner" aria-hidden="true" />
                {{ creating ? 'Creating…' : 'Create slot' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Edit slot drawer ───────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="editingSlot" class="modal-overlay" @click.self="editingSlot = null">
          <div class="modal-box" role="dialog" aria-modal="true" :aria-labelledby="'edit-slot-title'">
            <div class="modal-header">
              <h2 id="edit-slot-title" class="modal-title">Edit slot: {{ editingSlot.name }}</h2>
              <button class="modal-close" type="button" @click="editingSlot = null" aria-label="Close">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>
            <div class="modal-body">
              <!-- Read-only provider + port summary -->
              <div class="readonly-row">
                <div class="ro-cell">
                  <span class="ro-label">Provider</span>
                  <span class="ro-value mono">{{ editingSlot.type ?? '—' }}</span>
                </div>
                <div class="ro-cell">
                  <span class="ro-label">
                    Port
                    <span class="restart-icon" title="Restart required to apply">⟳</span>
                  </span>
                  <span class="ro-value mono">{{ editingSlot.port ?? '—' }}</span>
                </div>
              </div>

              <!-- Backend -->
              <div class="field">
                <label class="field-label" for="edit-backend">Backend</label>
                <select id="edit-backend" v-model="editForm.backend" class="field-input">
                  <option v-for="b in BACKENDS" :key="b" :value="b">{{ b }}</option>
                </select>
              </div>

              <!-- Model (filtered by backend) -->
              <div class="field">
                <label class="field-label" for="edit-model">Model</label>
                <template v-if="compatibleEditModels.length === 0">
                  <div class="empty-models">
                    <p class="empty-models-msg">No models compatible with backend <code class="mono">{{ editForm.backend }}</code>.</p>
                    <button type="button" class="btn-ghost btn-sm" @click="gotoAddModel">Add a model →</button>
                  </div>
                </template>
                <template v-else>
                  <select id="edit-model" v-model="editForm.model" class="field-input" @change="onEditModelChange">
                    <option value="">None</option>
                    <option v-for="m in compatibleEditModels" :key="m.id" :value="m.id">
                      {{ m.name ?? m.id }}{{ m.size_gb ? ` — ${m.size_gb}GB` : '' }} {{ modelFitLabel(m) }}
                    </option>
                  </select>
                </template>
              </div>

              <!-- Context size -->
              <div class="field">
                <label class="field-label" for="edit-ctx">
                  Context size (tokens)
                  <span class="restart-icon" title="Restart required to apply">⟳</span>
                </label>
                <input id="edit-ctx" v-model.number="editForm.ctx_size" @input="ctxSizeDirty = true" type="number" min="256" max="131072" step="512" class="field-input" />
                <p v-if="editSelectedModel?.metadata?.context_length" class="field-hint">max {{ editSelectedModel.metadata.context_length }}</p>
              </div>

              <label class="field-check">
                <input type="checkbox" v-model="editForm.auto_start" />
                Auto-start
              </label>

              <!-- ── Advanced disclosure ──────────────────────── -->
              <button
                type="button"
                class="adv-toggle"
                :aria-expanded="showAdvanced"
                @click="showAdvanced = !showAdvanced"
              >
                <span class="adv-caret" :class="{ 'is-open': showAdvanced }">▸</span>
                Advanced
              </button>

              <div v-if="showAdvanced" class="adv-body">
                <h3 class="adv-group">Model</h3>
                <div class="field">
                  <label class="field-label" for="edit-ngl">
                    n_gpu_layers
                    <span class="restart-icon" title="Restart required to apply">⟳</span>
                  </label>
                  <input id="edit-ngl" v-model.number="editForm.n_gpu_layers" type="number" min="-1" step="1" class="field-input" />
                  <p class="field-hint">-1 = offload all layers</p>
                </div>
                <div class="field">
                  <label class="field-label" for="edit-rope">rope_freq_base</label>
                  <input id="edit-rope" v-model.number="editForm.rope_freq_base" type="number" min="0" step="1" class="field-input" />
                  <p class="field-hint">0 = use model default</p>
                </div>

                <h3 class="adv-group">Server</h3>
                <div class="field">
                  <label class="field-label" for="edit-workers">workers</label>
                  <input id="edit-workers" v-model.number="editForm.workers" type="number" min="1" step="1" class="field-input" />
                </div>
                <div class="field">
                  <label class="field-label" for="edit-idle">idle_timeout_s</label>
                  <input id="edit-idle" v-model.number="editForm.idle_timeout_s" type="number" min="0" step="30" class="field-input" />
                  <p class="field-hint">0 = disable idle unload</p>
                </div>
                <div class="field">
                  <label class="field-label" for="edit-extra">extra_args</label>
                  <textarea id="edit-extra" v-model="editForm.extra_args" rows="2" class="field-input mono" placeholder="--threads 4 --batch-size 512"></textarea>
                </div>

                <!-- Effective flags preview -->
                <div class="field">
                  <label class="field-label">Effective flags (preview)</label>
                  <textarea readonly rows="2" class="field-input mono field-readonly" :value="effectiveFlagsPreview" aria-label="Merged launcher flags"></textarea>
                  <p class="field-hint">Slot flags override model defaults on collision; <code class="mono">--lora</code> / <code class="mono">--draft-model</code> / <code class="mono">--override-kv</code> append.</p>
                </div>
              </div>

              <p class="field-hint">Changes take effect on next load/restart unless flagged ⟳.</p>
            </div>
            <div class="modal-footer">
              <button
                v-if="editingSlot && !BUILTIN_SLOTS.has(editingSlot.name)"
                class="btn-ghost edit-delete"
                type="button"
                :disabled="editing"
                @click="() => { const s = editingSlot; editingSlot = null; deletingSlot = s }"
              >
                Delete slot
              </button>
              <button class="btn-ghost" type="button" @click="editingSlot = null" :disabled="editing">Cancel</button>
              <button class="btn-primary" type="button" @click="submitEdit" :disabled="editing">
                <span v-if="editing" class="spinner" aria-hidden="true" />
                {{ editing ? 'Saving…' : 'Save changes' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Restart-required confirm ───────────────────────────── -->
    <ConfirmDialog
      :open="!!pendingRestartSlot"
      :title="`Restart slot &quot;${pendingRestartSlot?.name ?? ''}&quot;?`"
      message="Some changes you made require a restart to take effect (ctx_size, n_gpu_layers, port). Restart now to apply them?"
      confirm-label="Restart slot"
      :loading="restarting"
      @update:open="(v) => { if (!v) pendingRestartSlot = null }"
      @confirm="confirmRestart"
      @cancel="pendingRestartSlot = null"
    />

    <!-- ── Logs drawer ────────────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="logsSlot" class="modal-overlay" @click.self="closeLogs">
          <div class="modal-box modal-wide" role="dialog" aria-modal="true" aria-labelledby="logs-title">
            <div class="modal-header">
              <h2 id="logs-title" class="modal-title">Logs: {{ logsSlot?.name }}</h2>
              <button class="modal-close" type="button" @click="closeLogs" aria-label="Close">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>
            <div class="logs-box" aria-live="polite" aria-label="Log output">
              <div v-if="logsLoading" class="logs-loading">Loading…</div>
              <div v-else class="logs-content">
                <div v-for="(line, i) in logsLines" :key="i" class="log-line">{{ line }}</div>
                <div v-if="logsLines.length === 0" class="logs-empty">No log output</div>
              </div>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

    <!-- ── Delete confirm ─────────────────────────────────────── -->
    <ConfirmDialog
      :open="!!deletingSlot"
      :title="`Delete slot &quot;${deletingSlot?.name ?? ''}&quot;?`"
      :message="(deletingSlot?.status === 'running' ? 'This slot is currently running and will be stopped. ' : '') + 'This permanently deletes the slot configuration. Model files are not affected.'"
      danger
      confirm-label="Delete slot"
      :loading="deleting"
      @update:open="(v) => { if (!v) deletingSlot = null }"
      @confirm="confirmDelete"
      @cancel="deletingSlot = null"
    />
  </div>
</template>

<style scoped>
.slots-page { display: flex; flex-direction: column; min-height: 100%; }
.page-body  { padding: 20px 24px; display: flex; flex-direction: column; gap: 8px; }

/* ── Slot list ────────────────────────────────────────────────── */
.slots-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}
.slot-cell { display: flex; flex-direction: column; gap: 6px; }
.slot-pending { display: flex; flex-wrap: wrap; gap: 4px; padding: 0 2px; }
.slots-list { display: flex; flex-direction: column; gap: 4px; }

.slot-row {
  display: grid;
  grid-template-columns: 200px 1fr auto;
  grid-template-rows: auto auto;
  align-items: center;
  gap: 0 16px;
  padding: 12px 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  transition: border-color 0.1s;
}
.slot-row:hover { border-color: var(--color-border-hi); }
.slot-row-error { border-color: color-mix(in oklch, var(--color-danger) 40%, var(--color-border)); }

.slot-row-skel { padding: 16px; }

.slot-left {
  display: flex;
  align-items: center;
  gap: 10px;
  grid-column: 1;
  min-width: 0;
}

.state-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}
.state-running { background: var(--color-success); box-shadow: 0 0 6px -1px var(--color-success); }
.state-idle    { background: var(--color-warning); }
.state-error   { background: var(--color-danger); }
.state-offline { background: var(--color-fg-faint); }

.slot-names { display: flex; flex-direction: column; min-width: 0; }
.slot-name  { font-size: 13px; font-weight: 600; color: var(--color-fg); }
.slot-model { font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.text-faint { color: var(--color-fg-faint) !important; }

/* Chips */
.slot-chips { display: flex; align-items: center; gap: 6px; grid-column: 2; flex-wrap: wrap; }
.chip {
  font-family: var(--font-mono);
  font-size: 10.5px;
  padding: 2px 7px;
  border-radius: 4px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-faint);
  white-space: nowrap;
}
.chip-port { color: var(--color-fg-muted); }
.chip-type { background: var(--color-surface-2); }
.chip-state-state-running { background: color-mix(in oklch, var(--color-success) 15%, transparent); color: var(--color-success); border-color: color-mix(in oklch, var(--color-success) 30%, transparent); }
.chip-state-state-idle    { background: color-mix(in oklch, var(--color-warning) 15%, transparent); color: var(--color-warning); border-color: color-mix(in oklch, var(--color-warning) 30%, transparent); }
.chip-state-state-error   { background: color-mix(in oklch, var(--color-danger) 15%, transparent);  color: var(--color-danger);  border-color: color-mix(in oklch, var(--color-danger) 30%, transparent); }
.chip-state-state-offline { }

/* Row error banner */
.row-error {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 10px;
  margin-top: 8px;
  border-radius: var(--radius);
  background: color-mix(in oklch, var(--color-danger) 10%, transparent);
  border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent);
  color: var(--color-danger);
  font-size: 12px;
}
.row-error-dismiss { background: transparent; border: none; color: inherit; cursor: pointer; font-size: 16px; line-height: 1; padding: 0; }

/* Actions */
.slot-actions {
  display: flex;
  align-items: center;
  gap: 6px;
  grid-column: 3;
  flex-wrap: wrap;
  justify-content: flex-end;
}

.model-select {
  padding: 5px 8px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 12px;
  cursor: pointer;
  max-width: 200px;
}
.model-select:focus { outline: none; border-color: var(--color-border-hi); }

.btn-act {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 5px 11px;
  border-radius: var(--radius);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.1s, color 0.1s, border-color 0.1s;
  white-space: nowrap;
}
.btn-act:disabled { opacity: 0.45; cursor: not-allowed; }

.btn-load { background: var(--hal0-accent); color: #000; font-family: var(--font-mono); font-weight: 500; }
.btn-load:hover:not(:disabled) { background: var(--hal0-accent-hover); }

.btn-sm { background: var(--color-surface-2); color: var(--color-fg-muted); border-color: var(--color-border); }
.btn-sm:hover:not(:disabled) { background: var(--color-surface-3); color: var(--color-fg); }

.btn-ghost { background: transparent; color: var(--color-fg-faint); border-color: transparent; }
.btn-ghost:hover:not(:disabled) { background: var(--color-surface-2); color: var(--color-fg-muted); }

.btn-danger-ghost { color: var(--color-danger); border-color: transparent; background: transparent; }
.btn-danger-ghost:hover:not(:disabled) { background: color-mix(in oklch, var(--color-danger) 10%, transparent); }

/* ── Modals ───────────────────────────────────────────────────── */
.modal-overlay {
  position: fixed; inset: 0; z-index: 200;
  background: rgba(0,0,0,0.6); backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  padding: 16px;
}
.modal-box {
  background: var(--color-surface);
  border: 1px solid var(--color-border-hi);
  border-radius: var(--radius-xl);
  width: min(520px, 100%);
  max-height: 90vh;
  display: flex; flex-direction: column;
  box-shadow: 0 24px 64px rgba(0,0,0,0.6);
  overflow: hidden;
}
.modal-sm  { width: min(380px, 100%); }
.modal-wide { width: min(720px, 100%); }

.modal-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
}
.modal-title { font-size: 15px; font-weight: 600; color: var(--color-fg); margin: 0; }
.modal-close {
  width: 28px; height: 28px; border-radius: var(--radius);
  background: transparent; border: 1px solid transparent;
  color: var(--color-fg-faint); cursor: pointer; display: grid; place-items: center;
}
.modal-close:hover { background: var(--color-surface-2); color: var(--color-fg); }

.modal-body { padding: 20px; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; flex: 1; }
.modal-footer {
  padding: 16px 20px;
  border-top: 1px solid var(--color-border);
  display: flex; justify-content: flex-end; gap: 8px;
}

/* ── Form fields ──────────────────────────────────────────────── */
.field { display: flex; flex-direction: column; gap: 5px; }
.field-label { font-size: 12.5px; font-weight: 600; color: var(--color-fg-muted); }
.req { color: var(--color-danger); }
.field-input {
  padding: 7px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-size: 13px;
  outline: none;
  transition: border-color 0.1s;
  box-sizing: border-box;
  width: 100%;
}
.field-input:focus { border-color: var(--color-border-hi); }
.field-error { border-color: var(--color-danger) !important; }
.field-err  { font-size: 11.5px; color: var(--color-danger); margin: 0; }
.field-hint { font-size: 11.5px; color: var(--color-fg-faint); margin: 0; font-family: var(--font-mono); }

.field-check {
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; color: var(--color-fg-muted); cursor: pointer;
}
.field-check input { cursor: pointer; }

.mono { font-family: var(--font-mono); }
.field-readonly {
  opacity: 0.85;
  background: var(--color-surface-3, var(--color-surface-2));
  cursor: default;
}

/* ── Empty-state CTA (zero compatible models) ─────────────── */
.empty-models {
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
  padding: 10px 12px;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}
.empty-models-msg { font-size: 12px; color: var(--color-fg-faint); margin: 0; }

/* ── Read-only provider + port summary ────────────────────── */
.readonly-row {
  display: flex; gap: 16px; padding: 8px 10px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
}
.ro-cell { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.ro-label { font-size: 10.5px; font-weight: 600; color: var(--color-fg-faint); text-transform: uppercase; letter-spacing: 0.04em; display: flex; align-items: center; gap: 4px; }
.ro-value { font-size: 13px; color: var(--color-fg); }

/* ── Restart-required marker ─────────────────────────────── */
.restart-icon {
  display: inline-block;
  margin-left: 4px;
  font-size: 11px;
  color: var(--color-warning, #d97706);
  cursor: help;
  line-height: 1;
}

/* ── Advanced disclosure ──────────────────────────────────── */
.adv-toggle {
  display: flex; align-items: center; gap: 6px;
  padding: 8px 0;
  background: transparent; border: none;
  color: var(--color-fg-muted);
  font-size: 12.5px; font-weight: 600;
  cursor: pointer;
  text-align: left;
}
.adv-toggle:hover { color: var(--color-fg); }
.adv-caret {
  display: inline-block; transition: transform 0.15s;
  font-size: 10px; color: var(--color-fg-faint);
}
.adv-caret.is-open { transform: rotate(90deg); }
.adv-body {
  display: flex; flex-direction: column; gap: 14px;
  padding: 12px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius);
  background: var(--color-surface-2);
}
.adv-group {
  font-size: 11px; font-weight: 700;
  color: var(--color-fg-faint);
  text-transform: uppercase; letter-spacing: 0.06em;
  margin: 0;
}

/* ── Logs drawer ──────────────────────────────────────────────── */
.logs-box {
  flex: 1;
  overflow-y: auto;
  background: oklch(10% 0.01 250);
  padding: 12px 16px;
  min-height: 300px;
  max-height: 60vh;
}
.logs-content { display: flex; flex-direction: column; gap: 1px; }
.log-line {
  font-family: var(--font-mono); font-size: 11.5px;
  color: var(--color-fg-muted);
  white-space: pre-wrap; word-break: break-all;
  padding: 1px 0;
}
.logs-loading, .logs-empty { color: var(--color-fg-faint); font-family: var(--font-mono); font-size: 12px; padding: 8px 0; }

/* ── Shared buttons ───────────────────────────────────────────── */
.btn-primary {
  display: flex; align-items: center; gap: 6px;
  padding: 7px 16px; border-radius: var(--radius);
  background: var(--hal0-accent); color: #000;
  font-family: var(--font-mono);
  font-size: 12px; font-weight: 500; border: none; cursor: pointer;
  transition: background 0.15s;
}
.btn-primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-primary:disabled { opacity: 0.45; cursor: not-allowed; }

.btn-ghost {
  padding: 7px 16px; border-radius: var(--radius);
  border: 1px solid var(--color-border); background: transparent;
  color: var(--color-fg-muted); font-family: var(--font-mono); font-size: 12px; cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }

.edit-delete { margin-right: auto; color: var(--color-danger); }
.edit-delete:hover:not(:disabled) {
  border-color: color-mix(in oklch, var(--color-danger) 50%, var(--color-border));
  background: color-mix(in oklch, var(--color-danger) 10%, transparent);
  color: var(--color-danger);
}
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }

.kbd-hint { font-family: var(--font-mono); font-size: 11px; color: var(--color-fg-faint); }
.kbd-hint kbd { display: inline-grid; place-items: center; min-width: 16px; height: 16px; padding: 0 4px; border-radius: 3px; border: 1px solid var(--color-border-hi); background: var(--color-surface-2); color: var(--color-fg-faint); font-size: 10px; font-family: var(--font-mono); }

.spinner {
  width: 11px; height: 11px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

.fade-enter-active, .fade-leave-active { transition: opacity 0.12s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }

/* ── Mobile: collapse grid to stacked ────────────────────────── */
@media (max-width: 768px) {
  .slot-row { grid-template-columns: 1fr; grid-template-rows: auto auto auto; gap: 10px; }
  .slot-left, .slot-chips, .slot-actions { grid-column: 1; }
  .slot-actions { flex-wrap: wrap; }
}
</style>
