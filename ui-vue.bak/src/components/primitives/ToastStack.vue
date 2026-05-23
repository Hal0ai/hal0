<script setup>
/**
 * primitives/ToastStack.vue — top-right toast queue renderer.
 *
 * Reads `useToastStore.queue` (the v2 toast store from
 * `stores/toast.js`) and renders one `<Toast>` per entry. Auto-removal
 * is handled inside the store via setTimeout — this component is
 * purely presentational.
 *
 * NOTE: this slice (#167) ships the primitive but does NOT mount it
 * at App root level — that's slice #5 chrome's job. The component is
 * importable from views/spec routes today (the primitives spec mounts
 * it to verify queue + auto-removal).
 */
import { useToastStore } from '../../stores/toast.js'
import Toast from './Toast.vue'

const toasts = useToastStore()
</script>

<template>
  <Teleport to="body">
    <div class="hal0-toast-stack" data-testid="toast-stack" aria-live="polite">
      <TransitionGroup name="hal0-toast" tag="div" class="hal0-toast-list">
        <Toast
          v-for="t in toasts.queue"
          :key="t.id"
          :msg="t.msg"
          :kind="t.kind"
          :on-dismiss="() => toasts.dismiss(t.id)"
          :data-toast-id="t.id"
        />
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<style scoped>
.hal0-toast-stack {
  position: fixed;
  top: 16px;
  right: 16px;
  z-index: 9999;
  pointer-events: none;
}
.hal0-toast-list {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}
.hal0-toast-enter-active,
.hal0-toast-leave-active { transition: opacity 0.18s ease, transform 0.18s ease; }
.hal0-toast-enter-from   { opacity: 0; transform: translateX(20px); }
.hal0-toast-leave-to     { opacity: 0; transform: translateX(20px); }
.hal0-toast-move         { transition: transform 0.18s ease; }
</style>
