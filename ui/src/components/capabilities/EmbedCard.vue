<script setup>
/**
 * EmbedCard
 *
 * Capability slot card for the `embed` slot. Two stacked sections — Embed
 * (text → vector) and Rerank (query+doc → score) — each with:
 *
 *   - A status pill (serving/idle/loading/error/offline) on the left of
 *     the header, driven by `selection.status` from /api/capabilities.
 *   - A CapabilityToggle on the right that POSTs `{enabled: …}` to
 *     /api/capabilities/embed/{child}.
 *   - Two cascading dropdowns: pick a model first, then the backend
 *     dropdown narrows to the backends that model can actually run on.
 *     A POST `{backend, provider, model}` lands on either change.
 *   - A metrics strip wired to useSlotMetrics() — embed → `embed`,
 *     rerank → `embed-rerank` (mirrors the backend's slot naming).
 *
 * The model-first picker replaced an older single-dropdown of all
 * (backend, model) pairs. Two dropdowns prevent the operator from
 * mixing incompatible pairs (e.g. backend=npu + an llama.cpp GGUF)
 * which used to crash the slot at start-up.
 *
 * Selection comes from the singleton useCapabilities() store; the parent
 * just hands us the slice. We call setSelection() ourselves so the
 * optimistic patch + revert lives in one place.
 */
import { computed, ref } from 'vue'
import { useCapabilities } from '../../composables/useCapabilities.js'
import { useSlotMetrics } from '../../composables/useStats.js'
import { useToastsStore } from '../../stores/toasts.js'
import { usePullJob, fmtBytes } from '../../composables/usePullJob.js'
import { useSystemStore } from '../../stores/system.js'
import CapabilityToggle from './CapabilityToggle.vue'

const props = defineProps({
  // { embed: { backend, provider, model, enabled, slot, status }, rerank: {...} }
  selection: { type: Object, required: true },
})

const cap = useCapabilities()
const toasts = useToastsStore()
const system = useSystemStore()
const { metrics } = useSlotMetrics()

// Map { capability → upstream slot name }. Mirrors the backend's slot
// naming convention so the metrics keyed in /api/slots/metrics line up
// with the cards. Keep in sync with the backend agent.
const SLOT_NAME = { embed: 'embed', rerank: 'embed-rerank' }
const ENDPOINTS  = { embed: '/v1/embeddings', rerank: '/v1/rerankings' }

// Port of the upstream slot, surfaced alongside the endpoint so
// operators can curl the slot directly without cross-referencing
// /api/slots.
function portFor(capability) {
  const name = SLOT_NAME[capability]
  return system.slots.find((s) => s.name === name)?.port ?? null
}

// Per-child loading flag for the toggle spinner. Keyed by capability so
// flipping one pill doesn't lock the other.
const togglePending = ref({ embed: false, rerank: false })

