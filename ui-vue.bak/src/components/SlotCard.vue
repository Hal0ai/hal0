<script setup>
/**
 * SlotCard.vue — v2 instrumented slot card.
 *
 * Mirrors slots.jsx::SlotCard (lines 17-116) from the v2 design source,
 * adapted to the live API surface:
 *   - State-dot motion (halo-glow / pulse) from the slot.status field
 *     (with `slot.state` fallback for the SSE event payload shape).
 *   - Per-type metric strip (llm / embed / rerank / transcription / tts /
 *     image) — derived from props.metrics; KV% renders '—' when null
 *     (the GPU-llama-server gap noted in PR-12 #179).
 *   - ⋯ overflow menu via SlotOverflowMenu.
 *   - Inline model-swap `▾` popover via InlineSwapPopover.
 *
 * Preservation from prior slot card:
 *   (a) `useNuclearEvictBanner` reads `slot.state` SSE events — this
 *       component still consumes `slot.status` AND `slot.state` as
 *       fallback so the dot stays in sync regardless of which channel
 *       the parent updates.
 *   (b) PR-15 [CPU] chip: rendered when provider === 'kokoro'. The
 *       data-testid="cpu-only-chip" + aria-label survive the rewrite
 *       so lemonade-voice-chip.spec.ts keeps passing once it seeds a
 *       kokoro tts slot.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { isSlotServing } from '../composables/useSlotStats.js'
import InlineSwapPopover from './slots/InlineSwapPopover.vue'
import SlotOverflowMenu from './slots/SlotOverflowMenu.vue'
import ErrorSlotCard from './slots/ErrorSlotCard.vue'
import { api } from '../composables/useApi.js'
import { useToastsStore } from '../stores/toasts.js'

const props = defineProps({
  slot: { type: Object, required: true },
  metrics: { type: Object, default: null },
  /** Optional sparkline series for tok/s — { tps: [], pps: [] }. */
  sparkData: { type: Object, default: () => ({ tps: [], pps: [] }) },
  actionLoading: { type: String, default: null },
  /** Optional persistent error message displayed below the card. */
  errorMessage: { type: String, default: null },
})

const emit = defineEmits([
  'action',          // a ∈ 'load' | 'unload' | 'restart' | 'retry' | 're-pull'
  'logs',
  'edit',
  'delete',
  'set-default',
  'copy-curl',
  'swapped',
])

const toasts = useToastsStore()

// Builtin slots cannot be deleted. Match src/hal0/slots/__init__.py.
const BUILTIN_SLOTS = new Set(['primary', 'embed', 'stt', 'tts'])
const isBuiltin = computed(() => BUILTIN_SLOTS.has(props.slot.name))

// ── State-dot motion ───────────────────────────────────────────────────
// Brief flash on lifecycle transition. Respects prefers-reduced-motion.
const transitionFlash = ref(false)
let flashTimer = null
const reducedMotion = typeof window !== 'undefined'
  && window.matchMedia
  && window.matchMedia('(prefers-reduced-motion: reduce)').matches

const status = computed(() => props.slot.status || props.slot.state || 'offline')

watch(status, (next, prev) => {
  if (!next || next === prev) return
  if (reducedMotion) return
  transitionFlash.value = true
  if (flashTimer) clearTimeout(flashTimer)
  flashTimer = setTimeout(() => { transitionFlash.value = false }, 700)
})

const running = computed(() =>
  isSlotServing({ ...props.slot, status: status.value }),
)
const busy = computed(() => !!props.actionLoading)

const dotState = computed(() => {
  if (props.actionLoading) return 'loading'
  const s = status.value
  if (s === 'error' || s === 'failed') return 'error'
  if (s === 'pulling' || s === 'unloading' || s === 'starting' || s === 'warming') return 'loading'
  if (!running.value) return 'offline'
  const reqs = props.metrics?.requests_processing ?? 0
  if (reqs > 0) return 'serving'
  return 'ready'
})

