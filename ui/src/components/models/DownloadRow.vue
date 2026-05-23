<script setup>
/**
 * DownloadRow.vue — single Downloads-pane row for the v2 Models view.
 *
 * Mirrors the React `DownloadRow` in
 *   /tmp/hal0-design/hal0-v2/project/dash/model-modals.jsx
 * Seven canonical states (per issue #171):
 *   pulling | paused | cancelled | error | verifying | completed | queued
 *
 * Wires into `usePullJob` shape (`bytes_downloaded` / `bytes_total` /
 * `speed_bps` / `eta_s` / `error`). The composable owns the actual
 * job lifecycle; this row just renders state + emits action events the
 * parent translates into job calls.
 *
 * Multi-file (sharded) downloads expose per-file rows under `dl.files`;
 * the parent row collapses them by default and reveals on hover.
 *
 * `dl` shape (parent-supplied):
 *   { id, name, state, pct, downloaded, size,           // pretty strings
 *     rate, eta, errorMessage?, files?: [{ name, pct, state }] }
 *
 * Completed rows auto-remove 5s after entering `completed` unless the
 * user is hovering the row (hover-defer). The parent owns the actual
 * splice; this component fires `remove` when the timer elapses.
 */
import { ref, watch, onUnmounted, computed } from 'vue'

const props = defineProps({
  dl: { type: Object, required: true },
  autoRemoveMs: { type: Number, default: 5000 },
})

const emit = defineEmits([
  'pause', 'resume', 'cancel', 'retry', 'remove',
])

const hovered = ref(false)
const expanded = ref(false)
let removeTimer = null

const isMulti = computed(() =>
  Array.isArray(props.dl?.files) && props.dl.files.length > 1
)

const stateClass = computed(() => `dl-state-${props.dl?.state || 'queued'}`)

function statusText(state) {
  switch (state) {
    case 'completed':  return '✓ done'
    case 'queued':     return 'queued'
    case 'pulling':    return `${props.dl?.pct ?? 0}%`
    case 'paused':     return `${props.dl?.pct ?? 0}% paused`
    case 'verifying':  return 'verifying…'
    case 'cancelled':  return 'cancelled'
    case 'error':      return 'failed'
    default:           return state
  }
}

function clearTimer() {
  if (removeTimer) {
    clearTimeout(removeTimer)
    removeTimer = null
  }
}

function maybeScheduleAutoRemove() {
  clearTimer()
  if (props.dl?.state === 'completed' && !hovered.value) {
    removeTimer = setTimeout(() => emit('remove', props.dl), props.autoRemoveMs)
  }
}

watch(() => props.dl?.state, () => maybeScheduleAutoRemove(), { immediate: true })
watch(hovered, () => {
  if (hovered.value) clearTimer()
  else if (props.dl?.state === 'completed') maybeScheduleAutoRemove()
})

onUnmounted(clearTimer)

function onMouseEnter() {
  hovered.value = true
  if (isMulti.value) expanded.value = true
}
function onMouseLeave() {
  hovered.value = false
  expanded.value = false
}
</script>

