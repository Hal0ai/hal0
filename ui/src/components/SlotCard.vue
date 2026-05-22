<script setup>
/**
 * SlotCard.vue — a compact slot card for the Slots grid.
 *
 * Modelled on the haloai SlotCard layout (status dot, model line, stats
 * row, sparkline, footer with model picker + lifecycle buttons), redrawn
 * with hal0's CSS-variable design tokens — no Tailwind utility soup so the
 * component reads cleanly and respects the global theme.
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { isSlotServing } from '../composables/useSlotStats.js'
import { api } from '../composables/useApi.js'
import { usePullJob, fmtBytes } from '../composables/usePullJob.js'
import { useToastsStore } from '../stores/toasts.js'

const props = defineProps({
  slot: { type: Object, required: true },
  metrics: { type: Object, default: null },
  sparkData: { type: Object, default: () => ({ tps: [], pps: [] }) },
  actionLoading: { type: String, default: null },
})

// `swap` was the interim B2 fallback (parent routed to openEdit). C1 wires
// the inline popover directly to /api/slots/{name}/swap — emit `swapped` on
// success so parents that want to refetch can do so without listening to the
// events ring. The bottom-of-card model `<select>` was removed in favour of
// the inline-trigger popover, so we no longer emit `select-model` either.
const emit = defineEmits(['action', 'logs', 'edit', 'delete', 'swapped'])

const toasts = useToastsStore()

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
// A slot is "running" only when it can actually serve: active state +
// either a loaded model or a self-managed provider (moonshine/kokoro/
// vibevoice serve a baked-in model). A slot stuck in ready/serving
// without a model is shown as not-running, matching the navbar count.
const running = computed(() =>
  isSlotServing({ ...props.slot, status: props.slot.status || props.slot.state }),
)
const busy = computed(() => !!props.actionLoading)

const dotState = computed(() => {
  if (props.actionLoading) return 'loading'
  const s = props.slot.status
  if (s === 'error' || s === 'failed') return 'error'
  if (!running.value) return 'offline'
  if ((m.value.requests_processing || 0) > 0) return 'active'
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

// Backend / runtime identifier — the concrete tech driving the slot
// (vulkan / rocm / cuda / flm / cpu / kokoro / moonshine / …). Surfaced
// next to the hardware-target chip so the user can tell two iGPU slots
// apart at a glance ("iGPU via vulkan" vs "iGPU via kokoro").
const backendTech = computed(() => {
  const backend = (props.slot.backend || '').toLowerCase()
  const provider = (props.slot.provider || '').toLowerCase()
  // Self-managed providers carry their own runtime name in `provider` and
  // a generic backend (often `cpu` or empty), so prefer provider there.
  if (provider && provider !== 'llama-server' && provider !== 'llama.cpp') {
    return { id: provider, label: provider }
  }
  if (backend) return { id: backend, label: backend }
  if (provider) return { id: provider, label: provider }
  return null
})

// -----------------------------------------------------------------------------
// Inline model-swap popover (C1)
// -----------------------------------------------------------------------------
// Module-level cache: one fetch per session, refetched lazily every 30s. All
// SlotCard instances share it so opening five popovers doesn't fire five
// /api/models requests. Stored at module scope (outside setup) on purpose.
const MODEL_CACHE = { data: null, ts: 0 }
const MODEL_TTL_MS = 30_000

async function loadModelsCached() {
  const now = Date.now()
  if (MODEL_CACHE.data && (now - MODEL_CACHE.ts) < MODEL_TTL_MS) {
    return MODEL_CACHE.data
  }
  const data = await api('/api/models')
  MODEL_CACHE.data = Array.isArray(data) ? data : (data?.models || [])
  MODEL_CACHE.ts = now
  return MODEL_CACHE.data
}

// FLM slots multiplex chat/embed/asr — the swap action becomes "edit set"
// which is out of scope for C1. Hide the inline affordance for them; users
// edit the model set through the standard Edit modal.
const isFlmSlot = computed(() => {
  const b = (props.slot.backend || '').toLowerCase()
  const p = (props.slot.provider || '').toLowerCase()
  return b === 'flm' || p === 'flm'
})
const swapAvailable = computed(() => !isFlmSlot.value)

const swapOpen = ref(false)
const swapModels = ref([])
const swapLoading = ref(false)
const swapError = ref('')
const swapSubmitting = ref(false)
const swapIndex = ref(-1)            // keyboard focus index into filteredModels
const swapPlacement = ref('below')   // 'below' | 'above'
const swapPos = ref({ left: 0, top: 0, width: 240 })

const swapTriggerRef = ref(null)
const swapPopoverRef = ref(null)

const currentModelId = computed(() => {
  const raw = props.slot.model_id || props.slot.model || props.slot.model_name || ''
  return typeof raw === 'string' ? raw : (raw?.default ?? '')
})

const filteredModels = computed(() => {
  const slotBackend = (props.slot.backend || '').toLowerCase()
  return (swapModels.value || []).filter((m) => {
    const backends = Array.isArray(m.backends) ? m.backends.map((b) => String(b).toLowerCase()) : []
    // Universal-safe: empty backends list means "compatible with anything"
    // (legacy registry entries pre-A2 migration).
    if (backends.length === 0) return true
    if (!slotBackend) return true
    return backends.includes(slotBackend)
  })
})

// VRAM/RAM fit heuristic — mirrors Slots.vue's modelFit so the popover
// agrees with the create/edit modal. `slot.context_size` is the per-slot ctx
// from config; `model.size_gb` is approximate weights size. 10% overhead.
function modelFit(model) {
  if (!model || model.size_gb == null) return null
  const reqMb = Number(model.size_gb) * 1024 * 1.1
  const ctxMb = Number(props.slot.context_size || 0) / 256  // ~256 tok/MB cache rule-of-thumb
  const totalMb = reqMb + ctxMb
  // No hardware envelope on the card — use a soft cap so we can flag the
  // obviously-too-big without false-positives on the merely-large.
  if (totalMb > 96 * 1024) return false
  if (totalMb > 64 * 1024) return null
  return true
}

function computePosition() {
  const trigger = swapTriggerRef.value
  if (!trigger) return
  const rect = trigger.getBoundingClientRect()
  const POPOVER_W = Math.max(rect.width, 260)
  const POPOVER_MAX_H = 280
  const vh = window.innerHeight
  const vw = window.innerWidth
  const spaceBelow = vh - rect.bottom
  const spaceAbove = rect.top
  const placeAbove = spaceBelow < 200 && spaceAbove > spaceBelow
  swapPlacement.value = placeAbove ? 'above' : 'below'

  let left = rect.left
  // Clamp horizontally so the popover never spills off-screen.
  if (left + POPOVER_W > vw - 8) left = Math.max(8, vw - POPOVER_W - 8)
  if (left < 8) left = 8

  const top = placeAbove
    ? Math.max(8, rect.top - Math.min(POPOVER_MAX_H, spaceAbove) - 6)
    : rect.bottom + 6

  swapPos.value = { left, top, width: POPOVER_W }
}

async function openSwap() {
  if (!swapAvailable.value) return
  if (swapOpen.value) {
    closeSwap()
    return
  }
  swapOpen.value = true
  swapError.value = ''
  swapIndex.value = -1
  computePosition()
  // Listeners for outside-click + resize/scroll reposition.
  document.addEventListener('mousedown', onDocMouseDown, true)
  window.addEventListener('resize', onWindowChange, true)
  window.addEventListener('scroll', onWindowChange, true)

  // Lazy fetch — if cache stale, show stale data immediately then refresh.
  if (MODEL_CACHE.data) swapModels.value = MODEL_CACHE.data
  swapLoading.value = true
  try {
    swapModels.value = await loadModelsCached()
  } catch (err) {
    swapError.value = err?.message || 'failed to load models'
  } finally {
    swapLoading.value = false
  }
  // Focus first option after popover renders.
  await nextTick()
  const first = swapPopoverRef.value?.querySelector('[role="option"]')
  if (first) {
    swapIndex.value = 0
    first.focus()
  }
}

function closeSwap() {
  swapOpen.value = false
  swapIndex.value = -1
  document.removeEventListener('mousedown', onDocMouseDown, true)
  window.removeEventListener('resize', onWindowChange, true)
  window.removeEventListener('scroll', onWindowChange, true)
  // Restore focus to the trigger for keyboard users.
  if (swapTriggerRef.value) swapTriggerRef.value.focus()
}

function onDocMouseDown(ev) {
  const trigger = swapTriggerRef.value
  const pop = swapPopoverRef.value
  if (trigger && trigger.contains(ev.target)) return
  if (pop && pop.contains(ev.target)) return
  closeSwap()
}

function onWindowChange() {
  if (!swapOpen.value) return
  computePosition()
}

function onPopoverKey(ev) {
  if (!swapOpen.value) return
  const opts = swapPopoverRef.value?.querySelectorAll('[role="option"]') || []
  if (ev.key === 'Escape') {
    ev.preventDefault()
    closeSwap()
  } else if (ev.key === 'ArrowDown') {
    ev.preventDefault()
    if (opts.length === 0) return
    swapIndex.value = Math.min(opts.length - 1, swapIndex.value + 1)
    opts[swapIndex.value]?.focus()
  } else if (ev.key === 'ArrowUp') {
    ev.preventDefault()
    if (opts.length === 0) return
    swapIndex.value = Math.max(0, swapIndex.value - 1)
    opts[swapIndex.value]?.focus()
  } else if (ev.key === 'Enter') {
    ev.preventDefault()
    const m = filteredModels.value[swapIndex.value]
    if (m) selectSwapModel(m)
  } else if (ev.key === 'Home') {
    ev.preventDefault()
    if (opts.length === 0) return
    swapIndex.value = 0
    opts[0]?.focus()
  } else if (ev.key === 'End') {
    ev.preventDefault()
    if (opts.length === 0) return
    swapIndex.value = opts.length - 1
    opts[swapIndex.value]?.focus()
  }
}

// Pull job for the inline popover — when the user clicks an un-downloaded
// model row, we kick off /api/models/{id}/pull, hold the popover open
// while the bar fills, then fire the swap once weights are on disk.
const swapPull = usePullJob()

async function selectSwapModel(model) {
  if (!model || swapSubmitting.value || swapPull.inFlight.value) return
  const modelId = model.id || model.name || model
  if (modelId === currentModelId.value) {
    closeSwap()
    return
  }
  const slotName = props.slot.name
  const label = model.name || modelId

  // First: download if missing. `installed === false` is the registry's
  // signal that the GGUF isn't on disk yet. We keep the popover open
  // during the pull so progress is visible in-context, then transition
  // to the swap once the file lands. If the model entry has no HF
  // coords (upstream-routed stub), short-circuit with a clear error
  // rather than 422-ing on /pull.
  if (model.installed === false) {
    const pullable = !!(model.hf_repo && (model.hf_filename || model.hf_file))
    if (!pullable) {
      const msg = `"${label}" has no download source (upstream-routed). ` +
        `Add hf_repo + hf_filename on the registry entry to enable pull.`
      toasts.error(msg)
      swapError.value = msg
      return
    }
    try {
      await swapPull.pullAndWait(modelId)
      toasts.success(`downloaded ${label}`)
    } catch (err) {
      toasts.error(`download ${label} failed: ${err?.message || err}`)
      swapError.value = String(err?.message || err)
      return
    }
  }

  // Backend swap is unload → systemctl restart → wait-for-health,
  // routinely 10-30s and a 180s timeout on failure. Fire and forget so
  // the popover doesn't lock up for minutes; the SlotCard's existing
  // state ring animates warming → ready on its own.
  toasts.success(`swapping ${slotName} → ${label}…`)
  emit('swapped', { slot: slotName, model_id: modelId })
  closeSwap()
  api(`/api/slots/${encodeURIComponent(slotName)}/swap`, {
    method: 'POST',
    body: JSON.stringify({ model_id: modelId }),
  })
    .then(() => {
      toasts.success(`${slotName} ready on ${label}`)
    })
    .catch((err) => {
      const msg = err?.message || 'swap failed'
      toasts.error(`swap ${slotName} failed: ${msg}`)
    })
}

onBeforeUnmount(() => {
  // Defensive: tear down global listeners even if the card unmounts mid-open
  // (e.g. parent re-renders the slots list during a swap).
  document.removeEventListener('mousedown', onDocMouseDown, true)
  window.removeEventListener('resize', onWindowChange, true)
  window.removeEventListener('scroll', onWindowChange, true)
  if (flashTimer) clearTimeout(flashTimer)
})

// Start button delegate. When the slot already has a model assigned, just
// emit `action: 'load'` and let the parent (Slots.vue / doLoad) drive the
// /api/slots/{name}/load call. When no model is set yet, the bottom <select>
// used to be the affordance; with that removed we route Start straight into
// the swap popover so the operator always has a path to pick a model.
function startSlot() {
  if (!currentModelId.value && swapAvailable.value) {
    openSwap()
    return
  }
  emit('action', 'load')
}

function fmtUptime(s) {
  if (!s || s < 60) return '—'
  const mins = Math.floor(s / 60), hrs = Math.floor(mins / 60), days = Math.floor(hrs / 24)
  if (days > 0) return `${days}d ${hrs % 24}h`
  if (hrs > 0) return `${hrs}h ${mins % 60}m`
  return `${mins}m`
}

// TTFT — time-to-first-token, measured at the dispatcher: the gap
// between forwarding the request and the first emitted SSE chunk.
// '—' when no in-window sample (slot idle or just started).
function fmtTtft(s) {
  if (s == null || !Number.isFinite(s)) return '—'
  if (s < 1) return `${Math.round(s * 1000)}ms`
  return `${s.toFixed(2)}s`
}

// KV-cache % is a gauge scraped from the slot's llama-server /metrics.
// Older Vulkan/CPU builds don't emit the gauge — shows '—' there
// rather than a misleading zero.
function fmtKv(v) {
  if (v == null || !Number.isFinite(v)) return '—'
  return `${(v * 100).toFixed(0)}%`
}

const ttftTitle = computed(() => {
  const cur = props.metrics?.ttft_seconds
  const avg = props.metrics?.ttft_avg_seconds
  if (cur == null) return 'TTFT — no recent samples'
  const ms = (x) => `${Math.round(x * 1000)} ms`
  return avg != null && avg !== cur
    ? `TTFT — latest ${ms(cur)} · 60s avg ${ms(avg)}`
    : `TTFT — latest ${ms(cur)}`
})

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
  <div class="slot-card" :class="{ 'is-running': running, 'is-serving': dotState === 'active', 'is-busy': busy, 'sc-flash': transitionFlash, [`sc-state-${dotState}`]: true }">
    <!-- Header -->
    <div class="sc-head">
      <span class="sc-dot" />
      <span class="sc-name">{{ slot.name }}</span>
      <span class="sc-port mono">{{ slot.port ? `:${slot.port}` : '—' }}</span>
    </div>

    <div class="sc-models">
      <template v-if="isFlmSlot && hasMultipleModels">
        <!-- FLM multiplexes chat + embed + asr on one NPU runtime; the
             chips here are the auxiliary tags. Non-FLM slots may also
             advertise multiple entries in `slot.models` (draft model
             for speculative decoding, embedded vocab models, etc.) —
             those still want the swap trigger, so gate on isFlmSlot
             rather than on count alone. -->
        <span
          v-for="(m, i) in allModels"
          :key="m"
          class="sc-model-chip mono"
          :class="{ primary: i === 0 }"
          :title="m"
        >{{ truncateModel(m) }}</span>
      </template>
      <template v-else>
        <!-- Non-FLM slots get an inline swap trigger on the model label.
             Styled as a select-like control so the affordance is obvious
             without hover. FLM slots multiplex multi-model sets — out of
             scope for C1, the label stays read-only and the chevron hides. -->
        <button
          v-if="swapAvailable"
          ref="swapTriggerRef"
          type="button"
          class="sc-model-trigger mono"
          :class="{ 'is-empty': !currentModelId, 'is-open': swapOpen }"
          :title="currentModelId ? `Swap model (${modelLabel})` : 'Pick a model'"
          :aria-haspopup="'listbox'"
          :aria-expanded="swapOpen"
          :aria-label="currentModelId
            ? `Swap model for ${slot.name}, current ${modelLabel}`
            : `Pick a model for ${slot.name}`"
          @click.stop="openSwap"
        >
          <svg class="sc-swap-icon" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M7 16V4m0 0L3 8m4-4l4 4M17 8v12m0 0l4-4m-4 4l-4-4"/>
          </svg>
          <span class="sc-model-text">{{ currentModelId ? modelLabel : 'select model…' }}</span>
          <svg class="sc-swap-caret" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">
            <path stroke-linecap="round" stroke-linejoin="round" d="M6 9l6 6 6-6"/>
          </svg>
        </button>
        <span v-else class="sc-model mono" :title="modelLabel">{{ modelLabel }}</span>
      </template>
    </div>

    <!-- Inline swap popover. Teleported to <body> so dense grid containers
         and footer z-index stacking don't clip it. Position computed from
         trigger getBoundingClientRect on open + on resize/scroll. -->
    <Teleport to="body">
      <div
        v-if="swapOpen"
        ref="swapPopoverRef"
        class="sc-swap-popover"
        :class="[`is-${swapPlacement}`]"
        :style="{ left: swapPos.left + 'px', top: swapPos.top + 'px', minWidth: swapPos.width + 'px' }"
        role="listbox"
        :aria-label="`Compatible models for ${slot.name}`"
        :aria-busy="swapLoading || swapSubmitting"
        tabindex="-1"
        @keydown="onPopoverKey"
      >
        <div class="sc-swap-head">
          <span class="sc-swap-title mono">swap model</span>
          <span class="sc-swap-sub mono">backend: {{ slot.backend || '—' }}</span>
        </div>

        <div v-if="swapLoading && filteredModels.length === 0" class="sc-swap-empty mono">loading…</div>
        <div v-else-if="swapError" class="sc-swap-empty sc-swap-err mono">{{ swapError }}</div>
        <div v-else-if="filteredModels.length === 0" class="sc-swap-empty mono">
          no compatible models for backend "{{ slot.backend || '—' }}"
        </div>

        <ul v-else class="sc-swap-list">
          <li
            v-for="(m, i) in filteredModels"
            :key="m.id || m.name || i"
            role="option"
            :tabindex="swapOpen ? 0 : -1"
            class="sc-swap-row"
            :class="{
              'is-current': (m.id || m.name) === currentModelId,
              'is-focused': i === swapIndex,
              'fit-yes': modelFit(m) === true,
              'fit-no':  modelFit(m) === false,
            }"
            :aria-selected="(m.id || m.name) === currentModelId"
            :aria-disabled="swapSubmitting"
            @click="selectSwapModel(m)"
            @focus="swapIndex = i"
          >
            <span class="sc-swap-name mono">
              <!-- Three states:
                     ● downloaded  — file on disk, ready to load
                     ⬇ pullable    — has hf_repo/hf_file, click downloads
                     ✕ no-source   — upstream-routed stub; not pullable
                   `installed` is the registry's authoritative on-disk
                   signal; the hf_repo + hf_filename pair is what
                   /api/models/{id}/pull needs to fetch from HuggingFace. -->
              <span
                v-if="m.installed !== false"
                class="sc-swap-dl ready"
                title="On disk, ready to load"
                aria-label="downloaded"
              >
                <svg width="9" height="9" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                  <circle cx="12" cy="12" r="6"/>
                </svg>
              </span>
              <span
                v-else-if="m.hf_repo && (m.hf_filename || m.hf_file)"
                class="sc-swap-dl needs-dl"
                title="Click to download (not on disk yet)"
                aria-label="needs download"
              >
                <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M12 4v12m0 0l-5-5m5 5l5-5M5 20h14"/>
                </svg>
              </span>
              <span
                v-else
                class="sc-swap-dl no-source"
                title="No download source (upstream-routed stub)"
                aria-label="no download source"
              >
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" aria-hidden="true">
                  <path stroke-linecap="round" stroke-linejoin="round" d="M6 6l12 12M18 6L6 18"/>
                </svg>
              </span>
              {{ m.name || m.id }}
            </span>
            <span class="sc-swap-meta mono">
              <span v-if="m.size_gb != null" class="sc-swap-size">{{ Number(m.size_gb).toFixed(1) }}G</span>
              <span v-if="modelFit(m) === true"  class="sc-swap-fit fit-yes" title="should fit">fits</span>
              <span v-else-if="modelFit(m) === false" class="sc-swap-fit fit-no" title="may exceed envelope">large</span>
            </span>
            <span v-if="(m.id || m.name) === currentModelId" class="sc-swap-current mono">current</span>
          </li>
        </ul>

        <div v-if="swapSubmitting" class="sc-swap-foot mono">swapping…</div>
        <div v-if="swapPull.inFlight.value" class="sc-swap-pull">
          <div class="sc-swap-pull-bar"><div class="sc-swap-pull-fill" :style="{ width: (swapPull.pct.value ?? 0) + '%' }" /></div>
          <span class="sc-swap-pull-label mono">
            ↓ {{ swapPull.modelId.value }} · {{ swapPull.pct.value ?? 0 }}% · {{ fmtBytes(swapPull.downloaded.value) }} / {{ fmtBytes(swapPull.total.value) }}
          </span>
          <button class="sc-swap-pull-cancel mono" type="button" @click="swapPull.cancel()">cancel</button>
        </div>
      </div>
    </Teleport>

    <!-- Stats row -->
    <div class="sc-stats">
      <div class="sc-stat" :title="m.kv_cache_usage != null ? 'KV-cache fill — % of model context occupied' : 'KV-cache % unavailable on this build of llama-server'">
        <div class="sc-stat-l">KV</div>
        <div class="sc-stat-v" :class="{ active: m.kv_cache_usage != null }">{{ fmtKv(m.kv_cache_usage) }}</div>
      </div>
      <div class="sc-stat">
        <div class="sc-stat-l">T/S</div>
        <div class="sc-stat-v" :class="{ active: running }">{{ (m.tokens_per_sec || 0).toFixed(1) }}</div>
      </div>
      <div class="sc-stat" :title="ttftTitle">
        <div class="sc-stat-l">TTFT</div>
        <div class="sc-stat-v" :class="{ active: m.ttft_seconds != null }">{{ fmtTtft(m.ttft_seconds) }}</div>
      </div>
      <div class="sc-stat">
        <div class="sc-stat-l">MEM</div>
        <div class="sc-stat-v">{{ (m.mem_rss_mb || 0) > 0 ? ((m.mem_rss_mb / 1024).toFixed(1) + 'G') : '—' }}</div>
      </div>
    </div>

    <!-- Sparkline. Corner badges: ACT (in-flight requests) top-left,
         UP (uptime) bottom-right. T/S + TTFT live in the stats row
         above so the chart stays the chart. -->
    <div v-if="running || hasHistory" class="sc-spark" :title="`gen ${(m.tokens_per_sec||0).toFixed(1)} t/s · prompt ${(m.prompt_tokens_per_sec||0).toFixed(1)} t/s`">
      <div class="sc-spark-inner" v-html="sparkSvg"></div>
      <span class="sc-spark-badge sc-spark-act" title="Active requests in flight">
        <span class="sc-spark-l">ACT</span>
        <span class="sc-spark-v" :class="{ active: (m.requests_processing || 0) > 0 }">{{ m.requests_processing || 0 }}</span>
      </span>
      <span class="sc-spark-badge sc-spark-up" title="Slot uptime">
        <span class="sc-spark-l">UP</span>
        <span class="sc-spark-v">{{ fmtUptime(m.uptime_seconds) }}</span>
      </span>
    </div>

    <!-- Footer: hardware + backend chips on the left, lifecycle actions on
         the right. The model picker used to live here as a redundant
         <select>; it's been folded into the inline trigger on the model
         label (which already supports hot-swap + pull-if-missing). In its
         place we surface the slot's hardware target + concrete backend so
         two iGPU slots running different runtimes are distinguishable. -->
    <div class="sc-foot">
      <div class="sc-foot-chips" :title="`Hardware: ${hardwareTarget.label}${backendTech ? ' · backend: ' + backendTech.label : ''}`">
        <span class="sc-chip hw" :class="`hw-${hardwareTarget.id}`">{{ hardwareTarget.label }}</span>
        <span
          v-if="backendTech && backendTech.id !== hardwareTarget.id.toLowerCase()"
          class="sc-chip backend"
          :class="`be-${backendTech.id}`"
        >{{ backendTech.label }}</span>
        <span v-if="slot.context_size" class="sc-chip dim">ctx {{ (slot.context_size / 1024).toFixed(0) }}K</span>
      </div>

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
      <button
        v-else
        class="sc-btn good"
        type="button"
        :disabled="busy"
        @click="startSlot"
        :title="currentModelId ? 'Start' : 'Pick a model to start'"
      >
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
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 22%, var(--color-border));
  border-radius: var(--radius-lg);
  padding: 0;
  overflow: hidden;
  color: var(--color-fg);
  transition: border-color 0.18s ease, box-shadow 0.18s ease, opacity 0.18s ease;
}
.slot-card::before {
  content: '';
  position: absolute; inset: 0;
  border-radius: var(--radius-lg);
  pointer-events: none;
  box-shadow: inset 0 0 0 1px color-mix(in oklch, var(--hal0-accent) 8%, transparent);
}
/* Running slot is the headline metric — amber top rail (symmetric vs the
   previous left-side inset; reads cleaner in a grid of cards). */
