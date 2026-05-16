<script setup>
/**
 * SlotCard.vue — a compact slot card for the Slots grid.
 *
 * Modelled on the haloai SlotCard layout (status dot, model line, stats
 * row, sparkline, footer with model picker + lifecycle buttons), redrawn
 * with hal0's CSS-variable design tokens — no Tailwind utility soup so the
 * component reads cleanly and respects the global theme.
 */
import { computed, ref, watch } from 'vue'

const props = defineProps({
  slot: { type: Object, required: true },
  metrics: { type: Object, default: null },
  sparkData: { type: Object, default: () => ({ tps: [], pps: [] }) },
  models: { type: Array, default: () => [] },
  selectedModel: { type: String, default: '' },
  actionLoading: { type: String, default: null },
})

defineEmits(['action', 'select-model', 'logs', 'edit', 'delete'])

// Mirror src/hal0/slots/__init__.py's BUILTIN_SLOTS tuple.  These slots
// are always present and can be restarted / load / unload / swap-modeled,
// but never deleted — SlotManager.delete() raises on them and the UI
// shouldn't offer the affordance.
const BUILTIN_SLOTS = new Set(['primary', 'embed', 'stt', 'tts'])

// Brief flash on lifecycle state transition. Driven by SSE updates from
// Slots.vue's per-slot state stream — the parent updates `slot.status`
// on every transition, so we just watch that field. Respects
// prefers-reduced-motion.
const transitionFlash = ref(false)
let flashTimer = null
const reducedMotion = typeof window !== 'undefined'
  && window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches

watch(() => props.slot.status, (next, prev) => {
  if (!next || next === prev) return
  if (reducedMotion) return
  transitionFlash.value = true
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { transitionFlash.value = false }, 700)
})

const m = computed(() => props.metrics || {})
const isBuiltin = computed(() => BUILTIN_SLOTS.has(props.slot.name))
const running = computed(() => {
  const s = props.slot.status || props.slot.state
  return s === 'running' || s === 'ready' || s === 'serving' || s === 'idle'
})
const busy = computed(() => !!props.actionLoading)

const dotState = computed(() => {
  if (props.actionLoading) return 'loading'
  const s = props.slot.status
  if (s === 'error' || s === 'failed') return 'error'
  if (!running.value) return 'offline'
  if ((m.value.requests_processing || 0) > 0) return 'live'
  return 'idle'
})

const modelLabel = computed(() => {
  const raw = props.slot.model_name || props.slot.model || props.slot.model_id || ''
  const s = typeof raw === 'string' ? raw : (raw?.default ?? '')
  if (!s) return 'no model'
  return s.length > 36 ? s.slice(0, 34) + '…' : s
})

// Multi-model display: FLM slots multiplex chat + embed + asr on one NPU.
// `slot.models` is the full list (chat tag first, then auxiliary tags).
const allModels = computed(() => {
  const raw = props.slot.models
  if (Array.isArray(raw) && raw.length > 0) return raw
  // Fall back to the single-model fields so non-FLM slots still render.
  return [modelLabel.value].filter((m) => m && m !== 'no model')
})
const hasMultipleModels = computed(() => allModels.value.length > 1)
function truncateModel(m) {
  const s = String(m || '')
  return s.length > 28 ? s.slice(0, 26) + '…' : s
}

// Hardware-target indicator. Maps slot backend/provider to a category so
// the chip reads as "this slot runs on the NPU" rather than the abstract
// "slot" kind. Colours follow our token palette (success=green, accent=cyan,
// warning=amber, danger=red); CPU is intentionally muted.
const hardwareTarget = computed(() => {
  const backend = (props.slot.backend || '').toLowerCase()
  const provider = (props.slot.provider || '').toLowerCase()
  // NPU first — FLM and any future amdxdna backends.
  if (backend === 'flm' || provider === 'flm') return { id: 'npu', label: 'NPU' }
  // Discrete GPU paths.
  if (backend === 'cuda' || backend === 'rocm') return { id: 'gpu', label: 'GPU' }
  // Integrated GPU — Vulkan on Strix Halo / generic iGPU.
  if (backend === 'vulkan' || backend === 'metal') return { id: 'igpu', label: 'iGPU' }
  if (backend === 'cpu') return { id: 'cpu', label: 'CPU' }
  // Custom providers that don't declare a backend — best-effort fallback
  // based on provider name. Voice / vibevoice / moonshine run on iGPU+CPU
  // mix in haloai but show as 'iGPU' for now.
  if (provider === 'kokoro' || provider === 'moonshine') return { id: 'igpu', label: 'iGPU' }
  return { id: 'unknown', label: backend || provider || 'slot' }
})

