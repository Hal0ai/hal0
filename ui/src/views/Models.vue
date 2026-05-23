<script setup>
/**
 * Models.vue — v2 dashboard /models route.
 *
 * 3-pane layout (slice #171):
 *   - left  : ModelList (filters + sectioned list)
 *   - right-top: ModelDetail (header + recipe + used-by + on-disk + actions)
 *   - right-bottom: DownloadsPane (7-state DownloadRow stack)
 *
 * Below 1080px the layout collapses to list-primary; detail and downloads
 * panes slide in as Drawer overlays so the user always has the catalog
 * one tap away.
 *
 * BannerStack scope="models" mounts at the top — `useBannerStore` drives
 * what shows (hf-gated, disk-full, etc.) from upstream events.
 *
 * Downloads state is owned here. Each row binds to a `usePullJob` instance
 * so multiple pulls run in parallel; SSE-derived state maps onto the
 * canonical 7-state vocabulary the DownloadRow renders.
 *
 * Mock fallback: `/api/models` returns real data when the backend is up;
 * if it's unreachable or returns nothing usable we render the v2 mock
 * catalog so the page is never empty in dev/offline contexts (per
 * issue #166 — `useMock`).
 */
import { ref, computed, onMounted, onUnmounted, reactive } from 'vue'
import { useSystemStore } from '../stores/system.js'
import { useToastsStore } from '../stores/toasts.js'
import { useLemonadeStore } from '../stores/lemonade.js'
import { useBannerStore } from '../stores/banner.js'
import { api, Hal0Error } from '../composables/useApi.js'
import { usePullJob, fmtBytes, fmtSpeed, fmtEta } from '../composables/usePullJob.js'
import { MOCK_DATA } from '../composables/useMock.js'

import PageHeader from '../components/PageHeader.vue'
import BannerStack from '../components/primitives/BannerStack.vue'
import Drawer from '../components/primitives/Drawer.vue'

import ModelList from '../components/models/ModelList.vue'
import ModelDetail from '../components/models/ModelDetail.vue'
import ModelRowSkeleton from '../components/skeletons/ModelRowSkeleton.vue'
import DownloadsPane from '../components/models/DownloadsPane.vue'
import AddByHFModal from '../components/models/AddByHFModal.vue'
import DeleteModelDialog from '../components/models/DeleteModelDialog.vue'

const system = useSystemStore()
const toasts = useToastsStore()
const lemonade = useLemonadeStore()
const banner = useBannerStore()

// ── State ──────────────────────────────────────────────────────────
const models = ref([])
const loading = ref(true)
const error = ref(null)
const selectedId = ref(null)
const showAddByHF = ref(false)
const deletingModel = ref(null)

// Per-model recipe overrides (in-memory; backend wiring is registry
// territory). Hydrated from /api/models/<id> on selection in a future
// pass; for now seeded from a per-runtime baseline so the Edit form has
// something to edit.
const recipes = reactive({})

// Downloads — local registry of usePullJob instances keyed by id.
// Each entry: { id, job, name, repo, files? } — `job` is the composable
// instance, derived state is computed from job state.
const pullJobs = reactive({})

// Responsive drawer state (mobile / <1080px collapse)
const detailDrawerOpen = ref(false)
const downloadsDrawerOpen = ref(false)
const isCompact = ref(false)

// Tracks completed-row removals so we don't double-emit
const removed = reactive(new Set())

// ── Computed selection / mock fallback ────────────────────────────
const selected = computed(() => models.value.find((m) => m.id === selectedId.value) || null)

const recipeForSelected = computed(() => {
  if (!selected.value) return {}
  return recipes[selected.value.id] || defaultRecipe(selected.value)
})