function fmtMem(mb) {
  if (mb == null) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(2)} MB`
}

// Compact uptime — "1h23m" / "12m" / "45s". Drops the smaller unit once
// we're over an hour so the cell stays narrow.
function fmtUptime(sec) {
  if (sec == null) return '—'
  const s = Math.floor(sec)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return m ? `${h}h ${m}m` : `${h}h`
}

// Grouped per-model catalog entries for this child capability.
function modelsFor(capability) {
  return cap.modelsForCapability('embed', capability)
}

// The catalog entry currently selected for this capability — model-level
// metadata such as size_gb and the legal backends list. Null when the
// selection has no model yet.
function selectedEntry(capability) {
  const s = props.selection?.[capability]
  if (!s?.model) return null
  return modelsFor(capability).find((m) => m.id === s.model) ?? null
}

// The per-backend descriptor for the current (model, backend) pair — the
// place where `downloaded` and `pullable` live in the new shape. Null
// when either side of the pair is unset.
function selectedBackend(capability) {
  const entry = selectedEntry(capability)
  const s = props.selection?.[capability]
  if (!entry || !s?.backend) return null
  return entry.backends.find((b) => b.id === s.backend) ?? null
}

// Backend dropdown options for the picked model. Joined with
// cap.backends.value so the option carries display metadata (short
// label, multiplex flag) the catalog row alone doesn't include.
function backendOptionsFor(capability) {
  const entry = selectedEntry(capability)
  if (!entry) return []
  return entry.backends.map((b) => {
    const meta = cap.backendById(b.id) ?? null
    return {
      id: b.id,
      label: meta?.label ?? b.id,
      short: meta?.short ?? b.id,
      provider: b.provider,
      downloaded: b.downloaded,
      pullable: b.pullable,
    }
  })
}

// Three-state download icon for a backend option (`◉` downloaded,
// `⬇` pullable, `✕` upstream-only / no download path).
function backendIcon(b) {
  if (b.downloaded !== false) return '◉'
  return b.pullable !== false ? '⬇' : '✕'
}

// One pull job per capability child — `embed` and `rerank` can race in
// parallel without sharing progress state. Each card exposes its own
// inline progress strip driven off these reactive refs.
const pull = {
  embed: usePullJob(),
  rerank: usePullJob(),
}

// Commit a (model, backend) pair. Pulls first when the chosen pair
// isn't on disk; reverts the optimistic UI if the pull or apply fails.
async function commit(capability, modelId, backendId) {
  const entry = modelsFor(capability).find((m) => m.id === modelId)
  if (!entry) return
  const backend = entry.backends.find((b) => b.id === backendId)
  if (!backend) return
  if (backend.downloaded === false) {
    if (backend.pullable === false) {
      toasts.error(
        `"${modelId}" has no download source (upstream-routed model). ` +
        `Add an hf_repo + hf_filename on the registry entry to enable pull.`,
      )
      return
    }
    try {
      await pull[capability].pullAndWait(modelId)
      // Catalog rows carry `downloaded` snapshots from the last
      // /api/capabilities fetch. Refresh so the icon flips to ◉ on the
      // next render.
      await cap.refresh()
    } catch (err) {
      toasts.error(`download "${modelId}" failed: ${err?.message ?? err}`)
      return
    }
  }
  try {
    await cap.setSelection('embed', capability, {
      backend: backendId,
      provider: backend.provider || null,
      model: modelId,
    })
    toasts.success(`embed.${capability} → ${modelId} on ${backendId}`)
  } catch (err) {
    toasts.error(`failed to set ${capability}: ${err?.message ?? err}`)
  }
}

async function onModelChange(capability, ev) {
  const modelId = ev.target.value
  if (!modelId) return
  const entry = modelsFor(capability).find((m) => m.id === modelId)
  if (!entry || entry.backends.length === 0) return
  // Hold the current backend if the new model can serve it; otherwise
  // snap to the model's first legal backend.
  const current = props.selection?.[capability]?.backend
  const keep = entry.backends.find((b) => b.id === current)
  const backendId = keep?.id ?? entry.backends[0].id
  await commit(capability, modelId, backendId)
}

async function onBackendChange(capability, ev) {
  const backendId = ev.target.value
  if (!backendId) return
  const modelId = props.selection?.[capability]?.model
  if (!modelId) return
  await commit(capability, modelId, backendId)
}

async function onToggle(capability, enabled) {
  togglePending.value[capability] = true
  try {
    await cap.setSelection('embed', capability, { enabled })
  } catch (err) {
    toasts.error(`toggle failed: ${err?.message ?? err}`)
  } finally {
    togglePending.value[capability] = false
  }
}

function backendFor(capability) {
  const s = props.selection?.[capability]
  return s ? cap.backendById(s.backend) : null
}

// ── Live metrics ───────────────────────────────────────────────────────
// Read directly from useSlotMetrics() keyed by the upstream slot name.
// Per-capability row pulls from real backend fields: active reqs from
// `requests_processing`, tok/s from `tokens_per_sec`, memory from
// `mem_rss_mb`, uptime from `uptime_seconds`. Cells without data render
// `—` rather than fake numbers.
function metricsFor(capability) {
  const slotName = SLOT_NAME[capability]
  return slotName ? (metrics.value?.[slotName] ?? null) : null
}

// Concurrent in-flight requests — integer, no rate computation. Backend
// emits `requests_processing` from the slot's queue depth.
function activeReqs(m) {
  if (!m) return null
  return m.requests_processing ?? null
}

// Throughput — only meaningful for token-streaming slots (chat). The
// embed/rerank/voice/img slots don't report it; the cell renders "—".
function tokRate(m) {
  if (!m) return null
  return m.tokens_per_sec ?? m.tps ?? null
}

function mem(m) {
  if (!m) return null
  return m.mem_rss_mb ?? m.vram_mb ?? m.gtt_mb ?? null
}

function uptime(m) {
  if (!m) return null
  return m.uptime_seconds ?? null
}

const embedActive = computed(() => {
  const s = props.selection?.embed
  return s?.enabled && s?.status !== 'offline'
})
const rerankActive = computed(() => {
  const s = props.selection?.rerank
  return s?.enabled && s?.status !== 'offline'
})

const headerPill = computed(() => {
  const states = ['embed', 'rerank']
    .map((c) => props.selection?.[c]?.status)
    .filter(Boolean)
  const serving = states.filter((s) => s === 'serving').length
  if (serving === 2) return { cls: 'cap-pill-ok',   text: '2 children · ready' }
  if (serving === 1) return { cls: 'cap-pill-ok',   text: '1/2 serving' }
  if (states.some((s) => s === 'error')) return { cls: 'cap-pill-err', text: 'error' }
  return { cls: 'cap-pill-idle', text: 'idle' }
})
</script>

<template>
  <div class="cap-card">
    <header class="cap-head">
      <div class="cap-head-l">
        <span class="cap-dot cap-dot-ok" />
        <h3 class="cap-title">embed</h3>
        <span class="cap-type">capability slot</span>
      </div>
      <span class="cap-pill" :class="headerPill.cls">{{ headerPill.text }}</span>
    </header>

    <!-- Embed section -->
    <section class="cap-section">
      <div class="cap-section-head">
        <span class="cap-section-head-l">
          <span class="cap-status" :data-state="selection?.embed?.status || 'offline'">
            <span class="cap-status-dot" />
            {{ selection?.embed?.status || 'offline' }}
          </span>
          <span class="cap-section-label">Embed</span>
          <span class="cap-section-sub">
            {{ ENDPOINTS.embed }}
            <span v-if="portFor('embed')" class="cap-section-port">· :{{ portFor('embed') }}</span>
          </span>
        </span>
        <CapabilityToggle
          :model-value="!!selection?.embed?.enabled"
          :loading="togglePending.embed"
          label="enable embed"
          @update:model-value="(v) => onToggle('embed', v)"
        />
      </div>
      <div class="cap-pickers">
        <select
          class="cap-select cap-select-model"
          :value="selection?.embed?.model || ''"
          :disabled="togglePending.embed || pull.embed.inFlight.value"
          @change="onModelChange('embed', $event)"
        >
          <option value="" disabled>pick model…</option>
          <option
            v-for="m in modelsFor('embed')"
            :key="m.id"
            :value="m.id"
          >{{ m.id }}{{ m.size_gb ? ` — ${m.size_gb} GB` : '' }}</option>
        </select>
        <select
          class="cap-select cap-select-backend"
          :value="selection?.embed?.backend || ''"
          :disabled="togglePending.embed || pull.embed.inFlight.value || !selectedEntry('embed')"
          @change="onBackendChange('embed', $event)"
        >
          <option value="" disabled>backend…</option>
          <option
            v-for="b in backendOptionsFor('embed')"
            :key="b.id"
            :value="b.id"
          >{{ backendIcon(b) }} {{ b.short }}</option>
        </select>
      </div>
      <div v-if="pull.embed.inFlight.value" class="cap-pull">
        <div class="cap-pull-bar"><div class="cap-pull-fill" :style="{ width: (pull.embed.pct.value ?? 0) + '%' }" /></div>
        <span class="cap-pull-label mono">
          ↓ {{ pull.embed.modelId.value }} · {{ pull.embed.pct.value ?? 0 }}% · {{ fmtBytes(pull.embed.downloaded.value) }} / {{ fmtBytes(pull.embed.total.value) }}
        </span>
        <button class="cap-pull-cancel" type="button" @click="pull.embed.cancel()">cancel</button>
      </div>
      <div class="cap-meta">
        <span class="cap-chip" :data-backend="backendFor('embed')?.id">{{ backendFor('embed')?.label || '—' }}</span>
        <span class="cap-meta-item" v-if="selectedEntry('embed')?.size_gb">{{ selectedEntry('embed').size_gb }} GB</span>
        <span class="cap-meta-item" v-if="backendFor('embed')?.multiplex">⚡ shared {{ backendFor('embed').label }} process</span>
      </div>
      <div v-if="embedActive" class="cap-metrics">
        <div class="cap-metric cap-metric-headline" :class="{ 'cap-metric-na': activeReqs(metricsFor('embed')) == null }">
          <span class="cap-metric-v">{{ activeReqs(metricsFor('embed')) ?? '—' }}</span>
          <span class="cap-metric-u">active reqs</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': tokRate(metricsFor('embed')) == null }">
          <span class="cap-metric-v">{{ tokRate(metricsFor('embed')) != null ? Number(tokRate(metricsFor('embed'))).toFixed(1) : '—' }}</span>
          <span class="cap-metric-u">tok/s</span>
        </div>
        <div class="cap-metric cap-metric-mem">
          <span class="cap-metric-v">{{ fmtMem(mem(metricsFor('embed'))) }}</span>
          <span class="cap-metric-u">memory</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': uptime(metricsFor('embed')) == null }">
          <span class="cap-metric-v">{{ fmtUptime(uptime(metricsFor('embed'))) }}</span>
          <span class="cap-metric-u">uptime</span>
        </div>
      </div>
    </section>

    <!-- Rerank section -->
    <section class="cap-section">
      <div class="cap-section-head">
        <span class="cap-section-head-l">
          <span class="cap-status" :data-state="selection?.rerank?.status || 'offline'">
            <span class="cap-status-dot" />
            {{ selection?.rerank?.status || 'offline' }}
          </span>
          <span class="cap-section-label">Rerank</span>
          <span class="cap-section-sub">
            {{ ENDPOINTS.rerank }}
            <span v-if="portFor('rerank')" class="cap-section-port">· :{{ portFor('rerank') }}</span>
          </span>
        </span>
        <CapabilityToggle
          :model-value="!!selection?.rerank?.enabled"
          :loading="togglePending.rerank"
          label="enable rerank"
          @update:model-value="(v) => onToggle('rerank', v)"
        />
      </div>
      <div class="cap-pickers">
        <select
          class="cap-select cap-select-model"
          :value="selection?.rerank?.model || ''"
          :disabled="togglePending.rerank || pull.rerank.inFlight.value"
          @change="onModelChange('rerank', $event)"
        >
          <option value="" disabled>pick model…</option>
          <option
            v-for="m in modelsFor('rerank')"
            :key="m.id"
            :value="m.id"
          >{{ m.id }}{{ m.size_gb ? ` — ${m.size_gb} GB` : '' }}</option>
        </select>
        <select
          class="cap-select cap-select-backend"
          :value="selection?.rerank?.backend || ''"
          :disabled="togglePending.rerank || pull.rerank.inFlight.value || !selectedEntry('rerank')"
          @change="onBackendChange('rerank', $event)"
        >
          <option value="" disabled>backend…</option>
          <option
            v-for="b in backendOptionsFor('rerank')"
            :key="b.id"
            :value="b.id"
          >{{ backendIcon(b) }} {{ b.short }}</option>
        </select>
      </div>
      <div v-if="pull.rerank.inFlight.value" class="cap-pull">
        <div class="cap-pull-bar"><div class="cap-pull-fill" :style="{ width: (pull.rerank.pct.value ?? 0) + '%' }" /></div>
        <span class="cap-pull-label mono">
          ↓ {{ pull.rerank.modelId.value }} · {{ pull.rerank.pct.value ?? 0 }}% · {{ fmtBytes(pull.rerank.downloaded.value) }} / {{ fmtBytes(pull.rerank.total.value) }}
        </span>
        <button class="cap-pull-cancel" type="button" @click="pull.rerank.cancel()">cancel</button>
      </div>
      <div class="cap-meta">
        <span class="cap-chip" :data-backend="backendFor('rerank')?.id">{{ backendFor('rerank')?.label || '—' }}</span>
        <span class="cap-meta-item" v-if="selectedEntry('rerank')?.size_gb">{{ selectedEntry('rerank').size_gb }} GB</span>
        <span class="cap-meta-item" v-if="backendFor('rerank')?.multiplex">⚡ shared {{ backendFor('rerank').label }} process</span>
      </div>
      <div v-if="rerankActive" class="cap-metrics">
        <div class="cap-metric cap-metric-headline" :class="{ 'cap-metric-na': activeReqs(metricsFor('rerank')) == null }">
          <span class="cap-metric-v">{{ activeReqs(metricsFor('rerank')) ?? '—' }}</span>
          <span class="cap-metric-u">active reqs</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': tokRate(metricsFor('rerank')) == null }">
          <span class="cap-metric-v">{{ tokRate(metricsFor('rerank')) != null ? Number(tokRate(metricsFor('rerank'))).toFixed(1) : '—' }}</span>
          <span class="cap-metric-u">tok/s</span>
        </div>
        <div class="cap-metric cap-metric-mem">
          <span class="cap-metric-v">{{ fmtMem(mem(metricsFor('rerank'))) }}</span>
          <span class="cap-metric-u">memory</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': uptime(metricsFor('rerank')) == null }">
          <span class="cap-metric-v">{{ fmtUptime(uptime(metricsFor('rerank'))) }}</span>
          <span class="cap-metric-u">uptime</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* Structural styles only; shared chip/metric/section styles live in
 * CapabilitiesSection.vue as a non-scoped block so all four cards stay
 * visually aligned without duplicate-copy drift. */
.cap-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px 18px;
  display: flex; flex-direction: column; gap: 16px;
}

.cap-head { display: flex; align-items: center; justify-content: space-between; }
.cap-head-l { display: flex; align-items: center; gap: 10px; }
.cap-dot { width: 8px; height: 8px; border-radius: 50%; }
.cap-dot-ok { background: var(--color-success); box-shadow: 0 0 6px -1px var(--color-success); }
.cap-title { font-size: 14px; font-weight: 600; color: var(--color-fg); margin: 0; }
.cap-type { font-family: var(--font-mono); font-size: 10.5px; color: var(--color-fg-faint); }
.cap-pill {
  font-family: var(--font-mono); font-size: 10.5px;
  padding: 2px 8px; border-radius: 999px;
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
}
.cap-pill-ok {
  border-color: color-mix(in oklch, var(--color-success) 30%, transparent);
  background: color-mix(in oklch, var(--color-success) 12%, transparent);
  color: var(--color-success);
}
.cap-pill-idle {
  border-color: color-mix(in oklch, var(--color-warning) 30%, transparent);
  background: color-mix(in oklch, var(--color-warning) 12%, transparent);
  color: var(--color-warning);
}
.cap-pill-err {
  border-color: color-mix(in oklch, var(--color-danger) 30%, transparent);
  background: color-mix(in oklch, var(--color-danger) 8%, transparent);
  color: var(--color-danger);
}

/* Picker layout lives in the shared non-scoped block in
 * CapabilitiesSection.vue so all three cards stay aligned. */
</style>
