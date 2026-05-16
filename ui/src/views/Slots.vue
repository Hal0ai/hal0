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
import { useRoute } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useToastsStore } from '../stores/toasts.js'
import { useSlotMetrics } from '../composables/useStats.js'
import { api } from '../composables/useApi.js'
import PageHeader from '../components/PageHeader.vue'
import Card from '../components/Card.vue'
import LoadingSkeleton from '../components/LoadingSkeleton.vue'
import EmptyState from '../components/EmptyState.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import SlotCard from '../components/SlotCard.vue'

const { metrics: slotMetrics, history: slotHistory } = useSlotMetrics(2500)

const route  = useRoute()
const system = useSystemStore()
const toasts = useToastsStore()

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

// Edit slot drawer
const editingSlot = ref(null)
const editForm    = ref({})
const editing     = ref(false)

// Delete confirm
const deletingSlot = ref(null)
const deleting     = ref(false)

// Swap model
const swapSlot  = ref(null)
const swapModel = ref('')
const swapping  = ref(false)

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
  const model = slot._selectedModel
  if (!model) { toasts.warning('Select a model first'); return }
  await slotAction(slot.name, 'load', { model })
}

async function doSwap() {
  if (!swapSlot.value || !swapModel.value) return
  const name = swapSlot.value.name
  const modelId = swapModel.value
  swapping.value = true
  try {
    // Step 1: hot-swap the running container (runtime only).  Body shape
    // is {model_id} — must match src/hal0/api/routes/slots.py::swap_slot.
    await api(`/api/slots/${name}/swap`, {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId }),
    })

    // Step 2: persist to /etc/hal0/slots/<name>.toml so the change
    // survives a restart.  Treated as a soft failure — runtime swap
    // already succeeded, so we keep the modal closed and warn instead.
    let persisted = true
    try {
      await api(`/api/install/slots/${name}/model`, {
        method: 'PUT',
        body: JSON.stringify({ model_id: modelId }),
      })
    } catch (persistErr) {
      persisted = false
      toasts.warning(
        `Swapped "${name}" → ${modelId}, but failed to persist: ${persistErr.message}. ` +
        `Change will revert on next restart.`
      )
    }
    if (persisted) {
      toasts.success(`Swapped "${name}" → ${modelId} (persisted)`)
    }
    swapSlot.value = null
    swapModel.value = ''
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    swapping.value = false
  }
}

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
  editForm.value = {
    backend:    slot.backend ?? 'vulkan',
    model:      slot.model ?? '',
    ctx_size:   slot.ctx_size ?? 4096,
    auto_start: slot.auto_start ?? false,
  }
}

async function submitEdit() {
  if (!editingSlot.value) return
  editing.value = true
  try {
    await api(`/api/slots/${editingSlot.value.name}/config`, {
      method: 'PUT',
      body: JSON.stringify(editForm.value),
    })
    toasts.success(`Slot "${editingSlot.value.name}" updated`)
    editingSlot.value = null
    await system.fetchStatus()
  } catch (e) {
    toasts.error(e.message)
  } finally {
    editing.value = false
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
    else if (swapSlot.value) { swapSlot.value = null }
  }
}

// ── Per-slot SSE state subscription ──────────────────────────────────
//
// Wires /api/slots/{name}/state/stream so lifecycle transitions surface
// in <1s instead of waiting for the next 5s /api/status poll. The poll
// stays as a safety net — a missed event during reconnect still gets
// reconciled on the next tick.
//
// `liveStates` is a name → latest-payload map; the augmented `slots`
// computed prefers it over the polled snapshot's `status` field. Stream
// teardown happens on unmount and when a slot disappears from the
// configured list (e.g. delete).
const liveStates = ref({})  // { [slotName]: { state, port, model_id, updated_at } }
const liveStreams = new Map()  // name → EventSource

function openSlotStream(name) {
  if (liveStreams.has(name)) return
  let es
  try {
    es = new EventSource(`/api/slots/${encodeURIComponent(name)}/state/stream`)
  } catch {
    return
  }
  const onFrame = (evt) => {
    try {
      const payload = JSON.parse(evt.data)
      liveStates.value = { ...liveStates.value, [name]: payload }
    } catch { /* ignore malformed frame */ }
  }
  es.addEventListener('state', onFrame)
  es.onmessage = onFrame  // proxies/middleware that strip event names
  es.onerror = () => {
    // EventSource auto-reconnects; the 5s poll covers the gap. We don't
    // close here — closing on every transient error breaks the reconnect.
  }
  liveStreams.set(name, es)
}