.slot-card.is-running {
  border-color: color-mix(in srgb, var(--hal0-accent) 40%, var(--color-border));
}
/* Top rail is now a *live-traffic* signal — appears only while
   requests_processing > 0. Steady readiness lives in the dot color. */
.slot-card.is-serving {
  box-shadow: inset 0 3px 0 var(--hal0-accent);
}
.slot-card.is-busy { opacity: 0.78; }
.slot-card:hover { border-color: var(--color-border-hi); }
.slot-card.is-running:hover { border-color: color-mix(in srgb, var(--hal0-accent) 55%, var(--color-border)); }

.sc-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 14px 16px 6px;
}
.sc-name { font-size: 14px; font-weight: 600; color: var(--hal0-accent); margin: 0; text-transform: uppercase; letter-spacing: 0.04em; }
.sc-port { margin-left: auto; font-size: 11px; color: var(--color-fg-faint); font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1; }
.sc-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--hal0-accent);
  box-shadow: 0 0 6px -1px var(--hal0-accent);
  flex-shrink: 0;
}
.sc-state-active .sc-dot { background: var(--color-success); animation: dot-breathe 2.4s ease-in-out infinite; }

/* SSE-driven state-transition flash. Brief border highlight + dot pop so
   the user sees instant feedback on a transition without the noise of a
   full re-render. Reduced-motion users get nothing (gated in script). */