function defaultRecipe(model) {
  if (!model) return {}
  const runtime = (model.runtime || '').toLowerCase()
  if (runtime === 'flm') {
    return {
      ctx_size: 4096,
      flm_args: '--asr 1 --embed 1',
    }
  }
  if (runtime === 'kokoro' || runtime === 'moonshine') {
    return { device: model.device || 'cpu' }
  }
  // llama-cpp default — surface ctx_size + n_gpu_layers + llamacpp_args
  return {
    ctx_size: 8192,
    n_gpu_layers: 99,
    rope_freq_base: 0,
    llamacpp_args: '--parallel 1 --threads 8',
  }
}

const ALL_DOWNLOADS = computed(() => Object.values(pullJobs).filter((d) => !removed.has(d.id)))

// ── Loaders ────────────────────────────────────────────────────────
async function loadModels() {
  loading.value = true
  error.value = null
  try {
    const data = await api('/api/models')
    const list = Array.isArray(data) ? data : (data?.models ?? [])
    if (list.length === 0) {
      // Backend up but empty registry. Surface the mock catalog so the
      // page demonstrates the 3-pane layout in dev. Tag the rows so a
      // future "promoted from mock" badge can find them.
      models.value = MOCK_DATA.models.map((m) => ({ ...m, _mock: true }))
    } else {
      models.value = list
    }
  } catch (e) {
    error.value = e?.message ?? 'failed to load models'
    // Mock fallback so the catalog renders.
    models.value = MOCK_DATA.models.map((m) => ({ ...m, _mock: true }))
  } finally {
    loading.value = false
    if (!selectedId.value && models.value.length) {
      selectedId.value = models.value[0].id
    }
  }
}

async function reattachInFlightPulls() {
  for (const m of models.value) {
    if (!m?.id) continue
    const entry = ensureJobEntry(m.id, { name: m.longName || m.name || m.id, repo: m.repo })
    try {
      await entry.job.reattach(m.id)
    } catch {
      /* swallow — reattach is best-effort */
    }
    if (!entry.job.inFlight.value && !entry.job.terminal.value) {
      // No in-flight job — drop the registry entry so the row doesn't
      // appear in the Downloads pane.
      delete pullJobs[m.id]
    }
  }
}

// ── Pull / job lifecycle ──────────────────────────────────────────
function ensureJobEntry(id, meta = {}) {
  if (!pullJobs[id]) {
    pullJobs[id] = reactive({
      id,
      job: usePullJob(),
      name: meta.name || id,
      repo: meta.repo || null,
      files: meta.files || null,
    })
  }
  removed.delete(id)
  return pullJobs[id]
}

// Map composable state → DownloadRow canonical state.
function mapState(job, manual) {
  if (manual?.cancelled) return 'cancelled'
  if (manual?.paused) return 'paused'
  if (!job) return 'queued'
  const s = job.state?.value ?? job.state
  if (s === 'queued') return 'queued'
  if (s === 'running') return 'pulling'
  if (s === 'completed') return manual?.verified === false ? 'verifying' : 'completed'
  if (s === 'failed') return 'error'
  if (s === 'cancelled') return 'cancelled'
  return 'queued'
}

// Manual overlays — usePullJob doesn't yet expose pause; we model it
// client-side so the row's button vocabulary stays complete.
const manualOverlays = reactive({})

// Test-only fixture downloads (window.__hal0_fixture_downloads). When
// set, replaces the live download UI list — keeps the 7-state DownloadRow
// e2e spec self-contained without needing 7 backend lifecycles.
const fixtureDownloads = ref(null)
if (typeof window !== 'undefined') {
  window.__hal0_setFixtureDownloads = (arr) => { fixtureDownloads.value = arr }
}

