<script setup>
/**
 * BackendUninstallModal.vue — slot fan-out confirmation.
 *
 * Mirrors the React `BackendUninstallModal` in
 *   /tmp/hal0-design/hal0-v2/project/dash/flow-modals.jsx (lines 73–127).
 *
 * "N slots will lose this backend" — when `usedBy` is non-empty the
 * footer adds a `[Move slots first →]` link that routes to /slots, plus
 * an explicit danger-button `[Uninstall anyway]`. When zero, just the
 * uninstall.
 */
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import Modal from '../primitives/Modal.vue'
import { useSystemStore } from '../../stores/system.js'
import { useBackendsStore } from '../../stores/backends.js'
import { useToastStore } from '../../stores/toast.js'

const props = defineProps({
  open: { type: Boolean, default: false },
  backend: { type: Object, default: null },
  onClose: { type: Function, default: () => {} },
})

const router = useRouter()
const system = useSystemStore()
const backends = useBackendsStore()
const toasts = useToastStore()

const id = computed(() => props.backend?.id || '')
const slotsUsing = computed(() => {
  // Prefer the row's own usedBy list (authoritative per /api/backends).
  // Fall back to a heuristic over system.slots so dev/mock renders too.
  const named = props.backend?.usedBy
  if (Array.isArray(named) && named.length > 0) {
    return system.slots.filter((s) => named.includes(s.name))
  }
  return system.slots.filter((s) => {
    if (id.value.includes('llamacpp') && (s.device || '').startsWith('gpu')) return true
    if (id.value === 'flm:npu' && s.device === 'npu') return true
    if (id.value === 'kokoro' && s.type === 'tts') return true
    if (id.value.includes('sdcpp') && s.type === 'image') return true
    return false
  })
})
const count = computed(() => slotsUsing.value.length)

function moveSlots() {
  props.onClose()
  router.push('/slots')
}

async function uninstall() {
  if (!id.value) return
  toasts.push(`Uninstalling ${id.value}`, 'warn')
  try {
    await backends.uninstall(id.value)
    toasts.push(`${id.value} uninstalled`, 'ok')
  } catch (e) {
    toasts.push(e?.message || 'Uninstall failed', 'err')
  } finally {
    props.onClose()
  }
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="onClose"
    eyebrow="Backends · uninstall"
    :title="`Uninstall ${id || 'backend'}?`"
    :width="580"
  >
    <span data-testid="backend-uninstall-modal" hidden></span>
    <p class="copy">
      Removes <span class="mono fg">{{ id }}</span> from
      <span class="mono fg">/opt/lemonade/bin</span>. Models on disk are not
      touched; they just won't have a backend to load through.
    </p>

    <div v-if="count > 0" class="warn-box">
      <div class="warn-head mono">⚠ slots using this backend</div>
      <div
        v-for="s in slotsUsing"
        :key="s.name"
        class="slot-row mono"
      >
        <span class="slot-name">{{ s.name }}</span>
        <span class="slot-model">{{ s.model || '—' }}</span>
        <span class="slot-x">will go offline</span>
      </div>
    </div>
    <div v-else class="ok-box mono">
      No slots currently use this backend. Safe to uninstall.
    </div>

    <template #foot>
      <span :class="['foot-l', count > 0 ? 'text-err' : '']">
        {{ count }} slot{{ count === 1 ? '' : 's' }} will lose this backend.
      </span>
      <span class="foot-actions">
        <button class="btn-ghost sm" type="button" @click="onClose">Cancel</button>
        <button
          v-if="count > 0"
          class="btn-ghost sm"
          type="button"
          data-testid="backend-uninstall-move-first"
          @click="moveSlots"
        >Move slots first →</button>
        <button
          class="btn-danger sm"
          type="button"
          data-testid="backend-uninstall-confirm"
          @click="uninstall"
        >Uninstall anyway</button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.copy { font-size: 13px; color: var(--color-fg-muted); line-height: 1.6; margin: 0 0 14px; }
.mono { font-family: var(--font-mono); }
.fg { color: var(--color-fg); }
.text-err { color: var(--color-danger); }

.warn-box {
  padding: 12px 14px;
  background: color-mix(in oklch, var(--color-danger) 10%, var(--color-surface));
  border: 1px solid color-mix(in oklch, var(--color-danger) 30%, transparent);
  border-radius: var(--radius);
}
.warn-head {
  font-size: 11px;
  color: var(--color-danger);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 8px;
}
.slot-row {
  display: grid;
  grid-template-columns: 100px 1fr auto;
  gap: 12px;
  padding: 6px 0;
  font-size: 12px;
  border-bottom: 1px solid color-mix(in srgb, var(--color-danger) 18%, transparent);
}
.slot-row:last-child { border-bottom: none; }
.slot-name { color: var(--color-fg); font-weight: 500; }
.slot-model { color: var(--color-fg-muted); }
.slot-x { color: var(--color-danger); }

.ok-box {
  padding: 10px 12px;
  background: color-mix(in oklch, var(--color-success) 10%, var(--color-surface));
  border: 1px solid color-mix(in oklch, var(--color-success) 30%, transparent);
  border-radius: var(--radius);
  color: var(--color-success);
  font-size: 12px;
}

.foot-l { color: var(--color-fg-muted); font-size: 11px; }
.foot-actions { display: inline-flex; gap: 8px; }

.btn-ghost {
  padding: 5px 12px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
}
.btn-danger {
  padding: 5px 12px;
  border-radius: var(--radius);
  border: 1px solid color-mix(in srgb, var(--color-danger) 35%, var(--color-border));
  background: transparent;
  color: var(--color-danger);
  font-family: var(--font-mono);
  font-size: 11.5px;
  cursor: pointer;
}
.btn-danger:hover { background: color-mix(in srgb, var(--color-danger) 12%, transparent); }
</style>