function fmtUptime(s) {
  if (!s || s < 60) return '—'
  const mins = Math.floor(s / 60), hrs = Math.floor(mins / 60), days = Math.floor(hrs / 24)
  if (days > 0) return `${days}d ${hrs % 24}h`
  if (hrs > 0) return `${hrs}h ${mins % 60}m`
  return `${mins}m`
}

const hasHistory = computed(() => {
  const d = props.sparkData
  return !!(d && ((d.tps && d.tps.length > 1) || (d.pps && d.pps.length > 1)))
})

const sparkSvg = computed(() => {
  const d = props.sparkData || {}
  const tps = d.tps || []
  const pps = d.pps || []
  const w = 200, h = 28
  const peak = Math.max(...tps, ...pps, 1)
  const yFor = (v) => h - 2 - ((v || 0) / peak) * (h - 4)
  const baselineY = h - 2

  const buildPath = (pts) => {
    if (!pts || pts.length < 2) return { line: '', area: '' }
    const step = w / (pts.length - 1)
    const ys = pts.map(yFor)
    const line = ys.map((y, i) => `${i ? 'L' : 'M'} ${(i * step).toFixed(1)} ${y.toFixed(1)}`).join(' ')
    const area = `${line} L ${w} ${baselineY} L 0 ${baselineY} Z`
    return { line, area }
  }

  const gen = buildPath(tps)
  const prompt = buildPath(pps)
  const track = `<line x1="0" y1="${baselineY}" x2="${w}" y2="${baselineY}" stroke="rgba(255,255,255,0.06)" stroke-width="1"/>`
  const idA = 'sg' + Math.random().toString(36).slice(2, 6)
  const idB = 'sp' + Math.random().toString(36).slice(2, 6)
  const promptLayer = prompt.line
    ? `<path d="${prompt.area}" fill="url(#${idB})"/><path d="${prompt.line}" stroke="#22d3ee" stroke-width="1" fill="none" opacity="0.7"/>`
    : ''
  const genLayer = gen.line
    ? `<path d="${gen.area}" fill="url(#${idA})"/><path d="${gen.line}" stroke="var(--color-accent)" stroke-width="1.3" fill="none"/>`
    : ''

  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">`
    + `<defs>`
    + `<linearGradient id="${idA}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="currentColor" stop-opacity="0.32"/><stop offset="100%" stop-color="currentColor" stop-opacity="0"/></linearGradient>`
    + `<linearGradient id="${idB}" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="#22d3ee" stop-opacity="0.18"/><stop offset="100%" stop-color="#22d3ee" stop-opacity="0"/></linearGradient>`
    + `</defs>${track}${promptLayer}${genLayer}</svg>`
})
</script>

