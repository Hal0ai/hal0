<script setup>
/**
 * Slots.vue — dash v2 /slots view (slice #170).
 *
 * Replaces the v1 list+capability-cards hybrid with the v2 grouped-card
 * layout from slots.jsx. Sections:
 *
 *   Chat   — group=chat / kind=llama-server (non-NPU)
 *   Embed  — embedding / reranking slots
 *   Voice  — transcription / tts slots
 *   Image  — image (sdcpp) slots
 *   NPU    — single rollup card (NpuBlock default · NpuReactor tweak)
 *   Custom — anything that didn't fit a known group
 *
 * Hotkey `N` opens Create modal. Edit drawer is route-driven: navigating
 * to /slots/:name opens the drawer for that slot; closing the drawer
 * goes back to /slots.
 *
 * Skip-path: when the user lands on /slots with zero slots configured
 * (post-FirstRun skip), render six seeded EmptySlotCards + trigger
 * banner #19 (`skip-path`). Each Configure button opens Create
 * pre-filled with {name, type, group, device}.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useSystemStore } from '../stores/system.js'
import { useToastsStore } from '../stores/toasts.js'
import { useAgentStore } from '../stores/agent.js'
import { useTweaksStore } from '../stores/tweaks.js'
import { useBannerStore } from '../stores/banner.js'
import { useSlotMetrics } from '../composables/useStats.js'
import { useEvents } from '../composables/useEvents.js'
import { api } from '../composables/useApi.js'
import BannerStack from '../components/primitives/BannerStack.vue'
import SlotCard from '../components/SlotCard.vue'
import EmptySlotCard from '../components/slots/EmptySlotCard.vue'
import CreateSlotModal from '../components/slots/CreateSlotModal.vue'
import EditSlotDrawer from '../components/slots/EditSlotDrawer.vue'
import NpuBlock from '../components/slots/NpuBlock.vue'
import NpuReactor from '../components/slots/NpuReactor.vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import AgentPendingChip from '../components/agent/AgentPendingChip.vue'
import SlotCardSkeleton from '../components/skeletons/SlotCardSkeleton.vue'
import NpuSubRowSkeleton from '../components/skeletons/NpuSubRowSkeleton.vue'

const route   = useRoute()
const router  = useRouter()
const system  = useSystemStore()
const toasts  = useToastsStore()
const agent   = useAgentStore()
const tweaks  = useTweaksStore()
const banners = useBannerStore()

const { metrics: slotMetrics, history: slotHistory } = useSlotMetrics(2500)

// ── State ──────────────────────────────────────────────────────────────
const actionBusy   = ref({})           // { [slotName]: 'load'|'unload'|... }
const rowErrors    = ref({})           // { [slotName]: errorMessage }
const showCreate   = ref(false)
const createDefaults = ref({})         // pre-filled values for skip-path
const deletingSlot = ref(null)
const deleting     = ref(false)
const models       = ref([])
const hardware     = ref(null)

// ── Group classification (mirrors slots.jsx renderGroup buckets) ──────
const NPU_BACKENDS = new Set(['flm', 'npu'])

function groupFor(slot) {
  if (slot.group) return String(slot.group).toLowerCase()
  const backend = String(slot.backend || '').toLowerCase()
  if (NPU_BACKENDS.has(backend) || slot.device === 'npu') return 'npu'
  // Match either `kind` (concrete provider tag) OR `type` (canonical
  // capability tag). Users like piper-gpu register a custom kind while
  // keeping type='tts' — that should still group into voice.
  const tags = [
    String(slot.type || '').toLowerCase(),
    String(slot.kind || '').toLowerCase(),
  ]
  const has = (...vals) => tags.some((t) => vals.includes(t))
  if (has('llama-server', 'llm', 'flm')) return 'chat'
  if (has('embedding', 'embed', 'reranking', 'rerank')) return 'embed'
  if (has('transcription', 'stt', 'tts', 'kokoro', 'moonshine', 'whispercpp', 'vibevoice')) return 'voice'
  if (has('image', 'sdcpp')) return 'img'
  return 'custom'
}

// ── Live state overlay (SSE ring → polled snapshot) ───────────────────
const events = useEvents()
const liveStates = computed(() => {
  const out = {}
  for (const evt of events.events.value) {
    if (evt?.type !== 'slot.state') continue
    const d = evt.data || {}
    const name = d.slot
      ?? (typeof evt.source === 'string' && evt.source.startsWith('slot:') ? evt.source.slice(5) : null)
    if (!name) continue
    out[name] = { state: d.to ?? d.state, model_id: d.model_id, updated_at: evt.ts }
  }
  return out
})

const allSlots = computed(() => {
  return system.slots.map((s) => {
    const live = liveStates.value[s.name]
    return { ...s, status: live?.state ?? s.status }
  })
})

const grouped = computed(() => {
  const g = { chat: [], embed: [], voice: [], img: [], npu: [], custom: [] }
  for (const s of allSlots.value) {
    const grp = groupFor(s)
    if (g[grp]) g[grp].push(s)
    else g.custom.push(s)
  }
  // Sort by slot.name within each group; primary first if present.
  const order = (a, b) => {
    if (a.name === 'primary') return -1
    if (b.name === 'primary') return 1
    return String(a.name).localeCompare(String(b.name))
  }
  for (const k of Object.keys(g)) g[k].sort(order)
  return g
})

const totalSlots = computed(() => allSlots.value.length)

// ── Edit drawer routing ───────────────────────────────────────────────
const editingSlotName = ref(null)

const editingSlot = computed(() => {
  if (!editingSlotName.value) return null
  return allSlots.value.find((s) => s.name === editingSlotName.value) || null
})

watch(() => route.params.name, (name) => {
  editingSlotName.value = name ? String(name) : null
}, { immediate: true })

function openEdit(slot) {
  router.push({ name: 'slot-detail', params: { name: slot.name } })
}
function closeEdit() {
  router.push({ name: 'slots' })
}

// ── Skip-path 6-card grid + banner #19 ────────────────────────────────
//
// When the user lands on /slots with zero slots configured (e.g. they
// hit the skip button on FirstRun), render six seeded placeholder cards
// and prompt them to either Configure each or re-run the bundle picker.
const SKIP_PATH_SEEDS = Object.freeze([
  { name: 'primary',  type: 'llama-server', group: 'chat',  device: 'gpu-vulkan' },
  { name: 'nano',     type: 'llama-server', group: 'chat',  device: 'gpu-vulkan' },
  { name: 'embed',    type: 'embedding',    group: 'embed', device: 'cpu' },
  { name: 'rerank',   type: 'reranking',    group: 'embed', device: 'cpu' },
  { name: 'stt',      type: 'transcription', group: 'voice', device: 'cpu' },
  { name: 'tts',      type: 'tts',          group: 'voice', device: 'cpu' },
])

const showSkipPath = computed(() => totalSlots.value === 0 && !system.loading && !!system.status)

// Initial-load skeleton: render placeholder cards on first paint, before
// /api/status has ever returned. ``system.loading`` flips true on every
// background poll, so we gate on ``!system.status`` to avoid flashing
// skeletons over already-rendered slots.
const showInitialSkeleton = computed(() => !system.status && system.loading)

watch(showSkipPath, (skip) => {
  if (skip) banners.show('skip-path')
  else banners.dismiss('skip-path')
}, { immediate: true })

function openCreateForSeed(seed) {
  createDefaults.value = { ...seed }
  showCreate.value = true
}

// ── NPU variant from tweaks ───────────────────────────────────────────
const npuVariant = computed(() => tweaks.npuVariant)

// ── Lifecycle actions ─────────────────────────────────────────────────
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
    rowErrors.value[slotName] = e?.message || String(e)
    toasts.error(`${action} ${slotName}: ${e?.message || e}`)
  } finally {
    actionBusy.value[slotName] = null
  }
}

async function confirmDelete() {
  if (!deletingSlot.value) return
  deleting.value = true
  try {
    await api(`/api/slots/${deletingSlot.value.name}`, { method: 'DELETE' })
    toasts.success(`Slot "${deletingSlot.value.name}" deleted`)
    deletingSlot.value = null
    await system.fetchStatus()
    // If the drawer was open for that slot, close it.
    if (editingSlotName.value === deletingSlot.value?.name) closeEdit()
  } catch (e) {
    toasts.error(e?.message || 'delete failed')
  } finally {
    deleting.value = false
  }
}

// ── Keyboard ──────────────────────────────────────────────────────────
function handleKey(ev) {
  if (ev.target instanceof HTMLInputElement || ev.target instanceof HTMLTextAreaElement || ev.target instanceof HTMLSelectElement) return
  if (ev.key === 'n' && !showCreate.value && !editingSlot.value) {
    ev.preventDefault()
    createDefaults.value = {}
    showCreate.value = true
  }
}

// ── Models + hardware ─────────────────────────────────────────────────
async function loadModels() {
  try {
    const data = await api('/api/models')
    models.value = Array.isArray(data) ? data : (data?.models ?? [])
  } catch { models.value = [] }
}
async function loadHardware() {
  try { hardware.value = await api('/api/hardware') }
  catch { hardware.value = system.hardware || {} }
}

onMounted(() => {
  window.addEventListener('keydown', handleKey)
  Promise.all([loadModels(), loadHardware()])
  if (system.slots.length === 0) system.fetchStatus()
})
onBeforeUnmount(() => {
  window.removeEventListener('keydown', handleKey)
  banners.dismiss('skip-path')
})

// ── Helpers ───────────────────────────────────────────────────────────
const existingNames = computed(() => allSlots.value.map((s) => s.name))

function renderSlot(slot) {
  return slot
}

function metricsFor(slot) {
  return slotMetrics.value[slot.name] || null
}
function sparkFor(slot) {
  return slotHistory.value[slot.name] || { tps: [], pps: [] }
}
function errorFor(slot) {
  return rowErrors.value[slot.name] || null
}
</script>

<template>
  <div class="slots-page">
    <!-- View header. The `vh` hook + Press-N hint mirror the v2 vh strip. -->
    <header class="vh">
      <span class="vh-eye mono">Lifecycle</span>
      <h1>Slots</h1>
      <span class="vh-spacer" />
      <span class="kbd-hint mono" aria-hidden="true">Press <kbd>N</kbd> to create</span>
      <button class="btn primary sm" type="button" @click="createDefaults = {}; showCreate = true">
        <svg width="11" height="11" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
          <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v16m8-8H4"/>
        </svg>
        New slot
      </button>
    </header>

    <!-- Banner stack: slot-scoped banners (nuclear-evict, npu-swap, queue,
         drift, model-missing, all-disabled, skip-path) + global. -->
    <BannerStack scope="slots" />

    <!-- ── Initial-load skeleton (slice #175) ────────────────────── -->
    <section v-if="showInitialSkeleton" class="sec-grp" data-testid="slots-skeleton">
      <div class="sec">
        <h2>Loading slots<span class="ct mono">—</span></h2>
        <div class="rule" />
      </div>
      <div class="slots-grid">
        <SlotCardSkeleton v-for="i in 4" :key="i" />
      </div>
      <div class="npu-skel-stack" aria-hidden="true">
        <NpuSubRowSkeleton v-for="i in 3" :key="`npu-${i}`" />
      </div>
    </section>

    <!-- ── Skip-path: 6 seeded EmptySlotCards ────────────────────── -->
    <template v-else-if="showSkipPath">
      <section class="sec-grp">
        <div class="sec">
          <h2>Configure your slots<span class="ct mono">{{ SKIP_PATH_SEEDS.length }}</span></h2>
          <div class="rule" />
        </div>
        <div class="slots-grid">
          <EmptySlotCard
            v-for="seed in SKIP_PATH_SEEDS"
            :key="seed.name"
            v-bind="seed"
            @configure="openCreateForSeed(seed)"
          />
        </div>
      </section>
    </template>

    <!-- ── Grouped slot sections ─────────────────────────────────── -->
    <template v-else>
      <section v-if="grouped.chat.length" class="sec-grp">
        <div class="sec">
          <h2>Chat<span class="ct mono">{{ grouped.chat.length }}</span></h2>
          <div class="rule" />
        </div>
        <div class="slots-grid">
          <div v-for="slot in grouped.chat" :key="slot.name" class="slot-cell">
            <SlotCard
              :slot="renderSlot(slot)"
              :metrics="metricsFor(slot)"
              :spark-data="sparkFor(slot)"
              :action-loading="actionBusy[slot.name]"
              :error-message="errorFor(slot)"
              @action="(a) => slotAction(slot.name, a)"
              @edit="openEdit(slot)"
              @logs="openEdit(slot)"
              @delete="deletingSlot = slot"
              @set-default="(s) => toasts.success(`${s.name} set as default for ${s.type || s.kind}`)"
              @copy-curl="() => toasts.success('curl example copied to clipboard')"
            />
            <div v-if="agent.pendingForResource('slot', slot.name).length" class="slot-pending">
              <AgentPendingChip
                v-for="p in agent.pendingForResource('slot', slot.name)"
                :key="p.id"
                :entry="p"
              />
            </div>
          </div>
        </div>
      </section>

      <section v-if="grouped.embed.length" class="sec-grp">
        <div class="sec">
          <h2>Embed<span class="ct mono">{{ grouped.embed.length }}</span></h2>
          <div class="rule" />
        </div>
        <div class="slots-grid">
          <div v-for="slot in grouped.embed" :key="slot.name" class="slot-cell">
            <SlotCard
              :slot="renderSlot(slot)"
              :metrics="metricsFor(slot)"
              :spark-data="sparkFor(slot)"
              :action-loading="actionBusy[slot.name]"
              :error-message="errorFor(slot)"
              @action="(a) => slotAction(slot.name, a)"
              @edit="openEdit(slot)"
              @logs="openEdit(slot)"
              @delete="deletingSlot = slot"
            />
            <div v-if="agent.pendingForResource('slot', slot.name).length" class="slot-pending">
              <AgentPendingChip
                v-for="p in agent.pendingForResource('slot', slot.name)"
                :key="p.id"
                :entry="p"
              />
            </div>
          </div>
        </div>
      </section>

      <section v-if="grouped.voice.length" class="sec-grp">
        <div class="sec">
          <h2>Voice<span class="ct mono">{{ grouped.voice.length }}</span></h2>
          <div class="rule" />
        </div>
        <div class="slots-grid">
          <div v-for="slot in grouped.voice" :key="slot.name" class="slot-cell">
            <SlotCard
              :slot="renderSlot(slot)"
              :metrics="metricsFor(slot)"
              :spark-data="sparkFor(slot)"
              :action-loading="actionBusy[slot.name]"
              :error-message="errorFor(slot)"
              @action="(a) => slotAction(slot.name, a)"
              @edit="openEdit(slot)"
              @logs="openEdit(slot)"
              @delete="deletingSlot = slot"
            />
            <div v-if="agent.pendingForResource('slot', slot.name).length" class="slot-pending">
              <AgentPendingChip
                v-for="p in agent.pendingForResource('slot', slot.name)"
                :key="p.id"
                :entry="p"
              />
            </div>
          </div>
        </div>
      </section>

      <section v-if="grouped.img.length" class="sec-grp">
        <div class="sec">
          <h2>Image<span class="ct mono">{{ grouped.img.length }}</span></h2>
          <div class="rule" />
        </div>
        <div class="slots-grid">
          <div v-for="slot in grouped.img" :key="slot.name" class="slot-cell">
            <SlotCard
              :slot="renderSlot(slot)"
              :metrics="metricsFor(slot)"
              :spark-data="sparkFor(slot)"
              :action-loading="actionBusy[slot.name]"
              :error-message="errorFor(slot)"
              @action="(a) => slotAction(slot.name, a)"
              @edit="openEdit(slot)"
              @logs="openEdit(slot)"
              @delete="deletingSlot = slot"
            />
            <div v-if="agent.pendingForResource('slot', slot.name).length" class="slot-pending">
              <AgentPendingChip
                v-for="p in agent.pendingForResource('slot', slot.name)"
                :key="p.id"
                :entry="p"
              />
            </div>
          </div>
        </div>
      </section>

      <!-- NPU trio rollup — variant toggled from useTweaksStore.npuVariant. -->
      <section v-if="grouped.npu.length" class="sec-grp">
        <div class="sec">
          <h2>NPU<span class="ct mono">trio · 1 process · 3 roles</span></h2>
          <div class="rule" />
        </div>
        <component
          :is="npuVariant === 'reactor' ? NpuReactor : NpuBlock"
          :slots="grouped.npu"
          @swap-chat="(s) => openEdit(s)"
        />
      </section>

      <section v-if="grouped.custom.length" class="sec-grp">
        <div class="sec">
          <h2>Custom<span class="ct mono">{{ grouped.custom.length }}</span></h2>
          <div class="rule" />
        </div>
        <div class="slots-grid">
          <div v-for="slot in grouped.custom" :key="slot.name" class="slot-cell">
            <SlotCard
              :slot="renderSlot(slot)"
              :metrics="metricsFor(slot)"
              :spark-data="sparkFor(slot)"
              :action-loading="actionBusy[slot.name]"
              :error-message="errorFor(slot)"
              @action="(a) => slotAction(slot.name, a)"
              @edit="openEdit(slot)"
              @logs="openEdit(slot)"
              @delete="deletingSlot = slot"
            />
            <div v-if="agent.pendingForResource('slot', slot.name).length" class="slot-pending">
              <AgentPendingChip
                v-for="p in agent.pendingForResource('slot', slot.name)"
                :key="p.id"
                :entry="p"
              />
            </div>
          </div>
        </div>
      </section>
    </template>

    <!-- ── Modals + Drawers ─────────────────────────────────────── -->
    <CreateSlotModal
      :open="showCreate"
      :existing-names="existingNames"
      :models="models"
      :hardware="hardware || {}"
      :defaults="createDefaults"
      @close="showCreate = false"
      @created="() => system.fetchStatus()"
    />

    <EditSlotDrawer
      :open="!!editingSlot"
      :slot="editingSlot"
      :models="models"
      @close="closeEdit"
      @saved="() => system.fetchStatus()"
      @delete="(s) => { deletingSlot = s; closeEdit() }"
    />

    <ConfirmDialog
      :open="!!deletingSlot"
      :title="`Delete slot &quot;${deletingSlot?.name ?? ''}&quot;?`"
      :message="(deletingSlot?.status === 'running' || deletingSlot?.status === 'ready' ? 'This slot is currently running and will be stopped. ' : '') + 'This permanently deletes the slot configuration. Model files are not affected.'"
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
.slots-page {
  padding: 20px 24px;
  display: flex; flex-direction: column;
  gap: 18px;
  min-height: 100%;
}

.vh {
  display: flex; align-items: center; gap: 12px;
  margin-bottom: 4px;
}
.vh-eye {
  font-size: 10px;
  color: var(--hal0-accent);
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.vh h1 {
  font-size: 22px; font-weight: 500;
  letter-spacing: -0.02em;
  margin: 0;
  color: var(--color-fg);
}
.vh-spacer { flex: 1; }
.kbd-hint {
  font-size: 11px;
  color: var(--color-fg-faint);
}
.kbd-hint kbd {
  display: inline-grid; place-items: center;
  min-width: 16px; height: 16px; padding: 0 4px;
  border: 1px solid var(--color-border-hi);
  background: var(--color-surface-2);
  border-radius: 3px;
  font-family: var(--font-mono); font-size: 10px;
  color: var(--color-fg-faint);
}

.sec-grp { display: flex; flex-direction: column; gap: 12px; }
.sec {
  display: flex; align-items: center; gap: 14px;
  padding: 0;
}
.sec h2 {
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-fg-muted);
  margin: 0;
}
.sec h2 .ct { color: var(--color-fg-faint); margin-left: 6px; font-weight: 400; }
.sec .rule { flex: 1; height: 1px; background: var(--color-border); }

.slots-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 14px;
}
@media (min-width: 1280px) {
  .slots-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (min-width: 1600px) {
  .slots-grid { grid-template-columns: repeat(3, 1fr); }
}

.slot-cell { display: flex; flex-direction: column; gap: 6px; }
.slot-pending { display: flex; flex-wrap: wrap; gap: 4px; padding: 0 2px; }

.btn {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 5px 11px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-family: var(--font-mono); font-size: 11px;
  cursor: pointer;
}
.btn.primary { background: var(--hal0-accent); color: #000; border-color: var(--hal0-accent); }
.btn.primary:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn.sm { padding: 5px 10px; font-size: 11px; }
</style>
