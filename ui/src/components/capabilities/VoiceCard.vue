<script setup>
/**
 * VoiceCard
 *
 * Capability slot card for the `voice` slot. Two stacked sections (STT /
 * TTS) — each with a status pill + on/off toggle + two cascading
 * dropdowns (model → backend) + live metrics strip. See EmbedCard for
 * the long-form comments — this card is the same shape, just with
 * different capabilities + endpoint sub-labels.
 */
import { computed, ref } from 'vue'
import { useCapabilities } from '../../composables/useCapabilities.js'
import { useSlotMetrics } from '../../composables/useStats.js'
import { useToastsStore } from '../../stores/toasts.js'
import { usePullJob, fmtBytes } from '../../composables/usePullJob.js'
import { useSystemStore } from '../../stores/system.js'
import CapabilityToggle from './CapabilityToggle.vue'

const props = defineProps({
  selection: { type: Object, required: true },
})

const cap = useCapabilities()
const toasts = useToastsStore()
const system = useSystemStore()
const { metrics } = useSlotMetrics()

const CAPS = ['stt', 'tts']
const SLOT_NAME = { stt: 'stt', tts: 'tts' }
const ENDPOINTS = {
  stt: '/v1/audio/transcriptions',
  tts: '/v1/audio/speech',
}

// PR-15: kokoro:cpu disclosure. Plan §1 #2 + ADR-0008 §2 locked
// kokoro:cpu as the only v0.2 TTS recipe — no GPU-Kokoro on Linux
// upstream. The TTS sub-section gets a small "[CPU]" chip + tooltip so
// the constraint is legible without a banner. Hard-coded to
// provider === 'kokoro' per plan; no device-detection logic. GPU TTS
// will land in v0.3.
const CPU_ONLY_TOOLTIP =
  'Kokoro TTS runs on CPU in v0.2. GPU-accelerated TTS is planned for v0.3.'
function isCpuOnly(capability) {
  if (capability !== 'tts') return false
  const provider = (props.selection?.[capability]?.provider || '').toLowerCase()
  return provider === 'kokoro'
}

function portFor(capability) {
  const name = SLOT_NAME[capability]
  return system.slots.find((s) => s.name === name)?.port ?? null
}

const togglePending = ref({ stt: false, tts: false })

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

function modelsFor(capability) {
  return cap.modelsForCapability('voice', capability)
}

function selectedEntry(capability) {
  const s = props.selection?.[capability]
  if (!s?.model) return null
  return modelsFor(capability).find((m) => m.id === s.model) ?? null
}

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

function backendIcon(b) {
  if (b.downloaded !== false) return '◉'
  return b.pullable !== false ? '⬇' : '✕'
}

function backendFor(capability) {
  const s = props.selection?.[capability]
  return s ? cap.backendById(s.backend) : null
}

// One pull job per voice child (stt, tts) so they can race in parallel
// with independent progress strips.
const pull = {
  stt: usePullJob(),
  tts: usePullJob(),
}

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
      await cap.refresh()
    } catch (err) {
      toasts.error(`download "${modelId}" failed: ${err?.message ?? err}`)
      return
    }
  }
  try {
    await cap.setSelection('voice', capability, {
      backend: backendId,
      provider: backend.provider || null,
      model: modelId,
    })
    toasts.success(`voice.${capability} → ${modelId} on ${backendId}`)
  } catch (err) {
    toasts.error(`failed to set ${capability}: ${err?.message ?? err}`)
  }
}

async function onModelChange(capability, ev) {
  const modelId = ev.target.value
  if (!modelId) return
  const entry = modelsFor(capability).find((m) => m.id === modelId)
  if (!entry || entry.backends.length === 0) return
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
    await cap.setSelection('voice', capability, { enabled })
  } catch (err) {
    toasts.error(`toggle failed: ${err?.message ?? err}`)
  } finally {
    togglePending.value[capability] = false
  }
}

function metricsFor(capability) {
  const slotName = SLOT_NAME[capability]
  return slotName ? (metrics.value?.[slotName] ?? null) : null
}

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

function isActive(c) {
  const s = props.selection?.[c]
  return s?.enabled && s?.status !== 'offline'
}

