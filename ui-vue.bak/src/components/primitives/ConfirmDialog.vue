<script setup>
/**
 * primitives/ConfirmDialog.vue — recoverable + destructive confirm modal.
 *
 * Mirrors the React `ConfirmDialog` in
 *   /tmp/hal0-design-v3/dash/primitives.jsx (lines 78–121)
 *
 * Wraps `primitives/Modal.vue` (not the v1 `ConfirmDialog.vue` in the
 * parent components/ dir — that one stays bound to v1 surfaces).
 *
 * Variants
 * ────────
 *   - Recoverable (default) — neutral btn, footer reads
 *     "You can undo this later."
 *   - `destructive=true` — red btn, footer reads
 *     "This action is permanent." + eyebrow "Destructive · cannot be undone"
 *   - `typeToConfirm="some text"` — adds a mono input; the confirm
 *     button is disabled until the input value matches exactly.
 */
import { ref, watch } from 'vue'
import Modal from './Modal.vue'

const props = defineProps({
  open:          { type: Boolean, default: false },
  onCancel:      { type: Function, default: () => {} },
  onConfirm:     { type: Function, default: () => {} },
  title:         { type: String, required: true },
  message:       { type: String, default: '' },
  confirmLabel:  { type: String, default: 'Confirm' },
  cancelLabel:   { type: String, default: 'Cancel' },
  destructive:   { type: Boolean, default: false },
  typeToConfirm: { type: String, default: null },
})

const typed = ref('')

// Mirror React `useEffect(() => { if (open) setTyped("") }, [open])`.
watch(() => props.open, (v) => { if (v) typed.value = '' })

const canConfirm = () => !props.typeToConfirm || typed.value === props.typeToConfirm

function handleConfirm() {
  if (!canConfirm()) return
  props.onConfirm()
}
</script>

<template>
  <Modal
    :open="open"
    :on-close="onCancel"
    :title="title"
    :eyebrow="destructive ? 'Destructive · cannot be undone' : ''"
    :width="520"
  >
    <div class="cd-message" :class="{ 'has-input': typeToConfirm }">{{ message }}</div>
    <div v-if="typeToConfirm">
      <div class="cd-input-label mono">
        Type <span class="cd-input-token">{{ typeToConfirm }}</span> to confirm:
      </div>
      <input
        v-model="typed"
        class="input mono cd-input"
        type="text"
        :placeholder="typeToConfirm"
        autocomplete="off"
        spellcheck="false"
      />
    </div>
    <template #foot>
      <span class="cd-foot-note">
        {{ destructive ? 'This action is permanent.' : 'You can undo this later.' }}
      </span>
      <span class="cd-foot-actions">
        <button type="button" class="btn ghost sm" @click="onCancel">{{ cancelLabel }}</button>
        <button
          type="button"
          :class="['btn', 'sm', { danger: destructive, 'cd-confirm-destructive': destructive }]"
          :disabled="!canConfirm()"
          @click="handleConfirm"
        >{{ confirmLabel }}</button>
      </span>
    </template>
  </Modal>
</template>

<style scoped>
.cd-message {
  font-size: 13px;
  color: var(--fg-2);
  line-height: 1.6;
}
.cd-message.has-input { margin-bottom: 16px; }

.cd-input-label {
  font-size: 11px;
  color: var(--fg-4);
  margin-bottom: 6px;
}
.cd-input-token { color: var(--err); }

.cd-input {
  width: 100%;
  box-sizing: border-box;
}

.cd-foot-note { color: var(--fg-4); }
.cd-foot-actions { display: inline-flex; gap: 8px; }

/* Destructive btn override — design uses an inline style with
 * background/border-color = var(--err) and color #0a0a0a. Class-scoped
 * here so we don't leak into other `.btn.danger` usages elsewhere. */
.cd-confirm-destructive {
  background: var(--err);
  border-color: var(--err);
  color: #0a0a0a;
}
.cd-confirm-destructive:hover { filter: brightness(1.06); }
</style>