<template>
  <div class="slot-card" :class="{ 'is-running': running, 'is-busy': busy, 'sc-flash': transitionFlash, [`sc-state-${dotState}`]: true }">
    <!-- Header -->
    <div class="sc-head">
      <span class="sc-dot" />
      <span class="sc-name">{{ slot.name }}</span>
      <span class="sc-port mono">{{ slot.port ? `:${slot.port}` : '—' }}</span>
    </div>

    <div class="sc-models">
      <template v-if="hasMultipleModels">
        <span
          v-for="(m, i) in allModels"
          :key="m"
          class="sc-model-chip mono"
          :class="{ primary: i === 0 }"
          :title="m"
        >{{ truncateModel(m) }}</span>
      </template>
      <template v-else>
        <span class="sc-model mono" :title="modelLabel">{{ modelLabel }}</span>
      </template>
    </div>

    <div class="sc-meta">
      <span class="sc-chip" :class="`hw-${hardwareTarget.id}`">{{ hardwareTarget.label }}</span>
      <span v-if="slot.context_size" class="sc-chip dim">ctx {{ (slot.context_size / 1024).toFixed(0) }}K</span>
    </div>

    <!-- Stats row -->
    <div class="sc-stats">
      <div class="sc-stat">
        <div class="sc-stat-l">T/S</div>
        <div class="sc-stat-v" :class="{ active: running }">{{ (m.tokens_per_sec || 0).toFixed(1) }}</div>
      </div>
      <div class="sc-stat">
        <div class="sc-stat-l">ACT</div>
        <div class="sc-stat-v" :class="{ active: (m.requests_processing || 0) > 0 }">
          {{ m.requests_processing || 0 }}
        </div>
      </div>
      <div class="sc-stat">
        <div class="sc-stat-l">MEM</div>
        <div class="sc-stat-v">{{ (m.mem_rss_mb || 0) > 0 ? ((m.mem_rss_mb / 1024).toFixed(1) + 'G') : '—' }}</div>
      </div>
      <div class="sc-stat">
        <div class="sc-stat-l">UP</div>
        <div class="sc-stat-v">{{ fmtUptime(m.uptime_seconds) }}</div>
      </div>
    </div>

    <!-- Sparkline -->
    <div v-if="running || hasHistory" class="sc-spark" :title="`gen ${(m.tokens_per_sec||0).toFixed(1)} t/s · prompt ${(m.prompt_tokens_per_sec||0).toFixed(1)} t/s`">
      <div class="sc-spark-inner" v-html="sparkSvg"></div>
      <span class="sc-spark-label">tps</span>
    </div>

    <!-- Footer: model picker + actions -->
    <div class="sc-foot">
      <select
        class="sc-select mono"
        :value="selectedModel"
        :disabled="!models || models.length === 0"
        @change="$emit('select-model', $event.target.value)"
      >
        <option value="">{{ (!models || models.length === 0) ? 'no models' : 'pick model…' }}</option>
        <option v-for="mdl in models" :key="mdl.id || mdl" :value="mdl.id || mdl">
          {{ mdl.name || mdl.id || mdl }}
        </option>
      </select>

      <button class="sc-btn" type="button" @click="$emit('edit')" title="Edit">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6"><path stroke-linecap="round" stroke-linejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z"/></svg>
      </button>
      <button class="sc-btn" type="button" @click="$emit('logs')" title="Logs">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6"><path stroke-linecap="round" stroke-linejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"/></svg>
      </button>
      <button v-if="running" class="sc-btn warn" type="button" :disabled="busy" @click="$emit('action', 'restart')" title="Restart">
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6"><path stroke-linecap="round" stroke-linejoin="round" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"/></svg>
      </button>
      <button v-if="running" class="sc-btn danger" type="button" :disabled="busy" @click="$emit('action', 'unload')" title="Stop">
        <svg width="14" height="14" fill="currentColor" viewBox="0 0 24 24"><rect x="6" y="6" width="12" height="12" rx="1"/></svg>
      </button>
      <button v-else class="sc-btn good" type="button" :disabled="busy" @click="$emit('action', selectedModel ? 'load' : 'load')" title="Start">
        <svg width="14" height="14" fill="currentColor" viewBox="0 0 24 24"><path d="M8 5.14v14l11-7-11-7z"/></svg>
      </button>
      <!-- Delete is hidden for built-in slots (primary/embed/stt/tts); the
           backend rejects deleting them and the UI shouldn't suggest it. -->
      <button
        v-if="!isBuiltin"
        class="sc-btn danger"
        type="button"
        :disabled="busy"
        @click="$emit('delete')"
        title="Delete slot"
        :aria-label="`Delete slot ${slot.name}`"
      >
        <svg width="14" height="14" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="1.6"><path stroke-linecap="round" stroke-linejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.slot-card {
  position: relative;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 0;
  overflow: hidden;
  color: var(--color-fg);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
}
/* Running slot is the headline metric — amber top rail (symmetric vs the
   previous left-side inset; reads cleaner in a grid of cards). */
.slot-card.is-running {
  border-color: color-mix(in srgb, var(--hal0-accent) 40%, var(--color-border));
  box-shadow: inset 0 3px 0 var(--hal0-accent);
}
.slot-card.is-busy { opacity: 0.78; }
.slot-card:hover { border-color: var(--color-border-hi); }
.slot-card.is-running:hover { border-color: color-mix(in srgb, var(--hal0-accent) 55%, var(--color-border)); }

.sc-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 10px 12px 4px;
}
.sc-name { font-family: var(--font-mono); font-weight: 600; font-size: 13px; color: var(--color-fg); }
.sc-port { margin-left: auto; font-size: 11px; color: var(--color-fg-faint); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.sc-dot {
  width: 6px;
  height: 6px;
  border-radius: 999px;
  background: var(--color-fg-faint);
  flex-shrink: 0;
}
.sc-state-live .sc-dot   { background: var(--hal0-accent); box-shadow: 0 0 0 4px color-mix(in srgb, var(--hal0-accent) 22%, transparent), 0 0 8px var(--hal0-accent); animation: dot-pulse 1.4s ease-in-out infinite; }

/* SSE-driven state-transition flash. Brief border highlight + dot pop so
   the user sees instant feedback on a transition without the noise of a
   full re-render. Reduced-motion users get nothing (gated in script). */
.slot-card.sc-flash { animation: sc-flash 0.7s ease-out; }
.slot-card.sc-flash .sc-dot { animation: sc-flash-dot 0.7s ease-out; }
@keyframes sc-flash {
  0%   { box-shadow: 0 0 0 2px color-mix(in oklch, var(--color-accent), transparent 60%); }
  100% { box-shadow: 0 0 0 0 transparent; }
}
@keyframes sc-flash-dot {
  0%   { transform: scale(1.6); }
  100% { transform: scale(1); }
}
@media (prefers-reduced-motion: reduce) {
  .slot-card.sc-flash, .slot-card.sc-flash .sc-dot { animation: none; }
}
.sc-state-idle .sc-dot   { background: var(--hal0-accent); opacity: 0.65; }
.sc-state-loading .sc-dot { background: var(--color-warning); animation: dot-pulse 1.4s ease-in-out infinite; }
.sc-state-error .sc-dot  { background: var(--color-danger); }
.sc-state-offline .sc-dot { background: var(--color-fg-faint); }
@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 color-mix(in oklch, currentColor, transparent 70%); }
  50%      { box-shadow: 0 0 0 4px color-mix(in oklch, currentColor, transparent 90%); }
}