function closeSlotStream(name) {
  const es = liveStreams.get(name)
  if (es) {
    es.close()
    liveStreams.delete(name)
  }
  // Drop the stale live state so the polled snapshot wins on reattach.
  if (liveStates.value[name]) {
    const next = { ...liveStates.value }
    delete next[name]
    liveStates.value = next
  }
}

function reconcileSlotStreams() {
  const wanted = new Set(system.slots
    .filter((s) => s._synthetic !== true)  // synthetic upstreams have no SSE
    .map((s) => s.name))
  // Open new
  for (const name of wanted) openSlotStream(name)
  // Close removed
  for (const name of Array.from(liveStreams.keys())) {
    if (!wanted.has(name)) closeSlotStream(name)
  }
}

watch(() => system.slots.map((s) => s.name).join(','), reconcileSlotStreams, { immediate: false })

// ── Augmented slots list (adds _selectedModel for load UI) ────────────
//
// Overlays the SSE-driven `liveStates[name].state` onto the polled
// snapshot's `status` field. Without this, transitions only appear on
// the next 5s poll tick; with it, the SlotCard re-renders within ~1
// network RTT of a state machine transition.
const slots = computed(() =>
  system.slots.map((s) => {
    const live = liveStates.value[s.name]
    const status = live?.state ?? s.status
    return { ...s, status, _selectedModel: s._selectedModel ?? '' }
  })
)

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
  // Open per-slot SSE streams for whatever the polled snapshot already
  // shows. The watcher takes over for subsequent slot list deltas.
  reconcileSlotStreams()
})

onUnmounted(() => {
  window.removeEventListener('keydown', handleKey)
  if (logEs) logEs.close()
  for (const name of Array.from(liveStreams.keys())) closeSlotStream(name)
})