.slot-card.sc-flash { animation: sc-flash 1.6s ease-in-out; }
.slot-card.sc-flash .sc-dot { animation: sc-flash-dot 1.6s ease-in-out; }
@keyframes sc-flash {
  0%   { box-shadow: 0 0 0 0 color-mix(in oklch, var(--color-accent), transparent 100%); }
  35%  { box-shadow: 0 0 0 5px color-mix(in oklch, var(--color-accent), transparent 70%); }
  100% { box-shadow: 0 0 0 9px color-mix(in oklch, var(--color-accent), transparent 100%); }
}
@keyframes sc-flash-dot {
  0%   { transform: scale(1); }
  40%  { transform: scale(1.35); }
  100% { transform: scale(1); }
}
@media (prefers-reduced-motion: reduce) {
  .slot-card.sc-flash, .slot-card.sc-flash .sc-dot { animation: none; }
}
.sc-state-idle .sc-dot   { background: var(--color-warning); }
.sc-state-loading .sc-dot { background: var(--color-warning); box-shadow: 0 0 6px -1px var(--color-warning); animation: dot-pulse 1.4s ease-in-out infinite; }
.sc-state-error .sc-dot  { background: var(--color-danger); box-shadow: 0 0 6px -1px var(--color-danger); }
.sc-state-offline .sc-dot { background: var(--color-fg-faint); box-shadow: none; }
@keyframes dot-pulse {
  0%, 100% { box-shadow: 0 0 0 0 color-mix(in oklch, currentColor, transparent 70%); }
  50%      { box-shadow: 0 0 0 4px color-mix(in oklch, currentColor, transparent 90%); }
}
/* Soft active-traffic breathe: halo expands and fades smoothly without
   the punchy ring of dot-pulse. Carried by the success-green dot. */