.sc-models {
  padding: 0 12px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 17px;
}
.sc-model {
  font-size: 11px;
  color: var(--color-fg-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
}
.sc-model-chip {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border);
  white-space: nowrap;
}
.sc-model-chip.primary {
  color: var(--hal0-accent);
  border-color: color-mix(in srgb, var(--hal0-accent) 40%, transparent);
  background: color-mix(in srgb, var(--hal0-accent) 12%, transparent);
}

.sc-meta {
  padding: 4px 12px 0;
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
}
.sc-chip {
  font-family: var(--font-mono);
  font-size: 9px;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border);
  letter-spacing: 0.04em;
}
.sc-chip.dim { opacity: 0.7; text-transform: lowercase; }

/* Hardware target chip — colour-coded so the user can see at a glance
   which compute path the slot uses. NPU is amber (distinctive), GPU red,
   iGPU green (Strix Halo's default path), CPU muted. */
.sc-chip.hw-npu  {
  color: var(--color-warning);
  border-color: color-mix(in oklch, var(--color-warning), transparent 60%);
  background: color-mix(in oklch, var(--color-warning), transparent 88%);
}
.sc-chip.hw-gpu  {
  color: var(--color-danger);
  border-color: color-mix(in oklch, var(--color-danger), transparent 60%);
  background: color-mix(in oklch, var(--color-danger), transparent 88%);
}
.sc-chip.hw-igpu {
  color: var(--color-success);
  border-color: color-mix(in oklch, var(--color-success), transparent 60%);
  background: color-mix(in oklch, var(--color-success), transparent 88%);
}
.sc-chip.hw-cpu  {
  color: var(--color-fg-muted);
  border-color: var(--color-border-hi);
  background: var(--color-surface-3);
}
.sc-chip.hw-unknown { opacity: 0.6; text-transform: lowercase; }

.sc-stats {
  margin: 8px 12px 0;
  padding: 6px 0;
  border-top: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  text-align: center;
  font-family: var(--font-mono);
  font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1;
}
.sc-stat-l { font-size: 8px; color: var(--hal0-accent); text-transform: uppercase; letter-spacing: 0.08em; opacity: 0.75; }
.sc-stat-v { font-size: 12px; font-weight: 500; color: var(--color-fg-faint); margin-top: 1px; }
.sc-stat-v.active { color: var(--hal0-accent); }

.sc-spark {
  position: relative;
  margin: 4px 12px 0;
  height: 28px;
  background: var(--hal0-bg-sunken);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--hal0-accent);
  overflow: hidden;
}
.sc-spark-inner :deep(svg) { width: 100%; height: 100%; display: block; }
.sc-spark-label {
  position: absolute;
  top: 2px;
  left: 4px;
  font-family: var(--font-mono);
  font-size: 8px;
  color: var(--color-fg-faint);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  pointer-events: none;
}

.sc-foot {
  margin-top: auto;
  padding: 8px 10px;
  border-top: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: 4px;
}
.sc-select {
  flex: 1;
  min-width: 0;
  background: var(--color-surface-2);
  color: var(--color-fg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 4px 6px;
  font-size: 11px;
}
.sc-select:focus { outline: none; border-color: var(--color-accent); }
.sc-select:disabled { opacity: 0.5; }

.sc-btn {
  background: transparent;
  border: 0;
  color: var(--color-fg-faint);
  padding: 4px 5px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.sc-btn:hover:not(:disabled) { background: var(--color-surface-3); color: var(--color-fg); }
.sc-btn:disabled { opacity: 0.4; cursor: default; }
.sc-btn.good:hover:not(:disabled)   { color: var(--color-success); background: color-mix(in oklch, var(--color-success), transparent 90%); }
.sc-btn.warn:hover:not(:disabled)   { color: var(--color-warning); background: color-mix(in oklch, var(--color-warning), transparent 90%); }
.sc-btn.danger:hover:not(:disabled) { color: var(--color-danger);  background: color-mix(in oklch, var(--color-danger), transparent 90%); }
</style>