// ── Display helpers ────────────────────────────────────────────────────
const stateClass = (s) => ({
  running: 'state-running', ready: 'state-running', serving: 'state-running',
  idle: 'state-idle', warming: 'state-idle', starting: 'state-idle', pulling: 'state-idle',
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
      <template v-if="system.loading && system.slots.length === 0">
        <Card v-for="i in 3" :key="i" class="slot-row-skel"><LoadingSkeleton :lines="2" /></Card>
      </template>

      <template v-else-if="system.slots.length === 0">
        <Card :padded="false">
          <EmptyState
            title="No slots yet"
            description="Create your first slot to start serving inference requests. Slots map a backend process to a model."
            cta-label="Create slot"
            @cta="showCreate = true"
          />
        </Card>
      </template>

      <template v-else>
        <div class="slots-grid" role="list" aria-label="Inference slots">
          <SlotCard
            v-for="slot in slots"
            :key="slot.name"
            :slot="slot"
            :metrics="slotMetrics[slot.name]"
            :spark-data="slotHistory[slot.name] || { tps: [], pps: [] }"
            :models="models"
            :selected-model="slot._selectedModel"
            :action-loading="actionBusy[slot.name]"
            @select-model="(v) => { const s = slots.find(x => x.name === slot.name); if (s) s._selectedModel = v }"
            @action="(a) => a === 'load' ? doLoad(slot) : slotAction(slot.name, a)"
            @logs="openLogs(slot)"
            @edit="openEdit(slot)"
            @delete="deletingSlot = slot"
          />
        </div>
      </template>

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
                <span class="slot-model" v-if="slot.model">{{ slot.model }}</span>
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
              <template v-if="!slot.model || slot.status === 'offline'">
                <select
                  class="model-select"
                  :value="slot._selectedModel"
                  @change="(e) => { const s = slots.find(x => x.name === slot.name); if (s) s._selectedModel = e.target.value }"
                  :aria-label="`Select model for slot ${slot.name}`"
                >
                  <option value="">Select model…</option>
                  <option v-for="m in models" :key="m.id" :value="m.id">
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
                <button
                  class="btn-act btn-sm"
                  type="button"
                  :disabled="!!actionBusy[slot.name]"
                  @click="() => { swapSlot = slot; swapModel = slot.model ?? '' }"
                  :title="`Swap model in slot ${slot.name}`"
                >
                  Swap
                </button>
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

              <!-- Model (hardware-aware) -->
              <div class="field">
                <label class="field-label" for="slot-model">Initial model</label>
                <select id="slot-model" v-model="createForm.model" class="field-input">
                  <option value="">None (load later)</option>
                  <option v-for="m in models" :key="m.id" :value="m.id">
                    {{ m.name ?? m.id }}
                    {{ m.size_gb ? `— ${m.size_gb}GB` : '' }}
                    {{ modelFit(m) === true ? '✓ fits' : modelFit(m) === false ? '✗ may not fit' : '' }}
                  </option>
                </select>
                <p v-if="availMemMb" class="field-hint">{{ (availMemMb / 1024).toFixed(1) }}GB available. ✓ = fits, ✗ = may exceed memory.</p>
              </div>

              <!-- Context size -->
              <div class="field">
                <label class="field-label" for="slot-ctx">Context size (tokens)</label>
                <input id="slot-ctx" v-model.number="createForm.ctx_size" type="number" min="256" max="131072" step="512" class="field-input" :class="{ 'field-error': createErrors.ctx_size }" />
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
              <div class="field">
                <label class="field-label" for="edit-backend">Backend</label>
                <select id="edit-backend" v-model="editForm.backend" class="field-input">
                  <option v-for="b in BACKENDS" :key="b" :value="b">{{ b }}</option>
                </select>
              </div>
              <div class="field">
                <label class="field-label" for="edit-model">Model</label>
                <select id="edit-model" v-model="editForm.model" class="field-input">
                  <option value="">None</option>
                  <option v-for="m in models" :key="m.id" :value="m.id">
                    {{ m.name ?? m.id }}{{ m.size_gb ? ` — ${m.size_gb}GB` : '' }} {{ modelFitLabel(m) }}
                  </option>
                </select>
              </div>
              <div class="field">
                <label class="field-label" for="edit-ctx">Context size (tokens)</label>
                <input id="edit-ctx" v-model.number="editForm.ctx_size" type="number" min="256" max="131072" step="512" class="field-input" />
              </div>
              <label class="field-check">
                <input type="checkbox" v-model="editForm.auto_start" />
                Auto-start
              </label>
              <p class="field-hint">Changes take effect on next load/restart.</p>
            </div>
            <div class="modal-footer">
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

    <!-- ── Swap model modal ───────────────────────────────────── -->
    <Teleport to="body">
      <Transition name="fade">
        <div v-if="swapSlot" class="modal-overlay" @click.self="swapSlot = null">
          <div class="modal-box modal-sm" role="dialog" aria-modal="true" aria-labelledby="swap-title">
            <div class="modal-header">
              <h2 id="swap-title" class="modal-title">Swap model: {{ swapSlot?.name }}</h2>
              <button class="modal-close" type="button" @click="swapSlot = null" aria-label="Close">
                <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5"><path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/></svg>
              </button>
            </div>
            <div class="modal-body">
              <div class="field">
                <label class="field-label" for="swap-model">New model</label>
                <select id="swap-model" v-model="swapModel" class="field-input">
                  <option value="">Select model…</option>
                  <option v-for="m in models" :key="m.id" :value="m.id">
                    {{ m.name ?? m.id }}{{ m.size_gb ? ` — ${m.size_gb}GB` : '' }} {{ modelFitLabel(m) }}
                  </option>
                </select>
              </div>
              <p class="field-hint">
                The slot will reload with the new model — in-flight requests complete first.
                The change is also written to <code class="mono">/etc/hal0/slots/{{ swapSlot?.name }}.toml</code>
                so it survives a restart.
              </p>
            </div>
            <div class="modal-footer">
              <button class="btn-ghost" type="button" @click="swapSlot = null" :disabled="swapping">Cancel</button>
              <button class="btn-primary" type="button" :disabled="!swapModel || swapping" @click="doSwap">
                <span v-if="swapping" class="spinner" aria-hidden="true" />
                {{ swapping ? 'Swapping…' : 'Swap model' }}
              </button>
            </div>
          </div>
        </div>
      </Transition>
    </Teleport>

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
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: 12px;
}
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