@keyframes dot-breathe {
  0%, 100% {
    box-shadow: 0 0 0 0 color-mix(in oklch, var(--color-success), transparent 70%),
                0 0 5px 0 color-mix(in oklch, var(--color-success), transparent 80%);
  }
  50% {
    box-shadow: 0 0 0 6px color-mix(in oklch, var(--color-success), transparent 100%),
                0 0 12px 2px color-mix(in oklch, var(--color-success), transparent 55%);
  }
}

.sc-models {
  padding: 6px 16px 2px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  min-height: 28px;
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
  margin: 12px 16px 0;
  padding: 10px 0 8px;
  border-top: 1px solid var(--color-border);
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  text-align: center;
  font-family: var(--font-mono);
  font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1;
}
.sc-stat-l { font-size: 9px; color: var(--hal0-accent); text-transform: uppercase; letter-spacing: 0.08em; opacity: 0.75; }
.sc-stat-v { font-size: 14px; font-weight: 500; color: var(--color-fg-faint); margin-top: 3px; }
.sc-stat-v.active { color: var(--hal0-accent); }

.sc-spark {
  position: relative;
  margin: 8px 16px 4px;
  height: 64px;
  background: var(--hal0-bg-sunken);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--hal0-accent);
  overflow: hidden;
}
.sc-spark-inner :deep(svg) { width: 100%; height: 100%; display: block; }
/* Corner badges over the sparkline. ACT pins top-left, UP pins bottom-right.
   pointer-events stay enabled on each badge so their own :title fires; the
   sparkline's outer wrapper :title still works in the gaps between badges. */