// ── Type / device normalisation ───────────────────────────────────────
const slotType = computed(() => {
  // Prefer the capability tag (`type`) over the provider tag (`kind`)
  // — see NpuBlock.typeOf. Match either when the capability isn't
  // explicit, falling back to the canonical 'llm' for unknowns.
  const candidates = [props.slot.type, props.slot.kind].map((v) => String(v || '').toLowerCase())
  const matches = (...vals) => candidates.some((c) => vals.includes(c))
  if (matches('llama-server', 'flm', 'llm')) return 'llm'
  if (matches('embed', 'embedding')) return 'embedding'
  if (matches('rerank', 'reranking')) return 'reranking'
  if (matches('moonshine', 'whispercpp', 'stt', 'transcription')) return 'transcription'
  if (matches('kokoro', 'vibevoice', 'tts')) return 'tts'
  if (matches('sdcpp', 'image')) return 'image'
  return candidates[0] || 'llm'
})

const slotDevice = computed(() => {
  if (props.slot.device) return props.slot.device
  const backend = String(props.slot.backend || '').toLowerCase()
  if (backend === 'rocm') return 'gpu-rocm'
  if (backend === 'vulkan') return 'gpu-vulkan'
  if (backend === 'cuda') return 'gpu-cuda'
  if (backend === 'flm' || backend === 'npu') return 'npu'
  if (backend === 'cpu') return 'cpu'
  return 'gpu-vulkan'
})

const isDefault = computed(() => props.slot.is_default || props.slot.isDefault || false)
const coresident = computed(() => !!props.slot.coresident_group)

// PR-15: kokoro:cpu disclosure chip. Hard-coded provider check.
const isKokoroCpu = computed(() => {
  const provider = String(props.slot.provider || props.slot.kind || '').toLowerCase()
  return provider === 'kokoro'
})
const cpuOnlyTooltip = 'Kokoro TTS runs on CPU in v0.2. GPU-accelerated TTS is planned for v0.3.'

// ── Model label / inline swap trigger ─────────────────────────────────
const currentModelId = computed(() => {
  const raw = props.slot.model_id || props.slot.model || props.slot.model_name || ''
  return typeof raw === 'string' ? raw : (raw?.default ?? '')
})

const modelLabel = computed(() => {
  const raw = currentModelId.value
  if (!raw) return 'select model…'
  return raw.length > 42 ? raw.slice(0, 40) + '…' : raw
})

const allModels = computed(() => {
  const raw = props.slot.models
  if (Array.isArray(raw) && raw.length > 0) return raw
  return [currentModelId.value].filter(Boolean)
})
const hasMultipleModels = computed(() => allModels.value.length > 1)
const isFlmSlot = computed(() => slotDevice.value === 'npu' || String(props.slot.backend || '').toLowerCase() === 'flm')
// PR-11/PR-12 SlotCard regression note: llama.cpp speculative-decoding
// slots advertise BOTH the main + draft GGUF in /v1/models. Hide the
// chip cluster for that path and use the regular swap trigger.
const showModelChips = computed(() => isFlmSlot.value && hasMultipleModels.value)
const swapAvailable = computed(() => !isFlmSlot.value)

function truncateModel(s) {
  const str = String(s || '')
  return str.length > 26 ? str.slice(0, 24) + '…' : str
}

