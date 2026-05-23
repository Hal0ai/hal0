<script setup>
import { useToastsStore } from '../stores/toasts.js'

const toasts = useToastsStore()

const typeStyles = {
  success: 'toast-success',
  error:   'toast-error',
  warning: 'toast-warning',
  info:    'toast-info',
}
</script>

<template>
  <Teleport to="body">
    <div
      class="toast-container"
      role="region"
      aria-label="Notifications"
      aria-live="polite"
    >
      <TransitionGroup name="toast" tag="div" class="toast-list">
        <div
          v-for="t in toasts.toasts"
          :key="t.id"
          class="toast"
          :class="typeStyles[t.type] ?? 'toast-info'"
          role="alert"
          @click="toasts.remove(t.id)"
        >
          <span class="toast-msg">{{ t.message }}</span>
          <button
            class="toast-close"
            type="button"
            :aria-label="'Dismiss notification'"
            @click.stop="toasts.remove(t.id)"
          >
            <svg width="12" height="12" fill="none" stroke="currentColor" viewBox="0 0 24 24" stroke-width="2.5" aria-hidden="true">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 18L18 6M6 6l12 12"/>
            </svg>
          </button>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.toast-container {
  position: fixed;
  bottom: 20px;
  right: 20px;
  z-index: 9999;
  width: 320px;
  max-width: calc(100vw - 32px);
  pointer-events: none;
}

.toast-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.toast {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 11px 14px;
  border-radius: var(--radius-lg);
  border: 1px solid;
  font-size: 13px;
  line-height: 1.4;
  cursor: pointer;
  pointer-events: all;
  backdrop-filter: blur(8px);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  transition: opacity 0.1s;
}
.toast:hover { opacity: 0.9; }

.toast-msg  { flex: 1; }
.toast-close {
  background: transparent;
  border: none;
  cursor: pointer;
  color: inherit;
  opacity: 0.6;
  padding: 0;
  flex-shrink: 0;
  line-height: 1;
}
.toast-close:hover { opacity: 1; }

.toast-success {
  background: color-mix(in oklch, var(--color-success) 15%, var(--color-surface));
  border-color: color-mix(in oklch, var(--color-success) 30%, transparent);
  color: color-mix(in oklch, var(--color-success) 90%, var(--color-fg));
}
.toast-error {
  background: color-mix(in oklch, var(--color-danger) 15%, var(--color-surface));
  border-color: color-mix(in oklch, var(--color-danger) 30%, transparent);
  color: color-mix(in oklch, var(--color-danger) 90%, var(--color-fg));
}
.toast-warning {
  background: color-mix(in oklch, var(--color-warning) 15%, var(--color-surface));
  border-color: color-mix(in oklch, var(--color-warning) 30%, transparent);
  color: color-mix(in oklch, var(--color-warning) 90%, var(--color-fg));
}
.toast-info {
  background: color-mix(in oklch, var(--color-info) 15%, var(--color-surface));
  border-color: color-mix(in oklch, var(--color-info) 30%, transparent);
  color: color-mix(in oklch, var(--color-info) 90%, var(--color-fg));
}

/* Transition */
.toast-enter-active { transition: all 0.2s ease; }
.toast-leave-active { transition: all 0.18s ease; }
.toast-enter-from   { opacity: 0; transform: translateX(20px); }
.toast-leave-to     { opacity: 0; transform: translateX(20px); }
.toast-move         { transition: transform 0.2s ease; }
</style>
