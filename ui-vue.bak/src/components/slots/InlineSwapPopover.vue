<script setup>
/**
 * InlineSwapPopover.vue — body-teleported model-swap popover for SlotCard.
 *
 * Mirrors slot-modals.jsx::InlineSwapPopover (lines 319-349) with the
 * keyboard + outside-click + reposition mechanics carried over from the
 * pre-rewrite SlotCard. The popover lives at <body> so dense grid
 * containers don't clip it.
 *
 * Position is computed from the anchor element's getBoundingClientRect
 * on open + on resize/scroll. Backend filter mirrors the slot's
 * `backend` field; legacy registry entries with no `backends` array are
 * treated as universal (don't hide them on a fresh install).
 *
 * Emits
 * -----
 *   - `close` — Esc / outside-click / item pick.
 *   - `pick`  — { id, name, repo, size_gb, installed, hf_repo, ... }
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { api } from '../../composables/useApi.js'

const props = defineProps({
  open:            { type: Boolean, default: false },
  anchor:          { type: [Object, null], default: null },
  slot:            { type: Object, required: true },
  currentModelId:  { type: String, default: '' },
})

const emit = defineEmits(['close', 'pick'])

// ── Module-scope model cache (shared across cards) ────────────────────
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

// ── Reactive state ────────────────────────────────────────────────────
const models = ref([])
const loading = ref(false)
const error = ref('')
const focusIdx = ref(-1)
const placement = ref('below')
const pos = ref({ left: 0, top: 0, width: 260 })
const popoverRef = ref(null)

const filteredModels = computed(() => {
  const backend = String(props.slot.backend || '').toLowerCase()
  const slotType = String(props.slot.kind || props.slot.type || '').toLowerCase()
  return (models.value || []).filter((m) => {
    // Backend compatibility
    const backends = Array.isArray(m.backends) ? m.backends.map((b) => String(b).toLowerCase()) : []
    const backendOk = backends.length === 0 || !backend || backends.includes(backend)
    // Type compatibility (m.type when populated; otherwise allow)
    const typeOk = !m.type || !slotType || String(m.type).toLowerCase() === slotType
      || (slotType === 'llama-server' && String(m.type).toLowerCase() === 'llm')
    return backendOk && typeOk
  })
})

function modelFit(model) {
  if (!model || model.size_gb == null) return null
  const reqMb = Number(model.size_gb) * 1024 * 1.1
  if (reqMb > 96 * 1024) return false
  if (reqMb > 64 * 1024) return null
  return true
}

// ── Position the popover relative to the anchor ───────────────────────
function reposition() {
  if (!props.anchor) return
  const rect = props.anchor.getBoundingClientRect()
  const POPOVER_W = Math.max(rect.width, 280)
  const POPOVER_MAX_H = 320
  const vh = window.innerHeight
  const vw = window.innerWidth
  const spaceBelow = vh - rect.bottom
  const spaceAbove = rect.top
  const placeAbove = spaceBelow < 200 && spaceAbove > spaceBelow
  placement.value = placeAbove ? 'above' : 'below'
  let left = rect.left
  if (left + POPOVER_W > vw - 8) left = Math.max(8, vw - POPOVER_W - 8)
  if (left < 8) left = 8
  const top = placeAbove
    ? Math.max(8, rect.top - Math.min(POPOVER_MAX_H, spaceAbove) - 6)
    : rect.bottom + 6
  pos.value = { left, top, width: POPOVER_W }
}

function onDocMouseDown(ev) {
  if (props.anchor && props.anchor.contains(ev.target)) return
  if (popoverRef.value && popoverRef.value.contains(ev.target)) return
  emit('close')
}

function onWindowChange() {
  if (props.open) reposition()
}

function onPopoverKey(ev) {
  const opts = popoverRef.value?.querySelectorAll('[role="option"]') || []
  if (ev.key === 'Escape') {
    ev.preventDefault()
    emit('close')
  } else if (ev.key === 'ArrowDown') {
    ev.preventDefault()
    if (opts.length === 0) return
    focusIdx.value = Math.min(opts.length - 1, focusIdx.value + 1)
    opts[focusIdx.value]?.focus()
  } else if (ev.key === 'ArrowUp') {
    ev.preventDefault()
    if (opts.length === 0) return
    focusIdx.value = Math.max(0, focusIdx.value - 1)
    opts[focusIdx.value]?.focus()
  } else if (ev.key === 'Enter') {
    ev.preventDefault()
    const m = filteredModels.value[focusIdx.value]
    if (m) emit('pick', m)
  }
}

// ── Open / close lifecycle ────────────────────────────────────────────
watch(() => props.open, async (open) => {
  if (open) {
    error.value = ''
    focusIdx.value = -1
    reposition()
    document.addEventListener('mousedown', onDocMouseDown, true)
    window.addEventListener('resize', onWindowChange, true)
    window.addEventListener('scroll', onWindowChange, true)

    if (MODEL_CACHE.data) models.value = MODEL_CACHE.data
    loading.value = true
    try {
      models.value = await loadModelsCached()
    } catch (e) {
      error.value = e?.message || 'failed to load models'
    } finally {
      loading.value = false
    }
    await nextTick()
    const first = popoverRef.value?.querySelector('[role="option"]')
    if (first) {
      focusIdx.value = 0
      first.focus()
    }
  } else {
    document.removeEventListener('mousedown', onDocMouseDown, true)
    window.removeEventListener('resize', onWindowChange, true)
    window.removeEventListener('scroll', onWindowChange, true)
  }
}, { immediate: false })

onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocMouseDown, true)
  window.removeEventListener('resize', onWindowChange, true)
  window.removeEventListener('scroll', onWindowChange, true)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      ref="popoverRef"
      :class="['swap-pop', 'sc-swap-popover', `is-${placement}`]"
      :style="{ left: pos.left + 'px', top: pos.top + 'px', minWidth: pos.width + 'px' }"
      role="listbox"
      :aria-label="`Compatible models for ${slot.name}`"
      :aria-busy="loading"
      tabindex="-1"
      @keydown="onPopoverKey"
    >
      <div class="swap-pop-h">
        <span class="title mono">swap model · type {{ slot.kind || slot.type || '—' }}</span>
        <span class="sub mono">backend: {{ slot.backend || '—' }}</span>
      </div>

      <div v-if="loading && filteredModels.length === 0" class="swap-empty mono">loading…</div>
      <div v-else-if="error" class="swap-empty err mono">{{ error }}</div>
      <div v-else-if="filteredModels.length === 0" class="swap-empty mono">
        no compatible models for backend "{{ slot.backend || '—' }}"
      </div>

      <ul v-else class="swap-list">
        <li
          v-for="(m, i) in filteredModels"
          :key="m.id || m.name || i"
          role="option"
          :tabindex="open ? 0 : -1"
          :class="['swap-pop-item', { cur: (m.id || m.name) === currentModelId, focused: i === focusIdx }]"
          :aria-selected="(m.id || m.name) === currentModelId"
          @click="emit('pick', m)"
          @focus="focusIdx = i"
        >
          <div class="nm mono">
            {{ m.name || m.id }}
            <span v-if="m.repo" class="sub">{{ m.repo }}</span>
          </div>
          <div class="sz num mono">{{ m.size_gb != null ? Number(m.size_gb).toFixed(1) + 'G' : '—' }}</div>
          <div
            :class="['fit', { no: m.installed === false || modelFit(m) === false }]"
            :title="m.installed === false ? 'will pull from HuggingFace' : (modelFit(m) === false ? 'may exceed envelope' : 'fits in available memory')"
          >
            {{ m.installed === false ? 'will pull' : (modelFit(m) === false ? 'large' : 'fits ✓') }}
          </div>
        </li>
      </ul>

      <div class="swap-pop-h browse mono" role="button" tabindex="0" @click="emit('close')">
        + Browse all models →
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.swap-pop {
  position: fixed;
  z-index: 9999;
  max-height: 320px;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border-hi);
  border-radius: var(--radius);
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.45);
  overflow: hidden;
}
.swap-pop-h {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  padding: 6px 10px;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-2);
}
.swap-pop-h.browse {
  border-top: 1px solid var(--color-border);
  border-bottom: none;
  color: var(--hal0-accent);
  cursor: pointer;
}
.swap-pop-h .title {
  font-size: 10px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--hal0-accent);
}
.swap-pop-h .sub {
  font-size: 10px;
  color: var(--color-fg-faint);
}
.swap-list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
  overflow-y: auto;
  max-height: 240px;
}
.swap-pop-item {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 8px;
  padding: 6px 10px;
  font-size: 11px;
  cursor: pointer;
  color: var(--color-fg-muted);
  border-left: 2px solid transparent;
}
.swap-pop-item:hover,
.swap-pop-item.focused,
.swap-pop-item:focus-visible {
  background: var(--color-surface-3);
  color: var(--color-fg);
  outline: none;
  border-left-color: var(--hal0-accent);
}
.swap-pop-item.cur { color: var(--hal0-accent); }
.swap-pop-item .nm {
  display: flex; flex-direction: column;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.swap-pop-item .nm .sub {
  color: var(--color-fg-faint);
  font-size: 10px;
}
.swap-pop-item .sz { color: var(--color-fg-muted); font-feature-settings: 'tnum' 1; }
.swap-pop-item .fit {
  color: var(--color-success);
  font-size: 10px;
  text-align: right;
}
.swap-pop-item .fit.no { color: var(--color-warning); }
.swap-empty {
  padding: 12px 10px;
  font-size: 11px;
  color: var(--color-fg-faint);
  text-align: center;
}
.swap-empty.err { color: var(--color-danger); }
</style>