// ── Per-type metric strip ─────────────────────────────────────────────
const metricsRow = computed(() => {
  const m = props.metrics || {}
  const t = slotType.value
  if (t === 'llm') {
    const tps = m.tokens_per_sec ?? m.toks ?? 0
    const ttftRaw = m.ttft_seconds ?? m.ttft
    const ttft = ttftRaw == null ? null : (typeof ttftRaw === 'number' ? Math.round(ttftRaw * (ttftRaw < 5 ? 1000 : 1)) : ttftRaw)
    const ctx = m.ctx ?? props.slot.context_size ?? props.slot.ctx_size ?? '—'
    const kvRaw = m.kv_cache_usage ?? m.kv ?? null
    // GPU llama-server slots (Lemonade gap): kv stays null. Show '—'.
    const kv = kvRaw == null ? null : (typeof kvRaw === 'number' && kvRaw <= 1 ? Math.round(kvRaw * 100) : kvRaw)
    return [
      { l: 'tok/s', v: Number(tps).toFixed(1), u: '' },
      { l: 'ttft',  v: ttft == null ? '—' : ttft, u: ttft == null ? '' : 'ms', dim: ttft == null },
      { l: 'ctx',   v: ctx, u: '' },
      { l: 'kv',    v: kv == null ? '—' : kv, u: kv == null ? '' : '%', dim: kv == null },
    ]
  }
  if (t === 'embedding') return [
    { l: 'req/min', v: m.rpm ?? 0, u: '' },
    { l: 'p50',     v: m.lat ?? '—', u: m.lat ? 'ms' : '', dim: m.lat == null },
    { l: 'dim',     v: m.dim ?? '—', u: '' },
    { l: 'mem',     v: fmtMem(m), u: memUnit(m) },
  ]
  if (t === 'reranking') return [
    { l: 'req/min', v: m.rpm ?? 0, u: '' },
    { l: 'p50',     v: m.lat ?? '—', u: m.lat ? 'ms' : '', dim: m.lat == null },
    { l: 'max/req', v: m.maxDocs ?? m.max_docs ?? '—', u: '' },
    { l: 'mem',     v: fmtMem(m), u: memUnit(m) },
  ]
  if (t === 'transcription') return [
    { l: 'req/min', v: m.rpm ?? 0, u: '' },
    { l: 'xrt',     v: m.xrt ?? '—', u: '', dim: m.xrt == null },
    { l: 'prec',    v: m.precision ?? 'int8', u: '' },
    { l: 'mem',     v: fmtMem(m), u: memUnit(m) },
  ]
  if (t === 'tts') return [
    { l: 'req/min', v: m.rpm ?? 0, u: '' },
    { l: 'sec/min', v: m.secs ?? '—', u: '', dim: m.secs == null },
    { l: 'voice',   v: m.voice ?? 'af_bella', u: '' },
    { l: 'mem',     v: fmtMem(m), u: memUnit(m) },
  ]
  if (t === 'image') return [
    { l: 'req/min', v: m.rpm ?? 0, u: '' },
    { l: 'avg',     v: m.avg ?? '—', u: m.avg ? 's' : '', dim: m.avg == null },
    { l: 'res',     v: m.res ?? '512²', u: '' },
    { l: 'mem',     v: fmtMem(m), u: memUnit(m) },
  ]
  return []
})

function fmtMem(m) {
  const gb = m.mem ?? (m.mem_rss_mb != null ? m.mem_rss_mb / 1024 : null)
  if (gb == null) return '—'
  if (gb * 1024 < 1000) return Math.round(gb * 1024)
  return Number(gb).toFixed(1)
}
function memUnit(m) {
  const gb = m.mem ?? (m.mem_rss_mb != null ? m.mem_rss_mb / 1024 : null)
  if (gb == null) return ''
  return gb * 1024 < 1000 ? 'MB' : 'GB'
}

// ── Inline swap popover (refactored to a child component) ─────────────
const swapTriggerRef = ref(null)
const swapOpen = ref(false)

function toggleSwap(ev) {
  ev?.stopPropagation()
  if (!swapAvailable.value) return
  swapOpen.value = !swapOpen.value
}

async function onPickSwap(model) {
  const modelId = model.id || model.name
  const slotName = props.slot.name
  if (!modelId || modelId === currentModelId.value) {
    swapOpen.value = false
    return
  }
  swapOpen.value = false
  toasts.success(`swapping ${slotName} → ${model.name || modelId}…`)
  emit('swapped', { slot: slotName, model_id: modelId })
  // Fire-and-forget — backend swap is 10-30s, parent's SSE ring repaints.
  api(`/api/slots/${encodeURIComponent(slotName)}/swap`, {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId }),
  }).then(() => toasts.success(`${slotName} ready on ${model.name || modelId}`))
    .catch((e) => toasts.error(`swap ${slotName} failed: ${e?.message || e}`))
}

// ── Overflow menu ─────────────────────────────────────────────────────
const overflowTriggerRef = ref(null)
const overflowOpen = ref(false)