const downloadsForUI = computed(() => {
  if (fixtureDownloads.value) return fixtureDownloads.value
  return ALL_DOWNLOADS.value.map((entry) => {
    const overlay = manualOverlays[entry.id] || {}
    const job = entry.job
    const state = mapState(job, overlay)
    const pct = (() => {
      if (state === 'completed') return 100
      if (state === 'cancelled') return job.pct?.value ?? 0
      return job.pct?.value ?? 0
    })()
    const downloaded = fmtBytes(job.downloaded?.value ?? 0)
    const size = fmtBytes(job.total?.value ?? 0)
    const rate = fmtSpeed(job.speedBps?.value ?? 0)
    const eta = fmtEta(job.etaS?.value ?? 0)
    return {
      id: entry.id,
      name: entry.name,
      state,
      pct,
      downloaded,
      size,
      rate,
      eta,
      errorMessage: job.error?.value?.message || null,
      files: entry.files,
    }
  })
})

async function startPullFromHF(payload) {
  // payload: { repo, variant, name, labels, mmproj, size, size_bytes }
  const id = payload.name
  const entry = ensureJobEntry(id, { name: id, repo: payload.repo })
  try {
    await entry.job.start(id, {
      hf_url: payload.repo,
      variant: payload.variant,
      labels: payload.labels,
      mmproj: payload.mmproj,
    })
    toasts.success(`Pulling ${id} · ${payload.size || ''}`.trim())
    // Optimistically insert a row so the user can see what's coming.
    if (!models.value.some((m) => m.id === id)) {
      models.value = [
        ...models.value,
        {
          id,
          longName: id,
          repo: `${payload.repo}:${payload.variant}`,
          ns: 'pulled',
          installed: false,
          labels: payload.labels,
          size: payload.size,
          _pending: true,
        },
      ]
    }
  } catch (e) {
    const msg = e instanceof Hal0Error ? e.message : String(e?.message ?? e)
    toasts.error(`Pull failed: ${msg}`)
    if (e instanceof Hal0Error && e.code === 'hf.gated') {
      banner.show('hf-gated')
    }
  }
}

function startPullFromRow(model) {
  // "Pull" pressed from the detail pane on an uninstalled model.
  if (!model?.id) return
  const entry = ensureJobEntry(model.id, { name: model.longName || model.name || model.id, repo: model.repo })
  entry.job.start(model.id).catch((e) => {
    const msg = e instanceof Hal0Error ? e.message : String(e?.message ?? e)
    toasts.error(`Pull failed: ${msg}`)
  })
}

async function pauseDownload(dl) {
  // No backend pause-API today; tag the row visually + soft-toast.
  manualOverlays[dl.id] = { ...(manualOverlays[dl.id] || {}), paused: true }
  toasts.info(`Pause requested for ${dl.name}`)
}
async function resumeDownload(dl) {
  manualOverlays[dl.id] = { ...(manualOverlays[dl.id] || {}), paused: false }
  toasts.info(`Resume requested for ${dl.name}`)
}
async function cancelDownload(dl) {
  const entry = pullJobs[dl.id]
  if (!entry) return
  manualOverlays[dl.id] = { ...(manualOverlays[dl.id] || {}), cancelled: true }
  try { await entry.job.cancel() } catch { /* surfaced via job.error */ }
}
async function retryDownload(dl) {
  const entry = pullJobs[dl.id]
  if (!entry) return
  manualOverlays[dl.id] = {}
  try { await entry.job.start(dl.id) } catch (e) {
    toasts.error(`Retry failed: ${e?.message ?? e}`)
  }
}
function removeDownload(dl) {
  removed.add(dl.id)
  delete manualOverlays[dl.id]
  // We intentionally leave the usePullJob instance alive for reattach
  // semantics if the user re-selects this model row; it's GC'd on
  // unmount.
}

// ── Detail-pane actions ───────────────────────────────────────────
async function loadModelNow(model) {
  if (!model?.id) return
  // Pick the first compatible idle slot — minimal heuristic for now.
  const compat = system.slots.filter((s) => s.type === model.type || s.kind === model.type)
  const target = compat.find((s) => s.state === 'idle' || s.state === 'ready') || compat[0]
  if (!target) {
    toasts.warning(`No compatible ${model.type || 'llm'} slot found.`)
    return
  }
  try {
    await api(`/api/slots/${encodeURIComponent(target.name)}/swap`, {
      method: 'POST',
      body: JSON.stringify({ model: model.id }),
    })
    toasts.success(`Loading ${model.longName || model.id} into ${target.name}`)
    await system.fetchStatus()
  } catch (e) {
    toasts.error(`Load failed: ${e?.message ?? e}`)
  }
}

