<script setup>
/**
 * ConfirmDialog — Modal for destructive action confirmations.
 * Requires 2-step confirmation for broad-impact actions (impact > 1).
 */
import { ref, watch } from 'vue'

const props = defineProps({
  open:    { type: Boolean, default: false },
  title:   { type: String,  required: true },
  message: { type: String,  default: '' },
  danger:  { type: Boolean, default: false },
  confirmLabel: { type: String, default: 'Confirm' },
  /** impact > 1 = requires typing confirmation text */
  impact:  { type: Number,  default: 1 },
  /** text user must type when impact > 1 */
  confirmText: { type: String, default: '' },
  loading: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open', 'confirm', 'cancel'])

const typed = ref('')

watch(() => props.open, (v) => { if (!v) typed.value = '' })

const typeMatch = () => !props.confirmText || typed.value === props.confirmText

function confirm() {
  if (!typeMatch()) return
  emit('confirm')
}

function cancel() {
  emit('update:open', false)
  emit('cancel')
}
</script>

<template>
  <Teleport to="body">
    <Transition name="fade">
      <div
        v-if="open"
        class="dialog-overlay"
        role="dialog"
        aria-modal="true"
        :aria-labelledby="'confirm-title'"
        @click.self="cancel"
      >
        <div class="dialog-box">
          <h2 id="confirm-title" class="dialog-title" :class="{ 'is-danger': danger }">
            {{ title }}
          </h2>
          <p v-if="message" class="dialog-message">{{ message }}</p>
          <slot />

          <!-- 2-step confirmation for high-impact actions -->
          <div v-if="impact > 1 && confirmText" class="confirm-type-wrap">
            <label class="confirm-type-label">
              Type <code class="confirm-code">{{ confirmText }}</code> to confirm:
            </label>
            <input
              v-model="typed"
              class="confirm-input"
              type="text"
              :placeholder="confirmText"
              autocomplete="off"
              spellcheck="false"
            />
          </div>

          <div class="dialog-actions">
            <button type="button" class="btn-ghost" @click="cancel" :disabled="loading">
              Cancel
            </button>
            <button
              type="button"
              class="btn-action"
              :class="{ 'btn-danger': danger }"
              :disabled="loading || !typeMatch()"
              @click="confirm"
            >
              <span v-if="loading" class="spinner" aria-hidden="true" />
              {{ loading ? 'Working…' : confirmLabel }}
            </button>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: 300;
  background: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 16px;
}

.dialog-box {
  background: var(--color-surface);
  border: 1px solid var(--color-border-hi);
  border-radius: var(--radius-xl);
  padding: 24px;
  width: min(420px, 100%);
  box-shadow: 0 24px 64px rgba(0, 0, 0, 0.6);
}

.dialog-title {
  font-size: 16px;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 8px;
}
.dialog-title.is-danger { color: var(--color-danger); }

.dialog-message {
  font-size: 13px;
  color: var(--color-fg-muted);
  margin: 0 0 16px;
  line-height: 1.6;
}

.confirm-type-wrap {
  margin: 12px 0 16px;
}
.confirm-type-label {
  display: block;
  font-size: 12px;
  color: var(--color-fg-muted);
  margin-bottom: 6px;
}
.confirm-code {
  font-family: var(--font-mono);
  font-size: 11px;
  padding: 1px 5px;
  border-radius: 3px;
  background: var(--color-surface-3);
  color: var(--color-danger);
}
.confirm-input {
  width: 100%;
  padding: 7px 10px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: var(--color-surface-2);
  color: var(--color-fg);
  font-family: var(--font-mono);
  font-size: 13px;
  outline: none;
  transition: border-color 0.1s;
  box-sizing: border-box;
}
.confirm-input:focus { border-color: var(--color-border-hi); }

.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 20px;
}

.btn-ghost {
  padding: 7px 16px;
  border-radius: var(--radius);
  border: 1px solid var(--color-border);
  background: transparent;
  color: var(--color-fg-muted);
  font-family: var(--font-mono);
  font-size: 12px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-hi); color: var(--color-fg); }
.btn-ghost:disabled { opacity: 0.5; cursor: not-allowed; }

.btn-action {
  padding: 7px 18px;
  border-radius: var(--radius);
  border: none;
  background: var(--hal0-accent);
  color: #000;
  font-family: var(--font-mono);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 7px;
  transition: background 0.15s;
}
.btn-action:hover:not(:disabled) { background: var(--hal0-accent-hover); }
.btn-action:disabled { opacity: 0.45; cursor: not-allowed; }
.btn-action.btn-danger { background: var(--color-danger); color: #fff; }
.btn-action.btn-danger:hover:not(:disabled) { background: color-mix(in oklch, var(--color-danger) 90%, #fff); }

.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid rgba(255,255,255,0.3);
  border-top-color: white;
  border-radius: 50%;
  animation: spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes spin { to { transform: rotate(360deg); } }

.fade-enter-active, .fade-leave-active { transition: opacity 0.12s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