function toggleOverflow(ev) {
  ev?.stopPropagation()
  overflowOpen.value = !overflowOpen.value
  // close swap if it was open — overflow + swap shouldn't both render
  if (overflowOpen.value) swapOpen.value = false
}

onBeforeUnmount(() => {
  if (flashTimer) clearTimeout(flashTimer)
})
</script>

<template>
  <div :class="['slot', `sc-state-${dotState}`, { serving: dotState === 'serving', 'sc-flash': transitionFlash }]" :data-slot-name="slot.name">
    <!-- Header -->
    <div class="slot-h">
      <span class="dot" :class="dotState" />
      <div class="slot-name mono">
        <span class="nm">{{ slot.name }}</span>
      </div>
      <div class="right">
        <span v-if="isDefault" class="default-badge mono" title="Default for this type">★ default</span>
        <span
          v-if="coresident"
          class="chip coresident"
          data-testid="coresident-badge"
          :title="`coresident — ${slot.coresident_group}`"
        >coresident</span>
        <button
          ref="overflowTriggerRef"
          class="more"
          type="button"
          aria-label="More slot actions"
          @click="toggleOverflow"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
            <circle cx="3" cy="8" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="13" cy="8" r="1.5"/>
          </svg>
        </button>
      </div>
    </div>

    <!-- Overflow menu — popover via primitives/Menu, body-teleported -->
    <SlotOverflowMenu
      :open="overflowOpen"
      :anchor="overflowTriggerRef"
      :slot="slot"
      @close="overflowOpen = false"
      @view-logs="(s) => emit('logs', s)"
      @set-default="(s) => emit('set-default', s)"
      @copy-curl="(s) => emit('copy-curl', s)"
      @delete="(s) => emit('delete', s)"
    />

    <!-- Model row — inline swap trigger OR multi-model chips for FLM. -->
    <div v-if="showModelChips" class="slot-model-chips">
      <span
        v-for="(m, i) in allModels"
        :key="m"
        :class="['sc-model-chip', 'mono', { primary: i === 0 }]"
        :title="m"
      >{{ truncateModel(m) }}</span>
    </div>
    <button
      v-else-if="swapAvailable"
      ref="swapTriggerRef"
      class="slot-model mono"
      type="button"
      :aria-haspopup="'listbox'"
      :aria-expanded="swapOpen"
      :title="currentModelId ? `Swap model (${currentModelId})` : 'Pick a model'"
      @click="toggleSwap"
    >
      <span class="mid">{{ modelLabel }}</span>
      <svg class="chev" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">
        <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6"/>
      </svg>
    </button>
    <div v-else class="slot-model mono" :title="currentModelId">
      <span class="mid">{{ modelLabel }}</span>
    </div>

    <InlineSwapPopover
      :open="swapOpen"
      :anchor="swapTriggerRef"
      :slot="slot"
      :current-model-id="currentModelId"
      @close="swapOpen = false"
      @pick="onPickSwap"
    />

    <!-- Type / device / cpu / state chips -->
    <div class="slot-chips">
      <span class="chip">{{ slotType }}</span>
      <span :class="['chip', 'dev-' + slotDevice.replace('gpu-', '')]">{{ slotDevice }}</span>
      <span
        v-if="isKokoroCpu"
        class="chip cpu-only"
        data-testid="cpu-only-chip"
        :title="cpuOnlyTooltip"
        :aria-label="`CPU-only backend — ${cpuOnlyTooltip}`"
        tabindex="0"
      >CPU</span>
      <span :class="['chip', 'state-' + dotState]">{{ status }}</span>
    </div>

    <!-- Per-type metric strip -->
    <div v-if="metricsRow.length" class="slot-metrics">
      <div v-for="m in metricsRow" :key="m.l" class="slot-met">
        <div class="l">{{ m.l }}</div>
        <div :class="['v', 'mono', 'num', { dim: m.dim }]">
          {{ m.v }}<span v-if="m.u" class="u">{{ m.u }}</span>
        </div>
      </div>
    </div>

    <!-- Actions row -->
    <div class="slot-actions">
      <button
        v-if="running"
        class="btn ghost sm"
        type="button"
        :disabled="busy"
        @click="emit('action', 'restart')"
        title="Restart"
      >Restart</button>
      <button
        v-if="running"
        class="btn ghost sm"
        type="button"
        :disabled="busy"
        @click="emit('action', 'unload')"
        title="Stop"
      >Unload</button>
      <button
        v-else
        class="btn ghost sm"
        type="button"
        :disabled="busy"
        @click="emit('action', 'load')"
        :title="currentModelId ? 'Start' : 'Pick a model to start'"
      >Start</button>
      <button class="btn ghost sm" type="button" @click="emit('edit')" title="Edit">Edit</button>
      <span class="spacer" />
    </div>

    <!-- Persistent error banner appended when load failed -->
    <ErrorSlotCard
      v-if="errorMessage"
      :slot-name="slot.name"
      :message="errorMessage"
      @retry="emit('action', 'retry')"
      @re-pull="emit('action', 're-pull')"
    />
  </div>
