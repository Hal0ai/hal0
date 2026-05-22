<script setup>
/**
 * ImgCard
 *
 * Capability slot card for the `img` slot. Single section (image
 * generation); ComfyUI loads VAE / text-encoder / LoRAs internally so
 * they're not exposed as children at v1. Wires to the same
 * /api/capabilities/img/img endpoint as the other capability cards via
 * useCapabilities().setSelection().
 *
 * Metrics show active reqs / tok/s / memory / uptime — operator-visible
 * throughput is rare-event-y for image gen, so a per-hour rollup reads
 * more usefully than the underlying per-sec rate.
 *
 * Picker: model dropdown → backend dropdown (narrowed to that model's
 * legal backends). Same pattern as EmbedCard / VoiceCard.
 */
import { computed, ref } from 'vue'
import { useCapabilities } from '../../composables/useCapabilities.js'
import { useSlotMetrics } from '../../composables/useStats.js'
import { useToastsStore } from '../../stores/toasts.js'
import { usePullJob, fmtBytes } from '../../composables/usePullJob.js'
import { useSystemStore } from '../../stores/system.js'
import CapabilityToggle from './CapabilityToggle.vue'

const props = defineProps({
  // { img: { backend, provider, model, enabled, slot, status } }
  selection: { type: Object, required: true },
})

const cap = useCapabilities()
const toasts = useToastsStore()
const system = useSystemStore()
const { metrics } = useSlotMetrics()

const SLOT_NAME = 'img'
const togglePending = ref(false)

const slotPort = computed(
  () => system.slots.find((s) => s.name === SLOT_NAME)?.port ?? null,
)