const headerPill = computed(() => {
  const states = CAPS.map((c) => props.selection?.[c]?.status).filter(Boolean)
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
        <h3 class="cap-title">voice</h3>
        <span class="cap-type">capability slot</span>
      </div>
      <span class="cap-pill" :class="headerPill.cls">{{ headerPill.text }}</span>
    </header>

    <section v-for="c in CAPS" :key="c" class="cap-section">
      <div class="cap-section-head">
        <span class="cap-section-head-l">
          <span class="cap-status" :data-state="selection?.[c]?.status || 'offline'">
            <span class="cap-status-dot" />
            {{ selection?.[c]?.status || 'offline' }}
          </span>
          <span class="cap-section-label">{{ c.toUpperCase() }}</span>
          <!-- PR-15: kokoro:cpu disclosure chip on the TTS sub-section.
               Neutral slate hue (info, not warning); the constraint is
               intentional in v0.2. Focusable so keyboard users can see
               the native title= tooltip. aria-label includes the full
               disclosure copy for screen-reader users. -->
          <span
            v-if="isCpuOnly(c)"
            class="cap-cpu-chip"
            data-testid="cpu-only-chip"
            :title="CPU_ONLY_TOOLTIP"
            :aria-label="`CPU-only backend — ${CPU_ONLY_TOOLTIP}`"
            tabindex="0"
          >CPU</span>
          <span class="cap-section-sub">
            {{ ENDPOINTS[c] }}
            <span v-if="portFor(c)" class="cap-section-port">· :{{ portFor(c) }}</span>
          </span>
        </span>
        <CapabilityToggle
          :model-value="!!selection?.[c]?.enabled"
          :loading="togglePending[c]"
          :label="`enable ${c}`"
          @update:model-value="(v) => onToggle(c, v)"
        />
      </div>
      <div class="cap-pickers">
        <select
          class="cap-select cap-select-model"
          :value="selection?.[c]?.model || ''"
          :disabled="togglePending[c] || pull[c].inFlight.value"
          @change="onModelChange(c, $event)"
        >
          <option value="" disabled>pick model…</option>
          <option v-for="m in modelsFor(c)" :key="m.id" :value="m.id">
            {{ m.id }}{{ m.size_gb ? ` — ${m.size_gb} GB` : '' }}
          </option>
        </select>
        <select
          class="cap-select cap-select-backend"
          :value="selection?.[c]?.backend || ''"
          :disabled="togglePending[c] || pull[c].inFlight.value || !selectedEntry(c)"
          @change="onBackendChange(c, $event)"
        >
          <option value="" disabled>backend…</option>
          <option v-for="b in backendOptionsFor(c)" :key="b.id" :value="b.id">
            {{ backendIcon(b) }} {{ b.short }}
          </option>
        </select>
      </div>
      <div v-if="pull[c].inFlight.value" class="cap-pull">
        <div class="cap-pull-bar"><div class="cap-pull-fill" :style="{ width: (pull[c].pct.value ?? 0) + '%' }" /></div>
        <span class="cap-pull-label mono">
          ↓ {{ pull[c].modelId.value }} · {{ pull[c].pct.value ?? 0 }}% · {{ fmtBytes(pull[c].downloaded.value) }} / {{ fmtBytes(pull[c].total.value) }}
        </span>
        <button class="cap-pull-cancel" type="button" @click="pull[c].cancel()">cancel</button>
      </div>
      <div class="cap-meta">
        <span class="cap-chip" :data-backend="backendFor(c)?.id">{{ backendFor(c)?.label || '—' }}</span>
        <span class="cap-meta-item" v-if="selectedEntry(c)?.size_gb">{{ selectedEntry(c).size_gb }} GB</span>
        <span class="cap-meta-item" v-if="backendFor(c)?.multiplex">⚡ shared {{ backendFor(c).label }} process</span>
      </div>
      <div v-if="isActive(c)" class="cap-metrics">
        <div class="cap-metric cap-metric-headline" :class="{ 'cap-metric-na': activeReqs(metricsFor(c)) == null }">
          <span class="cap-metric-v">{{ activeReqs(metricsFor(c)) ?? '—' }}</span>
          <span class="cap-metric-u">active reqs</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': tokRate(metricsFor(c)) == null }">
          <span class="cap-metric-v">{{ tokRate(metricsFor(c)) != null ? Number(tokRate(metricsFor(c))).toFixed(1) : '—' }}</span>
          <span class="cap-metric-u">tok/s</span>
        </div>
        <div class="cap-metric cap-metric-mem">
          <span class="cap-metric-v">{{ fmtMem(mem(metricsFor(c))) }}</span>
          <span class="cap-metric-u">memory</span>
        </div>
        <div class="cap-metric" :class="{ 'cap-metric-na': uptime(metricsFor(c)) == null }">
          <span class="cap-metric-v">{{ fmtUptime(uptime(metricsFor(c))) }}</span>
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

/* PR-15: kokoro:cpu disclosure chip on the TTS sub-section header.
 * Slate / neutral palette so it reads as "info" rather than "warning" —
 * the constraint is intentional in v0.2, not an error state. cursor:
 * help to advertise the tooltip on hover. */
.cap-cpu-chip {
  display: inline-flex;
  align-items: center;
  font-family: var(--font-mono);
  font-size: 9px;
  letter-spacing: 0.08em;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border-hi);
  background: var(--color-surface-3);
  cursor: help;
}
.cap-cpu-chip:focus-visible {
  outline: 1px solid var(--color-accent);
  outline-offset: 2px;
}
</style>