function onReveal({ copied, path }) {
  if (copied) toasts.success(`Path copied: ${path}`)
}

function applyRecipe(next) {
  if (!selected.value) return
  recipes[selected.value.id] = next
  toasts.success('Recipe options updated (restart required for ctx_size + backend)')
  banner.show('restart-required')
}

function askDelete(model) {
  if (!model) return
  deletingModel.value = model
}

async function confirmDelete(model) {
  if (!model?.id) {
    deletingModel.value = null
    return
  }
  try {
    await api(`/api/models/${encodeURIComponent(model.id)}`, { method: 'DELETE' })
    models.value = models.value.filter((m) => m.id !== model.id)
    if (selectedId.value === model.id) {
      selectedId.value = models.value[0]?.id || null
    }
    toasts.success(`Deleted ${model.longName || model.id}`)
    await system.fetchStatus()
  } catch (e) {
    toasts.error(`Delete failed: ${e?.message ?? e}`)
  } finally {
    deletingModel.value = null
  }
}

// Selection from list → on compact layouts open the detail drawer.
function selectModel(id) {
  selectedId.value = id
  if (isCompact.value) detailDrawerOpen.value = true
}

// ── Responsive layout watcher ─────────────────────────────────────
let mql = null
function applyMatch(e) { isCompact.value = e.matches }
onMounted(() => {
  if (typeof window !== 'undefined' && window.matchMedia) {
    mql = window.matchMedia('(max-width: 1079px)')
    isCompact.value = mql.matches
    mql.addEventListener?.('change', applyMatch)
  }
  loadModels().then(() => reattachInFlightPulls())
  system.fetchStatus().catch(() => {})
  // Lemonade store is normally inited from App.vue's
  // useNuclearEvictBanner; subscribe defensively so loaded-model state
  // is fresh when the user opens /models directly.
  lemonade.init()
})

onUnmounted(() => {
  if (mql) mql.removeEventListener?.('change', applyMatch)
  lemonade.stop()
})

const hfTokenSet = computed(() => {
  // Heuristic: when the banner store shows 'hf-gated', token is unset.
  // The real source-of-truth would be a settings flag; we don't gate
  // dialog UX on it strictly, just surface auth status in pre-flight.
  return !banner.isActive('hf-gated')
})

const diskFreeBytes = computed(() => {
  const hw = system.hardware
  if (!hw) return null
  const freeMb = hw.disk_free_mb || hw.diskFreeMb
  return typeof freeMb === 'number' ? freeMb * 1024 * 1024 : null
})

const detailIsEmpty = computed(() => !selected.value)
</script>