.sc-spark-badge {
  position: absolute;
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-family: var(--font-mono);
  font-feature-settings: 'zero' 1, 'ss02' 1, 'tnum' 1;
}
.sc-spark-act { top: 4px; left: 6px; }
.sc-spark-up  { bottom: 4px; right: 6px; }
.sc-spark-l {
  font-size: 9px;
  color: var(--color-fg-faint);
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
.sc-spark-v {
  font-size: 11px;
  font-weight: 500;
  color: var(--color-fg);
}
.sc-spark-v.active { color: var(--hal0-accent); }

.sc-foot {
  margin-top: auto;
  padding: 10px 14px;
  border-top: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: 6px;
}
/* Left-side chip cluster replaces the legacy model `<select>`. Flex-grows
   so the action buttons hug the right edge regardless of how many chips
   render (cpu-only slots have just the hw chip; iGPU/vulkan slots have
   two; FLM slots inherit hw=NPU + backend=flm). */
.sc-foot-chips {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 5px;
  flex: 1;
  min-width: 0;
}

/* Backend tech chip — neutral by default so it doesn't compete with the
   hw-target chip's category colour. Specific runtimes get a recognisable
   accent (vulkan teal, rocm orange, flm warning-amber). */
.sc-chip.backend {
  text-transform: lowercase;
  letter-spacing: 0.03em;
}
.sc-chip.be-vulkan {
  color: #4fc3f7;
  border-color: color-mix(in oklch, #4fc3f7, transparent 60%);
  background: color-mix(in oklch, #4fc3f7, transparent 88%);
}
.sc-chip.be-rocm {
  color: #ff8a65;
  border-color: color-mix(in oklch, #ff8a65, transparent 60%);
  background: color-mix(in oklch, #ff8a65, transparent 88%);
}
.sc-chip.be-cuda {
  color: #aed581;
  border-color: color-mix(in oklch, #aed581, transparent 60%);
  background: color-mix(in oklch, #aed581, transparent 88%);
}
.sc-chip.be-metal {
  color: #ce93d8;
  border-color: color-mix(in oklch, #ce93d8, transparent 60%);
  background: color-mix(in oklch, #ce93d8, transparent 88%);
}
.sc-chip.be-flm {
  color: var(--color-warning);
  border-color: color-mix(in oklch, var(--color-warning), transparent 60%);
  background: color-mix(in oklch, var(--color-warning), transparent 90%);
}

/* Default action button — transparent ghost. Edit / Logs / Delete sit
   here so they don't compete visually with the load-cycle buttons. */
.sc-btn {
  background: transparent;
  border: 1px solid transparent;
  color: var(--color-fg-faint);
  padding: 5px 6px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background 0.15s, color 0.15s, border-color 0.15s;
}
.sc-btn:hover:not(:disabled) { background: var(--color-surface-3); color: var(--color-fg); }
.sc-btn:disabled { opacity: 0.4; cursor: default; }

/* Load-cycle buttons (Start / Restart / Stop) are the operator's
   primary affordance, so they wear an outline at rest — easy to spot
   on a card grid. Start = warning yellow (the "do something" hue),
   Stop = danger red, Restart = also warning yellow but with a different
   icon for distinction. */
.sc-btn.good {
  color: var(--color-warning);
  border-color: color-mix(in oklch, var(--color-warning) 60%, transparent);
}
.sc-btn.good:hover:not(:disabled) {
  color: var(--color-warning);
  background: color-mix(in oklch, var(--color-warning), transparent 85%);
  border-color: var(--color-warning);
}
.sc-btn.warn {
  color: var(--color-warning);
  border-color: color-mix(in oklch, var(--color-warning) 45%, transparent);
}
.sc-btn.warn:hover:not(:disabled) {
  color: var(--color-warning);
  background: color-mix(in oklch, var(--color-warning), transparent 88%);
  border-color: var(--color-warning);
}
.sc-btn.danger {
  color: var(--color-danger);
  border-color: color-mix(in oklch, var(--color-danger) 55%, transparent);
}
.sc-btn.danger:hover:not(:disabled) {
  color: var(--color-danger);
  background: color-mix(in oklch, var(--color-danger), transparent 88%);
  border-color: var(--color-danger);
}

/* Inline swap trigger — styled as a select-like control so the affordance
   reads as "click me" without depending on hover. A subtle accent-tinted
   frame distinguishes it from a static label; the left swap-arrows icon
   reinforces that this is a swap action, and the right caret indicates
   a dropdown. Empty-state ("select model…") gets a stronger amber accent
   to draw the eye for newly-created slots. */
.sc-model-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 3px 8px;
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.2;
  color: var(--color-fg);
  cursor: pointer;
  background: color-mix(in oklch, var(--hal0-accent) 5%, var(--color-surface-2));
  border: 1px solid color-mix(in oklch, var(--hal0-accent) 24%, var(--color-border));
  border-radius: var(--radius-sm);
  text-align: left;
  transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
}
.sc-model-trigger .sc-model-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex: 1;
  min-width: 0;
}
.sc-model-trigger .sc-swap-icon {
  flex-shrink: 0;
  color: var(--hal0-accent);
  opacity: 0.75;
}
.sc-model-trigger .sc-swap-caret {
  flex-shrink: 0;
  opacity: 0.7;
  color: var(--hal0-accent);
  transition: transform 0.15s ease, opacity 0.15s ease;
}
.sc-model-trigger:hover,
.sc-model-trigger:focus-visible,
.sc-model-trigger.is-open {
  background: color-mix(in oklch, var(--hal0-accent) 12%, var(--color-surface-2));
  border-color: color-mix(in oklch, var(--hal0-accent) 50%, var(--color-border));
}
.sc-model-trigger:hover .sc-swap-icon,
.sc-model-trigger:focus-visible .sc-swap-icon,
.sc-model-trigger.is-open .sc-swap-icon { opacity: 1; }
.sc-model-trigger:hover .sc-swap-caret,
.sc-model-trigger:focus-visible .sc-swap-caret { opacity: 1; }
.sc-model-trigger.is-open .sc-swap-caret { transform: rotate(180deg); opacity: 1; }
.sc-model-trigger:focus-visible { outline: 1px solid var(--color-accent); outline-offset: 2px; }

/* Empty-state — slot has no model assigned yet. Stronger accent so the
   operator knows this is the primary CTA to get the slot running. */
.sc-model-trigger.is-empty {
  color: var(--hal0-accent);
  background: color-mix(in oklch, var(--hal0-accent) 10%, var(--color-surface-2));
  border-color: color-mix(in oklch, var(--hal0-accent) 50%, var(--color-border));
  border-style: dashed;
}
.sc-model-trigger.is-empty:hover,
.sc-model-trigger.is-empty:focus-visible,
.sc-model-trigger.is-empty.is-open {
  background: color-mix(in oklch, var(--hal0-accent) 18%, var(--color-surface-2));
  border-color: var(--hal0-accent);
  border-style: solid;
}
</style>

<!-- Popover is teleported to <body>. Scoped styles travel with the root
     teleport node via Vue's scope-hash, but to keep things robust the
     selectors stay inside the scoped block. -->
<style scoped>
.sc-swap-popover {
  position: fixed;
  z-index: 9999;
  max-height: 280px;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border-hi);
  border-radius: var(--radius-md);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
  overflow: hidden;
  color: var(--color-fg);
}
.sc-swap-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 6px 10px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-2);
}
.sc-swap-title {
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--hal0-accent);
}
.sc-swap-sub {
  font-size: 10px;
  color: var(--color-fg-faint);
}
.sc-swap-list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
  overflow-y: auto;
  max-height: 240px;
}
.sc-swap-row {
  display: grid;
  grid-template-columns: 1fr auto auto;
  align-items: center;
  gap: 8px;
  padding: 6px 10px;
  font-size: 11px;
  cursor: pointer;
  color: var(--color-fg-muted);
  border-left: 2px solid transparent;
}
.sc-swap-row:hover,
.sc-swap-row.is-focused,
.sc-swap-row:focus-visible {
  background: var(--color-surface-3);
  color: var(--color-fg);
  outline: none;
  border-left-color: var(--hal0-accent);
}
.sc-swap-row.is-current {
  color: var(--hal0-accent);
}
.sc-swap-name {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.sc-swap-dl {
  display: inline-grid;
  place-items: center;
  width: 14px;
  height: 14px;
  flex-shrink: 0;
}
.sc-swap-dl.ready    { color: color-mix(in oklch, var(--hal0-accent) 80%, transparent); }
.sc-swap-dl.needs-dl { color: var(--color-warning); }
.sc-swap-dl.no-source { color: var(--color-fg-faint); opacity: 0.6; }
.sc-swap-meta {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 10px;
  color: var(--color-fg-faint);
}
.sc-swap-size { font-feature-settings: 'tnum' 1; }
.sc-swap-fit {
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}
.sc-swap-fit.fit-yes {
  color: var(--color-success);
  border-color: color-mix(in oklch, var(--color-success), transparent 60%);
  background: color-mix(in oklch, var(--color-success), transparent 88%);
}
.sc-swap-fit.fit-no {
  color: var(--color-warning);
  border-color: color-mix(in oklch, var(--color-warning), transparent 60%);
  background: color-mix(in oklch, var(--color-warning), transparent 88%);
}
.sc-swap-current {
  font-size: 9px;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: var(--hal0-accent);
}
.sc-swap-empty {
  padding: 12px 10px;
  font-size: 11px;
  color: var(--color-fg-faint);
  text-align: center;
}
.sc-swap-empty.sc-swap-err { color: var(--color-danger); }
.sc-swap-foot {
  padding: 4px 10px;
  border-top: 1px solid var(--color-border);
  font-size: 10px;
  color: var(--color-fg-faint);
  background: var(--color-surface-2);
}

/* Inline pull-progress strip rendered under the popover list while an
 * un-downloaded option is being fetched. Visually narrow so it fits in
 * the dense popover; matches the capability cards' .cap-pull palette. */
.sc-swap-pull {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 6px 8px;
  border-top: 1px solid var(--color-border);
  background: color-mix(in oklch, var(--hal0-accent) 6%, transparent);
}
.sc-swap-pull-bar {
  flex: 1;
  height: 3px;
  border-radius: 2px;
  background: var(--hal0-bg-sunken);
  overflow: hidden;
}
.sc-swap-pull-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--hal0-accent), var(--hal0-accent-hover));
  transition: width 0.2s ease-out;
}
.sc-swap-pull-label { font-size: 9.5px; color: var(--color-fg-muted); white-space: nowrap; }
.sc-swap-pull-cancel {
  background: transparent;
  border: 1px solid var(--color-border);
  color: var(--color-fg-faint);
  font-size: 9px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.sc-swap-pull-cancel:hover {
  color: var(--color-danger);
  border-color: color-mix(in oklch, var(--color-danger) 40%, var(--color-border));
}
.sc-swap-popover.is-above { transform-origin: bottom left; }
.sc-swap-popover.is-below { transform-origin: top left; }
</style>