<template>
  <div
    class="download-row"
    :class="stateClass"
    :data-state="dl.state"
    :data-id="dl.id"
    @mouseenter="onMouseEnter"
    @mouseleave="onMouseLeave"
  >
    <div class="download-row-h">
      <span class="dl-name" :class="{ strike: dl.state === 'cancelled' }">{{ dl.name }}</span>
      <span class="dl-status" :class="`status-${dl.state}`">{{ statusText(dl.state) }}</span>
    </div>

    <div class="dl-bar" role="progressbar" :aria-valuenow="dl.pct ?? 0" aria-valuemin="0" aria-valuemax="100">
      <i :style="{ width: `${dl.pct || 0}%` }" :class="`bar-${dl.state}`" />
    </div>

    <div v-if="dl.state === 'pulling'" class="dl-meta">
      <span>{{ dl.downloaded }} / {{ dl.size }}</span>
      <span>{{ dl.rate }} · {{ dl.eta }}</span>
    </div>

    <div v-if="dl.state === 'error'" class="dl-error mono">
      {{ dl.errorMessage || 'pull failed' }}
    </div>

    <!-- Sharded file expansion on hover -->
    <div v-if="isMulti && expanded" class="dl-files mono">
      <div
        v-for="(f, i) in dl.files"
        :key="i"
        class="dl-file-row"
      >
        <span class="ff-name">{{ f.name }}</span>
        <span class="ff-pct">{{ f.pct ?? 0 }}%</span>
        <span class="dl-bar dl-bar-mini"><i :style="{ width: `${f.pct || 0}%` }" /></span>
      </div>
    </div>

    <div class="dl-actions">
      <template v-if="dl.state === 'pulling'">
        <button class="btn ghost sm" @click="emit('pause', dl)">Pause</button>
        <button class="btn ghost sm" @click="emit('cancel', dl)">Cancel</button>
      </template>
      <template v-else-if="dl.state === 'paused'">
        <button class="btn ghost sm" @click="emit('resume', dl)">Resume</button>
        <button class="btn ghost sm" @click="emit('cancel', dl)">Cancel</button>
      </template>
      <template v-else-if="dl.state === 'queued'">
        <button class="btn ghost sm" @click="emit('cancel', dl)">Cancel</button>
      </template>
      <template v-else-if="dl.state === 'error'">
        <button class="btn ghost sm" @click="emit('retry', dl)">Retry</button>
        <button class="btn ghost sm" @click="emit('remove', dl)">Remove</button>
      </template>
      <template v-else-if="dl.state === 'cancelled'">
        <button class="btn ghost sm" @click="emit('remove', dl)">Remove</button>
      </template>
      <template v-else-if="dl.state === 'verifying'">
        <span class="dl-busy mono">verifying…</span>
      </template>
      <template v-else-if="dl.state === 'completed'">
        <button class="btn ghost sm" @click="emit('remove', dl)">Dismiss</button>
      </template>
    </div>
  </div>
</template>

<style scoped>
.download-row {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-soft);
  position: relative;
}
.download-row:last-child { border-bottom: none; }
.download-row-h {
  display: flex;
  justify-content: space-between;
  font-family: var(--jbm);
  font-size: 11.5px;
  margin-bottom: 6px;
  align-items: center;
  gap: 8px;
}
.dl-name {
  color: var(--fg);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  flex: 1;
}
.dl-name.strike { text-decoration: line-through; color: var(--fg-4); }
.dl-status { font-size: 11px; }
.status-completed { color: var(--ok); }
.status-queued    { color: var(--fg-4); }
.status-paused    { color: var(--warn); }
.status-error     { color: var(--err); }
.status-pulling, .status-verifying { color: var(--fg); }

.dl-bar {
  height: 4px;
  background: var(--bg-2);
  border-radius: 2px;
  overflow: hidden;
}
.dl-bar > i {
  display: block;
  height: 100%;
  background: var(--accent);
  transition: width 0.2s ease;
}
.bar-completed { background: var(--ok) !important; }
.bar-error     { background: var(--err) !important; }
.bar-paused, .bar-cancelled { background: var(--fg-4) !important; }

.dl-meta {
  display: flex;
  justify-content: space-between;
  font-family: var(--jbm);
  font-size: 10px;
  color: var(--fg-4);
  margin-top: 4px;
}
.dl-error {
  margin-top: 6px;
  padding: 8px 10px;
  background: var(--err-soft);
  border: 1px solid var(--err-line);
  border-radius: var(--rad-sm);
  font-size: 11px;
  color: var(--err);
}
.dl-actions {
  display: flex;
  gap: 4px;
  margin-top: 6px;
  flex-wrap: wrap;
}
.dl-busy {
  font-size: 11px;
  color: var(--fg-4);
}
.dl-files {
  margin: 6px 0 4px;
  padding: 6px 0;
  border-top: 1px dashed var(--line-soft);
  border-bottom: 1px dashed var(--line-soft);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.dl-file-row {
  display: grid;
  grid-template-columns: 1fr 40px 80px;
  gap: 8px;
  align-items: center;
  font-size: 10.5px;
  color: var(--fg-3);
}
.ff-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ff-pct  { text-align: right; }
.dl-bar-mini { height: 2px; }
.dl-bar-mini > i { background: var(--fg-3); }
</style>