<template>
  <div class="view models-view">
    <PageHeader eyebrow="Catalog" title="Models">
      <template #actions>
        <button
          type="button"
          class="btn primary add-hf-btn"
          data-test="add-by-hf"
          @click="showAddByHF = true"
        >+ Add by HF coords</button>
      </template>
    </PageHeader>

    <BannerStack scope="models" class="view-banners" />

    <div
      :class="['models-layout', { compact: isCompact }]"
      data-test="models-layout"
    >
      <!-- Initial-load skeleton — slice #175. Render placeholder rows
           while the very first /api/models call is in flight so the
           three-pane layout doesn't shift when results land. -->
      <div
        v-if="loading && models.length === 0"
        class="models-list-skel"
        data-testid="models-list-skeleton"
      >
        <ModelRowSkeleton v-for="i in 6" :key="i" />
      </div>

      <ModelList
        v-else
        :models="models"
        :selected-id="selectedId"
        @update:selected-id="selectModel"
      />

      <!-- Right column: detail + downloads at >=1080px -->
      <div v-if="!isCompact" class="models-right">
        <ModelDetail
          :model="selected"
          :recipe="recipeForSelected"
          :slots="system.slots"
          @load="loadModelNow"
          @reveal="onReveal"
          @delete="askDelete"
          @recipe-update="applyRecipe"
          @pull="startPullFromRow"
        />
        <DownloadsPane
          :downloads="downloadsForUI"
          @pause="pauseDownload"
          @resume="resumeDownload"
          @cancel="cancelDownload"
          @retry="retryDownload"
          @remove="removeDownload"
        />
      </div>

      <!-- Compact-layout floating quick-actions -->
      <div v-else class="compact-actions mono">
        <button
          type="button"
          class="btn ghost sm"
          @click="downloadsDrawerOpen = true"
          data-test="open-downloads-drawer"
        >Downloads · {{ ALL_DOWNLOADS.length }}</button>
        <button
          type="button"
          class="btn ghost sm"
          :disabled="detailIsEmpty"
          @click="detailDrawerOpen = true"
          data-test="open-detail-drawer"
        >Detail</button>
      </div>
    </div>

    <!-- Modals -->
    <AddByHFModal
      :open="showAddByHF"
      :hf-token-set="hfTokenSet"
      :disk-free-bytes="diskFreeBytes"
      @close="showAddByHF = false"
      @pull="startPullFromHF"
    />

    <DeleteModelDialog
      :open="!!deletingModel"
      :model="deletingModel"
      :slots="system.slots"
      @close="deletingModel = null"
      @confirm="confirmDelete"
    />

    <!-- Responsive drawers (mounted only in compact layout to avoid
         duplicating the DownloadsPane / ModelDetail DOM at wide widths). -->
    <template v-if="isCompact">
      <Drawer
        :open="detailDrawerOpen"
        :on-close="() => (detailDrawerOpen = false)"
        title="Model detail"
        :width="520"
      >
        <ModelDetail
          :model="selected"
          :recipe="recipeForSelected"
          :slots="system.slots"
          @load="loadModelNow"
          @reveal="onReveal"
          @delete="askDelete"
          @recipe-update="applyRecipe"
          @pull="startPullFromRow"
        />
      </Drawer>

      <Drawer
        :open="downloadsDrawerOpen"
        :on-close="() => (downloadsDrawerOpen = false)"
        title="Downloads"
        :width="420"
      >
        <DownloadsPane
          :downloads="downloadsForUI"
          @pause="pauseDownload"
          @resume="resumeDownload"
          @cancel="cancelDownload"
          @retry="retryDownload"
          @remove="removeDownload"
        />
      </Drawer>
    </template>
  </div>
</template>

<style scoped>
.models-view { padding: 0; }

.view-banners {
  padding: 12px 24px 0;
  max-width: 1600px;
  margin: 0 auto;
}
.view-banners:empty { padding: 0; }

.add-hf-btn {
  /* PageHeader's #right slot already aligns content; just style the btn */
}
.btn.primary {
  background: var(--accent);
  border-color: var(--accent);
  color: #0a0a0a;
}
.btn.primary:hover { filter: brightness(1.08); }
.btn.primary[disabled] {
  background: transparent;
  border-color: var(--line);
  color: var(--fg-4);
}

.models-layout {
  display: grid;
  grid-template-columns: 320px 1fr;
  gap: 14px;
  align-items: start;
  padding: 16px 24px 32px;
  max-width: 1600px;
  margin: 0 auto;
}
.models-layout.compact {
  grid-template-columns: 1fr;
}

.models-right {
  display: flex;
  flex-direction: column;
  gap: 14px;
  min-width: 0;
}

.compact-actions {
  display: flex;
  gap: 8px;
  padding: 12px 0;
  border-top: 1px dashed var(--line-soft);
  border-bottom: 1px dashed var(--line-soft);
}
</style>