</template>

<style scoped>
.slot {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: 16px 16px 14px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  position: relative;
  overflow: hidden;
  transition: border-color 0.12s ease;
}
.slot:hover { border-color: var(--color-border-hi); }
.slot.serving {
  border-color: color-mix(in srgb, var(--hal0-accent) 40%, var(--color-border));
}
.slot.sc-flash { animation: sc-flash 0.7s ease-out; }
@keyframes sc-flash {
  0%   { box-shadow: 0 0 0 0 color-mix(in oklch, var(--hal0-accent), transparent 60%); }
  100% { box-shadow: 0 0 0 8px color-mix(in oklch, var(--hal0-accent), transparent 100%); }
}
@media (prefers-reduced-motion: reduce) {
  .slot.sc-flash { animation: none; }
}

.slot-h { display: flex; align-items: center; gap: 8px; }
.slot-name { display: flex; align-items: center; gap: 8px; font-size: 14.5px; font-weight: 500; color: var(--color-fg); }
.slot-name .nm { letter-spacing: -0.01em; }
.right { margin-left: auto; display: flex; gap: 6px; align-items: center; position: relative; }

.dot {
  width: 8px; height: 8px; border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-fg-faint);
  transition: background 0.15s ease, box-shadow 0.15s ease;
}
.dot.ready    { background: var(--color-success); box-shadow: 0 0 6px var(--color-success); }
.dot.serving  { background: var(--hal0-accent); box-shadow: 0 0 8px var(--hal0-accent); animation: dot-breathe 2.2s ease-in-out infinite; }
.dot.loading  { background: var(--color-warning); animation: dot-pulse 1.2s ease-in-out infinite; }
.dot.error    { background: var(--color-danger); box-shadow: 0 0 6px var(--color-danger); }
.dot.offline  { background: var(--color-fg-faint); opacity: 0.6; }
@keyframes dot-pulse {
  0%, 100% { opacity: 1; }
  50%      { opacity: 0.45; }
}
@keyframes dot-breathe {
  0%, 100% { box-shadow: 0 0 4px 0 color-mix(in oklch, var(--hal0-accent), transparent 60%); }
  50%      { box-shadow: 0 0 10px 2px color-mix(in oklch, var(--hal0-accent), transparent 40%); }
}
@media (prefers-reduced-motion: reduce) {
  .dot.serving, .dot.loading { animation: none; }
}