function fmtMem(mb) {
  if (mb == null) return '—'
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb.toFixed(2)} MB`
}

function fmtUptime(sec) {
  if (sec == null) return '—'
  const s = Math.floor(sec)
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return m ? `${h}h ${m}m` : `${h}h`
}

function modelsFor() {
  return cap.modelsForCapability('img', 'img')
}

const selectedEntry = computed(() => {
  const s = props.selection?.img
  if (!s?.model) return null
  return modelsFor().find((m) => m.id === s.model) ?? null
})

const backendOptions = computed(() => {
  const entry = selectedEntry.value
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
})

function backendIcon(b) {
  if (b.downloaded !== false) return '◉'
  return b.pullable !== false ? '⬇' : '✕'
}

const pull = usePullJob()

async function commit(modelId, backendId) {
  const entry = modelsFor().find((m) => m.id === modelId)
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
      await pull.pullAndWait(modelId)
      await cap.refresh()
    } catch (err) {
      toasts.error(`download "${modelId}" failed: ${err?.message ?? err}`)
      return
    }
  }
  try {
    await cap.setSelection('img', 'img', {
      backend: backendId,
      provider: backend.provider || null,
      model: modelId,
    })
    toasts.success(`img.img → ${modelId} on ${backendId}`)
  } catch (err) {
    toasts.error(`failed to set img: ${err?.message ?? err}`)
  }
}

async function onModelChange(ev) {
  const modelId = ev.target.value
  if (!modelId) return
  const entry = modelsFor().find((m) => m.id === modelId)
  if (!entry || entry.backends.length === 0) return
  const current = props.selection?.img?.backend
  const keep = entry.backends.find((b) => b.id === current)
  const backendId = keep?.id ?? entry.backends[0].id
  await commit(modelId, backendId)
}

async function onBackendChange(ev) {
  const backendId = ev.target.value
  if (!backendId) return
  const modelId = props.selection?.img?.model
  if (!modelId) return
  await commit(modelId, backendId)
}

async function onToggle(enabled) {
  togglePending.value = true
  try {
    await cap.setSelection('img', 'img', { enabled })
  } catch (err) {
    toasts.error(`toggle failed: ${err?.message ?? err}`)
  } finally {
    togglePending.value = false
  }
}

const selectedBackend = computed(() => {
  const s = props.selection?.img
  return s ? cap.backendById(s.backend) : null
})
const isActive = computed(() => {
  const s = props.selection?.img
  return s?.enabled && s?.status !== 'offline'
})

// Live metrics for the img slot.
const imgMetrics = computed(() => metrics.value?.[SLOT_NAME] ?? null)
function activeReqs(m) {
  if (!m) return null
  return m.requests_processing ?? null
}
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

const headerPill = computed(() => {
  const s = props.selection?.img?.status
  if (s === 'serving') return { cls: 'cap-pill-ok',  text: 'ready' }
  if (s === 'error')   return { cls: 'cap-pill-err', text: 'error' }
  return { cls: 'cap-pill-idle', text: s || 'offline' }
})
</script>

<template>
  <div class="cap-card">
    <header class="cap-head">
      <div class="cap-head-l">
        <span class="cap-dot cap-dot-ok" />
        <h3 class="cap-title">img</h3>
        <span class="cap-type">capability slot</span>
      </div>
      <span class="cap-pill" :class="headerPill.cls">{{ headerPill.text }}</span>
    </header>

    <section class="cap-section">
      <div class="cap-section-head">
        <span class="cap-section-head-l">
          <span class="cap-status" :data-state="selection?.img?.status || 'offline'">
            <span class="cap-status-dot" />
            {{ selection?.img?.status || 'offline' }}
          </span>
          <span class="cap-section-label">Image</span>
          <span class="cap-section-sub">
            /v1/images/generations
            <span v-if="slotPort" class="cap-section-port">· :{{ slotPort }}</span>
          </span>
        </span>
        <CapabilityToggle
          :model-value="!!selection?.img?.enabled"
          :loading="togglePending"
          label="enable img"
          @update:model-value="onToggle"
        />
      </div>
      <div class="cap-pickers">
        <select
          class="cap-select cap-select-model"
          :value="selection?.img?.model || ''"
          :disabled="togglePending || pull.inFlight.value"
          @change="onModelChange"
        >
          <option value="" disabled>pick model…</option>
          <option v-for="m in modelsFor()" :key="m.id" :value="m.id">
            {{ m.id }}{{ m.size_gb ? ` — ${m.size_gb} GB` : '' }}
          </option>
        </select>
        <select
          class="cap-select cap-select-backend"
          :value="selection?.img?.backend || ''"
          :disabled="togglePending || pull.inFlight.value || !selectedEntry"
          @change="onBackendChange"
        >
          <option value="" disabled>backend…</option>
          <option v-for="b in backendOptions" :key="b.id" :value="b.id">
            {{ backendIcon(b) }} {{ b.short }}
          </option>
        </select>
      </div>
      <div v-if="pull.inFlight.value" class="cap-pull">
        <div class="cap-pull-bar"><div class="cap-pull-fill" :style="{ width: (pull.pct.value ?? 0) + '%' }" /></div>
        <span class="cap-pull-label mono">
          ↓ {{ pull.modelId.value }} · {{ pull.pct.value ?? 0 }}% · {{ fmtBytes(pull.downloaded.value) }} / {{ fmtBytes(pull.total.value) }}
        </span>
        <button class="cap-pull-cancel" type="button" @click="pull.cancel()">cancel</button>
      </div>
      <div class="cap-meta">
        <span class="cap-chip" :data-backend="selectedBackend?.id">{{ selectedBackend?.label || '—' }}</span>
        <span class="cap-meta-item" v-if="selectedEntry?.size_gb">{{ selectedEntry.size_gb }} GB</span>
        <span class="cap-meta-item">comfyui handles VAE + text encoder</span>
      </div>
      <div v-if="isActive" class="cap-metrics">
        <div class="cap-metric cap-metric-headline" :class="{ 'cap-metric-na': activeReqs(imgMetrics) == null }">
          <span class="cap-metric-v">{{ activeReqs(imgMetrics) ?? '—' }}</span>
          <span class="cap-metric-u">active reqs</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': tokRate(imgMetrics) == null }">
          <span class="cap-metric-v">{{ tokRate(imgMetrics) != null ? Number(tokRate(imgMetrics)).toFixed(1) : '—' }}</span>
          <span class="cap-metric-u">tok/s</span>
        </div>
        <div class="cap-metric cap-metric-mem">
          <span class="cap-metric-v">{{ fmtMem(mem(imgMetrics)) }}</span>
          <span class="cap-metric-u">memory</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': uptime(imgMetrics) == null }">
          <span class="cap-metric-v">{{ fmtUptime(uptime(imgMetrics)) }}</span>
          <span class="cap-metric-u">uptime</span>
        </div>
      </div>
    </section>
  </div>
</template>

<style scoped>
/* Structural styles only — shared chip/metric/section CSS is in
 * CapabilitiesSection.vue (non-scoped). */
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
  background: color-mix(in oklch, var(--color-danger) 12%, transparent);
  color: var(--color-danger);
}

/* Picker layout lives in the shared non-scoped block in
 * CapabilitiesSection.vue so all three cards stay aligned. */
</style>