.default-badge {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 2px 7px;
  border: 1px solid color-mix(in srgb, var(--hal0-accent) 40%, var(--color-border));
  border-radius: 3px;
  font-family: var(--font-mono);
  font-size: 9px;
  color: var(--hal0-accent);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.more {
  width: 22px; height: 22px;
  display: inline-flex; align-items: center; justify-content: center;
  color: var(--color-fg-faint); cursor: pointer; border-radius: var(--radius-sm);
  background: transparent; border: 1px solid transparent;
}
.more:hover { color: var(--color-fg); background: var(--color-surface-2); }

/* Inline swap trigger — single-model fallback. */
.slot-model {
  display: flex; align-items: center; gap: 8px;
  width: 100%;
  text-align: left;
  font-family: var(--font-mono); font-size: 13px;
  color: var(--color-fg-muted);
  padding: 8px 10px;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.slot-model:hover, .slot-model:focus-visible {
  border-color: var(--color-border-hi);
  color: var(--color-fg);
}
.slot-model[aria-expanded="true"] {
  border-color: color-mix(in srgb, var(--hal0-accent) 50%, var(--color-border));
}
.slot-model .mid { flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.slot-model .chev { color: var(--color-fg-faint); }

.slot-model-chips { display: flex; flex-wrap: wrap; gap: 4px; }
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

.slot-chips { display: flex; flex-wrap: wrap; gap: 5px; }
.chip {
  font-family: var(--font-mono);
  font-size: 10px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-surface-2);
  color: var(--color-fg-muted);
  border: 1px solid var(--color-border);
  letter-spacing: 0.04em;
}
.chip.coresident {
  color: rgba(200, 150, 255, 0.95);
  border-color: rgba(200, 150, 255, 0.30);
  background: rgba(200, 150, 255, 0.06);
}
.chip.cpu-only {
  color: var(--color-fg-muted);
  border-color: var(--color-border-hi);
  background: var(--color-surface-3);
  cursor: help;
}
.chip.cpu-only:focus-visible {
  outline: 1px solid var(--hal0-accent);
  outline-offset: 2px;
}
/* Device tinting */
.chip.dev-rocm   { color: #ff8a65; border-color: color-mix(in oklch, #ff8a65, transparent 60%); background: color-mix(in oklch, #ff8a65, transparent 88%); }
.chip.dev-vulkan { color: #4fc3f7; border-color: color-mix(in oklch, #4fc3f7, transparent 60%); background: color-mix(in oklch, #4fc3f7, transparent 88%); }
.chip.dev-cuda   { color: #aed581; border-color: color-mix(in oklch, #aed581, transparent 60%); background: color-mix(in oklch, #aed581, transparent 88%); }
.chip.dev-npu    { color: rgba(200, 150, 255, 0.95); border-color: rgba(200, 150, 255, 0.30); background: rgba(200, 150, 255, 0.06); }
.chip.dev-cpu    { color: var(--color-fg-muted); }

/* State chip tinting */
.chip.state-serving { color: var(--hal0-accent); border-color: color-mix(in srgb, var(--hal0-accent) 40%, transparent); }
.chip.state-ready   { color: var(--color-success); border-color: color-mix(in oklch, var(--color-success), transparent 60%); }
.chip.state-error   { color: var(--color-danger); border-color: color-mix(in oklch, var(--color-danger), transparent 60%); }
.chip.state-loading { color: var(--color-warning); border-color: color-mix(in oklch, var(--color-warning), transparent 60%); }
.chip.state-offline { opacity: 0.7; }

.slot-metrics {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 0;
  border-top: 1px solid var(--color-border);
  border-bottom: 1px solid var(--color-border);
  padding: 10px 0;
}
.slot-met {
  padding: 0 12px;
  border-right: 1px solid var(--color-border);
}
.slot-met:last-child { border-right: none; }
.slot-met:first-child { padding-left: 0; }
.slot-met .l {
  font-family: var(--font-mono);
  font-size: 9px;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-fg-faint);
  margin-bottom: 2px;
}
.slot-met .v {
  font-family: var(--font-mono);
  font-size: 15px;
  color: var(--color-fg);
  letter-spacing: -0.02em;
  font-feature-settings: 'tnum' 1;
}
.slot-met .v .u { color: var(--color-fg-faint); font-size: 10px; margin-left: 1px; }
.slot-met .v.dim { color: var(--color-fg-faint); }

.slot-actions { display: flex; gap: 6px; align-items: center; }
.slot-actions .spacer { flex: 1; }

.btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-family: var(--font-mono);
  font-size: 11px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.btn:hover:not(:disabled) {
  background: var(--color-surface-3);
  border-color: var(--color-border-hi);
}
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
.btn.ghost { background: transparent; border-color: transparent; color: var(--color-fg-muted); }
.btn.ghost:hover:not(:disabled) {
  background: var(--color-surface-2);
  color: var(--color-fg);
  border-color: var(--color-border);
}
.btn.sm { padding: 4px 8px; font-size: 10.5px; }
</style>
